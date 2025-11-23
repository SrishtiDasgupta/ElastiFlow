"""
EDF_Optimized_LA: Deadline-Driven Moldable EDF with License Awareness

This scheduler combines:
1. EDF (Earliest Deadline First) queue ordering
2. Sophisticated moldability from FCFS_Optimized_LA
3. Deadline-urgency-based scaling decisions
4. Preemptive resource reallocation
5. License-aware resource management

Key innovations:
- Deadline slack calculation for urgency-based scaling
- Preemptive scale-down of workflows with excess slack
- Resource reallocation to deadline-critical workflows
"""

import heapq
import math
import threading
import time
from typing import List, Dict, Tuple, Optional

from config.constants_LA import (
    COLD_START_TIME, DEADLINE_BUFFER, MIN_INSTANCE_COST,
    OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR,
    RESOURCE_REQUEST_TIMEOUT, SPEEDUP_THRESHOLD,
    WORKFLOW_POLLING,
    TOTAL_WORKFLOWS
)

from scripts.speedup import getRuntime
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from resource_manager.resource_manager_LA import ResourceManager_LA
from resource_manager.license.exceptions import LicenseError, InsufficientTokens
from utils.sim import getTime, getAllElements, peekElement, removeElement
from utils.resource_LA import getConstraintsFromWorkflow, getEstimate
from scheduler.scheduler_LA import Scheduler_LA


