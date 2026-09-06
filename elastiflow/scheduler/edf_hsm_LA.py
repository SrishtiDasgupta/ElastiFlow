"""
EDF_HSM_LA: Hybrid Static-Moldable EDF with License Awareness

This scheduler combines static and moldable approaches:

STATIC PHASE (iterations 0 .. STATIC_PHASE_ITERS):
- Holds the initial allocation; no scale-up/down
- Provides "deadline insurance" while early progress is established
- NB: iteration 0 is the initial allocation and is static for EVERY policy;
  HSM's distinct behaviour comes from also suppressing the first
  STATIC_PHASE_ITERS *renegotiations* (default 1 => iterations 0 and 1 static)

MOLDABLE PHASE (iterations > STATIC_PHASE_ITERS):
- Full EDF-LAMF moldability (deadline-driven triggers)
- Urgency-based scaling (CRITICAL/WARNING/EARLY/MID-ITERATION triggers)
- Graduated boost factors (1.2× → 2.0×)
- Smart scale-down guards (license pool, late iteration, deadline proximity)

Key idea:
- Best of both worlds: static's deadline protection in the opening iterations +
  moldable cost efficiency thereafter
- Static-phase length is the env-tunable STATIC_PHASE_ITERS (LA_HSM_STATIC_ITERS)
- Progressive OPTIM factors apply only once the moldable phase begins; the
  iteration-0 OPTIM factor (0.6) is inert (iteration 0 never renegotiates)
"""

import math
import threading
import time
from typing import Dict

from elastiflow.config.constants_LA import (
    DEADLINE_BUFFER,
    MIN_INSTANCE_COST,
    OPTIM_FCFS_DFACTOR,
    RESOURCE_REQUEST_TIMEOUT,
    SPEEDUP_THRESHOLD,
    WORKFLOW_POLLING,
    TOTAL_WORKFLOWS,
)

from elastiflow.scripts.speedup import getRuntime
from elastiflow.resource_manager.instance import OnPremInstance
from elastiflow.utils.resource_LA import getConstraintsFromWorkflow, getEstimate
from elastiflow.scheduler.edf_optimized_LA import EDF_Optimized_LA

import os

# ---------------------------------------------------------------------------
# HSM phase-transition policy (licence-aware static -> moldable gate)
# ---------------------------------------------------------------------------
# HSM is NOT a fixed schedule. A workflow begins in a STATIC phase that holds its
# initial allocation (scale-UP allowed for deadline protection, scale-DOWN
# forbidden) and transitions to the MOLDABLE phase -- where full EDF-LAMF
# cost-optimising logic applies -- only once it is *safe AND cheap* to do so:
#
#   safe  := projected deadline slack at the current allocation >= TAU * window
#            (the workflow has demonstrably banked enough margin that releasing
#             resources will not endanger its deadline)
#   cheap := licence-pool pressure < RHO
#            (tokens are abundant, so any later re-acquisition is cheap/low-risk)
#
# Release requires BOTH (the licence signal can VETO a release): the slack
# estimate is contention-blind (linear runtime model, no co-location penalty), so
# a slack-only release is over-optimistic and triggers the premature-scale-down /
# panic-scale-up thrash of pure-moldable scheduling. Requiring an uncontended pool
# before releasing is the contention-aware contribution, specialised to a
# licence-constrained setting. The transition is one-way and decided per workflow
# from its own deadline state and the *global* pool occupancy.
#
# STATIC_PHASE_ITERS is a hard floor (minimum renegotiations held static before
# the gate may fire) -- default 0 lets the gate decide from the first
# renegotiation. All three are env-tunable for the sensitivity study.
STATIC_PHASE_ITERS = int(os.environ.get('LA_HSM_STATIC_ITERS', '0'))
HSM_SLACK_TAU = float(os.environ.get('LA_HSM_SLACK_TAU', '0.15'))

