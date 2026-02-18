from collections import deque
import math
import threading
import time
from typing import List

from config.constants_HPO import (COLD_START_TIME, WORKFLOW_POLLING, SIMULATE, MIN_TRIALS, MAX_TRIALS,
                                  DEADLINE_BUFFER, MIN_INSTANCE_COST, SPEEDUP_THRESHOLD,
                                  OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR)
from scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from scripts.create_instance_HPO import createExecutorInstance, createWorkerInstances
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
import os
from resource_manager.resource_manager import ResourceManager

_HPO_RESOURCES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from utils.sim import getTime, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow, getEstimate
from utils.request import ExecutorRequest, sendRequest, getConfig
from scheduler.scheduler_HPO import Scheduler_HPO

# HPO-specific Moldable FCFS Scheduler with Dedicated Executor Design
# Supports full cross-type instance switching and elastic scaling
class FCFS_Optimized_HPO(Scheduler_HPO):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_trial'):
        self.resource_manager = ResourceManager(_HPO_RESOURCES)
        # HPO-specific sorting: prioritize by trial cost + cold start penalty
        func = lambda x: self.getHPOInstanceCost(x) + (COLD_START_TIME * 0.001 if isinstance(x, CloudOnDemandInstance) else 0)
        self.resource_manager.sortResourcesByFunction(func)
        super().__init__(queue, finish_queue, resource_request_queue)

    def getHPOInstanceCost(self, instance):
        """Get cost per trial for HPO instances"""
        if 'g4dn' in instance.name:
            return 0.798 / 3600  # g4dn.2xlarge cost per second
        elif 'g5' in instance.name:
            return 1.28 / 3600   # g5.2xlarge cost per second
        else:
            return 0.90 / 3600   # on-premise equivalent

    def run(self, sim = None, wf_mb = None, resource_request_mb = None):

        print(f'Starting HPO Moldable FCFS scheduler...')

        # Start a thread to periodically compute resource utilization
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(target=self.metrics.collectResourceUtilization, args=[sim, self.resource_manager])
            thread.start()

        while True:

            # Check queue for moldable resource requests
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                # Process moldable resource requests
                start = time.time()
                resource_request = eval(resource_request)
                if getTime(sim) - resource_request['request-time'] > 300:  # 5 min timeout
                    removeElement(resource_request_mb, self.resource_request_queue)
                    continue
                self.processMoldableRequestHPO(resource_request, sim)
                print(f"HPO Moldable resource processing overhead: {time.time() - start}")
                removeElement(resource_request_mb, self.resource_request_queue)
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

                # Moldable scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesMoldableHPO(constraints, sim)
                    print(f"{wf_plan['id']} moldable allocation: ", ips)

                    # Remove the element if we found the resources needed.
                    if ips:
                        removeElement(wf_mb, self.queue)
                        # NOTE: We start billing at this point
                        start_time = getTime(sim)
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO moldable resources to allocate, waiting...')

            (sim or time).sleep(WORKFLOW_POLLING)

    def allocateResourcesMoldableHPO(self, constraints, sim=None):
        """
        Moldable HPO resource allocation with resilient fallback
        Same logic as static but prepares for moldable adjustments between rounds
        """
        model = constraints['mesh']
        budget = constraints['budget']
        deadline_duration = constraints['deadline_duration']  # Duration in seconds (not absolute timestamp)
        trials = constraints['chains']
        epochs = constraints['tinydaIterations']

        # Get optimal instance type AND number of hosts
        optimal_type, num_hosts_requested = self.selectOptimalInstanceType(
            budget, deadline_duration, model, trials, epochs
        )

        instances = self.resource_manager.getResources()
        selected_instances = []
        remaining = num_hosts_requested

        # PRIORITY 1: On-premise (if type matches)
        for instance in instances:
            if isinstance(instance, OnPremInstance) and \
               self.getInstanceTypeForHPO(instance.name) == optimal_type:
                slots_available = instance.getFreeSlots()
                if slots_available >= remaining:
                    selected_instances = [(instance, remaining)]
                    remaining = 0
                    print(f"Moldable: Allocated {num_hosts_requested} on-prem {instance.name}")
                    break
                elif slots_available > 0:
                    selected_instances = [(instance, slots_available)]
                    remaining -= slots_available
                    print(f"Moldable: Partial on-prem {slots_available}/{num_hosts_requested}")
                    break

        # PRIORITY 2: Cloud reserved
        if remaining > 0:
            for instance in instances:
                if instance.type == 'reserved' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        selected_instances.append((instance, slots_available))
                        remaining -= slots_available
                        if remaining == 0:
                            break

        # PRIORITY 3: Cloud on-demand (with budget check)
        if remaining > 0:
            runtime_func = getRuntime_g5 if optimal_type == 'g5' else getRuntime_g4

            allocated_so_far = num_hosts_requested - remaining
            total_hosts = allocated_so_far + remaining

            if total_hosts >= trials:
                workers_per_trial = total_hosts // trials
                runtime = runtime_func(workers_per_trial, model, epochs)
            else:
                batches = math.ceil(trials / total_hosts)
                runtime = batches * runtime_func(1, model, epochs)

            for instance in instances:
                if instance.type == 'on-demand' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        cost = (runtime / 3600) * instance.cost_per_second * slots_available
                        if cost < budget:
                            selected_instances.append((instance, slots_available))
                            remaining -= slots_available
                            print(f"Moldable: {slots_available} on-demand {optimal_type} (${cost:.2f})")
                            if remaining == 0:
                                break

        # RESILIENT: Proceed with what we got
        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"⚠️  Moldable degraded: requested {num_hosts_requested}, got {allocated_hosts}")

        if allocated_hosts == 0:
            print(f"❌ Moldable: Failed to allocate any {optimal_type} hosts")
            return None, None

        # Allocate and create instances
        ips, alloc_resources = self.resource_manager.allocateResources(selected_instances)
        ips = self.createOnDemandWorkers(ips, sim)

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts (moldable version)
        Returns: (instance_type, num_hosts)

        Considers ALL available instance types (on-prem, g4, g5) with actual costs
        Explores different host counts to find best cost/performance tradeoff
        No instance type switching - stays within one type for entire workflow
        """
        instances = self.resource_manager.getResources()

        # Build list of unique instance types to evaluate
        instance_types = {}
        for instance in instances:
            inst_type = self.getInstanceTypeForHPO(instance.name)
            if inst_type not in instance_types:
                # Store cheapest cost for this type (prioritize reserved/on-prem over on-demand)
                cost = instance.cost_per_second
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                instance_types[inst_type] = {
                    'cost_per_second': cost,
                    'runtime_func': runtime_func,
                    'name': instance.name
                }
            else:
                # Update if we found a cheaper instance of same type
                if instance.cost_per_second < instance_types[inst_type]['cost_per_second']:
                    instance_types[inst_type]['cost_per_second'] = instance.cost_per_second
                    instance_types[inst_type]['name'] = instance.name

        best_instance_type = None
        best_num_hosts = 1
        best_score = float('inf')
        best_cost = 0
        best_runtime = 0

        # Evaluate each instance type
        for inst_type, info in instance_types.items():
            runtime_func = info['runtime_func']
            cost_per_second = info['cost_per_second']
            cost_per_hour = cost_per_second * 3600

            # Try different num_hosts for this instance type
            for num_hosts in range(1, trials + 1):
                # Calculate actual runtime based on execution pattern
                if num_hosts >= trials:
                    # Parallel execution: each trial gets (num_hosts // trials) workers
                    workers_per_trial = num_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    # Sequential batches: some trials must wait
                    batches = math.ceil(trials / num_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                # Calculate cost for this configuration (use cost_per_second directly)
                cost = runtime * cost_per_second * num_hosts

                # Check if meets constraints
                if runtime <= deadline and cost <= budget:
                    # Score: minimize cost with slight preference for faster completion
                    score = cost + (runtime / deadline) * 0.1
                    if score < best_score:
                        best_score = score
                        best_num_hosts = num_hosts
                        best_instance_type = inst_type
                        best_cost = cost
                        best_runtime = runtime

        if best_instance_type is None:
            # No solution found within constraints - return cheapest option
            cheapest_type = min(instance_types.items(), key=lambda x: x[1]['cost_per_second'])[0]
            print(f"⚠️  No configuration meets constraints, defaulting to cheapest: 1 × {cheapest_type}")
            return cheapest_type, 1

        print(f"Moldable selected: {best_num_hosts} × {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
        return (best_instance_type, best_num_hosts)

    def getInstanceTypeForHPO(self, instance_name):
        """Map instance names to HPO instance types"""
        if 'g4dn' in instance_name:
            return 'g4'
        elif 'g5' in instance_name:
            return 'g5'
        elif 'on-prem' in instance_name:
            return 'g5'  # On-prem has g5-equivalent performance
        else:
            return 'unknown'

    def processMoldableRequestHPO(self, request, sim):
        """
        Process moldable resource requests between HPO optimization rounds
        Decides: scale up, scale down, or maintain allocation (NO instance type switching)
        """
        wf_id = request['wf-id']
        (instances, budget, deadline, start_time, model) = self.resource_manager.getWorkflow(wf_id)

        # Calculate iteration-weighted constraints
        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]

        # Current allocation
        current_instance = instances[0][0]
        current_instance_type = current_instance.name  # Locked to this type (no switching!)
        current_trials = sum(count for _, count, _ in instances)

        # Check if can reduce resources (deadline not tight)
        trials_per_instance = 3
        request['count'] = None
        min_needed_trials = request['chains']

        # Determine runtime function
        if current_instance_type.startswith('g4dn'):
            runtime_per_trial = getRuntime_g4(1, model, request['tinyda-iterations'])
        else:
            runtime_per_trial = getRuntime_g5(1, model, request['tinyda-iterations'])

        while trials_per_instance > 0:
            runtime = trials_per_instance * runtime_per_trial
            if runtime < available_time:
                min_needed_instances = min_needed_trials // trials_per_instance + bool(min_needed_trials % trials_per_instance)
                if current_trials > min_needed_instances:
                    # Free excess instances
                    request['count'] = current_trials - min_needed_instances
                    self.freeResources(instances, request, sim)
                    return
                else:
                    # Need more instances
                    break
            else:
                trials_per_instance -= 1

        # Allocate additional resources if needed
        used_budget = self.metrics.computeCost(wf_id, getTime(sim))
        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            request['count'] = min_needed_instances - current_trials

        # Allocate new resources (same instance type only, homogeneous)
        alloc_instances = self.checkNewResourcesHPO(
            free_resources,
            instances,
            available_budget,
            available_time,
            request,
            model,
            current_instance_type,
            sim
        )

        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)

        # Create actual on-demand instances if allocated
        if ips:
            ips = self.createOnDemandWorkers(ips, sim)

        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def checkNewResourcesHPO(self, resources, current_resources, budget, available_runtime, request, model, instance_type_filter, sim):
        """
        Allocate additional resources for moldable HPO with cluster isolation
        - On-prem workflows scale ONLY within on-prem
        - Cloud workflows scale ONLY within cloud (reserved + on-demand)
        - NO cross-cluster migration
        """
        if budget < MIN_INSTANCE_COST:
            return []

        # Determine runtime function
        if instance_type_filter.startswith('g4dn'):
            runtime_func = getRuntime_g4
        else:
            runtime_func = getRuntime_g5

        # Check current cluster
        instance = current_resources[0][0]

        # CASE 1: On-prem workflow - scale ONLY within on-prem
        if isinstance(instance, OnPremInstance):
            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = instance.cost_per_second * runtime
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / cost_per_instance))

            if to_be_used > 0:
                print(f"Moldable: Scaling on-prem workflow, adding {to_be_used} on-prem instances")
                return [(instance, to_be_used)]
            else:
                print(f"Moldable: On-prem workflow cannot scale (no free on-prem slots or budget)")
                return []

        # CASE 2: Cloud workflow - scale ONLY within cloud (reserved + on-demand)
        # Filter out on-prem instances (cluster isolation!)
        available_instances = []
        for inst in resources:
            if (not isinstance(inst, OnPremInstance) and
                inst.name == instance_type_filter and
                inst.getFreeSlots() > 0):

                # Priority: reserved first, on-demand second (cost-based)
                priority = 0 if inst.type == 'reserved' else 1
                available_instances.append((priority, inst))

        # Sort by priority (reserved before on-demand)
        available_instances.sort(key=lambda x: x[0])

        # Allocate resources within budget
        acquired_count = 0
        acquired_instances = []
        trials_needed = request['count']

        for priority, inst in available_instances:
            if acquired_count >= trials_needed or budget < MIN_INSTANCE_COST:
                break

            # Calculate cost
            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = inst.cost_per_second * runtime

            # Add cold start for on-demand
            if inst.type == 'on-demand':
                cost_per_instance += COLD_START_TIME * inst.cost_per_second

            # Check speedup justification
            current_trials = request['chains'] - request['count'] + acquired_count
            new_trials = current_trials + 1

            current_runtime = runtime * (request['chains'] / max(current_trials, 1))
            new_runtime = runtime * (request['chains'] / new_trials)
            speedup = current_runtime / new_runtime if new_runtime > 0 else 0

            # Allocate if justified
            if (cost_per_instance < budget and
                speedup > SPEEDUP_THRESHOLD and
                new_runtime < available_runtime):

                to_be_used = min(trials_needed - acquired_count, inst.getFreeSlots())
                acquired_instances.append((inst, to_be_used))
                acquired_count += to_be_used
                budget -= to_be_used * cost_per_instance

        return acquired_instances

    def freeResources(self, instances, request, sim):
        """
        Free excess resources when deadline allows
        Uses LIFO strategy: frees most recently allocated instances first
        (typically on-demand instances allocated last)
        """
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []

        if request['count'] > 0:
            # LIFO: Iterate from END of instances list (most recently allocated)
            for i in range(len(instances) - 1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count'] - freed_count, count)
                # Free last IPs from this instance (LIFO within instance)
                to_free_instances.append((instance, to_free, ips[-to_free:]))
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break
            self.resource_manager.returnResources(request['wf-id'], to_free_instances)

        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, sim, request.get('client-ip', None))

    def sendNewResources(self, wf_id, ips, alloc_resources, sim, client_ip):
        """Send new resources to executor"""
        new_req = {
            "request": ExecutorRequest.REQUEST_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": ips,
        }
        print(f"{wf_id} allocated additional resources: ", ips)
        if sim:
            from executor_HPO import processNewResourcesHPO
            sim.process(processNewResourcesHPO, new_req)
        else:
            sendRequest(client_ip, getConfig('executor-incoming-port'), new_req)
        if alloc_resources:
            self.resource_manager.updateWorkflowResources(wf_id, alloc_resources)
            self.metrics.updateResources(wf_id, alloc_resources, getTime(sim))

    def sendFreedResources(self, wf_id, to_free_instances, instances, response_instances, sim, client_ip):
        """Send freed resources notification to executor"""
        new_req = {
            "request": ExecutorRequest.FREE_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": response_instances
        }
        print(f"Scheduler freeing {response_instances} for {wf_id} ")
        if sim:
            from executor_HPO import processNewResourcesHPO
            sim.process(processNewResourcesHPO, new_req)
        else:
            sendRequest(client_ip, getConfig('executor-incoming-port'), new_req)
        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            self.metrics.updateResources(wf_id, to_free_instances, None, getTime(sim))

    def createOnDemandWorkers(self, ips, sim):
        """Create actual on-demand worker instances for allocated virtual slots"""
        for instance_type, (count, ip_list) in ips.get('on-demand', {}).items():
            if count > 0 and len(ip_list) == 0:
                print(f"Creating {count} on-demand {instance_type} worker instances...")
                worker_ips = createWorkerInstances(instance_type, count, sim)
                ips['on-demand'][instance_type] = (count, worker_ips)
                print(f"Created {count} on-demand {instance_type} workers: {worker_ips}")
        return ips

    def sendWorkflowForExecutionHPO(self, wf_plan, ips, sim, deadline):
        """
        HPO-specific workflow execution with dedicated executor design
        Routes to on-prem executor OR creates cloud executor based on worker allocation
        """
        # Determine executor based on worker allocation
        # If on-prem workers → use on-prem executor (manually started)
        # If cloud workers → create dedicated cloud executor

        if 'on-prem' in ips and len(ips['on-prem'][1]) > 0:
            # On-prem workflow: Use on-prem executor (pre-started)
            executor_ip = ips['on-prem'][1][0]  # Head node IP
            print(f"HPO Moldable Workflow {wf_plan['id']}: Using on-prem executor at {executor_ip}")
        else:
            # Cloud workflow: Create dedicated executor instance
            executor_ip = createExecutorInstance('g4dn.2xlarge', sim)

            if not executor_ip:
                print(f"Error: Could not create dedicated executor instance for {wf_plan['id']}")
                return

            print(f"HPO Moldable Workflow {wf_plan['id']}: Created cloud executor at {executor_ip}")

        # Prepare request with separated executor and worker instances
        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,  # Worker instances only
            "executor-ip": executor_ip,  # Dedicated executor (on-prem or cloud)
            "deadline": deadline,
            "moldable": True  # Enable moldable features
        }

        print(f"  Workers: {ips}")

        # Send to executor
        if sim:
            # In simulation mode, process directly
            from executor_HPO import executeWorkflowHPO
            sim.process(executeWorkflowHPO, request, sim)
        else:
            # Send to executor instance
            sendRequest(executor_ip, getConfig('executor-incoming-port'), request)