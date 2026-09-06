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

from elastiflow.utils.request import ExecutorRequest
from elastiflow.resource_manager.resource_manager_LA import ResourceManager_LA
from elastiflow.scheduler.scheduler import EDFOrderingMixin
from elastiflow.scheduler.scheduler_LA import Scheduler_LA


class EDF_Scheduler_LA(EDFOrderingMixin, Scheduler_LA):
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

    metrics_prefix = 'EDF_Static_'
    drop_rejected_now = True

    def printBanner(self, backend):
        print(f'Starting EDF-LA Baseline (Static EDF with License Awareness) Scheduler...')
        print(f'  - Non-moldable: Resources allocated once at workflow start')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')
        print(f'  - License-aware: Dual allocation of compute + licenses')

    def handleRequest(self, request, backend):
        if request['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
            # Non-moldable: ignore scale-up requests (shouldn't happen)
            print(f"[WARNING] Scale-up request from {request['wf-id']} ignored (non-moldable mode)")
            self.allocateNewResources(request, backend)
        else:
            # Handle freeing (when workflow completes or releases resources)
            self.freeResources(request, backend)

    def nextWorkflow(self, backend):
        # Schedule new workflows in EDF order
        workflows = backend.workflows.pop_many(None)
        if workflows:
            # Sort by deadline (EDF)
            self.processWorkflowsByDeadline(workflows)
        # Process heap (even if no new workflows)
        return self.peekWorkflow(self.workflow_heap)

    def dropWorkflow(self, backend):
        self.popWorkflow(self.workflow_heap)
        backend.workflows.pop()

    def printAllocation(self, wf_plan, ips, backend, constraints=None):
        print(f"✓ {wf_plan['id']} allocated (deadline={constraints['deadline']:.1f}s):", ips)

    def rejectIfImpossible(self, wf_plan, constraints, backend) -> bool:
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
                        self.rejected_workflows.add(wf_plan['id'])
                        self.dropWorkflow(backend)
                        print(f'⊘ {wf_plan["id"]} REJECTED: needs {licenses_needed} {license_pool}, pool has {pool_status["total"]}')
                        return True
                except Exception as e:
                    print(f"  ⚠ Error checking license feasibility: {e}")
        return False

    def wait(self, wf_plan, constraints, backend):
        # Temporarily unavailable - wait for resources
        self.resource_manager.setResourcesAvailable(False)
        print(f'⏳ {wf_plan["id"]} (deadline={constraints["deadline"]:.1f}s) waiting for resources or licenses...')

    # =========================================================================
    # EDF HEAP MANAGEMENT (from edf_optimized_LA.py)
    # =========================================================================

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

