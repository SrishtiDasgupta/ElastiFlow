from collections import deque
import math
import threading
import time
from typing import List

from config.constants import CLOSENESS_TOLERANCE, COLD_START_TIME, DEADLINE_BUFFER, MIN_INSTANCE_COST, OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR, RESOURCE_REQUEST_TIMEOUT, SPEEDUP_THRESHOLD, WORKFLOW_POLLING
from scripts.speedup import getRuntime
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from resource_manager.resource_manager import ResourceManager
from utils.sim import getTime, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow, getEstimate
from scheduler.scheduler import Scheduler

# If requested resources are available, they are granted. Else the workflow waits
class FCFS_Optimized(Scheduler):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = ResourceManager()
        # func = lambda x: x.getValue(sort_key) + x.getValue('cold_start_cost') # NOTE: cost based 
        func = lambda x: x.getValue(sort_key) + (COLD_START_TIME if isinstance(x, CloudOnDemandInstance) else 0) # runtime based
        self.resource_manager.sortResourcesByFunction(func)
        super().__init__(queue, finish_queue, resource_request_queue)

    def run(self, sim = None, wf_mb = None, resource_request_mb = None):
        
        print(f'Starting scheduler...')

        # Start a thread to periodically compute resource utilization
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(target=self.metrics.collectResourceUtilization, args=[sim, self.resource_manager])
            thread.start()
        
        while True:

            # Check queue for resource requests
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                # Allocate new resources
                start = time.time()
                resource_request = eval(resource_request)
                if getTime(sim) - resource_request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
                    removeElement(resource_request_mb, self.resource_request_queue)
                    continue
                self.processFreeRequest(resource_request, sim)
                print(f"Resource stuff overhead: {time.time() - start}")
                removeElement(resource_request_mb, self.resource_request_queue)
                continue
            
            # Check the queue for new jobs
            # sched_start_time = time.time()
            workflow_plan = peekElement(wf_mb, self.queue)

            if workflow_plan:
                wf_plan = eval(workflow_plan) # Convert string back to dictionary

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics()
                    break 

                # If workflow cannot be executed, pop it to prevent stagnation
                # if self.purgeWorkflow(wf_plan, sim):
                #     removeElement(wf_mb, self.queue)
                #     self.resource_manager.setResourcesAvailable(True)
                #     continue
            
                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResources(constraints)
                    print(f"{wf_plan['id']} allocated: ", ips)
                    # print("Sched overhead: ", time.time() - sched_start_time)              

                    # Remove the element if we found the resources needed.
                    if ips:
                        removeElement(wf_mb, self.queue)
                        # NOTE: We start billing at this point
                        start_time = getTime(sim)
                        self.sendWorkflowForExecution(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        # NOTE: Can also suspend process and resume when resources are available again 
                        print('No resources to allocate, waiting...')
            
            (sim or time).sleep(WORKFLOW_POLLING)

    
    # request = {"wf-id": wf_id, "count": n, "iteration": ind, "tinyda-iterations": m}
    # current_resources = {obj: (count, ip)}
    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, available_runtime: float, request, mesh) -> List[tuple[Instance, int]]:
    
        # 1. If on-prem is already allocated, allocate possible on-prem instances
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            free_slots = instance.getFreeSlots()
            to_be_used = 0
            # At least 1 node per chain/moldability
            if free_slots >= request['count']:
                nodes_per_chain = request['chains'] - request['count'] + free_slots // request['chains']
                while nodes_per_chain > 0:
                    speedup_runtime = getRuntime(nodes_per_chain, mesh, instance.name)
                    cost_per_node_iteration = instance.cost_per_second * speedup_runtime
                    total_cost = getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1, nodes_per_chain * request['chains'])
                    # If there is budget, and runtime is within limits, allocate
                    if total_cost < budget and getRuntime(nodes_per_chain-1, mesh, instance.name) / speedup_runtime > SPEEDUP_THRESHOLD: 
                        if speedup_runtime * request['tinyda-iterations'] < available_runtime:
                            to_be_used = (nodes_per_chain * request['chains']) - (request['chains'] - request['count'])
                            print(f'Moldable onprem with {nodes_per_chain} nodes per chain')
                            break
                        else: return []
                    else:
                        nodes_per_chain -= 1
            return [(instance, to_be_used)]
                
        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        # We consider 3 cases:
        # 1. Instances of the same type (res/on-dem)
        # 2. Instances with almost the same runtime (res/on-dem)
        # 3. Any idle instances if speedup makes sense
        cur_instances = set()
        cur_instance_runtimes = []
        for inst_tuple in current_resources: 
            cur_instances.add(inst_tuple[0].name)
            cur_instance_runtimes.append(getRuntime(1, mesh, inst_tuple[0].name))
        free_slots = 0
        instances_to_be_used = [] # [instObj, count]
        ondemandFlag = False
        for inst in instances:
            free_nodes = inst.getFreeSlots()
            # BUGFIX: closeness check must use `inst` (the candidate) not
            # `instance` (the workflow's first existing instance, fixed
            # outside the loop). See scheduler.py for full rationale.
            if free_nodes and (inst.name in cur_instances or self.checkCloseness(inst, cur_instance_runtimes, mesh)):
                free_slots += free_nodes
                instances_to_be_used.append((inst, free_nodes))
                if isinstance(inst, CloudOnDemandInstance): ondemandFlag = True
        
        to_be_used, nodes_per_chain = 0, 0
        if free_slots:
            print(f'Going into moldable cloud with {free_slots}')
            nodes_per_chain = min((request['chains'] - request['count'] + free_slots) // request['chains'], 4)
            while nodes_per_chain > 0:
                speedup_runtime = getRuntime(nodes_per_chain, mesh, instances_to_be_used[-1][0].name) # slowest instance
                cost_per_node_iteration = instances_to_be_used[0][0].cost_per_second * speedup_runtime # max cost
                total_cost = getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1, nodes_per_chain * request['chains'])
                if ondemandFlag:
                    total_cost += COLD_START_TIME * instances_to_be_used[0][0].cost_per_second * nodes_per_chain * request['chains']
                # If there is budget, and speedup is substantial, allocate nodes
                if total_cost < budget and getRuntime(nodes_per_chain-1, mesh, instances_to_be_used[-1][0].name) / speedup_runtime > SPEEDUP_THRESHOLD:
                    if speedup_runtime * request['tinyda-iterations'] < available_runtime:
                        to_be_used = (nodes_per_chain * request['chains']) - (request['chains'] - request['count'])
                        print(f'Alloted moldable cloud with {nodes_per_chain} nodes per chain')
                        break
                    else: 
                        print('Not enough runtime for moldable cloud')
                        return []
                else:
                    print(f'Not enough budget for moldable cloud with {nodes_per_chain}. Total cost: {total_cost}, budget: {budget}')
                    nodes_per_chain -= 1
        
        # Extract last n to_be_used nodes from instances_to_be_used - cheaper
        acquired_instances = []
        while to_be_used > 0: 
            cur_node, cur_count = instances_to_be_used.pop()
            count = min(cur_count, to_be_used)
            acquired_instances.append((cur_node, count))
            cur_count -= count
            if cur_count > 0: instances_to_be_used.append((cur_node, cur_count))
            to_be_used -= count

        # If moldabiliy couldn't be handled, take at least available instances till budget allows
        # 3. Allocate any reserved/on-demand if we have budget and if speedup makes sense
        if not acquired_instances: # account for existing resources
            max_cur_runtime = max(cur_instance_runtimes)
            cost_per_node_iteration = current_resources[-1][0].cost_per_second * max_cur_runtime
            budget -= getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1, request['chains'] - request['count'])
            
            node_count = request['count'] if request['count'] > 0 else request['chains'] + request['count']
            current_node_count = request['chains'] - request['count']
            addedColdStartCost = False
            
            for inst in instances:
                if budget < MIN_INSTANCE_COST or node_count == 0: break
                free_nodes = inst.getFreeSlots()
                if not free_nodes: continue
                to_be_used = 1
                speedup_runtime = getRuntime(1, mesh, inst.name)
                if speedup_runtime > max_cur_runtime * 1.2:
                    if free_nodes > 1: 
                        to_be_used = 2 # get 2 nodes
                        speedup_runtime = getRuntime(2, mesh, inst.name)
                    else: continue
                total_cost = inst.cost_per_second * speedup_runtime * request['tinyda-iterations'] * to_be_used
                if isinstance(inst, CloudOnDemandInstance) or addedColdStartCost: 
                    total_cost += COLD_START_TIME * inst.cost_per_second
                    if not addedColdStartCost:
                        total_cost += COLD_START_TIME * current_node_count * inst.cost_per_second
                if budget - total_cost > 0:
                    budget -= total_cost
                    addedColdStartCost = True
                else: continue
                acquired_instances.append((inst, to_be_used))
                current_node_count += to_be_used
                node_count -= 1
            # if node_count > 0: acquired_instances = []
            else: print(f'moldable cloud free alloc with {current_node_count} for {request["chains"]}')
            
        # 4. Cannot allocate anything
        return acquired_instances 

    # Check if runtimes are within 10% of each other
    def checkCloseness(self, instance: Instance, runtimes_list, mesh) -> bool:
        runtime = getRuntime(1, mesh, instance.name)
        if isinstance(instance, CloudOnDemandInstance):
            runtime += COLD_START_TIME
        # Tolerance loaded from config.constants so CLI ablation can patch
        # it before this module is imported.
        closeness = lambda x: math.isclose(runtime, x, rel_tol=CLOSENESS_TOLERANCE)
        return any(map(closeness, runtimes_list))
        
    def processFreeRequest(self, request, sim):
        (instances, budget, deadline, _, mesh) = self.resource_manager.getWorkflow(request['wf-id'])
        
        # Check if additional resources are needed
        # request['iteration'] can be 0, 1, 2, 3, 4, 5
        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]
        
        cur_instance: Instance = instances[-1][0]
        cur_count = instances[-1][1]
        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = 0
            for inst_tuple in instances:
                cur_count += inst_tuple[1]

        # Check safeness without moldability
        # Can the workflow be completed with 3, 2, 1 chains per node?
        chains_per_node = 3
        request['count'] = None
        min_needed_count = request['chains']
        runtime_per_model = getRuntime(1, mesh, cur_instance.name)
        while chains_per_node > 0:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']
            if runtime < available_time: # can be completed
                # free to keep only chains / chains_per_node
                min_needed_count = request['chains'] // chains_per_node + bool(request['chains'] % chains_per_node)
                if cur_count >= min_needed_count:
                    request['count'] = cur_count - min_needed_count
                    self.freeResources(instances, request, sim)
                    self.metrics.recordScaleDownAttempt(request, request['count'])
                    return
                else: # allocate resources
                    break
            else:
                chains_per_node -= 1

        # Allocate resources
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]
        free_resources = self.resource_manager.getResources()
        if request['count'] == None:
            request['count'] = min_needed_count - cur_count
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, available_time, request, mesh) # {obj: count}
        # Path = 'on_prem' if workflow's first held instance is slurm,
        # 'cloud' otherwise. Locked in at initial allocation.
        path = 'on_prem' if isinstance(instances[0][0], OnPremInstance) else 'cloud'
        self.metrics.recordScaleUpAttempt(request, alloc_instances, path=path)
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances) # alloc_resource = {obj: (count, [ips])}
        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def freeResources(self, instances, request, sim):
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []   
        # Free the last n instances
        if request['count'] > 0:
            for i in range(len(instances)):
                instance, count, ips = instances[i]
                to_free = min(request['count']-freed_count, count)
                to_free_instances.append((instance, to_free, ips[-to_free:])) # last n vals
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break
            self.resource_manager.returnResources(request['wf-id'], to_free_instances)
        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, sim, request.get('client-ip', None))