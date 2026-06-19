"""
License-Aware Static FCFS Scheduler

Static (non-moldable) First-Come-First-Served scheduler with license awareness.
Resources and licenses are allocated once at workflow start and remain fixed.
"""

import threading
import time

from config.constants import WORKFLOW_POLLING
from utils.request import ExecutorRequest
from resource_manager.resource_manager_LA import ResourceManager_LA
from utils.sim import getTime, peekElement, removeElement
from utils.resource_LA import getConstraintsFromWorkflow  # Use LA version for license fields
from scheduler.scheduler_LA import Scheduler_LA


class FCFS_Scheduler_LA(Scheduler_LA):
    """
    Static FCFS scheduler with license-awareness

    Workflows processed in FCFS order.
    Both compute resources and licenses allocated at workflow start.
    No dynamic reallocation (use fcfs_optimized_LA for moldable variant).
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        # Use license-aware resource manager
        self.resource_manager = ResourceManager_LA()
        self.resource_manager.sortResources(sort_key)
        super().__init__(queue, finish_queue, resource_request_queue)

        # Use resource_manager's license_manager (shared instance)
        self.license_manager = self.resource_manager.license_manager

        # Baseline is non-moldable (static allocation only)
        self.is_moldable = False

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):

        print(f'Starting License-Aware Static FCFS Scheduler at {getTime(sim)}...')

        # Track workflows that are impossible to allocate (prevent infinite waiting)
        rejected_workflows = set()

        # Start a thread to periodically compute resource utilization
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

            # Check queue for resource requests (should be minimal in static mode)
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                # Handle resource requests (free resources mainly)
                resource_request = eval(resource_request)
                if resource_request['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                    self.allocateNewResources(resource_request, sim)
                else:
                    self.freeResources(resource_request, sim)
                removeElement(resource_request_mb, self.resource_request_queue)
                sim and sim.sleep(0.2)  # Scheduler overhead
                continue

            # Check the queue for new jobs
            workflow_plan = peekElement(wf_mb, self.queue)

            if workflow_plan:
                wf_plan = eval(workflow_plan)

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    removeElement(wf_mb, self.queue)
                    from config.constants_LA import TOTAL_WORKFLOWS
                    self.metrics.computeMetrics(
                        file_prefix=f'Baseline_{TOTAL_WORKFLOWS}_',
                        license_cost_by_owner=self.license_manager.license_cost_by_owner(
                            getTime(sim) if sim is not None else self.license_manager.sim_now))
                    break

                # Skip workflows that have been rejected as impossible
                if wf_plan['id'] in rejected_workflows:
                    removeElement(wf_mb, self.queue)
                    print(f"⊘ Skipping rejected workflow {wf_plan['id']}")
                    continue

                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)

                    # NEW: Allocate BOTH compute and licenses
                    ips, alloc_resources, license_holds = self.allocateResourcesWithLicenses(constraints)

                    if ips:
                        print(f"{wf_plan['id']} allocated at {getTime(sim)}:", ips)
                        if license_holds:
                            print(f"  with {len(license_holds)} license hold(s)")

                        removeElement(wf_mb, self.queue)

                        # Start billing
                        start_time = getTime(sim)

                        # Send workflow with license info
                        self.sendWorkflowForExecution(
                            wf_plan, ips, sim, constraints['deadline'], license_holds
                        )

                        # Track workflow with licenses
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
                        # Check if this is an impossible allocation (exceeds pool capacity)
                        license_pool = constraints.get('license_pool')
                        if license_pool:
                            # Get hypothetical instance allocation
                            instances = self.resource_manager.getResources()
                            count, instance_list = self.checkResources(instances, constraints['min_instances'])

                            if count >= constraints['min_instances']:
                                total_cores = sum(inst.cores * cnt for inst, cnt in instance_list)
                                licenses_needed = self.license_manager.calculate_tokens(
                                    pool=license_pool,
                                    cores=total_cores,
                                    chains=constraints.get('chains', 1)
                                )
                                pool_status = self.license_manager.get_pool_status(license_pool)

                                if licenses_needed > pool_status['total']:
                                    # Impossible allocation - reject permanently
                                    rejected_workflows.add(wf_plan['id'])
                                    print(f'⊘ {wf_plan["id"]} REJECTED - needs {licenses_needed} tokens, pool has {pool_status["total"]}')
                                    # Will be removed from queue on next iteration
                                    continue

                        # Temporary shortage - wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print(f'⏳ {wf_plan["id"]} waiting for resources or licenses...')

            (sim or time).sleep(WORKFLOW_POLLING)
