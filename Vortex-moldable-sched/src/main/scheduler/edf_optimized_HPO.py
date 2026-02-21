"""
EDF_Optimized_HPO: Moldable EDF Scheduler for HPO Workflows

Combines EDF (Earliest Deadline First) queue ordering with moldable
resource allocation from fcfs_optimized_HPO.py. Adds deadline-urgency-based
graduated scaling from the LA EDF pattern.

Key features:
- EDF heap ordering for deadline-based priority
- Moldable resource reallocation between HPO iterations
- Deadline urgency boost: CRITICAL (<30%) -> 2.0x, WARNING (<50%) -> 1.5x, Regular -> 1.2x
- Cluster isolation (on-prem vs cloud, no cross-cluster migration)
- Instance type locking (no g5<->g4 switching within workflow)
"""

import heapq
from collections import deque
import math
import threading
import time
from typing import List

from config.constants_HPO import (COLD_START_TIME, WORKFLOW_POLLING, SIMULATE, MIN_TRIALS, MAX_TRIALS,
                                  DEADLINE_BUFFER, MIN_INSTANCE_COST, SPEEDUP_THRESHOLD,
                                  OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR)
from scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from scripts.create_instance_HPO import createWorkerInstances
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
import os
from resource_manager.resource_manager import ResourceManager

_HPO_RESOURCES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from utils.sim import getTime, getAllElements, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow, getEstimate
from utils.request import ExecutorRequest, sendRequest, getConfig
from scheduler.scheduler_HPO import Scheduler_HPO


