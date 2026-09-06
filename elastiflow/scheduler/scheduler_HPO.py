from abc import ABC, abstractmethod
import time
from typing import List

from elastiflow.config.constants_HPO import AVG_WORKFLOW_ITERATIONS, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, MIN_RUNTIME, RESOURCE_REQUEST_TIMEOUT, SIMULATE
from elastiflow.scripts.create_instance_HPO import deleteInstanceFromIp
from elastiflow.scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from elastiflow.utils.metrics_HPO import MetricsHPO as Metrics
from elastiflow.utils.resource import getEstimate
from elastiflow.resource_manager.instance import Instance, OnPremInstance
from elastiflow.utils.request import ExecutorRequest, getConfig, getExecutor, sendRequest
from elastiflow.execution.backend import backend_for

class Scheduler_HPO(ABC):
    """
    HPO-specific base scheduler class
    Uses HPO constants, executor, and runtime functions
    """

    def __init__(self, queue, finish_queue, resource_request_queue):
        self.queue = queue
        self.finish_queue = finish_queue
        self.resource_request_queue = resource_request_queue
        self.metrics = Metrics()

    @abstractmethod
    def run(self, queue):
        pass

    def allocateResources(self, constraints):
        ips, alloc_resources = {}, []
        instances = self.resource_manager.getResources()
        count, instances = self.checkResources(instances, constraints['min_instances'])
        if count == constraints['min_instances']:
            ips, alloc_resources = self.resource_manager.allocateResources(instances)
        return ips, alloc_resources

    def sendWorkflowForExecution(self, wf_plan, ips, sim, deadline):
        """
        Note: HPO schedulers should override this with sendWorkflowForExecutionHPO
        This base implementation is for compatibility but should not be called directly
        """
        backend = backend_for(sim)
        # Send to the executor node - workflow parsing must be handled there
        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips, # {type: {name: [ips]/count}}
            "deadline": deadline
        }

        executor, on_demand_type = getExecutor(ips, sim)
        # TODO: What if executor is not created
        if on_demand_type:
            request['hosts']['on-demand'][on_demand_type] =  (request['hosts']['on-demand'][on_demand_type][0], [executor])
        backend.start_workflow(request, executor)
    def processJobCompletion(self, sim=None, mb=None):
        backend = backend_for(sim)
        print('HPO Scheduler started listening to completed jobs...')
        while True:
            try:
                data = backend.completions.peek()
                if data:
                    data = eval(data)
                    wf_id = data.get('wf-id')

                    # Guard against duplicate completion messages (e.g., TCP retry).
                    # Without this, returnResources().pop() on an already-completed
                    # workflow would KeyError and crash this thread permanently.
                    if not self.resource_manager.getWorkflow(wf_id):
                        print(f'[WARN] Duplicate completion for {wf_id}, ignoring')
                        backend.completions.pop()
                        backend.sleep(1)
                        continue

                    # Safety-net: terminate on-demand instances from scheduler side.
                    # The executor's finally block should have done this already,
                    # but if the executor crashed or never reached cleanup, this
                    # ensures on-demand instances don't run forever.
                    self._terminate_ondemand_instances(wf_id, data.get('hosts'), sim)

                    self.resource_manager.returnResources(wf_id)
                    self.metrics.updateDataframe(wf_id, {'exec_start_time': data.get('start-time'), 'finish_time': data.get('finish-time'), 'complete': data.get('complete')})
                    print(f'{wf_id} workflow freed at {backend.now()}')
                    with open('workflow_status.log', 'a') as f:
                        f.write(f'{wf_id} COMPLETED at {backend.now()}\n')
                    backend.completions.pop()
            except Exception as e:
                print(f'[ERROR] processJobCompletion exception: {e}')
                import traceback
                traceback.print_exc()
                # Don't crash the thread — skip this message and continue
                try:
                    backend.completions.pop()
                except Exception:
                    pass
            backend.sleep(60) # NOTE: polling interval

    def _terminate_ondemand_instances(self, wf_id, hosts, sim):
        """
        Safety-net termination of on-demand instances.
        Called from processJobCompletion before returnResources pops the workflow.
        Uses hosts dict from the completion message; falls back to stored workflow data.
        Idempotent: if executor already terminated them, deleteInstanceFromIp finds nothing.
        """
        if sim or SIMULATE:
            return

        # Collect on-demand IPs from completion message hosts
        ondemand_ips = []
        if hosts:
            for name, val in hosts.get('on-demand', {}).items():
                if isinstance(val, (tuple, list)) and len(val) >= 2:
                    ondemand_ips.extend(val[1])

        # Fallback: extract from stored workflow data (before returnResources pops it)
        if not ondemand_ips:
            wf_data = self.resource_manager.getWorkflow(wf_id)
            if wf_data:
                instances = wf_data[0]  # [(instance_obj, count, [ips])]
                for instance, count, ips in instances:
                    if instance.type == 'on-demand' and ips:
                        ondemand_ips.extend(ips)

        if ondemand_ips:
            print(f"[SAFETY-NET] Terminating on-demand instances for {wf_id}: {ondemand_ips}")
            try:
                deleteInstanceFromIp(ondemand_ips, sim)
            except Exception as e:
                print(f"[ERROR] Safety-net termination failed for {wf_id}: {e}")

    # request = {"wf-id", "count", "iteration": ind, "tinyda-iterations", "client-ip", "request-time"}
    def allocateNewResources(self, request, sim):
        backend = backend_for(sim)
        if backend.now() - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        (instances, budget, _, start_time, model) = self.resource_manager.getWorkflow(request['wf-id'])
        free_resources = self.resource_manager.getResources()
        used_budget = self.metrics.computeCost(request['wf-id'], backend.now())
        available_budget = max(0, budget - used_budget) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, model) # {obj: count}
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances) # alloc_resource = {obj: (count, [ips])}
        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def sendNewResources(self, wf_id, ips, alloc_resources, sim, client_ip):

        # Send to the executor node
        backend = backend_for(sim)
        new_req = {
            "request": ExecutorRequest.REQUEST_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": ips, # {cluster: {name: (count, [ips])}}
        }
        print(f"{wf_id} allocated additional resources: ", ips)
        backend.notify_resources(new_req, client_ip)
        if alloc_resources:
            self.resource_manager.updateWorkflowResources(wf_id, alloc_resources)
            self.metrics.updateResources(wf_id, alloc_resources, backend.now())


    # request = {"wf-id", "count", "request-time", "client-ip"}
    def freeResources(self, request, sim):
        backend = backend_for(sim)
        if backend.now() - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return
        (instances, _, deadline, _, _) = self.resource_manager.getWorkflow(request['wf-id'])

        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        # Only free resources if next iteration can happen in available time
        available_time = max(0, deadline - backend.now()) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        freed_count = 0
        to_free_instances = []
        if available_time > MIN_ITERATION_RUNTIME:
            # Free the last n instances
            for i in range(len(instances)-1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count']-freed_count, count)
                to_free_instances.append((instance, to_free, ips[-to_free:])) # last n vals
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break
            self.resource_manager.returnResources(request['wf-id'], to_free_instances)

            # Terminate freed on-demand EC2 instances immediately (don't wait for workflow end)
            if not sim and not SIMULATE:
                for instance, count, ips in to_free_instances:
                    if instance.type == 'on-demand' and ips:
                        print(f"[SCALE-DOWN] Terminating {len(ips)} freed on-demand instances: {ips}")
                        try:
                            deleteInstanceFromIp(ips, sim)
                        except Exception as e:
                            print(f"[ERROR] Scale-down termination failed: {e}")

        # Free resources
        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, sim, request.get('client-ip', None))

    def sendFreedResources(self, wf_id, to_free_instances, instances, response_instances, sim, client_ip):
        # Send hosts to be freed to executor
        backend = backend_for(sim)
        new_req = {
            "request": ExecutorRequest.FREE_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": response_instances # {cluster: {name: (count, ips)}}
        }
        print(f"HPO Scheduler freeing {response_instances} for {wf_id} ")
        backend.notify_resources(new_req, client_ip)
        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            self.metrics.updateResources(wf_id, to_free_instances, None, backend.now())

    def checkResources(self, instances: List[Instance], min_instances: int) -> tuple[int, List[tuple[Instance, int]]]:

        currently_acquired = 0
        acquired_instances = []

        for instance in instances:

            # Allocate on-prem only if it can be fully allocated
            if isinstance(instance, OnPremInstance):
                if instance.getFreeSlots() >= min_instances:
                    acquired_instances = [(instance, min_instances)]
                    return (min_instances, acquired_instances)
                else:
                    continue

            # Check if enough nodes are available
            to_be_used = min(min_instances-currently_acquired, instance.getFreeSlots())
            if to_be_used:
                # NOTE: For baseline FCFS, do not worry about budget and deadline, the metrics will handle that
                currently_acquired += to_be_used
                acquired_instances.append((instance, to_be_used))
                if currently_acquired == min_instances:
                    break

        return (currently_acquired, acquired_instances)

    # request = {"wf-id": wf_id, "count": n, "iteration": ind, "tinyda-iterations": m}
    # current_resources = {obj: (count, ip)}
    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, request, model) -> List[tuple[Instance, int]]:
        """
        HPO version: uses model parameter instead of mesh
        Uses HPO-specific runtime functions
        """
        if budget < MIN_INSTANCE_COST:
            return []

        # 1. If on-prem is already allocated, allocate possible on-prem instances
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            # Use HPO runtime function - assume g5 for on-prem
            runtime = getRuntime_g5(1, model, request['tinyda-iterations'])
            cost_per_iteration = (runtime / 3600) * instance.cost_per_second  # cost_per_second is $/hour
            instance_cost = getEstimate(cost_per_iteration, 1)  # Already includes tinyda-iterations in runtime
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / instance_cost))
            return [(instance, to_be_used)]

        acquired_count = 0
        acquired_instances = []

        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        for instance in instances:
            # Determine instance type and use appropriate runtime function
            if 'g5' in instance.name.lower():
                runtime = getRuntime_g5(1, model, request['tinyda-iterations'])
            else:  # g4dn
                runtime = getRuntime_g4(1, model, request['tinyda-iterations'])

            cost_per_iteration = (runtime / 3600) * instance.cost_per_second  # cost_per_second is $/hour
            instance_cost = getEstimate(cost_per_iteration, 1)  # Already includes tinyda-iterations
            to_be_used = min(request['count']-acquired_count, instance.getFreeSlots(), int(budget/instance_cost))

            if to_be_used:
                acquired_count += to_be_used
                budget -= to_be_used * instance_cost
                acquired_instances.append((instance, to_be_used))

                if acquired_count == request['count'] or budget < MIN_INSTANCE_COST:
                    return acquired_instances

        # 3. Else, do not allocate

        return acquired_instances

    # Purge and return if workflow has been purged
    def purgeWorkflow(self, wf_plan, sim) -> bool:
        # NOTE: We can have 2 workflow iterations at the least
        backend = backend_for(sim)
        runtime = MIN_ITERATION_RUNTIME + getEstimate(MIN_RUNTIME, 1 + wf_plan['constraints']['tinydaIterations'])
        if backend.now() + runtime > wf_plan['submit_time'] + wf_plan['constraints']['deadline']:
            print(f"HPO Workflow {wf_plan['id']} can no longer be executed, discarding it at {backend.now()}")
            return True
        return False


# ---------------------------------------------------------------------------
# Module-level helpers for instance classification.
#
# getFamily()      : GPU-architecture compatibility key. Two instances share a
#                    family iff they carry the same GPU and may participate in
#                    the same binding under a future Policy B bracket.
# getInstanceKey() : Full inventory key (== Instance.name). Selection scoring
#                    and resize lock both operate on this key, so multiple
#                    sizes within a family are kept distinct end-to-end.
#
# Both are pure functions of the instance name and have no callers yet that
# change behaviour — they exist so the registry in
# scripts/speedup_HPO_runtime.py and the deferred bracket predicate in
# docs/history/POLICY_B_TODO.md can be wired in additively.
# ---------------------------------------------------------------------------
def getFamily(instance_name: str) -> str:
    if 'g4dn' in instance_name or 'on-prem' in instance_name:
        return 'g4'
    if 'g5' in instance_name:
        return 'g5'
    return 'unknown'


def getInstanceKey(instance_name: str) -> str:
    return instance_name
