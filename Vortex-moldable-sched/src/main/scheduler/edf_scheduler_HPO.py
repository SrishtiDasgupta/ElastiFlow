"""
EDF_Scheduler_HPO: Static EDF Scheduler for HPO Workflows

Static (non-moldable) Earliest Deadline First scheduler for HPO.
Resources allocated once at workflow start - no scale-up/down.
Workflows processed in EDF order (earliest deadline first).

Baseline scheduler for comparison with:
- edf_optimized_HPO.py (Moldable EDF with deadline urgency)
- fcfs_optimized_HPO.py (Moldable FCFS)
"""

import heapq
import threading
import time
import math
from typing import List

from config.constants_HPO import WORKFLOW_POLLING, SIMULATE, COLD_START_TIME
from scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from scripts.create_instance_HPO import createWorkerInstances
from utils.request import ExecutorRequest, sendRequest, getConfig
import os
from resource_manager.resource_manager import ResourceManager
from resource_manager.instance import CloudOnDemandInstance, OnPremInstance

_HPO_RESOURCES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from utils.sim import getTime, getAllElements, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow
from scheduler.scheduler_HPO import Scheduler_HPO


class EDF_Scheduler_HPO(Scheduler_HPO):
    """
    Static EDF scheduler for HPO workflows.

    Workflows processed in EDF order (earliest deadline first).
    Resources allocated once at workflow start, no reallocation.
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost'):
        self.resource_manager = ResourceManager(_HPO_RESOURCES)
        func = lambda x: x.cost if hasattr(x, 'cost') else 0
        self.resource_manager.sortResourcesByFunction(func)

        # EDF heap setup
        self.workflow_heap = []
        self.workflow_counter = 0

        super().__init__(queue, finish_queue, resource_request_queue)

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):

        print(f'Starting HPO EDF Static scheduler at {getTime(sim)}...')
        print(f'  - Non-moldable: Resources allocated once at workflow start')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')

        # Start a thread to periodically compute resource utilization
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(target=self.metrics.collectResourceUtilization, args=[sim, self.resource_manager])
            thread.start()

        while True:

            # Check queue for resource requests (minimal for static version)
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                resource_request = eval(resource_request)
                print(f"HPO EDF Static Scheduler: Resource request received but ignored (static mode)")
                removeElement(resource_request_mb, self.resource_request_queue)
                continue

            # Collect new workflows and sort by deadline (EDF)
            workflows = getAllElements(wf_mb, self.queue, None)

            if workflows:
                self.processWorkflowsByDeadline(workflows)

            # Process heap (even if no new workflows)
            wf_plan = self.peekWorkflow(self.workflow_heap)

            if wf_plan:

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    self.popWorkflow(self.workflow_heap)
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics(file_prefix='EDF_Static_HPO_')
                    break

                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesHPO(constraints, sim)
                    print(f"{wf_plan['id']} allocated at {getTime(sim)} (deadline={constraints['deadline']:.1f}s):", ips)

                    # Remove the element if we found the resources needed.
                    if ips:
                        self.popWorkflow(self.workflow_heap)
                        removeElement(wf_mb, self.queue)
                        # NOTE: We start billing at this point
                        start_time = getTime(sim)
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO EDF resources to allocate, waiting...')

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
    # HPO RESOURCE ALLOCATION (same as fcfs_scheduler_HPO.py)
    # =========================================================================

    def allocateResourcesHPO(self, constraints, sim=None):
        """
        HPO-specific resource allocation with resilient fallback
        Tries to allocate optimal number of hosts, degrades gracefully if unavailable
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
                        print(f"Allocated {num_hosts_requested} on-prem {instance.name} (workflow locked to on-prem)")
                        break
                    elif slots_available > 0:
                        selected_instances = [(instance, slots_available)]
                        remaining -= slots_available
                        print(f"Allocated {slots_available} on-prem {instance.name} (degraded from {num_hosts_requested})")
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
                                print(f"Allocated {slots_available} on-demand {optimal_type} (cost: ${cost:.2f})")
                                if remaining == 0:
                                    break

        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"Degraded allocation: requested {num_hosts_requested}, got {allocated_hosts} hosts")

        if allocated_hosts == 0:
            print(f"Failed to allocate any hosts of type {optimal_type}")
            return None, None

        ips, alloc_resources = self.resource_manager.allocateResources(selected_instances)
        ips = self.createOnDemandWorkers(ips, sim)

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts
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

        print(f"Selected: {best_num_hosts} x {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
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
        on_prem_hosts = ips.get('on-prem', {})
        on_prem_ips = []
        for name, (count, ip_list) in on_prem_hosts.items():
            on_prem_ips.extend(ip_list)

        if on_prem_ips:
            executor_ip = on_prem_ips[0]
            print(f"HPO EDF Workflow {wf_plan['id']}: Using on-prem executor at {executor_ip}")
        else:
            # First allocated cloud IP acts as executor (reserved preferred)
            cloud_ips = []
            for name, (count, ip_list) in ips.get('reserved', {}).items():
                cloud_ips.extend(ip_list)
            for name, (count, ip_list) in ips.get('on-demand', {}).items():
                cloud_ips.extend(ip_list)
            executor_ip = cloud_ips[0]
            print(f"HPO EDF Workflow {wf_plan['id']}: Using cloud executor at {executor_ip}")

        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,
            "executor-ip": executor_ip,
            "deadline": deadline
        }

        print(f"  Workers: {ips}")

        if sim:
            from executor_HPO import executeWorkflowHPO
            sim.process(executeWorkflowHPO, request, sim)
        else:
            sendRequest(executor_ip, getConfig('executor-incoming-port'), request)
