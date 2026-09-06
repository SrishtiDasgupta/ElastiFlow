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
import os
import threading
import time
from typing import List, Tuple, Optional

from elastiflow.config.constants import (
    COLD_START_TIME, DEADLINE_BUFFER, MIN_INSTANCE_COST,
    OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR, RESOURCE_REQUEST_TIMEOUT,
    SPEEDUP_THRESHOLD, WORKFLOW_POLLING
)
from elastiflow.scripts.speedup import getRuntime
from elastiflow.resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from elastiflow.resource_manager.resource_manager_LA import ResourceManager_LA
from elastiflow.resource_manager.license.manager import LicenseManager
from elastiflow.resource_manager.license.exceptions import InsufficientTokens, LicenseError
from elastiflow.utils.resource_LA import getConstraintsFromWorkflow
from elastiflow.utils.resource import getEstimate
from elastiflow.scheduler.scheduler_LA import Scheduler_LA
from elastiflow.execution.backend import backend_for


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

        # Use resource_manager's license_manager (shared instance)
        self.license_manager = self.resource_manager.license_manager

        # LAMF is moldable (dynamic resource scaling)
        self.is_moldable = True

    def run(self, sim=None, wf_mb=None, resource_request_mb=None):

        backend = backend_for(sim)
        print(f'Starting LAMF (License-Aware Moldable FCFS) Scheduler...')

        # Track workflows that are impossible to allocate (prevent infinite retries)
        rejected_workflows = set()

        # Start resource utilization collection (including license pools)
        backend.spawn(self.metrics.collectResourceUtilization, sim, self.resource_manager, self.license_manager)

        while True:

            # Advance the license ledger clock to current sim time so every token
            # hold/release this cycle is billed at the correct sim timestamp.
            if sim is not None:
                self.license_manager.set_sim_time(backend.now())

            # Check queue for resource requests (moldable requests from executors)
            resource_request = backend.resource_requests.peek()

            if resource_request:
                start = time.time()
                resource_request = eval(resource_request)

                # Timeout check
                if backend.now() - resource_request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
                    backend.resource_requests.pop()
                    continue

                # Process moldable request (with license awareness)
                self.processFreeRequestWithLicenses(resource_request, sim)

                print(f"⏱ Resource request overhead: {time.time() - start:.3f}s")
                backend.resource_requests.pop()
                continue

            # Check the queue for new jobs
            workflow_plan = backend.workflows.peek()

            if workflow_plan:
                wf_plan = eval(workflow_plan)

                # End simulation
                if wf_plan['id'] == 'END':
                    backend.workflows.pop()
                    from elastiflow.config.constants_LA import TOTAL_WORKFLOWS
                    # Honest billing verification: ledger (Token-Hours) per pool.
                    _fs = backend.now() if sim is not None else self.license_manager.sim_now
                    _bp = self.license_manager.license_cost_by_pool(_fs)
                    print(f"[HONEST-BILL] ledger license cost by pool @sim={_fs:.0f}: "
                          f"LSDYNA €{_bp.get('LSDYNA',0):.0f}  ABAQUS €{_bp.get('ABAQUS',0):.0f}  "
                          f"ANSYS €{_bp.get('ANSYS',0):.0f}  TOTAL €{sum(_bp.values()):.0f}")
                    self.metrics.computeMetrics(
                        file_prefix=f'LAMF_{TOTAL_WORKFLOWS}_',
                        license_cost_by_owner=self.license_manager.license_cost_by_owner(_fs))
                    break

                # Skip workflows that have been rejected as impossible
                if wf_plan['id'] in rejected_workflows:
                    backend.workflows.pop()
                    print(f"⊘ Skipping rejected workflow {wf_plan['id']}")
                    continue

                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)

                    # Allocate BOTH compute and licenses
                    ips, alloc_resources, license_holds = self.allocateResourcesWithLicenses(constraints)

                    if ips:
                        print(f"✓ {wf_plan['id']} allocated:", ips)
                        if license_holds:
                            print(f"  with {len(license_holds)} license hold(s)")

                        backend.workflows.pop()

                        # Start billing
                        start_time = backend.now()

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

                        # FIX #1: Track initial allocation in scheduler's license_holds
                        # This ensures both tracking systems (Scheduler and ResourceManager) are synchronized from the start
                        if license_holds:
                            self.license_holds[wf_plan['id']] = license_holds

                        # Track initial allocation in moldability metrics (BUG FIX #1)
                        instances_added = sum(count for _, count, _ in alloc_resources)
                        cores_added = sum(inst.cores * count for inst, count, _ in alloc_resources)

                        # Calculate actual license tokens from holds
                        licenses_acquired = 0
                        if license_holds:
                            for hold_id in license_holds:
                                if hold_id in self.license_manager.allocations:
                                    licenses_acquired += self.license_manager.allocations[hold_id].amount

                        # Record as initial "scale-up" (iteration 0 allocation)
                        self.metrics.recordScaleUpAttempt(
                            success=True,
                            instances_added=instances_added,
                            cores_added=cores_added,
                            licenses_acquired=licenses_acquired,
                            workflow_id=wf_plan['id']
                        )
                        print(f"  📊 Tracked initial allocation: {instances_added} instances, {cores_added} cores, {licenses_acquired} licenses")

                    else:
                        # Allocation failed - could be compute OR licenses
                        # Check if this is an impossible allocation (exceeds pool capacity)

                        # Calculate required licenses to check if impossible
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

                        # Check if ANY compute resources are available
                        available_resources = self.resource_manager.getResources()
                        has_available_compute = any(r.getFreeSlots() > 0 for r in available_resources)

                        if not has_available_compute:
                            # Compute resources exhausted - block queue
                            self.resource_manager.setResourcesAvailable(False)
                            print(f'⏳ {wf_plan["id"]} waiting for compute resources...')
                        else:
                            # Temporary license shortage - don't block, just skip this workflow
                            # It will retry on next polling cycle
                            print(f'⏳ {wf_plan["id"]} insufficient licenses ({constraints.get("license_pool", "unknown")}), will retry...')
                            # Don't remove from queue - will retry later

            backend.sleep(WORKFLOW_POLLING)

    def processFreeRequestWithLicenses(self, request, sim):
        """
        Process moldable resource request WITH license awareness

        This is the core LAMF algorithm:
        1. Calculate iteration-weighted budget/deadline constraints
        2. Check if can scale down (free compute + licenses)
        3. If not, check if should scale up (allocate compute + licenses)
        4. Account for license availability in all decisions
        """
        backend = backend_for(sim)
        # LA workflows always return 7-value tuples
        instances, budget, deadline, start_time, mesh, software_id, license_holds = \
            self.resource_manager.getWorkflow(request['wf-id'])

        # Get license pool for this workflow
        license_pool = self.license_manager.get_pool_for_software(software_id) if software_id else None

        # Iteration-weighted constraints (from Vortex)
        ind = request['iteration']
        available_time = max(0, deadline - DEADLINE_BUFFER - backend.now()) * OPTIM_FCFS_DFACTOR[ind]

        cur_instance: Instance = instances[-1][0]
        cur_count = instances[-1][1]

        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = sum(inst_tuple[1] for inst_tuple in instances)

        # === DIAGNOSTIC LOGGING ===
        print(f"\n🔍 [DIAGNOSTIC] processFreeRequestWithLicenses called:")
        print(f"  wf-id: {request['wf-id']}, iteration: {ind}")
        print(f"  current instances: {cur_count}, chains: {request['chains']}, tinyda-iterations: {request['tinyda-iterations']}")
        print(f"  deadline: {deadline:.1f}s, current time: {backend.now():.1f}s")
        print(f"  available_time (after OPTIM factor {OPTIM_FCFS_DFACTOR[ind]}): {available_time:.1f}s")

        # === EARLY SCALE-UP TRIGGER (Priority 3 Fix) ===
        # Check if workflow is falling behind schedule - if so, skip scale-down and go to scale-up
        elapsed_time = backend.now() - start_time
        total_time = deadline - start_time
        time_progress = elapsed_time / total_time if total_time > 0 else 0.0

        used_budget = self.metrics.computeCurrentCost(request['wf-id'], backend.now())
        budget_progress = used_budget / budget if budget > 0 else 0.0

        # UPDATED: More sensitive trigger (5% instead of 10%) to intervene earlier
        # If time progress exceeds budget progress by 5%, workflow is falling behind
        # Skip scale-down entirely and go straight to scale-up attempt
        skip_scale_down = False
        force_scale_up_attempt = False

        if time_progress > budget_progress + 0.05:
            print(f"  ⚡ EARLY SCALE-UP TRIGGER: time {time_progress*100:.1f}% > budget {budget_progress*100:.1f}% + 5%")
            print(f"  → Skipping scale-down check, will attempt scale-up")
            skip_scale_down = True
            force_scale_up_attempt = True

        # === LATE-ITERATION PROACTIVE SCALE-UP ===
        # At late iterations (>= 3), proactively attempt scale-up if >50% time elapsed
        # Don't wait for deficit - preemptively add resources
        if ind >= 3 and time_progress > 0.50 and not force_scale_up_attempt:
            print(f"  ⚡ LATE-ITERATION SCALE-UP: iteration {ind}, time {time_progress*100:.1f}% elapsed")
            print(f"  → Proactively attempting scale-up (don't wait for crisis)")
            skip_scale_down = True
            force_scale_up_attempt = True

        # === SCALE DOWN CHECK ===
        # Can we free resources without missing deadline?
        chains_per_node = 3
        request['count'] = None
        min_needed_count = request['chains']
        runtime_per_model = getRuntime(1, mesh, cur_instance.name)
        print(f"  runtime_per_model: {runtime_per_model:.1f}s")

        # Only attempt scale-down if not skipped by early trigger
        while chains_per_node > 0 and not skip_scale_down:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']
            print(f"  [chains_per_node={chains_per_node}] runtime={runtime:.1f}s vs available_time={available_time:.1f}s")

            if runtime < available_time:  # Can complete with fewer resources
                min_needed_count = request['chains'] // chains_per_node + bool(request['chains'] % chains_per_node)
                print(f"  ✓ Can complete with fewer resources: min_needed={min_needed_count} vs cur_count={cur_count}")

                if cur_count > min_needed_count:  # Only scale down if have more than needed
                    # === SMART SCALE-DOWN GUARDS (Solution 2) ===
                    # Check if safe to scale down given license constraints
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

                    # GUARD 2: Check iteration number (don't scale down too late)
                    if should_scale_down and ind > 3:  # After iteration 3
                        print(f"  ⏸ Scale-down BLOCKED: too late in workflow (iteration {ind})")
                        should_scale_down = False
                        blocked_reason = 'late_iteration'

                    # GUARD 3: Check time progress (don't scale down late in workflow)
                    if should_scale_down:
                        elapsed_time = backend.now() - start_time
                        total_time = deadline - start_time
                        time_progress = elapsed_time / total_time if total_time > 0 else 1.0
                        # Ensure time_progress is a real number
                        time_progress = float(abs(time_progress)) if isinstance(time_progress, complex) else float(time_progress)

                        if time_progress > 0.70:  # More than 70% time elapsed
                            print(f"  ⏸ Scale-down BLOCKED: workflow {time_progress*100:.1f}% complete (threshold: 70%)")
                            should_scale_down = False
                            blocked_reason = 'time_progress'

                    # GUARD 4: Check budget progress (only scale down if ahead on budget)
                    if should_scale_down:
                        used_budget = self.metrics.computeCurrentCost(request['wf-id'], backend.now())
                        budget_progress = used_budget / budget if budget > 0 else 1.0

                        # Only scale down if both time AND budget are under 50% used
                        # This prevents releasing resources we'll need to re-acquire
                        if budget_progress > 0.50 or time_progress > 0.50:
                            print(f"  ⏸ Scale-down BLOCKED: budget {budget_progress*100:.1f}% or time {time_progress*100:.1f}% > 50%")
                            should_scale_down = False
                            blocked_reason = 'budget_or_time_progress'

                    # === OLD CODE (unconditional scale-down): ===
                    # request['count'] = cur_count - min_needed_count
                    # print(f"⬇ Scaling down: freeing {request['count']} instances")
                    # self.freeResourcesWithLicenses(instances, request, sim, license_pool)
                    # return

                    # === NEW CODE (conditional scale-down with guards): ===
                    if should_scale_down:
                        request['count'] = cur_count - min_needed_count
                        print(f"⬇ Scaling down: freeing {request['count']} instances (guards passed)")

                        # Track resource deallocation in metrics (before freeing)
                        current_time = backend.now()
                        self.metrics.updateResources(
                            request['wf-id'], cur_instance, request['count'], current_time, 'remove'
                        )

                        # Calculate cores being freed
                        cores_freed = cur_instance.cores * request['count']

                        # Free compute AND licenses (get ACTUAL licenses released)
                        actual_licenses_released = self.freeResourcesWithLicenses(instances, request, sim, license_pool)

                        # Record scale-down success with ACTUAL values (BUG FIX #3)
                        self.metrics.recordScaleDownAttempt(
                            success=True,
                            instances_removed=request['count'],
                            cores_removed=cores_freed,
                            licenses_released=actual_licenses_released  # Use actual, not calculated
                        )

                        return
                    else:
                        # Don't scale down - too risky
                        print(f"  → Keeping current allocation ({cur_count} instances)")

                        # Record blocked scale-down
                        self.metrics.recordScaleDownAttempt(
                            success=False,
                            blocked_reason=blocked_reason
                        )
                        break
                else:
                    # Need to scale up
                    print(f"  ✗ Cannot scale down: cur_count ({cur_count}) < min_needed ({min_needed_count})")
                    break
            else:
                chains_per_node -= 1

        if skip_scale_down:
            print(f"  → Scale-down skipped (workflow falling behind). Moving to scale-up check.\n")
        else:
            print(f"  → Scale-down check complete. Moving to scale-up check.\n")

        # === SCALE UP CHECK ===
        used_budget = self.metrics.computeCurrentCost(request['wf-id'], backend.now())

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
                return  # Exit - maintain current allocation

        # Check NEW resources with license constraints
        alloc_instances, license_holds_new = self.checkNewResourcesWithLicenses(
            free_resources, instances, available_budget, available_time,
            request, mesh, license_pool
        )

        if alloc_instances:
            print(f"⬆ Scaling up: allocating {sum(c for _, c in alloc_instances)} instances")

            # Allocate compute
            ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)

            # Track resource allocation in metrics
            current_time = backend.now()
            # Use alloc_resources (3-tuples after allocateResources mutation)
            instances_added = sum(c for _, c, _ in alloc_resources)
            cores_added = sum(inst.cores * count for inst, count, _ in alloc_resources)

            # Calculate actual license tokens (not hold_id count)
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

                # FIX #2: Synchronize to ResourceManager to prevent desync during scale-up
                # This ensures ResourceManager knows about new licenses acquired during scale-up
                self.resource_manager.updateWorkflowLicenses(
                    request['wf-id'],
                    self.license_holds[request['wf-id']],
                    mode='replace'
                )

        else:
            print(f"⏸ No scaling: insufficient resources or licenses")
            # Record scale-up failure (determine reason)
            # Check if it was compute or licenses that blocked
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
        # OLD (BUGGY): available_runtime not passed - parent class didn't use it
        # alloc_instances = self.checkNewResources(
        #     resources, current_resources, budget, request, mesh
        # )

        # NEW (FIXED): Now passing available_runtime for global view deadline checking.
        # Pass software_id so the depth (nodes-per-chain) search is license-cost-aware.
        sid = {'ANSYS': 1, 'ABAQUS': 2, 'LSDYNA': 3}.get(license_pool)
        alloc_instances = self.checkNewResources(
            resources, current_resources, budget, available_runtime, request, mesh,
            software_id=sid
        )

        if not alloc_instances:
            # Compute gate rejected the request; the licence pool was never tested.
            self._last_licence_outcome = 'deny_compute'
            self.metrics.recordNegotiationOutcome('deny_compute')
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
                self._last_licence_outcome = 'approve'
                self.metrics.recordNegotiationOutcome(
                    'approve', tokens_needed=licenses_needed,
                    tokens_available=available_tokens)
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
                    self._last_licence_outcome = 'modify'
                    self.metrics.recordNegotiationOutcome(
                        'modify',
                        requested=sum(c for _, c in alloc_instances),
                        granted=sum(c for _, c in feasible_instances),
                        tokens_needed=licenses_needed,
                        tokens_available=available_tokens)
                    return (feasible_instances, [hold_id])

                else:
                    print(f"  ✗ Cannot fit any allocation to available licenses")
                    self._last_licence_outcome = 'deny_licence'
                    self.metrics.recordNegotiationOutcome(
                        'deny_licence', tokens_needed=licenses_needed,
                        tokens_available=available_tokens)
                    return ([], [])

        except (InsufficientTokens, LicenseError) as e:
            print(f"  ✗ License error: {e}")
            self._last_licence_outcome = 'deny_licence'
            self.metrics.recordNegotiationOutcome('deny_licence')
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

        Returns:
            actual_licenses_released: Actual number of license tokens released
        """
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []
        actual_licenses_released = 0  # Track actual releases (BUG FIX #3)

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

            # === PARTIAL LICENSE RELEASE (Solution 3) ===
            # Free licenses corresponding to freed instances (but only partially)
            if license_pool and to_free_instances:
                total_cores_freed = sum(inst.cores * count for inst, count, _ in to_free_instances)

                try:
                    licenses_to_free = self.license_manager.calculate_tokens(
                        pool=license_pool,
                        cores=total_cores_freed,
                        chains=1
                    )

                    # === OLD CODE (release all licenses): ===
                    # wf_id = request['wf-id']
                    # if wf_id in self.license_holds and self.license_holds[wf_id]:
                    #     hold_id = self.license_holds[wf_id].pop()
                    #     self.license_manager.release(hold_id)
                    #     print(f"  ✓ Released ~{licenses_to_free} licenses (hold: {hold_id})")

                    # === NEW CODE (partial release with buffer retention): ===
                    import os
                    PARTIAL_RELEASE_FRACTION = float(os.environ.get('LA_PARTIAL_RELEASE', '0.90'))  # release frac; 0.50 keeps 50% buffer. Configurable for sensitivity.

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
                                actual_licenses_released += alloc.amount  # Track actual release
                                print(f"  ✓ Released full hold: {alloc.amount} licenses (hold: {hold_id})")
                            else:
                                # Partial release: release old hold, create new smaller hold
                                remaining_licenses = alloc.amount - licenses_to_actually_release

                                # Release old hold
                                self.license_holds[wf_id].pop()
                                self.license_manager.release(hold_id)
                                actual_licenses_released += licenses_to_actually_release  # Track actual release

                                # Create new smaller hold for retained licenses
                                new_hold_id = self.license_manager.hold(
                                    pool=license_pool,
                                    amount=remaining_licenses,
                                    owner=wf_id,
                                    ttl=300
                                )
                                self.license_manager.commit(new_hold_id)
                                self.license_holds[wf_id].append(new_hold_id)

                                # FIX: Synchronize ResourceManager tracking to prevent license accumulation
                                # Update ResourceManager to match Scheduler's new hold list
                                self.resource_manager.updateWorkflowLicenses(
                                    wf_id,
                                    self.license_holds[wf_id],
                                    mode='replace'
                                )

                                print(f"  ✓ Partial release: {licenses_to_actually_release}/{alloc.amount} licenses ({PARTIAL_RELEASE_FRACTION*100:.0f}%)")
                                print(f"    Retained {remaining_licenses} licenses as buffer (new hold: {new_hold_id})")
                        else:
                            # Fallback: just release the hold if not in allocations
                            # Estimate licenses (can't get exact amount without allocation record)
                            self.license_holds[wf_id].pop()
                            self.license_manager.release(hold_id)
                            actual_licenses_released += int(licenses_to_free * PARTIAL_RELEASE_FRACTION)  # Estimate
                            print(f"  ✓ Released hold: {hold_id} (allocation not tracked)")

                except LicenseError as e:
                    print(f"  ⚠ License release error: {e}")

        # Send freed resources notification
        self.sendFreedResources(
            request['wf-id'], to_free_instances, instances,
            response_instances, sim, request.get('client-ip', None)
        )

        return actual_licenses_released  # Return actual amount released (BUG FIX #3)

    # === INHERITED METHODS FROM PARENT ===
    # The following methods are inherited from Scheduler_LA:
    # - checkNewResources() - sophisticated moldable resource allocation (FIXED in Scheduler_LA)
    # - checkCloseness() - runtime similarity check (15% tolerance)

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

    # OLD (INCORRECT) COMMENT:
    # checkNewResources() is inherited from parent Scheduler_LA
    # It contains the full moldable logic from fcfs_optimized.py
    #
    # NEW (CORRECT) COMMENT:
    # checkNewResources() NOW inherits the FIXED sophisticated moldability from Scheduler_LA
    # which includes:
    # - Runtime feasibility checking (global view)
    # - Nodes_per_chain optimization
    # - Speedup threshold checking
    # - Instance closeness checking