# Per-pool contention threshold rho. A single global rho is crude: the three
# pools have different token economics (LSDYNA linear/cheap, ABAQUS power-law,
# ANSYS workgroup). Higher rho => "cheap" more often => release that pool freely
# (recover cost); lower rho => hold it under contention (protect deadlines). So
# the licence-aware tuning is: raise rho on abundant/cheap pools, lower it on
# scarce/expensive ones.
#
# DEPLOYED per-pool values (validated across the full N grid, 6 seeds): release
# the cheap/abundant LSDYNA pool (linear token law) freely at rho=0.95, hold the
# expensive ANSYS/ABAQUS pools under contention at rho=0.60. This beats EDF-LAMF
# on deadline misses / overall misses / effective licence utilisation at 6/7
# workload sizes, at a cost premium under ~1% on average (within seed noise).
# Each pool overridable via LA_HSM_POOL_RHO_<POOL>; LA_HSM_POOL_RHO is the
# fallback for any pool not listed.
HSM_POOL_RHO_DEFAULT = float(os.environ.get('LA_HSM_POOL_RHO', '0.70'))
HSM_POOL_RHO = {
    'ANSYS':  float(os.environ.get('LA_HSM_POOL_RHO_ANSYS',  '0.60')),
    'ABAQUS': float(os.environ.get('LA_HSM_POOL_RHO_ABAQUS', '0.60')),
    'LSDYNA': float(os.environ.get('LA_HSM_POOL_RHO_LSDYNA', '0.95')),
}


