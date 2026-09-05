import threading
import time
from typing import List

from config.constants import AVG_WORKFLOW_ITERATIONS, COLD_START_TIME, MIN_ALLOC_INSTANCES, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR, RESOURCE_REQUEST_TIMEOUT, WORKFLOW_POLLING
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from resource_manager.resource_manager import ResourceManager
from scripts.speedup import getRuntime
from utils.sim import getTime, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow, getEstimate
from scheduler.scheduler import Scheduler

# If requested resources are available, they are granted. Else the workflow waits
class FCFS_Optimized(Scheduler):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = ResourceManager()
        self.resource_manager.sortResources(sort_key)
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
                self.processExecutorRequest(resource_request, sim)
                removeElement(resource_request_mb, self.resource_request_queue)
                sim and sim.sleep(0.2) # NOTE: scheduler overhead
                continue
            
            # Check the queue for new jobs
            workflow_plan = peekElement(wf_mb, self.queue)

            if workflow_plan:
                wf_plan = eval(workflow_plan) # Convert string back to dictionary

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics()
                    break 

                # If workflow cannot be executed, pop it to prevent stagnation
                if self.purgeWorkflow(wf_plan, sim):
                    removeElement(wf_mb, self.queue)
                    self.resource_manager.setResourcesAvailable(True)
                    continue
            
                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResources(constraints, sim) # alloc_resources could be []              

                    # Remove the element if we found the resources needed.
                    if alloc_resources:
                        print(f"{wf_plan['id']} allocated: ", ips)
                        removeElement(wf_mb, self.queue)
                        # NOTE: We start billing at this point
                        start_time = getTime(sim)
                        self.sendWorkflowForExecution(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print('No resources to allocate, waiting...')
            
            (sim or time).sleep(WORKFLOW_POLLING)  # NOTE: Polling interval


    def allocateResources(self, constraints, sim):
        instances = self.resource_manager.getResources()
        # Save p% as reserve
        available_budget = (OPTIM_FCFS_BFACTOR[0] * constraints['budget']) / AVG_WORKFLOW_ITERATIONS
        available_runtime = (OPTIM_FCFS_DFACTOR[0] * (constraints['deadline'] - getTime(sim))) / AVG_WORKFLOW_ITERATIONS
        acquired_instances = self.checkResources(instances, constraints['min_instances'], available_budget, constraints['tinyda_iterations'], available_runtime, constraints['mesh'])
        ips, alloc_resources = self.resource_manager.allocateResources(acquired_instances)
        return ips, alloc_resources
    
    def checkResources(self, instances, min_instances, budget, tinyda_iterations, available_runtime, mesh, ignore_slots = False):
        
        acquired_instances = []
        acquired_count = 0
        MAX_ALLOC = min_instances * 2

        for instance in instances:

            free_slots = instance.getFreeSlots(ignore_slots)

            # On prem
            if isinstance(instance, OnPremInstance):
                # Case 1: 2n nodes
                if free_slots >= 2 * min_instances:
                   nodes_per_chain = 2
                # Case 2: n nodes
                elif free_slots >= min_instances:
                    nodes_per_chain = 1
                # Case 3: on-prem cannot be alloted
                else: continue
                while nodes_per_chain > 0:
                    speedup_runtime = getRuntime(nodes_per_chain, mesh, instance.name)
                    cost_per_node_iteration = instance.cost_per_second * speedup_runtime
                    total_cost = getEstimate(cost_per_node_iteration, tinyda_iterations, 1, nodes_per_chain*min_instances)
                    if total_cost <= budget: return [(instance, nodes_per_chain * min_instances)]
                    else: nodes_per_chain -= 1
                continue

            # Cloud - collect first 2n resources and then process
            to_be_used = min(free_slots, MAX_ALLOC - acquired_count)
            if to_be_used:
                acquired_instances.append((instance, to_be_used))
                acquired_count += to_be_used
                if acquired_count == MAX_ALLOC: break
           
        # Process collected cloud instances
        # Case 1: 2n nodes
        if acquired_count >= 2 * min_instances:
            nodes_per_chain = 2
        # Case 2: n nodes
        elif acquired_count >= min_instances:
            nodes_per_chain = 1
        # Case 3: not enough resources
        else: return []

        needed_count = nodes_per_chain * min_instances
        # check if we have budget
        while nodes_per_chain > 0:
            for i in range(len(acquired_instances)):
                speedup_runtime = getRuntime(nodes_per_chain, mesh, acquired_instances[i][0].name)
                cost_per_node_iteration = acquired_instances[i][0].cost_per_second * speedup_runtime
                to_be_used = min(acquired_instances[i][1], needed_count)
                total_cost = getEstimate(cost_per_node_iteration, tinyda_iterations, 1, to_be_used)
                if isinstance(instance, CloudOnDemandInstance):
                    total_cost += COLD_START_TIME * to_be_used
                if total_cost <= budget:
                    budget -= total_cost
                    needed_count -= to_be_used
                    acquired_instances[i] = (acquired_instances[i][0], to_be_used)
                    if needed_count == 0: return acquired_instances[:i][::-1]
                else:
                    nodes_per_chain -= 1
                    break
        
        return []
    
    # request = {"wf-id": wf_id, "count": n, "iteration": ind, "tinyda-iterations": m}
    # current_resources = {obj: (count, ip)}
    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, request, available_runtime, mesh) -> List[tuple[Instance, int]]:
        
        # 1. If on-prem is already allocated, allocate possible on-prem instances
        if isinstance(current_resources[0][0], OnPremInstance):
            instances = list(filter(lambda x: isinstance(x, OnPremInstance), resources))
            return self.checkResources(instances, request['chains'], budget, request['tinyda-iterations'], available_runtime, mesh, ignore_slots = True)
    
        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        alloc_count = 0
        for instance in current_resources:
            alloc_count += instance[1]
        return self.checkResources(instances, request['chains'], budget, request['tinyda-iterations'], available_runtime, mesh, ignore_slots = True)

    # Only allocate new resources if next iteration cannot be completed in available time
    def allocateNewResources(self, request, instances, available_budget, available_runtime, sim, mesh):
        free_resources = self.resource_manager.getResources()
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, available_runtime, mesh) # {obj: count}
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances) # alloc_resource = {obj: (count, [ips])}
        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    # request = {"wf-id", "count", "request-time", "client-ip"} 
    def freeResources(self, request, instances, sim):
        alloc_count = 0
        for obj, count, _ in instances: alloc_count += count 

        # Retain min alloc instances
        request['count'] = min(alloc_count - MIN_ALLOC_INSTANCES, request['count'])
        
        # Free the last n instances
        to_free_instances = []
        freed_count = 0
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        if request['count'] > 0:
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
        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, sim, request.get('client-ip', None))
        
    def processExecutorRequest(self, request, sim):
        request = eval(request)
        if getTime(sim) - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return
        (instances, budget, deadline, _, mesh) = self.resource_manager.getWorkflow(request['wf-id'])
        
        # request['iteration'] can be 0, 1, 2, 3, 4, 5
        ind = request['iteration']
        available_time = (max(0, deadline - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]) / max((AVG_WORKFLOW_ITERATIONS - ind), 1)
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = (max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]) / max((AVG_WORKFLOW_ITERATIONS - ind), 1)
        
        # If there is not enough budget, OR if deadline is far OR free resources
        cur_instance: Instance = instances[-1][0]
        cost_per_iteration = cur_instance.cost_per_second * getRuntime(1, mesh, cur_instance.name)
        runtime_per_iteration = getRuntime(1, mesh, cur_instance.name)
        if available_time < getEstimate(runtime_per_iteration, request['tinyda-iterations']):
            self.allocateNewResources(request, instances, available_budget, available_time, sim, mesh)
        else:
            self.freeResources(request, instances, sim)
            




        
    