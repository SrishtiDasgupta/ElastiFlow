from abc import ABC, abstractmethod
import math
import time
from typing import List

from elastiflow.config.constants import (
    AVG_WORKFLOW_ITERATIONS, CHAINS_PER_NODE, CLOSENESS_TOLERANCE, COLD_START_TIME,
    DEADLINE_BUFFER, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, MIN_RUNTIME,
    OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR,
    RESOURCE_REQUEST_TIMEOUT, SPEEDUP_THRESHOLD,
)
from elastiflow.executor import executeWorklow, processNewResources
from elastiflow.scripts.speedup import getRuntime
from elastiflow.utils.metrics import Metrics
from elastiflow.utils.resource import getEstimate
from elastiflow.resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from elastiflow.utils.request import ExecutorRequest, getConfig, getExecutor, sendRequest
from elastiflow.utils.sim import getTime, peekElement, removeElement

class Scheduler(ABC):

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
            sim.process(executeWorklow, request, sim)
        else:
            sendRequest(executor, getConfig('executor-incoming-port'), request)                    


    def processJobCompletion(self, sim=None, mb=None):
        print('Scheduler started listening to completed jobs...')
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
        
        (instances, budget, _, start_time, mesh) = self.resource_manager.getWorkflow(request['wf-id'])
        free_resources = self.resource_manager.getResources()
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = max(0, budget - used_budget) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, mesh) # {obj: count}
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
            sim.process(processNewResources, new_req)
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
        print(f"Scheduler freeing {response_instances} for {wf_id} ")
        if sim:
            sim.process(processNewResources, new_req)
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
    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, request, mesh) -> List[tuple[Instance, int]]:

        if budget < MIN_INSTANCE_COST:
            return []
    
        # 1. If on-prem is already allocated, allocate possible on-prem instances
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            cost_per_iteration = instance.cost_per_second * getRuntime(1, mesh, instance.name)
            instance_cost = getEstimate(cost_per_iteration, request['tinyda-iterations'])
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / instance_cost))
            return [(instance, to_be_used)]
        
        acquired_count = 0
        acquired_instances = []
        
        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        for instance in instances:
            cost_per_iteration = instance.cost_per_second * getRuntime(1, mesh, instance.name)
            instance_cost = getEstimate(cost_per_iteration, request['tinyda-iterations'], 1)
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
            print(f"Workflow {wf_plan['id']} can no longer be executed, discarding it at {getTime(sim)}")
            return True
        return False

    # ------------------------------------------------------------------
    # Rich moldable negotiation (ported from FCFS_Optimized so every
    # moldable scheduler — EDF, HEFT, Rank — gets the same sophisticated
    # iteration-boundary scale-up / scale-down logic with per-iteration
    # budget/time factors, speedup-aware scaling, and on-demand cloud
    # bursting. Used in place of the simpler allocateNewResources /
    # freeResources path when the scheduler's run() loop wants moldable
    # behaviour.
    # ------------------------------------------------------------------

    def checkCloseness(self, instance: Instance, runtimes_list, mesh) -> bool:
        """True iff `instance`'s runtime (for 1 node, given mesh) is within
        ±CLOSENESS_TOLERANCE of any runtime in `runtimes_list` — used to
        decide whether the instance is a sensible addition to a workflow's
        existing fleet. Tolerance is taken from config.constants so
        CLI-driven ablation can override it before this module's import."""
        runtime = getRuntime(1, mesh, instance.name)
        if isinstance(instance, CloudOnDemandInstance):
            runtime += COLD_START_TIME
        return any(math.isclose(runtime, x, rel_tol=CLOSENESS_TOLERANCE) for x in runtimes_list)

    def processFreeRequest(self, request, sim):
        """Rich moldable scale-up / scale-down negotiation. Decides whether
        to free or acquire resources for the workflow based on what the
        next iteration actually needs, the per-iteration budget/time
        factor schedule, and the available speedup of remaining nodes.

        This method handles BOTH REQUEST_RESOURCE and FREE_RESOURCE events
        — it makes its own up/down decision rather than trusting the
        executor's hint, so the caller can route both event types here.
        """
        (instances, budget, deadline, _, mesh) = self.resource_manager.getWorkflow(request['wf-id'])

        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]

        cur_instance: Instance = instances[-1][0]
        cur_count = instances[-1][1]
        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = 0
            for inst_tuple in instances:
                cur_count += inst_tuple[1]

        # Probe whether the workflow can complete with CHAINS_PER_NODE, then
        # CHAINS_PER_NODE-1, ..., 1 chains per node within the per-iteration
        # time budget. If so, scale DOWN to the minimum count that still fits.
        chains_per_node = CHAINS_PER_NODE
        request['count'] = None
        min_needed_count = request['chains']
        runtime_per_model = getRuntime(1, mesh, cur_instance.name)
        while chains_per_node > 0:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']
            if runtime < available_time:
                min_needed_count = request['chains'] // chains_per_node + bool(request['chains'] % chains_per_node)
                if cur_count >= min_needed_count:
                    request['count'] = cur_count - min_needed_count
                    self.freeResourcesMoldable(instances, request, sim)
                    self.metrics.recordScaleDownAttempt(request, request['count'])
                    return
                else:
                    break
            else:
                chains_per_node -= 1

        # Scale UP path: budget-aware, speedup-aware, OD-burst-aware.
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]
        free_resources = self.resource_manager.getResources()
        if request['count'] is None:
            request['count'] = min_needed_count - cur_count
        alloc_instances = self.checkNewResourcesMoldable(
            free_resources, instances, available_budget, available_time,
            request, mesh,
        )
        # Path = 'on_prem' if the workflow's first held instance is slurm,
        # 'cloud' otherwise. checkNewResources* uses this to decide which
        # tier to consider for scale-up, so the workflow's initial
        # allocation locks in its scale-up pool.
        path = 'on_prem' if isinstance(instances[0][0], OnPremInstance) else 'cloud'
        self.metrics.recordScaleUpAttempt(request, alloc_instances, path=path)
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)
        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def freeResourcesMoldable(self, instances, request, sim):
        """Variant of freeResources that takes the workflow's instance list
        directly (as already fetched by processFreeRequest), to avoid a
        second resource_manager round-trip."""
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []
        if request['count'] > 0:
            for i in range(len(instances)):
                instance, count, ips = instances[i]
                to_free = min(request['count'] - freed_count, count)
                to_free_instances.append((instance, to_free, ips[-to_free:]))
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break
            self.resource_manager.returnResources(request['wf-id'], to_free_instances)
        self.sendFreedResources(
            request['wf-id'], to_free_instances, instances, response_instances,
            sim, request.get('client-ip', None),
        )

    def checkNewResourcesMoldable(self, resources, current_resources, budget,
                                  available_runtime, request, mesh):
        """Rich scale-up planner — picks instances by speedup × budget,
        prefers same-type or runtime-close cloud instances, accounts for
        cold-start cost when bursting to on-demand. Falls back to a
        cost-ranked greedy fill if the moldable-cloud path can't fit."""
        # 1. If on-prem is already allocated, try to add on-prem nodes,
        #    backing off chains_per_node until something fits in budget
        #    AND gives a speedup > SPEEDUP_THRESHOLD.
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            free_slots = instance.getFreeSlots()
            to_be_used = 0
            if free_slots >= request['count']:
                nodes_per_chain = request['chains'] - request['count'] + free_slots // request['chains']
                while nodes_per_chain > 0:
                    speedup_runtime = getRuntime(nodes_per_chain, mesh, instance.name)
                    cost_per_node_iteration = instance.cost_per_second * speedup_runtime
                    total_cost = getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1,
                                             nodes_per_chain * request['chains'])
                    if total_cost < budget and \
                       getRuntime(nodes_per_chain - 1, mesh, instance.name) / speedup_runtime > SPEEDUP_THRESHOLD:
                        if speedup_runtime * request['tinyda-iterations'] < available_runtime:
                            to_be_used = (nodes_per_chain * request['chains']) - (request['chains'] - request['count'])
                            print(f'Moldable onprem with {nodes_per_chain} nodes per chain')
                            break
                        else:
                            return []
                    else:
                        nodes_per_chain -= 1
            return [(instance, to_be_used)]

        # 2. Try the "moldable cloud" path — same-type or close-runtime
        #    instances first, with explicit OD cold-start accounting.
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        cur_instances = set()
        cur_instance_runtimes = []
        for inst_tuple in current_resources:
            cur_instances.add(inst_tuple[0].name)
            cur_instance_runtimes.append(getRuntime(1, mesh, inst_tuple[0].name))
        free_slots = 0
        instances_to_be_used = []
        ondemandFlag = False
        for inst in instances:
            free_nodes = inst.getFreeSlots()
            # BUGFIX: closeness check must use `inst` (the candidate) not
            # `instance` (the workflow's first existing instance, fixed
            # outside the loop). The original code used the outer var,
            # making the closeness filter a no-op — every candidate passed
            # by virtue of trivially matching itself. With the fix, the
            # filter actually rejects candidates whose 1-node runtime is
            # more than ±15% off any current-fleet runtime, keeping the
            # workflow's fleet homogeneous and avoiding slowest-host
            # bottlenecks.
            if free_nodes and (inst.name in cur_instances or self.checkCloseness(inst, cur_instance_runtimes, mesh)):
                free_slots += free_nodes
                # Same fix in the accumulator — accumulate the candidate
                # (`inst`), not the outer `instance`. The original code's
                # accumulator was effectively unused because the rest of
                # the function recomputed slot counts directly.
                instances_to_be_used.append((inst, free_nodes))
                if isinstance(inst, CloudOnDemandInstance):
                    ondemandFlag = True

        to_be_used, nodes_per_chain = 0, 0
        if free_slots:
            print(f'Going into moldable cloud with {free_slots}')
            nodes_per_chain = min((request['chains'] - request['count'] + free_slots) // request['chains'], 4)
            while nodes_per_chain > 0:
                speedup_runtime = getRuntime(nodes_per_chain, mesh, instances_to_be_used[-1][0].name)
                cost_per_node_iteration = instances_to_be_used[0][0].cost_per_second * speedup_runtime
                total_cost = getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1,
                                         nodes_per_chain * request['chains'])
                if ondemandFlag:
                    total_cost += COLD_START_TIME * instances_to_be_used[0][0].cost_per_second * \
                        nodes_per_chain * request['chains']
                if total_cost < budget and \
                   getRuntime(nodes_per_chain - 1, mesh, instances_to_be_used[-1][0].name) / speedup_runtime > SPEEDUP_THRESHOLD:
                    if speedup_runtime * request['tinyda-iterations'] < available_runtime:
                        to_be_used = (nodes_per_chain * request['chains']) - (request['chains'] - request['count'])
                        print(f'Alloted moldable cloud with {nodes_per_chain} nodes per chain')
                        break
                    else:
                        print('Not enough runtime for moldable cloud')
                        return []
                else:
                    print(f'Not enough budget for moldable cloud with {nodes_per_chain}. '
                          f'Total cost: {total_cost}, budget: {budget}')
                    nodes_per_chain -= 1

        acquired_instances = []
        while to_be_used > 0:
            cur_node, cur_count = instances_to_be_used.pop()
            count = min(cur_count, to_be_used)
            acquired_instances.append((cur_node, count))
            cur_count -= count
            if cur_count > 0:
                instances_to_be_used.append((cur_node, cur_count))
            to_be_used -= count

        # 3. Fallback — if the moldable-cloud path couldn't fit, take any
        #    reserved / on-demand instance the budget allows whose speedup
        #    is meaningful (≥1.2× current slowest runtime).
        if not acquired_instances:
            max_cur_runtime = max(cur_instance_runtimes)
            cost_per_node_iteration = current_resources[-1][0].cost_per_second * max_cur_runtime
            budget -= getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1,
                                  request['chains'] - request['count'])

            node_count = request['count'] if request['count'] > 0 else request['chains'] + request['count']
            current_node_count = request['chains'] - request['count']
            addedColdStartCost = False
            for inst in instances:
                if budget < MIN_INSTANCE_COST or node_count == 0:
                    break
                free_nodes = inst.getFreeSlots()
                if not free_nodes:
                    continue
                to_be_used = 1
                speedup_runtime = getRuntime(1, mesh, inst.name)
                if speedup_runtime > max_cur_runtime * 1.2:
                    if free_nodes > 1:
                        to_be_used = 2
                        speedup_runtime = getRuntime(2, mesh, inst.name)
                total_cost = inst.cost_per_second * speedup_runtime * request['tinyda-iterations'] * to_be_used
                if isinstance(inst, CloudOnDemandInstance) or addedColdStartCost:
                    total_cost += COLD_START_TIME * inst.cost_per_second
                    if not addedColdStartCost:
                        total_cost += COLD_START_TIME * current_node_count * inst.cost_per_second
                if budget - total_cost > 0:
                    budget -= total_cost
                    addedColdStartCost = True
                else:
                    continue
                acquired_instances.append((inst, to_be_used))
                current_node_count += to_be_used
                node_count -= 1

        return acquired_instances