class EDF_Optimized_LA(Scheduler_LA):
    """
    Deadline-Driven Moldable EDF Scheduler with License Awareness

    Extends Scheduler_LA with:
    - EDF queue ordering (deadline-based priority)
    - Advanced moldability from FCFS_Optimized_LA
    - Deadline urgency-based scaling
    - Preemptive resource reallocation
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = ResourceManager_LA()

        # Sort resources by cost+runtime (like FCFS_Optimized_LA)
        func = lambda x: x.getValue(sort_key) + (COLD_START_TIME if isinstance(x, CloudOnDemandInstance) else 0)
        self.resource_manager.sortResourcesByFunction(func)

        # Priority heaps for EDF ordering
        self.workflow_heap = []
        self.resource_request_heap = []

        # Heap counters for tiebreaking (prevents dict comparison errors)
        self.workflow_counter = 0
        self.resource_request_counter = 0

        # License hold tracking (like LAMF)
        self.license_holds = {}  # {wf_id: [hold_ids]}

        super().__init__(queue, finish_queue, resource_request_queue)

        # Link license manager from resource manager (required for license-aware operations)
        self.license_manager = self.resource_manager.license_manager

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):
        """
        Main scheduler loop with EDF ordering
        """
        print(f'Starting EDF-ordered LAMF scheduler...')
        print(f'  - EDF heap ordering: Deadline-based priority queue')
        print(f'  - LAMF moldability: Iteration-based progress triggers')
        print(f'  - Iteration weighting: BFACTOR/DFACTOR {list(OPTIM_FCFS_DFACTOR.values())}')
        print(f'  - Progress triggers: time_progress vs budget_progress')
        print(f'  - License-aware guards: Pool saturation, late iteration, time/budget progress')

        # Start resource utilization monitoring (including license pools)
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager, self.license_manager)
        else:
            thread = threading.Thread(
                target=self.metrics.collectResourceUtilization,
                args=[sim, self.resource_manager, self.license_manager]
            )
            thread.start()

        rejected_workflows = set()  # Track impossible workflows
        loop_counter = 0  # Track loop iterations for debugging
        idle_loop_count = 0  # Track consecutive idle loops

        while True:
            loop_counter += 1

            # Progress indicator every 1000 iterations (reduced frequency)
            if loop_counter % 1000 == 0:
                current_time = getTime(sim)
                active_wfs = len(self.resource_manager.workflows)
                completed_wfs = self.metrics.workflow_count if hasattr(self.metrics, 'workflow_count') else 0
                wf_heap_size = len(self.workflow_heap)
                req_heap_size = len(self.resource_request_heap)
                print(f"[PROGRESS] Loop {loop_counter}, time={current_time:.1f}s, active={active_wfs}, completed={completed_wfs}, wf_heap={wf_heap_size}, req_heap={req_heap_size}, idle_count={idle_loop_count}")

            # Termination condition: If no active workflows and nothing in queue for 10 consecutive iterations
            active_wfs = len(self.resource_manager.workflows)
            wf_heap_empty = len(self.workflow_heap) == 0
            req_heap_empty = len(self.resource_request_heap) == 0

            if active_wfs == 0 and wf_heap_empty and req_heap_empty:
                idle_loop_count += 1
                if idle_loop_count > 10:
                    print(f"\n{'='*70}")
                    print(f"✓ All workflows completed. Terminating scheduler.")
                    print(f"  Total loops: {loop_counter}")
                    print(f"  Final simulated time: {getTime(sim):.1f}s")
                    print(f"{'='*70}\n")
                    from config.constants_LA import TOTAL_WORKFLOWS
                    self.metrics.computeMetrics(file_prefix=f'EDF_{TOTAL_WORKFLOWS}_')
                    break
                elif idle_loop_count == 1:
                    print(f"\n[INFO] No active workflows detected. Waiting for termination (idle_count={idle_loop_count}/10)...")
            else:
                if idle_loop_count > 0:
                    print(f"[DEBUG] Activity detected, resetting idle_count from {idle_loop_count} (active={active_wfs}, wf_heap={len(self.workflow_heap)}, req_heap={len(self.resource_request_heap)})")
                idle_loop_count = 0  # Reset if there's activity

            # === PHASE 1: Process Resource Requests (EDF ordering) ===
            resource_requests = getAllElements(resource_request_mb, self.resource_request_queue, None)

            if resource_requests:
                # Sort resource requests by deadline (EDF)
                self.processResourceRequestsByDeadline(resource_requests)
                resource_request = self.peekWorkflow(self.resource_request_heap)

                if resource_request:
                    # Process with deadline-urgency awareness
                    start = time.time()

                    if getTime(sim) - resource_request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
                        self.popWorkflow(self.resource_request_heap)
                        removeElement(resource_request_mb, self.resource_request_queue)
                        continue

                    self.processFreeRequestWithLicenses(sim, wf_mb, resource_request)
                    print(f"  Resource request overhead: {time.time() - start:.3f}s")

                    self.popWorkflow(self.resource_request_heap)
                    removeElement(resource_request_mb, self.resource_request_queue)
                    continue

            # === PHASE 2: Schedule New Workflows (EDF ordering) ===
            workflows = getAllElements(wf_mb, self.queue, None)

            # Debug heap state periodically
            if loop_counter % 10000 == 0 and len(self.workflow_heap) > 0:
                print(f"[DEBUG] Workflow heap has {len(self.workflow_heap)} workflows, checking top workflow...")
                resources_available = self.resource_manager.getResourcesAvailable()
                print(f"[DEBUG] Resources available: {resources_available}")

            if workflows:
                if loop_counter <= 100 or loop_counter % 10000 == 0:  # Debug early and periodically
                    print(f"[DEBUG] Received {len(workflows)} workflows at loop {loop_counter}")
                # Sort workflows by deadline (EDF)
                self.processWorkflowsByDeadline(workflows)

            # Process heap even if no new workflows arrived
            wf_plan = self.peekWorkflow(self.workflow_heap)
            if wf_plan:
                # Check for END signal
                if wf_plan['id'] == 'END':
                    self.popWorkflow(self.workflow_heap)
                    removeElement(wf_mb, self.queue)
                    from config.constants_LA import TOTAL_WORKFLOWS
                    self.metrics.computeMetrics(file_prefix=f'EDF_{TOTAL_WORKFLOWS}_')
                    break

                # Skip rejected workflows
                if wf_plan['id'] in rejected_workflows:
                    self.popWorkflow(self.workflow_heap)
                    removeElement(wf_mb, self.queue)
                    print(f"⊘ Skipping rejected workflow {wf_plan['id']}")
                    continue

                # Try to schedule if resources available
                resources_available = self.resource_manager.getResourcesAvailable()

                # Debug why scheduling is blocked
                if loop_counter % 10000 == 0:
                    print(f"[DEBUG] Phase 3: resources_available={resources_available}, heap_size={len(self.workflow_heap)}")

                if resources_available:
                    constraints = getConstraintsFromWorkflow(wf_plan)

                    # Allocate compute + licenses
                    ips, alloc_resources, license_holds = self.allocateResourcesWithLicenses(constraints)

                    if ips:
                        print(f"✓ {wf_plan['id']} allocated: {ips}")
                        if license_holds:
                            print(f"  with {len(license_holds)} license hold(s)")

                        self.popWorkflow(self.workflow_heap)
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

                        # Track initial allocation in scheduler
                        if license_holds:
                            self.license_holds[wf_plan['id']] = license_holds

                    elif ips is None and alloc_resources is None and license_holds is None:
                        # Check if workflow is truly impossible or just temporarily unavailable
                        constraints_check = getConstraintsFromWorkflow(wf_plan)
                        is_impossible = self.isWorkflowImpossible(constraints_check)

                        if is_impossible:
                            # Truly impossible (exceeds pool capacity) - permanently reject
                            rejected_workflows.add(wf_plan['id'])
                            self.popWorkflow(self.workflow_heap)
                            removeElement(wf_mb, self.queue)
                            print(f"⊘ Rejecting impossible workflow {wf_plan['id']}")
                        else:
                            # Temporarily unavailable - wait for resources
                            self.resource_manager.setResourcesAvailable(False)
                            if loop_counter % 10000 == 0:
                                print(f'⏳ {wf_plan["id"]} waiting for resources/licenses (heap_size={len(self.workflow_heap)})...')
                    else:
                        # Wait for resources
                        self.resource_manager.setResourcesAvailable(False)
                        if loop_counter % 10000 == 0:
                            print(f'[DEBUG] No resources to allocate, waiting... (heap_size={len(self.workflow_heap)})')
                else:
                    # Resources not available - this is likely the blocking condition
                    if loop_counter % 10000 == 0:
                        print(f'[DEBUG] Resources marked as unavailable, skipping allocation (heap_size={len(self.workflow_heap)})')

            (sim or time).sleep(WORKFLOW_POLLING)

    # =========================================================================
    # EDF HEAP MANAGEMENT (from HEFTResourceManager)
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
        """Sort resource requests by deadline (EDF ordering)"""
        from utils.request import ExecutorRequest

        for req in requests:
            req_dict = eval(req)
            if req_dict['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                # Min heap with deadlines
                wf = self.resource_manager.getWorkflow(req_dict['wf-id'])
                deadline = wf[2] if wf else float('inf')
            else:
                deadline = 0  # Free requests always on top

            try:
                heapq.heappush(self.resource_request_heap, (deadline, self.resource_request_counter, req_dict['wf-id'], req_dict))
                self.resource_request_counter += 1
            except Exception as e:
                print(f'HEAP ERROR: {e}')
                print(self.resource_request_heap)

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

    # =========================================================================
    # WORKFLOW FEASIBILITY CHECK
    # =========================================================================

    def isWorkflowImpossible(self, constraints) -> bool:
        """
        Check if a workflow is truly impossible (exceeds pool capacity)
        vs temporarily unavailable (resources in use)

        Returns:
            True if workflow exceeds license pool capacity (impossible)
            False if workflow can be scheduled when resources become available

        Note: We only check license capacity, not compute capacity, because
        Instance objects don't track total capacity (only free slots).
        This is conservative - we may retry impossible compute requests,
        but we won't incorrectly reject feasible workflows.
        """
        license_pool = constraints.get('license_pool', None)

        if not license_pool:
            # No license requirement - assume temporarily unavailable
            # (can't determine total compute capacity from Instance objects)
            return False

        # Check if license requirement exceeds pool capacity
        # Use checkResources to get a realistic instance allocation
        instances = self.resource_manager.getResources()
        count, instance_list = self.checkResources(
            instances, constraints['min_instances']
        )

        if count < constraints['min_instances']:
            # Not enough free compute capacity right now
            # But we can't determine if it's impossible or just in use
            # Conservative: assume temporarily unavailable
            return False

        # Check license capacity with the hypothetical allocation
        total_cores = sum(inst.cores * cnt for inst, cnt in instance_list)

        try:
            licenses_needed = self.license_manager.calculate_tokens(
                pool=license_pool,
                cores=total_cores,
                chains=constraints.get('chains', 1)
            )

            pool_status = self.license_manager.get_pool_status(license_pool)

            if licenses_needed > pool_status['total']:
                # Truly impossible - exceeds license pool capacity
                print(f"  ✗ IMPOSSIBLE: Workflow needs {licenses_needed} {license_pool} tokens, pool capacity is {pool_status['total']}")
                return True

            # License requirement is feasible - temporarily unavailable
            return False

        except Exception as e:
            # Error calculating licenses - assume temporarily unavailable
            print(f"  ⚠ Error checking license feasibility: {e}")
            return False

    # =========================================================================
    # INHERITED METHODS FROM PARENT (Scheduler_LA)
    # =========================================================================
    # The following methods are inherited from Scheduler_LA:
    # - checkNewResources() - sophisticated moldable resource allocation (FIXED in Scheduler_LA)
    # - checkCloseness() - runtime similarity check (15% tolerance)
    #
    # Both methods now have the "global view" of resource constraints that was
    # missing from the original LAMF implementation.

    # =========================================================================
    # LICENSE-AWARE RESOURCE MANAGEMENT
    # =========================================================================

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
        # First, get compute allocation (from checkNewResources)
        # GLOBAL VIEW: Pass available_runtime for deadline feasibility check
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
                    alloc_instances, available_tokens, license_pool, request
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
        license_pool: str,
        request
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
                        chains=request.get('chains', 1)
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

        Returns:
            actual_licenses_released: Actual number of license tokens released
        """
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []
        actual_licenses_released = 0

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

            # === PARTIAL LICENSE RELEASE ===
            # Free licenses corresponding to freed instances (but only partially)
            if license_pool and to_free_instances:
                total_cores_freed = sum(inst.cores * count for inst, count, _ in to_free_instances)

                try:
                    licenses_to_free = self.license_manager.calculate_tokens(
                        pool=license_pool,
                        cores=total_cores_freed,
                        chains=1
                    )

                    PARTIAL_RELEASE_FRACTION = 0.50  # Release 50%, keep 50% as buffer

                    wf_id = request['wf-id']
                    if wf_id in self.license_holds and self.license_holds[wf_id]:
                        # Get most recent hold (LIFO)
                        hold_id = self.license_holds[wf_id][-1]

                        # Get allocation info to determine hold size
                        if hold_id in self.license_manager.allocations:
                            alloc = self.license_manager.allocations[hold_id]
                            licenses_to_actually_release = int(licenses_to_free * PARTIAL_RELEASE_FRACTION)

                            if licenses_to_actually_release >= alloc.amount:
                                # Release entire hold (can't partially release more than exists)
                                self.license_holds[wf_id].pop()
                                self.license_manager.release(hold_id)
                                actual_licenses_released += alloc.amount
                                print(f"  ✓ Released full hold: {alloc.amount} licenses (hold: {hold_id})")
                            else:
                                # Partial release: release old hold, create new smaller hold
                                remaining_licenses = alloc.amount - licenses_to_actually_release

                                # Release old hold
                                self.license_holds[wf_id].pop()
                                self.license_manager.release(hold_id)
                                actual_licenses_released += licenses_to_actually_release

                                # Create new smaller hold for retained licenses
                                new_hold_id = self.license_manager.hold(
                                    pool=license_pool,
                                    amount=remaining_licenses,
                                    owner=wf_id,
                                    ttl=300
                                )
                                self.license_manager.commit(new_hold_id)
                                self.license_holds[wf_id].append(new_hold_id)

                                # Synchronize ResourceManager tracking
                                self.resource_manager.updateWorkflowLicenses(
                                    wf_id,
                                    self.license_holds[wf_id],
                                    mode='replace'
                                )

                                print(f"  ✓ Partial release: {licenses_to_actually_release}/{alloc.amount} licenses ({PARTIAL_RELEASE_FRACTION*100:.0f}%)")
                                print(f"    Retained {remaining_licenses} licenses as buffer (new hold: {new_hold_id})")
                        else:
                            # Fallback: just release the hold if not in allocations
                            self.license_holds[wf_id].pop()
                            self.license_manager.release(hold_id)
                            actual_licenses_released += int(licenses_to_free * PARTIAL_RELEASE_FRACTION)
                            print(f"  ✓ Released hold: {hold_id} (allocation not tracked)")

                except LicenseError as e:
                    print(f"  ⚠ License release error: {e}")

        # Send freed resources notification
        self.sendFreedResources(
            request['wf-id'], to_free_instances, instances,
            response_instances, sim, request.get('client-ip', None)
        )

        return actual_licenses_released

    # =========================================================================
    # DDM-EDF RESOURCE REQUEST PROCESSING (with urgency-based scaling)
    # =========================================================================

    def processFreeRequestWithLicenses(self, sim, wf_mb, request):
        """
        EDF-ordered LAMF: Iteration-based moldability with EDF queue ordering

        This is the core LAMF algorithm combined with EDF priority:
        1. EDF heap ordering for deadline-based priority (from DDM-EDF)
        2. Iteration-weighted budget/deadline constraints (from LAMF)
        3. Progress-based triggers for scale-up (from LAMF)
        4. License-aware scale-down guards (from LAMF)
        """
        # LA workflows always return 7-value tuples
        instances, budget, deadline, start_time, mesh, software_id, license_holds = \
            self.resource_manager.getWorkflow(request['wf-id'])

        # Get license pool for this workflow
        license_pool = self.license_manager.get_pool_for_software(software_id) if software_id else None

        # Iteration-weighted constraints (from LAMF)
        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) * OPTIM_FCFS_DFACTOR[ind]

        cur_instance: Instance = instances[-1][0]
        cur_count = instances[-1][1]

        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = sum(inst_tuple[1] for inst_tuple in instances)

        # === DIAGNOSTIC LOGGING ===
        print(f"\n🔍 [EDF-LAMF] processFreeRequestWithLicenses called:")
        print(f"  wf-id: {request['wf-id']}, iteration: {ind}")
        print(f"  current instances: {cur_count}, chains: {request['chains']}, tinyda-iterations: {request['tinyda-iterations']}")
        print(f"  deadline: {deadline:.1f}s, current time: {getTime(sim):.1f}s")
        print(f"  available_time (after OPTIM factor {OPTIM_FCFS_DFACTOR[ind]}): {available_time:.1f}s")

        # === PROGRESS-BASED TRIGGER (from LAMF) ===
        # Check if workflow is falling behind schedule - if so, skip scale-down and go to scale-up
        elapsed_time = getTime(sim) - start_time
        total_time = deadline - start_time
        time_progress = elapsed_time / total_time if total_time > 0 else 0.0

        used_budget = self.metrics.computeCurrentCost(request['wf-id'], getTime(sim))
        budget_progress = used_budget / budget if budget > 0 else 0.0

        skip_scale_down = False
        force_scale_up_attempt = False

        # TRIGGER 1: Early scale-up if falling behind
        if time_progress > budget_progress + 0.05:
            print(f"  ⚡ EARLY SCALE-UP TRIGGER: time {time_progress*100:.1f}% > budget {budget_progress*100:.1f}% + 5%")
            print(f"  → Skipping scale-down check, will attempt scale-up")
            skip_scale_down = True
            force_scale_up_attempt = True

        # TRIGGER 2: Late-iteration proactive scale-up
        if ind >= 3 and time_progress > 0.50 and not force_scale_up_attempt:
            print(f"  ⚡ LATE-ITERATION SCALE-UP: iteration {ind}, time {time_progress*100:.1f}% elapsed")
            print(f"  → Proactively attempting scale-up (don't wait for crisis)")
            skip_scale_down = True
            force_scale_up_attempt = True

        # === SCALE DOWN CHECK (with license-aware guards from LAMF) ===
        chains_per_node = 3
        request['count'] = None
        min_needed_count = request['chains']
        runtime_per_model = getRuntime(1, mesh, cur_instance.name)
        print(f"  runtime_per_model: {runtime_per_model:.1f}s")

        # Only attempt scale-down if not skipped by urgency trigger
        while chains_per_node > 0 and not skip_scale_down:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']
            print(f"  [chains_per_node={chains_per_node}] runtime={runtime:.1f}s vs available_time={available_time:.1f}s")

            if runtime < available_time:  # Can complete with fewer resources
                min_needed_count = request['chains'] // chains_per_node + bool(request['chains'] % chains_per_node)
                print(f"  ✓ Can complete with fewer resources: min_needed={min_needed_count} vs cur_count={cur_count}")

                if cur_count > min_needed_count:  # Only scale down if have more than needed
                    # === SMART SCALE-DOWN GUARDS (from LAMF) ===
                    should_scale_down = True
                    blocked_reason = None

                    # GUARD 1: License pool utilization
                    if license_pool:
                        pool_status = self.license_manager.get_pool_status(license_pool)
                        pool_utilization = pool_status['allocated'] / pool_status['total']

                        if pool_utilization > 0.70:  # Pool >70% utilized
                            print(f"  ⏸ Scale-down BLOCKED: {license_pool} pool at {pool_utilization*100:.1f}% utilization (threshold: 70%)")
                            should_scale_down = False
                            blocked_reason = 'license_pool_saturated'

                    # GUARD 2: Late iteration (don't scale down too late)
                    if should_scale_down and ind > 3:
                        print(f"  ⏸ Scale-down BLOCKED: too late in workflow (iteration {ind})")
                        should_scale_down = False
                        blocked_reason = 'late_iteration'

                    # GUARD 3: Check time progress (don't scale down late in workflow)
                    if should_scale_down:
                        elapsed_time = getTime(sim) - start_time
                        total_time = deadline - start_time
                        time_progress = elapsed_time / total_time if total_time > 0 else 1.0
                        time_progress = float(abs(time_progress)) if isinstance(time_progress, complex) else float(time_progress)

                        if time_progress > 0.70:
                            print(f"  ⏸ Scale-down BLOCKED: workflow {time_progress*100:.1f}% complete (threshold: 70%)")
                            should_scale_down = False
                            blocked_reason = 'time_progress'

                    # GUARD 4: Check budget progress (only scale down if ahead on budget)
                    if should_scale_down:
                        used_budget = self.metrics.computeCurrentCost(request['wf-id'], getTime(sim))
                        budget_progress = used_budget / budget if budget > 0 else 1.0

                        if budget_progress > 0.50 or time_progress > 0.50:
                            print(f"  ⏸ Scale-down BLOCKED: budget {budget_progress*100:.1f}% or time {time_progress*100:.1f}% > 50%")
                            should_scale_down = False
                            blocked_reason = 'budget_or_time_progress'

                    # Execute or block scale-down
                    if should_scale_down:
                        request['count'] = cur_count - min_needed_count
                        print(f"⬇ Scaling down: freeing {request['count']} instances (guards passed)")

                        # Track resource deallocation
                        current_time = getTime(sim)
                        self.metrics.updateResources(
                            request['wf-id'], cur_instance, request['count'], current_time, 'remove'
                        )

                        cores_freed = cur_instance.cores * request['count']
                        actual_licenses_released = self.freeResourcesWithLicenses(instances, request, sim, license_pool)

                        # Record scale-down success
                        self.metrics.recordScaleDownAttempt(
                            success=True,
                            instances_removed=request['count'],
                            cores_removed=cores_freed,
                            licenses_released=actual_licenses_released
                        )

                        return
                    else:
                        # Don't scale down - too risky
                        print(f"  → Keeping current allocation ({cur_count} instances)")
                        self.metrics.recordScaleDownAttempt(success=False, blocked_reason=blocked_reason)
                        break
                else:
                    print(f"  ✗ Cannot scale down: cur_count ({cur_count}) < min_needed ({min_needed_count})")
                    break
            else:
                chains_per_node -= 1

        if skip_scale_down:
            print(f"  → Scale-down skipped (workflow falling behind). Moving to scale-up check.\n")
        else:
            print(f"  → Scale-down check complete. Moving to scale-up check.\n")

        # === SCALE UP CHECK (with LAMF boost) ===
        used_budget = self.metrics.computeCurrentCost(request['wf-id'], getTime(sim))

        # SCALE-UP BOOST: When forcing scale-up (early trigger or late-iteration proactive),
        # allocate 20% more budget than normal OPTIM factor allows
        SCALE_UP_BOOST_FACTOR = 1.2

        if force_scale_up_attempt:
            available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind] * SCALE_UP_BOOST_FACTOR
            print(f"  💪 SCALE-UP BOOST: budget factor {OPTIM_FCFS_BFACTOR[ind]:.2f} → {OPTIM_FCFS_BFACTOR[ind] * SCALE_UP_BOOST_FACTOR:.2f}")
            print(f"  💰 Available budget: ${available_budget:.2f} (boosted from ${max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]:.2f})")
        else:
            available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            request['count'] = min_needed_count - cur_count

            # Handle case where scale-down was blocked (cur_count >= min_needed_count)
            if request['count'] <= 0:
                print(f"  → No resource adjustment needed (current: {cur_count}, minimum: {min_needed_count})")
                return

        # Check NEW resources with license constraints
        alloc_instances, license_holds_new = self.checkNewResourcesWithLicenses(
            free_resources, instances, available_budget, available_time,
            request, mesh, license_pool
        )

        if alloc_instances:
            print(f"⬆ Scaling up: allocating {sum(c for _, c in alloc_instances)} instances")

            # Allocate compute
            ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)

            # Track resource allocation
            current_time = getTime(sim)
            instances_added = sum(c for _, c, _ in alloc_resources)
            cores_added = sum(inst.cores * count for inst, count, _ in alloc_resources)

            # Calculate actual license tokens
            licenses_acquired = 0
            if license_holds_new and license_pool:
                total_cores = sum(inst.cores * count for inst, count, _ in alloc_resources)
                licenses_acquired = self.license_manager.calculate_tokens(
                    pool=license_pool,
                    cores=total_cores,
                    chains=request.get('chains', 1)
                )

            for instance_obj, count, _ in alloc_resources:
                self.metrics.updateResources(
                    request['wf-id'], instance_obj, count, current_time, 'add'
                )

            # Record scale-up success
            self.metrics.recordScaleUpAttempt(
                success=True,
                instances_added=instances_added,
                cores_added=cores_added,
                licenses_acquired=licenses_acquired,
                workflow_id=request['wf-id']
            )

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

                # Synchronize to ResourceManager
                self.resource_manager.updateWorkflowLicenses(
                    request['wf-id'],
                    self.license_holds[request['wf-id']],
                    mode='replace'
                )

        else:
            print(f"⏸ No scaling: insufficient resources or licenses")
            # Record scale-up failure
            free_compute = any(r.getFreeSlots() > 0 for r in free_resources)
            if not free_compute:
                self.metrics.recordScaleUpAttempt(success=False, reason='insufficient_compute', workflow_id=request['wf-id'])
            elif available_budget <= 0:
                self.metrics.recordScaleUpAttempt(success=False, reason='budget_exhausted', workflow_id=request['wf-id'])
            elif available_time <= 0:
                self.metrics.recordScaleUpAttempt(success=False, reason='time_exhausted', workflow_id=request['wf-id'])
            else:
                self.metrics.recordScaleUpAttempt(success=False, reason='insufficient_licenses', workflow_id=request['wf-id'])
