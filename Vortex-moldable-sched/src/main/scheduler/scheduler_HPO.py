from abc import ABC, abstractmethod
import time
from typing import List

from config.constants_HPO import AVG_WORKFLOW_ITERATIONS, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, MIN_RUNTIME, RESOURCE_REQUEST_TIMEOUT
from executor_HPO import executeWorkflowHPO, processNewResourcesHPO
from scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from utils.metrics_HPO import MetricsHPO as Metrics
from utils.resource import getEstimate
from resource_manager.instance import Instance, OnPremInstance
from utils.request import ExecutorRequest, getConfig, getExecutor, sendRequest
from utils.sim import getTime, peekElement, removeElement

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
        if sim:
            sim.process(executeWorkflowHPO, request, sim)
        else:
            sendRequest(executor, getConfig('executor-incoming-port'), request)


    def processJobCompletion(self, sim=None, mb=None):
        print('HPO Scheduler started listening to completed jobs...')
        while True:
            data = peekElement(mb, self.finish_queue)
            if data:
                data = eval(data)
                self.resource_manager.returnResources(data.get('wf-id'))
                self.metrics.updateDataframe(data.get('wf-id'), {'exec_start_time': data.get('start-time'), 'finish_time': data.get('finish-time'), 'complete': data.get('complete')})
                print(f'{data.get("wf-id")} workflow freed at {getTime(sim)}')
                removeElement(mb, self.finish_queue)
            (sim or time).sleep(60) # NOTE: polling interval

    # request = {"wf-id", "count", "iteration": ind, "tinyda-iterations", "client-ip", "request-time"}
    def allocateNewResources(self, request, sim):
        if getTime(sim) - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        (instances, budget, _, start_time, model) = self.resource_manager.getWorkflow(request['wf-id'])
        free_resources = self.resource_manager.getResources()
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = max(0, budget - used_budget) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, model) # {obj: count}
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances) # alloc_resource = {obj: (count, [ips])}
        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def sendNewResources(self, wf_id, ips, alloc_resources, sim, client_ip):

        # Send to the executor node
        new_req = {
            "request": ExecutorRequest.REQUEST_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": ips, # {cluster: {name: (count, [ips])}}
        }
        print(f"{wf_id} allocated additional resources: ", ips)
        if sim:
            sim.process(processNewResourcesHPO, new_req)
        else:
            sendRequest(client_ip, getConfig('executor-incoming-port'), new_req)
        if alloc_resources:
            self.resource_manager.updateWorkflowResources(wf_id, alloc_resources)
            self.metrics.updateResources(wf_id, alloc_resources, getTime(sim))


    # request = {"wf-id", "count", "request-time", "client-ip"}
    def freeResources(self, request, sim):
        if getTime(sim) - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return
        (instances, _, deadline, _, _) = self.resource_manager.getWorkflow(request['wf-id'])

        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        # Only free resources if next iteration can happen in available time
        available_time = max(0, deadline - getTime(sim)) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
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
        # Free resources
        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, sim, request.get('client-ip', None))

    def sendFreedResources(self, wf_id, to_free_instances, instances, response_instances, sim, client_ip):
        # Send hosts to be freed to executor
        new_req = {
            "request": ExecutorRequest.FREE_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": response_instances # {cluster: {name: (count, ips)}}
        }
        print(f"HPO Scheduler freeing {response_instances} for {wf_id} ")
        if sim:
            sim.process(processNewResourcesHPO, new_req)
        else:
            sendRequest(client_ip, getConfig('executor-incoming-port'), new_req)
        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            self.metrics.updateResources(wf_id, to_free_instances, None, getTime(sim))

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
            cost_per_iteration = instance.cost_per_second * runtime
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

            cost_per_iteration = instance.cost_per_second * runtime
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
        runtime = MIN_ITERATION_RUNTIME + getEstimate(MIN_RUNTIME, 1 + wf_plan['constraints']['tinydaIterations'])
        if getTime(sim) + runtime > wf_plan['submit_time'] + wf_plan['constraints']['deadline']:
            print(f"HPO Workflow {wf_plan['id']} can no longer be executed, discarding it at {getTime(sim)}")
            return True
        return False
