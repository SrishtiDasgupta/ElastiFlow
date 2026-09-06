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
import os
import threading
import time
from typing import List, Dict

from elastiflow.config.constants_LA import (
    COLD_START_TIME,
    DEADLINE_BUFFER,
    MIN_INSTANCE_COST,
    OPTIM_FCFS_BFACTOR,
    OPTIM_FCFS_DFACTOR,
    RESOURCE_REQUEST_TIMEOUT,
    SPEEDUP_THRESHOLD,
)

from elastiflow.scripts.speedup import getRuntime
from elastiflow.resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from elastiflow.resource_manager.resource_manager_LA import ResourceManager_LA
from elastiflow.utils.resource_LA import getConstraintsFromWorkflow, getEstimate
from elastiflow.scheduler.scheduler import EDFOrderingMixin
from elastiflow.scheduler.scheduler_LA import Scheduler_LA_Elastic


class EDF_Optimized_LA(EDFOrderingMixin, Scheduler_LA_Elastic):
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

    metrics_prefix = 'EDF_'

    def printBanner(self, backend):
        print(f'Starting EDF-ordered LAMF scheduler...')
        print(f'  - EDF heap ordering: Deadline-based priority queue')
        print(f'  - LAMF moldability: Iteration-based progress triggers')
        print(f'  - Iteration weighting: BFACTOR/DFACTOR {list(OPTIM_FCFS_DFACTOR.values())}')
        print(f'  - Progress triggers: time_progress vs budget_progress')
        print(f'  - License-aware guards: Pool saturation, late iteration, time/budget progress')

    def beforeLoop(self, backend):
        super().beforeLoop(backend)         # rejected_workflows: track impossible workflows
        self.loop_counter = 0               # Track loop iterations for debugging
        self.idle_loop_count = 0            # Track consecutive idle loops

    def beginCycle(self, backend) -> bool:
        self.loop_counter += 1
        super().beginCycle(backend)         # advance the license ledger clock
        loop_counter, idle_loop_count = self.loop_counter, self.idle_loop_count

        # Progress indicator every 1000 iterations (reduced frequency)
        if loop_counter % 1000 == 0:
            current_time = backend.now()
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
            self.idle_loop_count += 1
            if self.idle_loop_count > 10:
                print(f"\n{'='*70}")
                print(f"✓ All workflows completed. Terminating scheduler.")
                print(f"  Total loops: {loop_counter}")
                print(f"  Final simulated time: {backend.now():.1f}s")
                print(f"{'='*70}\n")
                self.computeFinalMetrics(backend)
                return True
            elif self.idle_loop_count == 1:
                print(f"\n[INFO] No active workflows detected. Waiting for termination (idle_count={self.idle_loop_count}/10)...")
        else:
            if self.idle_loop_count > 0:
                print(f"[DEBUG] Activity detected, resetting idle_count from {self.idle_loop_count} (active={active_wfs}, wf_heap={len(self.workflow_heap)}, req_heap={len(self.resource_request_heap)})")
            self.idle_loop_count = 0  # Reset if there's activity
        return False

    def serviceResourceRequests(self, backend) -> bool:
        # === PHASE 1: Process Resource Requests (EDF ordering) ===
        resource_requests = backend.resource_requests.pop_many(None)
        if not resource_requests:
            return False
        # Sort resource requests by deadline (EDF)
        self.processResourceRequestsByDeadline(resource_requests)
        resource_request = self.peekWorkflow(self.resource_request_heap)
        if not resource_request:
            return False
        # Process with deadline-urgency awareness
        start = time.time()

        if backend.now() - resource_request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            self.popWorkflow(self.resource_request_heap)
            backend.resource_requests.pop()
            return True

        self.processFreeRequestWithLicenses(backend, resource_request)
        print(f"  Resource request overhead: {time.time() - start:.3f}s")

        self.popWorkflow(self.resource_request_heap)
        backend.resource_requests.pop()
        return True

    def nextWorkflow(self, backend):
        # === PHASE 2: Schedule New Workflows (EDF ordering) ===
        workflows = backend.workflows.pop_many(None)

        # Debug heap state periodically
        if self.loop_counter % 10000 == 0 and len(self.workflow_heap) > 0:
            print(f"[DEBUG] Workflow heap has {len(self.workflow_heap)} workflows, checking top workflow...")
            resources_available = self.resource_manager.getResourcesAvailable()
            print(f"[DEBUG] Resources available: {resources_available}")

        if workflows:
            if self.loop_counter <= 100 or self.loop_counter % 10000 == 0:  # Debug early and periodically
                print(f"[DEBUG] Received {len(workflows)} workflows at loop {self.loop_counter}")
            # Sort workflows by deadline (EDF)
            self.processWorkflowsByDeadline(workflows)

        # Process heap even if no new workflows arrived
        return self.peekWorkflow(self.workflow_heap)

    def dropWorkflow(self, backend):
        self.popWorkflow(self.workflow_heap)
        backend.workflows.pop()

    def admissionDebug(self, wf_plan, available, backend):
        # Debug why scheduling is blocked
        if self.loop_counter % 10000 == 0:
            print(f"[DEBUG] Phase 3: resources_available={available}, heap_size={len(self.workflow_heap)}")

    def printAllocation(self, wf_plan, ips, backend, constraints=None):
        print(f"✓ {wf_plan['id']} allocated: {ips}")

    def afterAdmission(self, wf_plan, alloc_resources, license_holds):
        # Track initial allocation in scheduler
        if license_holds:
            self.license_holds[wf_plan['id']] = license_holds

    def whenRefused(self, wf_plan, constraints, ips, alloc_resources, license_holds, backend):
        if ips is None and alloc_resources is None and license_holds is None:
            # Check if workflow is truly impossible or just temporarily unavailable
            constraints_check = getConstraintsFromWorkflow(wf_plan)
            is_impossible = self.isWorkflowImpossible(constraints_check)

            if is_impossible:
                # Truly impossible (exceeds pool capacity) - permanently reject
                self.rejected_workflows.add(wf_plan['id'])
                self.dropWorkflow(backend)
                print(f"⊘ Rejecting impossible workflow {wf_plan['id']}")
            else:
                self.waitForResources(wf_plan, backend)
        else:
            self.waitNoResources(wf_plan, backend)

    def waitForResources(self, wf_plan, backend):
        # Temporarily unavailable - wait for resources
        self.resource_manager.setResourcesAvailable(False)
        if self.loop_counter % 10000 == 0:
            print(f'⏳ {wf_plan["id"]} waiting for resources/licenses (heap_size={len(self.workflow_heap)})...')

    def waitNoResources(self, wf_plan, backend):
        # Wait for resources
        self.resource_manager.setResourcesAvailable(False)
        if self.loop_counter % 10000 == 0:
            print(f'[DEBUG] No resources to allocate, waiting... (heap_size={len(self.workflow_heap)})')

    def whenUnavailable(self, wf_plan, backend):
        # Resources not available - this is likely the blocking condition
        if self.loop_counter % 10000 == 0:
            print(f'[DEBUG] Resources marked as unavailable, skipping allocation (heap_size={len(self.workflow_heap)})')

    # Log labels of the negotiation trace; HSM overrides them (B7.3).
    negotiation_label = 'EDF-LAMF'
    phase_note = ''

    def _feasibilityChains(self, request) -> int:
        # EDF-LAMF sizes licence feasibility with the workflow's chain count (FCFS-LAMF uses one).
        return request.get('chains', 1)

    def _holdAllocation(self, request, instances, deadline, start_time, mesh, license_pool, software_id, ind, backend) -> bool:
        """HSM's phase gate: True while a workflow holds its allocation (no
        scale-down). EDF-LAMF never holds; EDF_HSM_LA overrides this (B7.3)."""
        return False

    # =========================================================================
    # EDF HEAP MANAGEMENT (from HEFTResourceManager)
    # =========================================================================

    def processResourceRequestsByDeadline(self, requests: List[any]):
        """Sort resource requests by deadline (EDF ordering)"""
        from elastiflow.utils.request import ExecutorRequest

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

    # =========================================================================
    # DDM-EDF RESOURCE REQUEST PROCESSING (with urgency-based scaling)
    # =========================================================================

    def processFreeRequestWithLicenses(self, backend, request):
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

        # HSM's phase gate (False here): decided before the negotiation, see _holdAllocation.
        hold_allocation = self._holdAllocation(request, instances, deadline, start_time, mesh, license_pool,
                                               software_id, ind, backend)

        available_time = max(0, deadline - DEADLINE_BUFFER - backend.now()) * OPTIM_FCFS_DFACTOR[ind]

        cur_instance: Instance = instances[-1][0]
        cur_count = instances[-1][1]

        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = sum(inst_tuple[1] for inst_tuple in instances)

        # === DIAGNOSTIC LOGGING ===
        print(f"\n🔍 [{self.negotiation_label}] processFreeRequestWithLicenses called:")
        print(f"  wf-id: {request['wf-id']}, iteration: {ind}{self.phase_note}")
        print(f"  current instances: {cur_count}, chains: {request['chains']}, tinyda-iterations: {request['tinyda-iterations']}")
        print(f"  deadline: {deadline:.1f}s, current time: {backend.now():.1f}s")
        print(f"  available_time (after OPTIM factor {OPTIM_FCFS_DFACTOR[ind]}): {available_time:.1f}s")

        # === PROGRESS-BASED TRIGGER (from LAMF) ===
        # Check if workflow is falling behind schedule - if so, skip scale-down and go to scale-up
        elapsed_time = backend.now() - start_time
        total_time = deadline - start_time
        time_progress = elapsed_time / total_time if total_time > 0 else 0.0

        used_budget = self.metrics.computeCurrentCost(request['wf-id'], backend.now())
        budget_progress = used_budget / budget if budget > 0 else 0.0

        skip_scale_down = False
        force_scale_up_attempt = False

        # === DEADLINE-FIRST TRIGGERS (EDF-LAMF v5) ===
        # Calculate deadline urgency for direct deadline monitoring
        time_remaining = deadline - backend.now()
        deadline_urgency = time_remaining / total_time if total_time > 0 else 0.0

        # Track urgency mode for boost factor
        urgency_mode = 'NORMAL'

        # TRIGGER 1: Deadline critical (less than 30% time remaining)
        if deadline_urgency < 0.30:
            print(f"  🚨 DEADLINE CRITICAL: only {time_remaining:.1f}s ({deadline_urgency*100:.1f}%) remaining")
            print(f"  → Forcing aggressive scale-up (CRITICAL mode)")
            skip_scale_down = True
            force_scale_up_attempt = True
            urgency_mode = 'CRITICAL'

        # TRIGGER 2: Deadline warning (less than 50% time remaining) + falling behind
        elif deadline_urgency < 0.50 and time_progress > budget_progress + 0.03:
            print(f"  ⚠️ DEADLINE WARNING: {deadline_urgency*100:.1f}% time left, time {time_progress*100:.1f}% > budget {budget_progress*100:.1f}%")
            print(f"  → Forcing scale-up (WARNING mode)")
            skip_scale_down = True
            force_scale_up_attempt = True
            urgency_mode = 'WARNING'

        # TRIGGER 3: Early falling-behind detection (reduced from 5% to 3%)
        elif time_progress > budget_progress + 0.03:
            print(f"  ⚡ EARLY SCALE-UP: time {time_progress*100:.1f}% > budget {budget_progress*100:.1f}% + 3%")
            print(f"  → Skipping scale-down check, will attempt scale-up")
            skip_scale_down = True
            force_scale_up_attempt = True

        # TRIGGER 4: Mid-iteration proactive scale-up (earlier: iteration 2, 40% time)
        elif ind >= 2 and time_progress > 0.40 and not force_scale_up_attempt:
            print(f"  ⚡ MID-ITERATION SCALE-UP: iteration {ind}, time {time_progress*100:.1f}% elapsed")
            print(f"  → Proactively attempting scale-up")
            skip_scale_down = True
            force_scale_up_attempt = True

        # === SCALE DOWN CHECK (with license-aware guards from LAMF) ===
        chains_per_node = 3
        request['count'] = None
        min_needed_count = request['chains']
        runtime_per_model = getRuntime(1, mesh, cur_instance.name)
        print(f"  runtime_per_model: {runtime_per_model:.1f}s")

        # Only attempt scale-down if not skipped by urgency trigger AND the
        # workflow has left the HSM static phase (static phase forbids scale-down).
        if hold_allocation:
            skip_scale_down = True
            print(f"  📌 [HSM STATIC] scale-down suppressed (holding allocation); "
                  f"scale-up remains enabled")
        while chains_per_node > 0 and not skip_scale_down:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']
            print(f"  [chains_per_node={chains_per_node}] runtime={runtime:.1f}s vs available_time={available_time:.1f}s")

            if runtime < available_time:  # Can complete with fewer resources
                min_needed_count = request['chains'] // chains_per_node + bool(request['chains'] % chains_per_node)
                print(f"  ✓ Can complete with fewer resources: min_needed={min_needed_count} vs cur_count={cur_count}")

                if cur_count > min_needed_count:  # Only scale down if have more than needed
                    # === DEADLINE-PROTECTIVE SCALE-DOWN GUARDS (EDF-LAMF v5) ===
                    should_scale_down = True
                    blocked_reason = None

                    # GUARD 0+1 (unified, contention-conditional, license-aware — Henkel &
                    # Treiber 2015). Compute pool pressure once.
                    #  - Under SATURATION (util >= LA_GUARD_SAT): block ONLY cost-adverse
                    #    shrinks (those that raise this workflow's projected license cost under
                    #    its solver's token law). Cost-neutral shrinks (e.g. LS-Dyna, linear)
                    #    are allowed so they release tokens to waiting peers.
                    #  - Under SLACK (util < SAT): allow all shrinks — the freed resources are a
                    #    genuine saving (this is where unconditional blocking wasted hardware).
                    # No solver is hardcoded; the cost sign is computed per decision. SAT is the
                    # single saturation knob (was GUARD 1's 70%), configurable for sensitivity.
                    if license_pool:
                        LA_GUARD_SAT = float(os.environ.get('LA_GUARD_SAT', '0.70'))
                        pool_status = self.license_manager.get_pool_status(license_pool)
                        pool_utilization = (pool_status['allocated'] / pool_status['total']
                                            if pool_status['total'] else 0.0)
                        if pool_utilization >= LA_GUARD_SAT and software_id:
                            cpi = cur_instance.cores
                            iters = request['tinyda-iterations']
                            cpn_keep   = -(-request['chains'] // cur_count)
                            cpn_shrink = -(-request['chains'] // min_needed_count)
                            lic_keep   = self.metrics.calculate_license_cost(
                                software_id, cur_count * cpi,        cpn_keep   * runtime_per_model * iters)
                            lic_shrink = self.metrics.calculate_license_cost(
                                software_id, min_needed_count * cpi, cpn_shrink * runtime_per_model * iters)
                            if lic_shrink > lic_keep * 1.001:  # cost-adverse shrink under saturation
                                print(f"  ⏸ Scale-down BLOCKED: cost-adverse under saturation "
                                      f"(pool {pool_utilization*100:.0f}%>={LA_GUARD_SAT*100:.0f}%, "
                                      f"shrink €{lic_shrink:.1f}>hold €{lic_keep:.1f}, sw={software_id})")
                                should_scale_down = False
                                blocked_reason = 'license_cost_adverse_saturated'

                    # GUARD 2: Earlier iteration cutoff (iteration 2 vs 3)
                    if should_scale_down and ind > 2:
                        print(f"  ⏸ Scale-down BLOCKED: too late in workflow (iteration {ind} > 2)")
                        should_scale_down = False
                        blocked_reason = 'late_iteration'

                    # GUARD 3: Deadline proximity (NEW - don't scale down if <50% time remaining)
                    if should_scale_down:
                        time_remaining_now = deadline - backend.now()
                        total_time_now = deadline - start_time
                        deadline_urgency_check = time_remaining_now / total_time_now if total_time_now > 0 else 0.0

                        if deadline_urgency_check < 0.50:  # Less than 50% time remaining
                            print(f"  ⏸ Scale-down BLOCKED: deadline approaching ({deadline_urgency_check*100:.1f}% time remaining < 50%)")
                            should_scale_down = False
                            blocked_reason = 'deadline_proximity'

                    # GUARD 4: Time progress check (unchanged)
                    if should_scale_down:
                        elapsed_time = backend.now() - start_time
                        total_time = deadline - start_time
                        time_progress = elapsed_time / total_time if total_time > 0 else 1.0
                        time_progress = float(abs(time_progress)) if isinstance(time_progress, complex) else float(time_progress)

                        if time_progress > 0.70:
                            print(f"  ⏸ Scale-down BLOCKED: workflow {time_progress*100:.1f}% complete (threshold: 70%)")
                            should_scale_down = False
                            blocked_reason = 'time_progress'

                    # GUARD 5: Budget/time progress (more conservative: 40% vs 50%)
                    if should_scale_down:
                        used_budget = self.metrics.computeCurrentCost(request['wf-id'], backend.now())
                        budget_progress = used_budget / budget if budget > 0 else 1.0

                        if budget_progress > 0.40 or time_progress > 0.40:
                            print(f"  ⏸ Scale-down BLOCKED: budget {budget_progress*100:.1f}% or time {time_progress*100:.1f}% > 40%")
                            should_scale_down = False
                            blocked_reason = 'budget_or_time_progress'

                    # GUARD 6: Minimum instance protection (NEW)
                    if should_scale_down and cur_count <= 2:
                        print(f"  ⏸ Scale-down BLOCKED: at minimum instance count ({cur_count} ≤ 2)")
                        should_scale_down = False
                        blocked_reason = 'min_instance_limit'

                    # Execute or block scale-down
                    if should_scale_down:
                        request['count'] = cur_count - min_needed_count
                        print(f"⬇ Scaling down: freeing {request['count']} instances (guards passed)")

                        # Track resource deallocation
                        current_time = backend.now()
                        self.metrics.updateResources(
                            request['wf-id'], cur_instance, request['count'], current_time, 'remove'
                        )

                        cores_freed = cur_instance.cores * request['count']
                        actual_licenses_released = self.freeResourcesWithLicenses(instances, request, backend, license_pool)

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

        # === SCALE UP CHECK (with DEADLINE-AWARE GRADUATED BOOST) ===
        used_budget = self.metrics.computeCurrentCost(request['wf-id'], backend.now())

        # DEADLINE-AWARE GRADUATED BOOST (EDF-LAMF v5):
        # More aggressive intervention for deadline-critical workflows
        if urgency_mode == 'CRITICAL':
            SCALE_UP_BOOST_FACTOR = 2.0  # 100% boost for critical workflows
            print(f"  💪 CRITICAL BOOST: 2.0× budget allocation (deadline emergency)")
        elif urgency_mode == 'WARNING':
            SCALE_UP_BOOST_FACTOR = 1.5  # 50% boost for warning workflows
            print(f"  💪 WARNING BOOST: 1.5× budget allocation (deadline approaching)")
        elif force_scale_up_attempt:
            SCALE_UP_BOOST_FACTOR = 1.2  # 20% boost for regular scale-ups
            print(f"  💪 SCALE-UP BOOST: 1.2× budget allocation")
        else:
            SCALE_UP_BOOST_FACTOR = 1.0  # Normal allocation

        if force_scale_up_attempt:
            available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind] * SCALE_UP_BOOST_FACTOR
            print(f"  💰 Available budget: €{available_budget:.2f} (base: €{max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]:.2f} × {SCALE_UP_BOOST_FACTOR})")
        else:
            available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            # === CRITICAL FIX: Force additional resources when triggers fire ===
            # If workflow is falling behind/critical, requesting "minimum needed" is not enough!
            # We need ADDITIONAL resources to help it catch up on deadline

            if force_scale_up_attempt:
                # Request additional instances beyond current allocation
                # Scale by urgency: CRITICAL (50%), WARNING (40%), Regular (30%)
                if urgency_mode == 'CRITICAL':
                    scale_up_percentage = 0.50  # 50% more resources
                    print(f"  🚨 CRITICAL SCALE-UP: requesting 50% more resources")
                elif urgency_mode == 'WARNING':
                    scale_up_percentage = 0.40  # 40% more resources
                    print(f"  ⚠️ WARNING SCALE-UP: requesting 40% more resources")
                else:
                    scale_up_percentage = 0.30  # 30% more resources
                    print(f"  ⚡ FORCED SCALE-UP: requesting 30% more resources")

                additional_instances = max(1, int(cur_count * scale_up_percentage))
                request['count'] = additional_instances
                print(f"  💪 Requesting +{additional_instances} instances (current: {cur_count} → target: {cur_count + additional_instances})")
            else:
                # Normal case: only request if we need more to reach minimum
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
            current_time = backend.now()
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
                request['wf-id'], ips, alloc_resources, backend,
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
            # Attribute to licences ONLY when the Stage 1 licence gate actually
            # denied. This was previously the residual else-branch, so it absorbed
            # every rejection the other three tests did not explain without ever
            # consulting the pool, and reported phantom licence failures.
            free_compute = any(r.getFreeSlots() > 0 for r in free_resources)
            if getattr(self, '_last_licence_outcome', None) == 'deny_licence':
                _reason = 'insufficient_licenses'
            elif not free_compute:
                _reason = 'insufficient_compute'
            elif available_budget <= 0:
                _reason = 'budget_exhausted'
            elif available_time <= 0:
                _reason = 'time_exhausted'
            else:
                _reason = 'unattributed'
            self.metrics.recordScaleUpAttempt(success=False, reason=_reason, workflow_id=request['wf-id'])
