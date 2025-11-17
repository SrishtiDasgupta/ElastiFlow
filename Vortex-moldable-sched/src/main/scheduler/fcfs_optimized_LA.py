"""
LAMF - License-Aware Moldable FCFS Scheduler

Core implementation of License-Aware Moldable First-Come-First-Served scheduling.
Extends moldable scheduling with dual-resource constraints (compute + licenses).

Key Features:
- Iteration-weighted resource allocation (OPTIM_FCFS factors)
- Dynamic scale-up/scale-down between workflow iterations
- License availability checking before allocation
- License-constrained partial allocation (fit to available licenses)
- Automatic license release on resource freeing
"""

from collections import deque
import math
import threading
import time
from typing import List, Tuple, Optional

from config.constants import (
    COLD_START_TIME, DEADLINE_BUFFER, MIN_INSTANCE_COST,
    OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR, RESOURCE_REQUEST_TIMEOUT,
    SPEEDUP_THRESHOLD, WORKFLOW_POLLING
)
from scripts.speedup import getRuntime
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from resource_manager.resource_manager_LA import ResourceManager_LA
from resource_manager.license.manager import LicenseManager
from resource_manager.license.exceptions import InsufficientTokens, LicenseError
from utils.sim import getTime, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow, getEstimate
from scheduler.scheduler_LA import Scheduler_LA


