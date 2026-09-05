"""
EDF_Scheduler_LA: Non-Moldable EDF with License Awareness

Static (non-moldable) Earliest Deadline First scheduler with license awareness.
Resources and licenses allocated once at workflow start - no scale-up/down.
Workflows processed in EDF order (earliest deadline first).

This is a BASELINE scheduler for comparison with:
- edf_optimized_LA.py (DDM-EDF - moldable EDF with urgency-based scaling)
- fcfs_optimized_LA.py (LAMF - moldable FCFS)

Key characteristics:
- EDF ordering: Workflows sorted by absolute deadline (submit_time + deadline_constraint)
- Static allocation: Resources allocated once at start, no reallocation
- License-aware: Dual allocation of compute + licenses
- Non-moldable: No scale-up, no scale-down, no iteration-based reallocation
"""

import heapq
import threading
import time
from typing import List

from elastiflow.config.constants_LA import WORKFLOW_POLLING, TOTAL_WORKFLOWS
from elastiflow.utils.request import ExecutorRequest
from elastiflow.resource_manager.resource_manager_LA import ResourceManager_LA
from elastiflow.utils.sim import getTime, getAllElements, peekElement, removeElement
from elastiflow.utils.resource_LA import getConstraintsFromWorkflow
from elastiflow.scheduler.scheduler_LA import Scheduler_LA