class EDF_HSM_LA(EDF_Optimized_LA):
    """
    Hybrid Static-Moldable EDF Scheduler with License Awareness

    Combines static allocation (iteration 0) with moldable scaling (iterations 1-5):
    - EDF queue ordering (deadline-based priority)
    - Static baseline allocation in iteration 0 (no scaling)
    - Full EDF-LAMF moldability in iterations 1-5
    - License-aware guards and urgency-based triggers
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        # EDF-LAMF's constructor (resource manager sorted by cost plus cold start,
        # the EDF heaps and counters, the licence-hold table, the licence manager
        # link) plus the HSM phase table. Before B7.2 the same statements were
        # written out here.
        super().__init__(queue, finish_queue, resource_request_queue, sort_key=sort_key)

        # HSM per-workflow phase state: 'STATIC' (hold allocation) -> 'MOLDABLE'
        # (full EDF-LAMF). One-way; gated by the licence-aware criterion below.
        self.hsm_phase = {}  # {wf_id: 'STATIC' | 'MOLDABLE'}

    def run(self, backend):
        """
        Main scheduler loop with EDF ordering
        """
        print(f'Starting HSM (Hybrid Static-Moldable) scheduler...')
        print(f'  - Iteration 0: STATIC allocation (no scaling)')
        print(f'  - Iterations 1-5: MOLDABLE (EDF-LAMF triggers + guards)')
        print(f'  - EDF heap ordering: Deadline-based priority queue')
        print(f'  - Iteration weighting: BFACTOR/DFACTOR {list(OPTIM_FCFS_DFACTOR.values())}')
        print(f'  - Deadline triggers: CRITICAL (<30%), WARNING (<50%), EARLY (3%), MID-ITERATION')
        print(f'  - License-aware guards: Pool saturation, late iteration, deadline proximity')

        # Start resource utilization monitoring (including license pools)
        backend.spawn(self.metrics.collectResourceUtilization, backend, self.resource_manager, self.license_manager)

        rejected_workflows = set()  # Track impossible workflows
        loop_counter = 0  # Track loop iterations for debugging
        idle_loop_count = 0  # Track consecutive idle loops

        while True:
            loop_counter += 1

            # Advance the license ledger clock so every token hold/release this cycle
            # is billed at the correct backend timestamp (honest Token-Hours billing).
            if backend.simulated:
                self.license_manager.set_sim_time(backend.now())

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
                idle_loop_count += 1
                if idle_loop_count > 10:
                    print(f"\n{'='*70}")
                    print(f"✓ All workflows completed. Terminating scheduler.")
                    print(f"  Total loops: {loop_counter}")
                    print(f"  Final simulated time: {backend.now():.1f}s")
                    print(f"{'='*70}\n")
                    from elastiflow.config.constants_LA import TOTAL_WORKFLOWS
                    self.metrics.computeMetrics(
                        file_prefix=f'EDF_HSM_{TOTAL_WORKFLOWS}_',
                        license_cost_by_owner=self.license_manager.license_cost_by_owner(
                            backend.now() if backend.simulated else self.license_manager.sim_now))
                    break
                elif idle_loop_count == 1:
                    print(f"\n[INFO] No active workflows detected. Waiting for termination (idle_count={idle_loop_count}/10)...")
            else:
                if idle_loop_count > 0:
                    print(f"[DEBUG] Activity detected, resetting idle_count from {idle_loop_count} (active={active_wfs}, wf_heap={len(self.workflow_heap)}, req_heap={len(self.resource_request_heap)})")
                idle_loop_count = 0  # Reset if there's activity

            # === PHASE 1: Process Resource Requests (EDF ordering) ===
            resource_requests = backend.resource_requests.pop_many(None)

            if resource_requests:
                # Sort resource requests by deadline (EDF)
                self.processResourceRequestsByDeadline(resource_requests)
                resource_request = self.peekWorkflow(self.resource_request_heap)

                if resource_request:
                    # Process with deadline-urgency awareness
                    start = time.time()

                    if backend.now() - resource_request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
                        self.popWorkflow(self.resource_request_heap)
                        backend.resource_requests.pop()
                        continue

                    self.processFreeRequestWithLicenses(backend, resource_request)
                    print(f"  Resource request overhead: {time.time() - start:.3f}s")

                    self.popWorkflow(self.resource_request_heap)
                    backend.resource_requests.pop()
                    continue

            # === PHASE 2: Schedule New Workflows (EDF ordering) ===
            workflows = backend.workflows.pop_many(None)

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
                    backend.workflows.pop()
                    from elastiflow.config.constants_LA import TOTAL_WORKFLOWS
                    self.metrics.computeMetrics(
                        file_prefix=f'EDF_HSM_{TOTAL_WORKFLOWS}_',
                        license_cost_by_owner=self.license_manager.license_cost_by_owner(
                            backend.now() if backend.simulated else self.license_manager.sim_now))
                    break

                # Skip rejected workflows
                if wf_plan['id'] in rejected_workflows:
                    self.popWorkflow(self.workflow_heap)
                    backend.workflows.pop()
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
                        backend.workflows.pop()

                        # Start billing
                        start_time = backend.now()

                        # Send to executor
                        self.sendWorkflowForExecution(
                            wf_plan, ips, backend, constraints['deadline'], license_holds
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
                            backend.workflows.pop()
                            print(f"⊘ Rejecting impossible workflow {wf_plan['id']}")
                        else:
                            # Temporarily unavailable - retry on next polling cycle.
                            # NOTE: previously set setResourcesAvailable(False) globally,
                            # which created a bootstrap deadlock — once False, the entire
                            # Phase 3 block was skipped and no allocation could ever fire
                            # again because no completion could fire without an allocation.
                            # A guarded re-add was tested (only throttle when workflows are
                            # running) but recovered no cost (returnResources re-arms the
                            # flag every completion/release, so it almost never fires).
                            # Just skip this iteration; the natural polling loop retries.
                            if loop_counter % 10000 == 0:
                                print(f'⏳ {wf_plan["id"]} waiting for resources/licenses (heap_size={len(self.workflow_heap)})...')
                    else:
                        # Wait for resources — see note above; no global flag set.
                        if loop_counter % 10000 == 0:
                            print(f'[DEBUG] No resources to allocate, waiting... (heap_size={len(self.workflow_heap)})')
                else:
                    # Resources not available - this is likely the blocking condition
                    if loop_counter % 10000 == 0:
                        print(f'[DEBUG] Resources marked as unavailable, skipping allocation (heap_size={len(self.workflow_heap)})')

            backend.sleep(WORKFLOW_POLLING)

    # =========================================================================
    # EDF HEAP MANAGEMENT (from HEFTResourceManager)
    # =========================================================================

    # =========================================================================
    # WORKFLOW FEASIBILITY CHECK
    # =========================================================================

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

    negotiation_label = 'HSM MOLDABLE-PHASE'
    phase_note = ' (MOLDABLE)'

    def _holdAllocation(self, request, instances, deadline, start_time, mesh, license_pool, software_id, ind, backend) -> bool:
        # === HSM: LICENCE-AWARE STATIC -> MOLDABLE PHASE GATE ===
        # Decide (and persist) this workflow's phase from its own deadline slack
        # and the global licence-pool pressure. In STATIC the scale-DOWN branch of
        # EDF-LAMF's processFreeRequestWithLicenses is suppressed (scale-UP stays
        # enabled for deadline protection); in MOLDABLE it runs unchanged.
        return self._hsm_in_static_phase(
            request, instances, deadline, start_time, mesh, license_pool,
            software_id, ind, backend)

    def _hsm_in_static_phase(self, request, instances, deadline, start_time, mesh,
                             license_pool, software_id, ind, backend):
        """Licence-aware static -> moldable gate. Returns True if the workflow
        should remain in the STATIC phase (scale-down suppressed) this renegotiation.

        Transition (one-way) STATIC -> MOLDABLE fires only when BOTH hold:
          safe  := projected deadline slack at the current allocation
                   >= HSM_SLACK_TAU * (deadline - start_time), AND
          cheap := licence-pool pressure (allocated/total) < rho[pool].
        (The implementation below is `floor_ok and safe and cheap`; an earlier
        revision of this docstring said EITHER/OR and was never correct.)
        A workflow already in MOLDABLE stays there. STATIC_PHASE_ITERS is a hard
        floor: the gate cannot fire while ind <= STATIC_PHASE_ITERS.
        """
        wf_id = request['wf-id']
        if self.hsm_phase.get(wf_id, 'STATIC') == 'MOLDABLE':
            return False  # already moldable -- stay moldable

        # --- projected deadline slack at the CURRENT (held) allocation ---
        cur_instance = instances[-1][0]
        cur_count = instances[-1][1]
        if not isinstance(instances[0][0], OnPremInstance):
            cur_count = sum(t[1] for t in instances)
        cur_count = max(1, cur_count)

        runtime_per_model = getRuntime(1, mesh, cur_instance.name)
        chains = request['chains']
        tinyda = request['tinyda-iterations']
        chains_per_node_eff = -(-chains // cur_count)  # ceil
        runtime_per_iter = chains_per_node_eff * runtime_per_model * tinyda

        last_iter = max(OPTIM_FCFS_DFACTOR)          # highest iteration index
        remaining_iters = max(0, last_iter - ind)    # iterations still to run
        now = backend.now()
        projected_finish = now + remaining_iters * runtime_per_iter
        slack = (deadline - DEADLINE_BUFFER) - projected_finish
        window = max(1e-9, deadline - start_time)
        safe = slack >= HSM_SLACK_TAU * window

        # --- licence-pool pressure vs this pool's own threshold ---
        pool_pressure = 0.0
        rho = HSM_POOL_RHO_DEFAULT
        if license_pool:
            ps = self.license_manager.get_pool_status(license_pool)
            pool_pressure = (ps['allocated'] / ps['total']) if ps['total'] else 0.0
            rho = HSM_POOL_RHO.get(license_pool, HSM_POOL_RHO_DEFAULT)
        cheap = pool_pressure < rho

        floor_ok = ind > STATIC_PHASE_ITERS  # hard minimum static window

        # Release ONLY when the workflow is BOTH on-track (safe) AND the pool is
        # uncontended (cheap). Rationale: the slack estimate is contention-blind
        # (linear runtime model, no co-location penalty), so a slack-only release
        # is over-optimistic -- the licence-pool signal must be able to VETO a
        # cost-driven scale-down under scarcity. Hold static if at-risk OR contended.
        if floor_ok and safe and cheap:
            self.hsm_phase[wf_id] = 'MOLDABLE'
            print(f"\n🔀 [HSM GATE] {wf_id} STATIC->MOLDABLE @iter{ind}: "
                  f"slack={slack:.0f}s (>= {HSM_SLACK_TAU:.2f}*{window:.0f}={HSM_SLACK_TAU*window:.0f}? {safe}), "
                  f"pool={pool_pressure*100:.0f}% (< rho[{license_pool}]={rho*100:.0f}%? {cheap})")
            return False

        veto = 'at-risk' if not safe else ('contended' if not cheap else 'floor')
        print(f"\n📌 [HSM GATE] {wf_id} STATIC @iter{ind} ({veto}): holding allocation "
              f"(slack={slack:.0f}s safe={safe}, pool={pool_pressure*100:.0f}% cheap={cheap}, "
              f"floor_ok={floor_ok})")
        return True