class EDF_Optimized_HPO(Scheduler_HPO):
    """
    Moldable EDF scheduler for HPO workflows.

    EDF ordering + moldable resource reallocation + deadline urgency boost.
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_trial'):
        self.resource_manager = ResourceManager(_HPO_RESOURCES)
        func = lambda x: self.getHPOInstanceCost(x) + (COLD_START_TIME * 0.001 if isinstance(x, CloudOnDemandInstance) else 0)
        self.resource_manager.sortResourcesByFunction(func)

        # EDF heap setup
        self.workflow_heap = []
        self.resource_request_heap = []
        self.workflow_counter = 0
        self.resource_request_counter = 0

        super().__init__(queue, finish_queue, resource_request_queue)

    def getHPOInstanceCost(self, instance):
        """Get cost per trial for HPO instances"""
        if 'g4dn' in instance.name:
            return 0.798 / 3600
        elif 'g5' in instance.name:
            return 1.28 / 3600
        else:
            return 0.90 / 3600

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):

        print(f'Starting HPO Moldable EDF scheduler...')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')
        print(f'  - Moldable: Dynamic resource reallocation between iterations')
        print(f'  - Deadline urgency boost: CRITICAL 2.0x, WARNING 1.5x, Regular 1.2x')

        # Start a thread to periodically compute resource utilization
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(target=self.metrics.collectResourceUtilization, args=[sim, self.resource_manager])
            thread.start()

        while True:

            # === PHASE 1: Process resource requests sorted by deadline ===
            resource_requests = getAllElements(resource_request_mb, self.resource_request_queue, None)

            if resource_requests:
                self.processResourceRequestsByDeadline(resource_requests)

            resource_request = self.peekWorkflow(self.resource_request_heap)

            if resource_request:
                start = time.time()
                if getTime(sim) - resource_request['request-time'] > 300:  # 5 min timeout
                    self.popWorkflow(self.resource_request_heap)
                    removeElement(resource_request_mb, self.resource_request_queue)
                    continue
                self.processMoldableRequestHPO(resource_request, sim)
                print(f"  HPO EDF Moldable resource processing overhead: {time.time() - start}")
                self.popWorkflow(self.resource_request_heap)
                removeElement(resource_request_mb, self.resource_request_queue)
                continue

            # === PHASE 2: Schedule new workflows in EDF order ===
            workflows = getAllElements(wf_mb, self.queue, None)

            if workflows:
                self.processWorkflowsByDeadline(workflows)

            wf_plan = self.peekWorkflow(self.workflow_heap)

            if wf_plan:

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    self.popWorkflow(self.workflow_heap)
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics(file_prefix='EDF_Moldable_HPO_')
                    break

                # Moldable scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesMoldableHPO(constraints, sim)
                    print(f"{wf_plan['id']} EDF moldable allocation (deadline={constraints['deadline']:.1f}s): ", ips)

                    if ips:
                        self.popWorkflow(self.workflow_heap)
                        removeElement(wf_mb, self.queue)
                        start_time = getTime(sim)
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO EDF moldable resources to allocate, waiting...')

            (sim or time).sleep(WORKFLOW_POLLING)

    # =========================================================================
    # EDF HEAP MANAGEMENT
    # =========================================================================

    def processWorkflowsByDeadline(self, workflows: List[any]):
        """Sort workflows by deadline (EDF ordering)"""
        for wf in workflows:
            wf_plan = eval(wf)
            if wf_plan['id'] == 'END':
                heapq.heappush(self.workflow_heap, (1000000, self.workflow_counter, wf_plan['id'], wf_plan))
            else:
                deadline = wf_plan['submit_time'] + wf_plan['constraints']['deadline']
                heapq.heappush(self.workflow_heap, (deadline, self.workflow_counter, wf_plan['id'], wf_plan))
            self.workflow_counter += 1

    def processResourceRequestsByDeadline(self, requests: List[any]):
        """Sort resource requests by deadline (EDF ordering) — urgent workflows get resources first"""
        for req in requests:
            req_dict = eval(req)
            # Get workflow deadline for ordering
            try:
                wf = self.resource_manager.getWorkflow(req_dict['wf-id'])
                deadline = wf[2] if wf else float('inf')
            except Exception:
                deadline = float('inf')

            heapq.heappush(self.resource_request_heap, (deadline, self.resource_request_counter, req_dict['wf-id'], req_dict))
            self.resource_request_counter += 1

    def peekWorkflow(self, heap):
        """Peek at top of heap without removing"""
        return heap and heap[0][3]

    def popWorkflow(self, heap):
        """Remove top of heap"""
        try:
            heapq.heappop(heap)
        except Exception as e:
            print(f'HEAP POP ERROR: {e}')
            print(heap)

    # =========================================================================
    # HPO MOLDABLE RESOURCE ALLOCATION (from fcfs_optimized_HPO.py)
    # =========================================================================

    def allocateResourcesMoldableHPO(self, constraints, sim=None):
        """
        Moldable HPO resource allocation with resilient fallback
        """
        model = constraints['mesh']
        budget = constraints['budget']
        deadline_duration = constraints['deadline_duration']
        trials = constraints['chains']
        epochs = constraints['tinydaIterations']

        optimal_type, num_hosts_requested = self.selectOptimalInstanceType(
            budget, deadline_duration, model, trials, epochs
        )

        instances = self.resource_manager.getResources()
        selected_instances = []
        remaining = num_hosts_requested

        # Check if on-prem can satisfy (any free slots of the right type)
        on_prem_available = False
        for instance in instances:
            if isinstance(instance, OnPremInstance) and \
               self.getInstanceTypeForHPO(instance.name) == optimal_type:
                if instance.getFreeSlots() > 0:
                    on_prem_available = True
                    break

        if on_prem_available:
            # ON-PREM PATH: only allocate on-prem (skip cloud entirely)
            for instance in instances:
                if isinstance(instance, OnPremInstance) and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = instance.getFreeSlots()
                    if slots_available >= remaining:
                        selected_instances = [(instance, remaining)]
                        remaining = 0
                        print(f"EDF Moldable: Allocated {num_hosts_requested} on-prem {instance.name}")
                        break
                    elif slots_available > 0:
                        selected_instances = [(instance, slots_available)]
                        remaining -= slots_available
                        print(f"EDF Moldable: Partial on-prem {slots_available}/{num_hosts_requested}")
                        break
        else:
            # CLOUD PATH: reserved then on-demand (skip on-prem entirely)
            for instance in instances:
                if instance.type == 'reserved' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        selected_instances.append((instance, slots_available))
                        remaining -= slots_available
                        if remaining == 0:
                            break

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
                                print(f"EDF Moldable: {slots_available} on-demand {optimal_type} (${cost:.2f})")
                                if remaining == 0:
                                    break

        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"EDF Moldable degraded: requested {num_hosts_requested}, got {allocated_hosts}")

        if allocated_hosts == 0:
            print(f"EDF Moldable: Failed to allocate any {optimal_type} hosts")
            return None, None

        ips, alloc_resources = self.resource_manager.allocateResources(selected_instances)
        ips = self.createOnDemandWorkers(ips, sim)

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts (moldable version)
        Returns: (instance_type, num_hosts)
        """
        instances = self.resource_manager.getResources()

        instance_types = {}
        for instance in instances:
            inst_type = self.getInstanceTypeForHPO(instance.name)
            if inst_type not in instance_types:
                cost = instance.cost_per_second
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                instance_types[inst_type] = {
                    'cost_per_second': cost,
                    'runtime_func': runtime_func,
                    'name': instance.name
                }
            else:
                if instance.cost_per_second < instance_types[inst_type]['cost_per_second']:
                    instance_types[inst_type]['cost_per_second'] = instance.cost_per_second
                    instance_types[inst_type]['name'] = instance.name

        best_instance_type = None
        best_num_hosts = 1
        best_score = float('inf')
        best_cost = 0
        best_runtime = 0

        for inst_type, info in instance_types.items():
            runtime_func = info['runtime_func']
            cost_per_second = info['cost_per_second']

            for num_hosts in range(1, trials + 1):
                if num_hosts >= trials:
                    workers_per_trial = num_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    batches = math.ceil(trials / num_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                cost = (runtime / 3600) * cost_per_second * num_hosts

                if runtime <= deadline and cost <= budget:
                    score = cost + (runtime / deadline) * 0.1
                    if score < best_score:
                        best_score = score
                        best_num_hosts = num_hosts
                        best_instance_type = inst_type
                        best_cost = cost
                        best_runtime = runtime

        if best_instance_type is None:
            cheapest_type = min(instance_types.items(), key=lambda x: x[1]['cost_per_second'])[0]
            print(f"No configuration meets constraints, defaulting to cheapest: 1 x {cheapest_type}")
            return cheapest_type, 1

        print(f"EDF Moldable selected: {best_num_hosts} x {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
        return (best_instance_type, best_num_hosts)

    def getInstanceTypeForHPO(self, instance_name):
        """Map instance names to HPO instance types"""
        if 'g4dn' in instance_name:
            return 'g4'
        elif 'g5' in instance_name:
            return 'g5'
        elif 'on-prem' in instance_name:
            return 'g4'
        else:
            return 'unknown'

    # =========================================================================
    # MOLDABLE REQUEST PROCESSING WITH DEADLINE URGENCY BOOST
    # =========================================================================

    def processMoldableRequestHPO(self, request, sim):
        """
        Process moldable resource requests between HPO optimization rounds.
        Adds deadline-urgency-based graduated scaling from LA EDF pattern.

        Urgency modes:
        - CRITICAL (<30% time remaining): 2.0x budget boost, aggressive scale-up
        - WARNING (<50% time remaining): 1.5x budget boost
        - Regular (falling behind): 1.2x budget boost
        """
        wf_id = request['wf-id']
        (instances, budget, deadline, start_time, model) = self.resource_manager.getWorkflow(wf_id)

        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]

        # Current allocation
        current_instance = instances[0][0]
        current_instance_type = current_instance.name
        current_trials = sum(count for _, count, _ in instances)

        # === DEADLINE URGENCY ASSESSMENT ===
        elapsed_time = getTime(sim) - start_time
        total_time = deadline - start_time
        time_progress = elapsed_time / total_time if total_time > 0 else 0.0

        used_budget = self.metrics.computeCost(wf_id, getTime(sim))
        budget_progress = used_budget / budget if budget > 0 else 0.0

        time_remaining = deadline - getTime(sim)
        deadline_urgency = time_remaining / total_time if total_time > 0 else 0.0

        urgency_mode = 'NORMAL'
        skip_scale_down = False
        force_scale_up = False

        # CRITICAL: <30% time remaining
        if deadline_urgency < 0.30:
            print(f"  DEADLINE CRITICAL for {wf_id}: only {time_remaining:.1f}s ({deadline_urgency*100:.1f}%) remaining")
            urgency_mode = 'CRITICAL'
            skip_scale_down = True
            force_scale_up = True

        # WARNING: <50% time remaining and falling behind
        elif deadline_urgency < 0.50 and time_progress > budget_progress + 0.03:
            print(f"  DEADLINE WARNING for {wf_id}: {deadline_urgency*100:.1f}% time left")
            urgency_mode = 'WARNING'
            skip_scale_down = True
            force_scale_up = True

        # Falling behind
        elif time_progress > budget_progress + 0.03:
            print(f"  EARLY SCALE-UP for {wf_id}: time {time_progress*100:.1f}% > budget {budget_progress*100:.1f}%")
            skip_scale_down = True
            force_scale_up = True

        # === SCALE DOWN CHECK ===
        trials_per_instance = 3
        request['count'] = None
        min_needed_trials = request['chains']
        min_needed_instances = min_needed_trials

        if current_instance_type.startswith('g4dn') or 'on-prem' in current_instance_type:
            runtime_per_trial = getRuntime_g4(1, model, request['tinyda-iterations'])
        else:
            runtime_per_trial = getRuntime_g5(1, model, request['tinyda-iterations'])

        while trials_per_instance > 0 and not skip_scale_down:
            runtime = trials_per_instance * runtime_per_trial
            if runtime < available_time:
                min_needed_instances = min_needed_trials // trials_per_instance + bool(min_needed_trials % trials_per_instance)
                if current_trials > min_needed_instances:
                    request['count'] = current_trials - min_needed_instances
                    self.freeResources(instances, request, sim)
                    return
                else:
                    break
            else:
                trials_per_instance -= 1

        # === SCALE UP CHECK WITH DEADLINE URGENCY BOOST ===
        used_budget = self.metrics.computeCost(wf_id, getTime(sim))

        # Graduated boost factor based on urgency
        if urgency_mode == 'CRITICAL':
            boost_factor = 2.0
            print(f"  CRITICAL BOOST: 2.0x budget allocation for {wf_id}")
        elif urgency_mode == 'WARNING':
            boost_factor = 1.5
            print(f"  WARNING BOOST: 1.5x budget allocation for {wf_id}")
        elif force_scale_up:
            boost_factor = 1.2
            print(f"  SCALE-UP BOOST: 1.2x budget allocation for {wf_id}")
        else:
            boost_factor = 1.0

        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind] * boost_factor

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            if force_scale_up:
                # Request additional instances scaled by urgency
                if urgency_mode == 'CRITICAL':
                    additional = max(1, int(current_trials * 0.50))
                elif urgency_mode == 'WARNING':
                    additional = max(1, int(current_trials * 0.40))
                else:
                    additional = max(1, int(current_trials * 0.30))
                request['count'] = additional
                print(f"  Requesting +{additional} instances (current: {current_trials})")
            else:
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

        if ips:
            ips = self.createOnDemandWorkers(ips, sim)

        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def checkNewResourcesHPO(self, resources, current_resources, budget, available_runtime, request, model, instance_type_filter, sim):
        """
        Allocate additional resources for moldable HPO with cluster isolation.
        Identical to fcfs_optimized_HPO version.
        """
        if budget < MIN_INSTANCE_COST:
            return []

        if instance_type_filter.startswith('g4dn') or 'on-prem' in instance_type_filter:
            runtime_func = getRuntime_g4
        else:
            runtime_func = getRuntime_g5

        instance = current_resources[0][0]

        # CASE 1: On-prem workflow - scale ONLY within on-prem
        if isinstance(instance, OnPremInstance):
            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = (runtime / 3600) * instance.cost_per_second
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / cost_per_instance))

            if to_be_used > 0:
                print(f"EDF Moldable: Scaling on-prem workflow, adding {to_be_used} on-prem instances")
                return [(instance, to_be_used)]
            else:
                print(f"EDF Moldable: On-prem workflow cannot scale (no free slots or budget)")
                return []

        # CASE 2: Cloud workflow - scale ONLY within cloud (reserved + on-demand)
        available_instances = []
        for inst in resources:
            if (not isinstance(inst, OnPremInstance) and
                inst.name == instance_type_filter and
                inst.getFreeSlots() > 0):
                priority = 0 if inst.type == 'reserved' else 1
                available_instances.append((priority, inst))

        available_instances.sort(key=lambda x: x[0])

        acquired_count = 0
        acquired_instances = []
        trials_needed = request['count']

        for priority, inst in available_instances:
            if acquired_count >= trials_needed or budget < MIN_INSTANCE_COST:
                break

            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = (runtime / 3600) * inst.cost_per_second

            if inst.type == 'on-demand':
                cost_per_instance += COLD_START_TIME * inst.cost_per_second

            current_trials = request['chains'] - request['count'] + acquired_count
            new_trials = current_trials + 1

            current_runtime = runtime * (request['chains'] / max(current_trials, 1))
            new_runtime = runtime * (request['chains'] / new_trials)
            speedup = current_runtime / new_runtime if new_runtime > 0 else 0

            if (cost_per_instance < budget and
                speedup > SPEEDUP_THRESHOLD and
                new_runtime < available_runtime):

                to_be_used = min(trials_needed - acquired_count, inst.getFreeSlots())
                acquired_instances.append((inst, to_be_used))
                acquired_count += to_be_used
                budget -= to_be_used * cost_per_instance

        return acquired_instances

    def freeResources(self, instances, request, sim):
        """Free excess resources when deadline allows (LIFO strategy)"""
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []

        if request['count'] > 0:
            for i in range(len(instances) - 1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count'] - freed_count, count)
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
        print(f"{wf_id} EDF allocated additional resources: ", ips)
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
        print(f"EDF Scheduler freeing {response_instances} for {wf_id}")
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
        """
        on_prem_hosts = ips.get('on-prem', {})
        on_prem_ips = []
        for name, (count, ip_list) in on_prem_hosts.items():
            on_prem_ips.extend(ip_list)

        if on_prem_ips:
            executor_ip = on_prem_ips[0]
            print(f"HPO EDF Moldable Workflow {wf_plan['id']}: Using on-prem executor at {executor_ip}")
        else:
            # First allocated cloud IP acts as executor (reserved preferred)
            cloud_ips = []
            for name, (count, ip_list) in ips.get('reserved', {}).items():
                cloud_ips.extend(ip_list)
            for name, (count, ip_list) in ips.get('on-demand', {}).items():
                cloud_ips.extend(ip_list)
            executor_ip = cloud_ips[0]
            print(f"HPO EDF Moldable Workflow {wf_plan['id']}: Using cloud executor at {executor_ip}")

        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,
            "executor-ip": executor_ip,
            "deadline": deadline,
            "moldable": True
        }

        print(f"  Workers: {ips}")

        if sim:
            from executor_HPO import executeWorkflowHPO
            sim.process(executeWorkflowHPO, request, sim)
        else:
            sendRequest(executor_ip, getConfig('executor-incoming-port'), request)