class FCFS_Optimized_LA(Scheduler_LA):
    """
    LAMF - License-Aware Moldable FCFS Scheduler

    Moldable FCFS with dual-resource (compute + license) constraints.
    Dynamically adjusts resources between iterations while respecting license availability.
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        # Use license-aware resource manager
        self.resource_manager = ResourceManager_LA()

        # Sort resources by cost + cold start penalty
        func = lambda x: x.getValue(sort_key) + (COLD_START_TIME if isinstance(x, CloudOnDemandInstance) else 0)
        self.resource_manager.sortResourcesByFunction(func)

        super().__init__(queue, finish_queue, resource_request_queue)

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):

        print(f'Starting LAMF (License-Aware Moldable FCFS) Scheduler...')

        # Start resource utilization collection
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(
                target=self.metrics.collectResourceUtilization,
                args=[sim, self.resource_manager]
            )
            thread.start()

        while True:

            # Check queue for resource requests (moldable requests from executors)
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                start = time.time()
                resource_request = eval(resource_request)

                # Timeout check
                if getTime(sim) - resource_request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
                    removeElement(resource_request_mb, self.resource_request_queue)
                    continue

                # Process moldable request (with license awareness)
                self.processFreeRequestWithLicenses(resource_request, sim)

                print(f"⏱ Resource request overhead: {time.time() - start:.3f}s")
                removeElement(resource_request_mb, self.resource_request_queue)
                continue

            # Check the queue for new jobs
            workflow_plan = peekElement(wf_mb, self.queue)

            if workflow_plan:
                wf_plan = eval(workflow_plan)

                # End simulation
                if wf_plan['id'] == 'END':
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics()
                    break

                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)

                    # Allocate BOTH compute and licenses
                    ips, alloc_resources, license_holds = self.allocateResourcesWithLicenses(constraints)

                    if ips:
                        print(f"✓ {wf_plan['id']} allocated:", ips)
                        if license_holds:
                            print(f"  with {len(license_holds)} license hold(s)")

                        removeElement(wf_mb, self.queue)

                        # Start billing
                        start_time = getTime(sim)

                        # Send to executor
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
                        # Wait until BOTH resources and licenses become available
                        self.resource_manager.setResourcesAvailable(False)
                        print(f'⏳ {wf_plan["id"]} waiting for resources or licenses...')

            (sim or time).sleep(WORKFLOW_POLLING)

    def processFreeRequestWithLicenses(self, request, sim):
        """
        Process moldable resource request WITH license awareness

        This is the core LAMF algorithm:
        1. Calculate iteration-weighted budget/deadline constraints
        2. Check if can scale down (free compute + licenses)
        3. If not, check if should scale up (allocate compute + licenses)
        4. Account for license availability in all decisions
        """
        wf_data = self.resource_manager.getWorkflow(request['wf-id'])
        if len(wf_data) == 7:
            instances, budget, deadline, start_time, mesh, software_id, license_holds = wf_data
        else:
            # Fallback for workflows without license tracking
            instances, budget, deadline, start_time, mesh = wf_data
            software_id = 0
            license_holds = []

        # Get license pool for this workflow
        license_pool = self.license_manager.get_pool_for_software(software_id) if software_id else None

        # Iteration-weighted constraints (from Vortex)
        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]

        cur_instance: Instance = instances[-1][0]
        cur_count = instances[-1][1]

        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = sum(inst_tuple[1] for inst_tuple in instances)

        # === SCALE DOWN CHECK ===
        # Can we free resources without missing deadline?
        chains_per_node = 3
        request['count'] = None
        min_needed_count = request['chains']
        runtime_per_model = getRuntime(1, mesh, cur_instance.name)

        while chains_per_node > 0:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']
            if runtime < available_time:  # Can complete with fewer resources
                min_needed_count = request['chains'] // chains_per_node + bool(request['chains'] % chains_per_node)

                if cur_count >= min_needed_count:
                    # Can scale down!
                    request['count'] = cur_count - min_needed_count
                    print(f"⬇ Scaling down: freeing {request['count']} instances")

                    # Free compute AND licenses
                    self.freeResourcesWithLicenses(instances, request, sim, license_pool)
                    return
                else:
                    # Need to scale up
                    break
            else:
                chains_per_node -= 1

        # === SCALE UP CHECK ===
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            request['count'] = min_needed_count - cur_count

        # Check NEW resources with license constraints
        alloc_instances, license_holds_new = self.checkNewResourcesWithLicenses(
            free_resources, instances, available_budget, available_time,
            request, mesh, license_pool
        )

        if alloc_instances:
            print(f"⬆ Scaling up: allocating {sum(c for _, c in alloc_instances)} instances")

            # Allocate compute
            ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)

            # Send to executor with new licenses
            self.sendNewResources(
                request['wf-id'], ips, alloc_resources, sim,
                request.get('client-ip', None), license_holds_new
            )

            # Update workflow's license holds
            if license_holds_new:
                self.license_holds[request['wf-id']] = (
                    self.license_holds.get(request['wf-id'], []) + license_holds_new
                )

        else:
            print(f"⏸ No scaling: insufficient resources or licenses")

    def checkNewResourcesWithLicenses(
        self,
        resources: List[Instance],
        current_resources: List[tuple[Instance, int, List]],
        budget: float,
        available_runtime: float,
        request,
        mesh,
        license_pool: Optional[str]
    ) -> Tuple[List[tuple[Instance, int]], List[str]]:
        """
        Check new resources WITH license constraints

        Returns:
            (alloc_instances, license_holds)
        """
        # First, get compute allocation (from parent class)
        alloc_instances = self.checkNewResources(
            resources, current_resources, budget, available_runtime, request, mesh
        )

        if not alloc_instances:
            return ([], [])

        # If no license pool, return compute allocation as-is
        if not license_pool:
            return (alloc_instances, [])

        # Calculate licenses needed for proposed allocation
        total_cores = sum(inst.cores * count for inst, count in alloc_instances)

        try:
            licenses_needed = self.license_manager.calculate_tokens(
                pool=license_pool,
                cores=total_cores,
                chains=request.get('chains', 1)
            )

            # Check if licenses available
            available_tokens = self.license_manager.get_available_tokens(license_pool)

            if available_tokens >= licenses_needed:
                # Can allocate! Hold licenses
                hold_id = self.license_manager.hold(
                    pool=license_pool,
                    amount=licenses_needed,
                    owner=request['wf-id'],
                    ttl=300
                )
                self.license_manager.commit(hold_id)

                print(f"  ✓ Allocated {licenses_needed} licenses (available: {available_tokens})")
                return (alloc_instances, [hold_id])

            else:
                # Insufficient licenses - try partial allocation
                print(f"  ⚠ Insufficient licenses: need {licenses_needed}, have {available_tokens}")

                feasible_instances = self.findLicenseFeasibleAllocation(
                    alloc_instances, available_tokens, license_pool
                )

                if feasible_instances:
                    # Partial allocation
                    total_cores_feasible = sum(inst.cores * count for inst, count in feasible_instances)
                    licenses_feasible = self.license_manager.calculate_tokens(
                        pool=license_pool,
                        cores=total_cores_feasible,
                        chains=request.get('chains', 1)
                    )

                    hold_id = self.license_manager.hold(
                        pool=license_pool,
                        amount=licenses_feasible,
                        owner=request['wf-id'],
                        ttl=300
                    )
                    self.license_manager.commit(hold_id)

                    print(f"  ✓ Partial allocation: {sum(c for _, c in feasible_instances)} instances, {licenses_feasible} licenses")
                    return (feasible_instances, [hold_id])

                else:
                    print(f"  ✗ Cannot fit any allocation to available licenses")
                    return ([], [])

        except (InsufficientTokens, LicenseError) as e:
            print(f"  ✗ License error: {e}")
            return ([], [])

    def findLicenseFeasibleAllocation(
        self,
        instances: List[tuple[Instance, int]],
        available_licenses: int,
        license_pool: str
    ) -> Optional[List[tuple[Instance, int]]]:
        """
        Find subset of instances that fits available licenses

        Tries to allocate as many instances as possible within license limit.
        """
        feasible = []
        licenses_used = 0

        for inst, count in instances:
            # Try adding instances one by one
            for i in range(count):
                # Calculate licenses for current + 1 instance
                test_cores = sum(i.cores * c for i, c in feasible) + inst.cores
                try:
                    test_licenses = self.license_manager.calculate_tokens(
                        pool=license_pool,
                        cores=test_cores,
                        chains=1
                    )

                    if test_licenses <= available_licenses:
                        # Can add this instance
                        if feasible and feasible[-1][0] == inst:
                            # Same instance type - increment count
                            feasible[-1] = (inst, feasible[-1][1] + 1)
                        else:
                            # New instance type
                            feasible.append((inst, 1))
                        licenses_used = test_licenses
                    else:
                        # Would exceed license limit - stop
                        break

                except LicenseError:
                    break

        return feasible if feasible else None

    def freeResourcesWithLicenses(self, instances, request, sim, license_pool: Optional[str]):
        """
        Free resources AND licenses

        Extends base freeResources() to also release licenses.
        """
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []

        # Free compute resources (LIFO)
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

            # Free licenses corresponding to freed instances
            if license_pool and to_free_instances:
                total_cores_freed = sum(inst.cores * count for inst, count, _ in to_free_instances)

                try:
                    licenses_to_free = self.license_manager.calculate_tokens(
                        pool=license_pool,
                        cores=total_cores_freed,
                        chains=1
                    )

                    # Release licenses (proportional to freed compute)
                    wf_id = request['wf-id']
                    if wf_id in self.license_holds and self.license_holds[wf_id]:
                        # Release most recent hold (LIFO)
                        hold_id = self.license_holds[wf_id].pop()
                        self.license_manager.release(hold_id)
                        print(f"  ✓ Released ~{licenses_to_free} licenses (hold: {hold_id})")

                except LicenseError as e:
                    print(f"  ⚠ License release error: {e}")

        # Send freed resources notification
        self.sendFreedResources(
            request['wf-id'], to_free_instances, instances,
            response_instances, sim, request.get('client-ip', None)
        )

    # === INHERITED METHODS FROM PARENT ===
    # The following methods are inherited from Scheduler_LA and fcfs_optimized:
    # - checkNewResources() - compute resource checking
    # - checkCloseness() - runtime similarity check

    def checkCloseness(self, instance: Instance, runtimes_list, mesh) -> bool:
        """
        Check if instance runtime is within 15% of existing runtimes

        Allows minor heterogeneity for SeisSol workloads (CPU-bound, tolerates straggling)
        """
        runtime = getRuntime(1, mesh, instance.name)
        if isinstance(instance, CloudOnDemandInstance):
            runtime += COLD_START_TIME

        closeness = lambda x: math.isclose(runtime, x, rel_tol=0.15)
        return any(map(closeness, runtimes_list))

    # checkNewResources() is inherited from parent Scheduler_LA
    # It contains the full moldable logic from fcfs_optimized.py