class EDF_Scheduler_LA(Scheduler_LA):
    """
    Static EDF scheduler with license-awareness

    Workflows processed in EDF order (earliest deadline first).
    Both compute resources and licenses allocated at workflow start.
    No dynamic reallocation (use edf_optimized_LA for moldable variant).

    Serves as a baseline to evaluate DDM-EDF's moldability benefits.
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        # Resource manager setup (from fcfs_scheduler_LA)
        self.resource_manager = ResourceManager_LA()
        self.resource_manager.sortResources(sort_key)

        # EDF heap setup (from edf_optimized_LA)
        self.workflow_heap = []
        self.resource_request_heap = []
        self.workflow_counter = 0
        self.resource_request_counter = 0

        super().__init__(queue, finish_queue, resource_request_queue)
        self.license_manager = self.resource_manager.license_manager

        # Baseline is non-moldable
        self.is_moldable = False

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):
        print(f'Starting EDF-LA Baseline (Static EDF with License Awareness) Scheduler...')
        print(f'  - Non-moldable: Resources allocated once at workflow start')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')
        print(f'  - License-aware: Dual allocation of compute + licenses')

        rejected_workflows = set()

        # Start resource utilization monitoring (including license pools)
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager, self.license_manager)
        else:
            thread = threading.Thread(
                target=self.metrics.collectResourceUtilization,
                args=[sim, self.resource_manager, self.license_manager]
            )
            thread.start()

        while True:
            # Advance the license ledger clock (honest Token-Hours billing — same
            # basis as the moldable schedulers, so cost is comparable across policies).
            if sim is not None:
                self.license_manager.set_sim_time(getTime(sim))

            # === PHASE 1: Handle resource requests (minimal for non-moldable) ===
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                resource_request = eval(resource_request)
                if resource_request['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                    # Non-moldable: ignore scale-up requests (shouldn't happen)
                    print(f"[WARNING] Scale-up request from {resource_request['wf-id']} ignored (non-moldable mode)")
                    self.allocateNewResources(resource_request, sim)
                else:
                    # Handle freeing (when workflow completes or releases resources)
                    self.freeResources(resource_request, sim)
                removeElement(resource_request_mb, self.resource_request_queue)
                sim and sim.sleep(0.2)
                continue

            # === PHASE 2: Schedule new workflows in EDF order ===
            workflows = getAllElements(wf_mb, self.queue, None)

            if workflows:
                # Sort by deadline (EDF)
                self.processWorkflowsByDeadline(workflows)

            # Process heap (even if no new workflows)
            wf_plan = self.peekWorkflow(self.workflow_heap)

            if wf_plan:
                # Check for END signal
                if wf_plan['id'] == 'END':
                    self.popWorkflow(self.workflow_heap)
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics(
                        file_prefix=f'EDF_Static_{TOTAL_WORKFLOWS}_',
                        license_cost_by_owner=self.license_manager.license_cost_by_owner(
                            getTime(sim) if sim is not None else self.license_manager.sim_now))
                    break

                # Skip rejected workflows
                if wf_plan['id'] in rejected_workflows:
                    self.popWorkflow(self.workflow_heap)
                    removeElement(wf_mb, self.queue)
                    print(f"⊘ Skipping rejected workflow {wf_plan['id']}")
                    continue

                # Try allocation if resources available
                if self.resource_manager.getResourcesAvailable():
                    constraints = getConstraintsFromWorkflow(wf_plan)

                    # Allocate compute + licenses (static, one-time allocation)
                    ips, alloc_resources, license_holds = self.allocateResourcesWithLicenses(constraints)

                    if ips:
                        print(f"✓ {wf_plan['id']} allocated (deadline={constraints['deadline']:.1f}s):", ips)
                        if license_holds:
                            print(f"  with {len(license_holds)} license hold(s)")

                        self.popWorkflow(self.workflow_heap)
                        removeElement(wf_mb, self.queue)

                        start_time = getTime(sim)

                        # Send to executor
                        self.sendWorkflowForExecution(
                            wf_plan, ips, sim, constraints['deadline'], license_holds
                        )

                        # Track workflow
                        wf = self.resource_manager.addWorkflow(
                            wf_plan['id'],
                            alloc_resources,
                            constraints['budget'],
                            constraints['deadline'],
                            start_time,
                            constraints['mesh'],
                            constraints.get('software_id', 0),
                            license_holds
                        )

                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])

                    else:
                        # Allocation failed - check if impossible or temporarily unavailable
                        license_pool = constraints.get('license_pool')

                        if license_pool:
                            # Check if workflow is truly impossible (exceeds pool capacity)
                            instances = self.resource_manager.getResources()
                            count, instance_list = self.checkResources(instances, constraints['min_instances'])

                            if count >= constraints['min_instances']:
                                # Compute available, check license capacity
                                total_cores = sum(inst.cores * cnt for inst, cnt in instance_list)

                                try:
                                    licenses_needed = self.license_manager.calculate_tokens(
                                        pool=license_pool,
                                        cores=total_cores,
                                        chains=constraints.get('chains', 1)
                                    )
                                    pool_status = self.license_manager.get_pool_status(license_pool)

                                    if licenses_needed > pool_status['total']:
                                        # Impossible - exceeds license pool capacity
                                        rejected_workflows.add(wf_plan['id'])
                                        self.popWorkflow(self.workflow_heap)
                                        removeElement(wf_mb, self.queue)
                                        print(f'⊘ {wf_plan["id"]} REJECTED: needs {licenses_needed} {license_pool}, pool has {pool_status["total"]}')
                                        continue
                                except Exception as e:
                                    print(f"  ⚠ Error checking license feasibility: {e}")

                        # Temporarily unavailable - wait for resources
                        self.resource_manager.setResourcesAvailable(False)
                        print(f'⏳ {wf_plan["id"]} (deadline={constraints["deadline"]:.1f}s) waiting for resources or licenses...')
                else:
                    # Resources not available - wait
                    pass

            (sim or time).sleep(WORKFLOW_POLLING)

    # =========================================================================
    # EDF HEAP MANAGEMENT (from edf_optimized_LA.py)
    # =========================================================================

    def processWorkflowsByDeadline(self, workflows: List[any]):
        """Sort workflows by deadline (EDF ordering)"""
        for wf in workflows:
            wf_plan = eval(wf)
            if wf_plan['id'] == 'END':
                heapq.heappush(self.workflow_heap, (1000000, self.workflow_counter, wf_plan['id'], wf_plan))
            else:
                # Calculate absolute deadline
                deadline = wf_plan['submit_time'] + wf_plan['constraints']['deadline']
                heapq.heappush(self.workflow_heap, (deadline, self.workflow_counter, wf_plan['id'], wf_plan))
            self.workflow_counter += 1

    def processResourceRequestsByDeadline(self, requests: List[any]):
        """Sort resource requests by deadline (EDF ordering)"""
        for req in requests:
            req_dict = eval(req)
            if req_dict['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                # Get workflow deadline
                wf = self.resource_manager.getWorkflow(req_dict['wf-id'])
                deadline = wf[2] if wf else float('inf')
            else:
                # Free requests always on top
                deadline = 0

            heapq.heappush(self.resource_request_heap, (deadline, self.resource_request_counter, req_dict['wf-id'], req_dict))
            self.resource_request_counter += 1

    def peekWorkflow(self, heap):
        """Peek at top of heap without removing"""
        return heap and heap[0][3]  # Index 3: (deadline, counter, wf_id, data)

    def popWorkflow(self, heap):
        """Remove top of heap"""
        try:
            heapq.heappop(heap)
        except Exception as e:
            print(f'HEAP POP ERROR: {e}')
            print(heap)
