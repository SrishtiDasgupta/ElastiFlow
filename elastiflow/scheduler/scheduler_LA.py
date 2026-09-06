"""
License-Aware Scheduler Base Class

Extends the standard scheduler with license management capabilities.
Integrates with the existing license manager for dual-resource (compute + license) scheduling.
"""

import math
import os
import time
from typing import List, Tuple, Optional

# Maximum nodes-per-chain (moldable DEPTH) the allocator may search. The cited
# cost lever (Henkel & Treiber 2015) places the concave Abaqus solver's license-
# cost optimum at deep d (up to ~8 on small meshes); the previous cloud cap of 4
# truncated it. Configurable (LA_MAX_DEPTH) for the depth-sensitivity figure.
MAX_NODES_PER_CHAIN = int(os.environ.get('LA_MAX_DEPTH', '8'))

# Depth-selection objective. 'cost' (default) = license-cost-aware per-solver depth
# (Henkel & Treiber). 'speedup' = the legacy solver-blind speedup-threshold search
# (kept for the thesis ablation and old-vs-new comparison). Set LA_DEPTH_MODE.
DEPTH_MODE = os.environ.get('LA_DEPTH_MODE', 'cost')

from elastiflow.config.constants import (
    AVG_WORKFLOW_ITERATIONS,
    MIN_INSTANCE_COST,
    RESOURCE_REQUEST_TIMEOUT,
    SPEEDUP_THRESHOLD,
    COLD_START_TIME,
    DEADLINE_BUFFER,
)
from elastiflow.scripts.speedup import getRuntime
from elastiflow.utils.metrics_LA import MetricsLA
from elastiflow.utils.resource import getEstimate
from elastiflow.utils.resource_LA import getConstraintsFromWorkflow
from elastiflow.resource_manager.instance import Instance, OnPremInstance, CloudOnDemandInstance
from elastiflow.resource_manager.license.manager import LicenseManager
from elastiflow.resource_manager.license.exceptions import InsufficientTokens, LicenseError
from elastiflow.utils.request import ExecutorRequest, getConfig, getExecutor, sendRequest
from elastiflow.scheduler.scheduler import Scheduler


class Scheduler_LA(Scheduler):
    """
    License-Aware Scheduler Base Class

    Provides license management on top of standard resource scheduling.
    All license-aware schedulers should inherit from this class.

    Since B7.1 a subclass of `Scheduler`: run, allocateResources, checkResources
    and purgeWorkflow are inherited unchanged; the licence layer overrides the
    admission, negotiation, completion and messaging methods.
    """

    metrics_class = MetricsLA

    def __init__(self, queue, finish_queue, resource_request_queue):
        super().__init__(queue, finish_queue, resource_request_queue)

        # License management - will use resource_manager's license_manager
        # (initialized in subclass after resource_manager is created)
        self.license_manager = None
        self.license_holds = {}  # {wf_id: [hold_ids]}

    # ------------------------------------------------------------------
    # The request loop's hooks for the licence policies (B7.4). Defaults are
    # FCFS-ST-LA's; the other four override what differs for them.
    # ------------------------------------------------------------------
    metrics_prefix = 'Baseline_'        # the metrics files: <prefix><TOTAL_WORKFLOWS>_
    drop_rejected_now = False           # EDF policies drop a rejected workflow at once; FCFS on the next cycle

    def startMonitoring(self, backend):
        # Start a thread to periodically compute resource utilization (including license pools)
        backend.spawn(self.metrics.collectResourceUtilization, backend, self.resource_manager, self.license_manager)

    def beforeLoop(self, backend):
        # Track workflows that are impossible to allocate (prevent infinite waiting)
        self.rejected_workflows = set()

    def beginCycle(self, backend) -> bool:
        # Advance the license ledger clock (honest Token-Hours billing — same
        # basis as the moldable schedulers, so cost is comparable across policies).
        if backend.simulated:
            self.license_manager.set_sim_time(backend.now())
        return False

    def ledgerNow(self, backend):
        return backend.now() if backend.simulated else self.license_manager.sim_now

    def computeFinalMetrics(self, backend):
        from elastiflow.config.constants_LA import TOTAL_WORKFLOWS      # late-bound at END, as the loops did
        self.metrics.computeMetrics(
            file_prefix=f'{self.metrics_prefix}{TOTAL_WORKFLOWS}_',
            license_cost_by_owner=self.license_manager.license_cost_by_owner(self.ledgerNow(backend)))

    def finish(self, wf_plan, backend):
        self.dropWorkflow(backend)
        self.computeFinalMetrics(backend)

    def skipWorkflow(self, wf_plan, backend) -> bool:
        # Skip workflows that have been rejected as impossible
        if wf_plan['id'] in self.rejected_workflows:
            self.dropWorkflow(backend)
            print(f"⊘ Skipping rejected workflow {wf_plan['id']}")
            return True
        return False

    def admit(self, wf_plan, backend):
        available = self.resource_manager.getResourcesAvailable()
        self.admissionDebug(wf_plan, available, backend)
        if not available:
            self.whenUnavailable(wf_plan, backend)
            return
        constraints = getConstraintsFromWorkflow(wf_plan)
        # Allocate BOTH compute and licenses
        ips, alloc_resources, license_holds = self.allocateResourcesWithLicenses(constraints)
        if ips:
            self.printAllocation(wf_plan, ips, backend, constraints)
            if license_holds:
                print(f"  with {len(license_holds)} license hold(s)")
            self.dropWorkflow(backend)
            # Start billing
            start_time = backend.now()
            # Send workflow with license info
            self.sendWorkflowForExecution(wf_plan, ips, backend, constraints['deadline'], license_holds)
            # Track workflow with licenses
            wf = self.resource_manager.addWorkflow(
                wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time,
                constraints['mesh'], constraints.get('software_id', 0), license_holds)
            self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
            self.afterAdmission(wf_plan, alloc_resources, license_holds)
        else:
            self.whenRefused(wf_plan, constraints, ips, alloc_resources, license_holds, backend)

    def admissionDebug(self, wf_plan, available, backend):
        pass

    def printAllocation(self, wf_plan, ips, backend, constraints=None):
        print(f"{wf_plan['id']} allocated at {backend.now()}:", ips)

    def afterAdmission(self, wf_plan, alloc_resources, license_holds):
        pass

    def whenUnavailable(self, wf_plan, backend):
        pass

    def whenRefused(self, wf_plan, constraints, ips, alloc_resources, license_holds, backend):
        # Check if this is an impossible allocation (exceeds pool capacity)
        if self.rejectIfImpossible(wf_plan, constraints, backend):
            return
        # Temporary shortage - wait until resources become available
        self.wait(wf_plan, constraints, backend)

    def rejectIfImpossible(self, wf_plan, constraints, backend) -> bool:
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
                    self.rejected_workflows.add(wf_plan['id'])
                    if self.drop_rejected_now:
                        self.dropWorkflow(backend)
                    print(f'⊘ {wf_plan["id"]} REJECTED - needs {licenses_needed} tokens, pool has {pool_status["total"]}')
                    # (FCFS: will be removed from queue on next iteration)
                    return True
        return False

    def wait(self, wf_plan, constraints, backend):
        self.resource_manager.setResourcesAvailable(False)
        print(f'⏳ {wf_plan["id"]} waiting for resources or licenses...')

    def allocateResourcesWithLicenses(self, constraints):
        """
        Allocate BOTH compute resources AND licenses

        Returns:
            (ips, alloc_resources, license_holds) or (None, None, None) if either unavailable
        """
        software_id = constraints.get('software_id', 0)
        license_pool = constraints.get('license_pool', None)

        # DEBUG: Print extracted license info
        print(f"[DEBUG allocateResourcesWithLicenses] wf_id={constraints.get('wf_id')}, software_id={software_id}, license_pool={license_pool}")

        # If no license pool specified, use standard allocation
        if not license_pool:
            print(f"[DEBUG] No license_pool specified, using standard allocation")
            ips, alloc_resources = self.allocateResources(constraints)
            return ips, alloc_resources, []

        # First, check compute resources
        instances = self.resource_manager.getResources()
        count, instance_list = self.checkResources(instances, constraints['min_instances'])

        if count < constraints['min_instances']:
            return None, None, None

        # Second, check license availability
        total_cores = sum(inst.cores * cnt for inst, cnt in instance_list)

        print(f"[DEBUG] Total cores for allocation: {total_cores}")

        try:
            # Calculate licenses needed based on cores
            licenses_needed = self.license_manager.calculate_tokens(
                pool=license_pool,
                cores=total_cores,
                chains=constraints.get('chains', 1)
            )

            print(f"[DEBUG] Licenses needed: {licenses_needed} tokens from {license_pool} pool")

            # Check if request is impossible (exceeds pool capacity)
            pool_status = self.license_manager.get_pool_status(license_pool)
            print(f"[DEBUG] Pool status: {pool_status}")

            if licenses_needed > pool_status['total']:
                print(f"✗ {constraints.get('wf_id', 'workflow')} needs {licenses_needed} {license_pool} tokens")
                print(f"  but pool only has {pool_status['total']} total capacity")
                print(f"  Workflow REJECTED - impossible to allocate")
                # Remove from queue to prevent infinite retries
                return None, None, None

            # Hold licenses (two-phase commit)
            print(f"[DEBUG] Attempting to hold {licenses_needed} licenses...")
            # Owner MUST equal the metrics df key (wf_plan['id'] == constraints['wf_id'])
            # so the token-hold ledger attributes this initial allocation to the right
            # workflow. The old "wf-" prefix orphaned ~22% of license cost.
            hold_id = self.license_manager.hold(
                pool=license_pool,
                amount=licenses_needed,
                owner=constraints.get('wf_id', 'unknown'),
                ttl=300  # 5 minute hold
            )
            print(f"[DEBUG] License hold successful: {hold_id}")

            # Allocate compute resources
            print(f"[DEBUG] Allocating compute resources...")
            ips, alloc_resources = self.resource_manager.allocateResources(instance_list)

            if not ips:
                # Compute allocation failed - release hold
                print(f"[DEBUG] Compute allocation failed, releasing license hold")
                self.license_manager.release(hold_id)
                return None, None, None

            # Commit licenses
            print(f"[DEBUG] Committing license hold...")
            self.license_manager.commit(hold_id)

            print(f"✓ Allocated {licenses_needed} licenses from pool '{license_pool}' (hold: {hold_id})")
            print(f"[DEBUG] Returning: ips={bool(ips)}, alloc_resources={len(alloc_resources)}, license_holds=[{hold_id}]")

            return ips, alloc_resources, [hold_id]

        except (InsufficientTokens, LicenseError) as e:
            print(f"✗ License allocation failed: {e}")
            return None, None, None

    def allocateLicensesForWorkflow(self, wf_id: str, instances: List[tuple], software_id: int, license_pool: str) -> List[str]:
        """
        Allocate licenses for a workflow based on allocated instances

        Args:
            wf_id: Workflow ID
            instances: List of (Instance, count, ips)
            software_id: Software identifier
            license_pool: License pool name (e.g., 'ANSYS')

        Returns:
            List of license hold IDs
        """
        if not license_pool:
            return []

        total_cores = sum(inst.cores * count for inst, count, _ in instances)

        try:
            licenses_needed = self.license_manager.calculate_tokens(
                pool=license_pool,
                cores=total_cores,
                chains=1  # Default
            )

            hold_id = self.license_manager.hold(
                pool=license_pool,
                amount=licenses_needed,
                owner=wf_id,
                ttl=300
            )

            self.license_manager.commit(hold_id)
            self.license_holds[wf_id] = self.license_holds.get(wf_id, []) + [hold_id]

            print(f"✓ Allocated {licenses_needed} licenses for {wf_id} (hold: {hold_id})")
            return [hold_id]

        except (InsufficientTokens, LicenseError) as e:
            print(f"✗ License allocation failed for {wf_id}: {e}")
            return []

    def releaseLicensesForWorkflow(self, wf_id: str):
        """
        Release all licenses held by a workflow

        Args:
            wf_id: Workflow ID
        """
        if wf_id not in self.license_holds:
            return

        for hold_id in self.license_holds[wf_id]:
            try:
                self.license_manager.release(hold_id)
                print(f"✓ Released licenses for {wf_id} (hold: {hold_id})")
            except LicenseError as e:
                print(f"✗ Error releasing licenses for {wf_id}: {e}")

        del self.license_holds[wf_id]

    def calculateDualShadowTime(self, wf_id: str, license_pool: Optional[str] = None) -> Tuple[float, float, float]:
        """
        Calculate shadow time for BOTH compute and licenses

        Returns:
            (compute_shadow, license_shadow, combined_shadow)
        """
        # TODO: Implement sophisticated shadow time calculation
        # For now, return infinity (no shadow time calculation)
        return (float('inf'), float('inf'), float('inf'))

    def sendWorkflowForExecution(self, wf_plan, ips, backend, deadline, license_holds=None):
        """
        Send workflow to executor with license information

        Extended to include license hold IDs in the request
        """
        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,
            "deadline": deadline,
            "license-holds": license_holds or []  # NEW: Include license holds
        }

        executor, on_demand_type = getExecutor(ips, backend)
        if on_demand_type:
            request['hosts']['on-demand'][on_demand_type] = (
                request['hosts']['on-demand'][on_demand_type][0],
                [executor]
            )

        backend.start_workflow(request, executor)
    def processJobCompletion(self, backend):
        """
        Process workflow completions and release BOTH compute and licenses
        """
        print('Scheduler started listening to completed jobs...')
        while True:
            data = backend.completions.peek()
            if data:
                data = eval(data)
                wf_id = data.get('wf-id')

                # Track final release in metrics ONLY for moldable schedulers
                if hasattr(self, 'is_moldable') and self.is_moldable:
                    try:
                        # Get workflow info before releasing
                        instances, budget, deadline, start_time, mesh, software_id, license_holds = \
                            self.resource_manager.getWorkflow(wf_id)

                        # Calculate cores being released
                        cores_removed = sum(inst.cores * count for inst, count, _ in instances)
                        instances_removed = sum(count for _, count, _ in instances)

                        # Calculate licenses being released
                        licenses_released = 0
                        if license_holds:
                            for hold_id in license_holds:
                                if hold_id in self.license_manager.allocations:
                                    licenses_released += self.license_manager.allocations[hold_id].amount

                        # Record as final "scale-down" (workflow completion release)
                        self.metrics.recordScaleDownAttempt(
                            success=True,
                            instances_removed=instances_removed,
                            cores_removed=cores_removed,
                            licenses_released=licenses_released
                        )
                        print(f"  📊 Tracked final release: {instances_removed} instances, {cores_removed} cores, {licenses_released} licenses")
                    except Exception as e:
                        print(f"  ⚠ Error tracking final release for {wf_id}: {e}")

                # Release compute resources (includes license release via returnResources)
                self.resource_manager.returnResources(wf_id)

                # FIX #3: Removed redundant releaseLicensesForWorkflow() call
                # returnResources() already releases licenses (resource_manager_LA.py:154-161)
                # Calling releaseLicensesForWorkflow() here would attempt to release the same licenses twice

                self.metrics.updateDataframe(
                    wf_id,
                    {
                        'exec_start_time': data.get('start-time'),
                        'finish_time': data.get('finish-time'),
                        'complete': data.get('complete')
                    }
                )
                print(f'{wf_id} workflow freed at {backend.now()}')
                backend.completions.pop()

            backend.sleep(60)

    def allocateNewResources(self, request, backend):
        """
        Allocate new resources for moldable workflows

        Base implementation (extended in child classes with license checks)
        """
        if backend.now() - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        # LA workflows always return 7-value tuples
        instances, budget, deadline, start_time, mesh, software_id, license_holds = \
            self.resource_manager.getWorkflow(request['wf-id'])

        free_resources = self.resource_manager.getResources()
        used_budget = self.metrics.computeCost(request['wf-id'], backend.now())
        available_budget = max(0, budget - used_budget) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)

        # OLD (BUGGY): available_runtime not calculated or passed
        # alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, mesh)

        # NEW (FIXED): Calculate available_runtime for global view deadline checking
        available_runtime = max(0, deadline - DEADLINE_BUFFER - backend.now())
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, available_runtime, request, mesh)
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)

        self.sendNewResources(request['wf-id'], ips, alloc_resources, backend, request.get('client-ip', None))

    def sendNewResources(self, wf_id, ips, alloc_resources, backend, client_ip, license_holds=None):
        """
        Send new resource allocation to executor

        Extended to include license hold IDs
        """
        new_req = {
            "request": ExecutorRequest.REQUEST_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": ips,
            "license-holds": license_holds or []  # NEW
        }

        print(f"{wf_id} allocated additional resources: ", ips)
        if license_holds:
            print(f"  with licenses: {license_holds}")

        backend.notify_resources(new_req, client_ip)

        if alloc_resources:
            self.resource_manager.updateWorkflowResources(wf_id, alloc_resources)
            # Metrics tracking now happens in fcfs_optimized_LA.py before calling sendNewResources()
            # self.metrics.updateResources(wf_id, alloc_resources, backend.now())  # OLD signature - removed

    def sendFreedResources(self, wf_id, to_free_instances, instances, response_instances, backend, client_ip):
        """
        Send freed resources notification to executor
        """
        new_req = {
            "request": ExecutorRequest.FREE_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": response_instances
        }

        print(f"Scheduler freeing {response_instances} for {wf_id}")

        backend.notify_resources(new_req, client_ip)

        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            # Metrics tracking now happens in fcfs_optimized_LA.py before calling freeResourcesWithLicenses()
            # self.metrics.updateResources(wf_id, to_free_instances, None, backend.now())  # OLD signature - removed

    # =========================================================================
    # OLD (BUGGY) checkNewResources - COMMENTED OUT FOR COMPARISON
    # =========================================================================
    # This was the original simple resource allocation that was missing:
    # - Runtime feasibility checking (global view of deadline constraints)
    # - Sophisticated moldability (nodes_per_chain optimization)
    # - Speedup threshold checking
    # - Instance closeness checking for heterogeneous resources
    #
    # To revert to OLD behavior for comparison:
    # 1. Uncomment lines below (old checkNewResources)
    # 2. Comment out new checkNewResources (lines ~485-620)
    # 3. Update signature in child classes to remove available_runtime parameter
    # =========================================================================
    # def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, request, mesh) -> List[tuple[Instance, int]]:
    #     """
    #     Check new resource availability for moldable scaling
    #
    #     Same as base scheduler (override in child classes for license checks)
    #     """
    #     if budget < MIN_INSTANCE_COST:
    #         return []
    #
    #     # 1. If on-prem is already allocated, allocate possible on-prem instances
    #     instance = current_resources[0][0]
    #     if isinstance(instance, OnPremInstance):
    #         cost_per_iteration = instance.cost_per_second * getRuntime(1, mesh, instance.name)
    #         instance_cost = getEstimate(cost_per_iteration, request['tinyda-iterations'])
    #         to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / instance_cost))
    #         return [(instance, to_be_used)]
    #
    #     acquired_count = 0
    #     acquired_instances = []
    #
    #     # 2. Allocate possible reserved/on-demand if not case 1
    #     instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
    #     for instance in instances:
    #         cost_per_iteration = instance.cost_per_second * getRuntime(1, mesh, instance.name)
    #         instance_cost = getEstimate(cost_per_iteration, request['tinyda-iterations'], 1)
    #         to_be_used = min(request['count']-acquired_count, instance.getFreeSlots(), int(budget/instance_cost))
    #
    #         if to_be_used:
    #             acquired_count += to_be_used
    #             budget -= to_be_used * instance_cost
    #             acquired_instances.append((instance, to_be_used))
    #
    #             if acquired_count == request['count'] or budget < MIN_INSTANCE_COST:
    #                 return acquired_instances
    #
    #     return acquired_instances

    # =========================================================================
    # NEW (FIXED) checkNewResources - SOPHISTICATED MOLDABILITY
    # =========================================================================
    # This is the corrected version with the "global view" from PLAIN fcfs_optimized.py
    # Adds the missing runtime feasibility check and moldability optimization.
    # =========================================================================

    def _best_depth_total(self, max_npc, mesh, inst_name, cores_per_node,
                          chains, tinyda_iters, budget, available_runtime,
                          cost_per_sec_node, software_id, cold_per_node=0.0):
        """Pick nodes-per-chain (moldable DEPTH) that minimises a workflow's total
        (hardware + license) cost, subject to the deadline (available_runtime) and
        budget. Returns 0 if no depth is feasible.

        This replaces the old solver-blind speedup-threshold search. License cost
        follows the cited token laws (Henkel & Treiber 2015) via
        metrics.calculate_license_cost: only the concave Abaqus law rewards depth>1
        (token count grows sublinearly while runtime falls), whereas LS-Dyna
        (linear) and Ansys (workgroup + flat MEBA) are minimised at depth 1. The
        old search picked depth purely for speedup, overshooting depth for the
        linear/workgroup solvers (paying extra tokens) and was capped below the
        Abaqus optimum. Minimising TOTAL cost is the defensible objective: deeper
        allocations use more nodes (hardware rises with depth), which the optimum
        balances against the solver's license curve.
        """
        if max_npc < 1:
            return 0
        if DEPTH_MODE == 'speedup':
            # Legacy solver-blind selection: largest depth (from max down) whose
            # marginal node still yields > SPEEDUP_THRESHOLD speedup AND fits budget;
            # reject (0) if that depth misses the deadline. Mirrors the old greedy loop.
            for d in range(int(max_npc), 0, -1):
                runtime = getRuntime(d, mesh, inst_name)
                total_nodes = d * chains
                hw_cost = getEstimate(cost_per_sec_node * runtime, tinyda_iters, 1, total_nodes)
                if cold_per_node:
                    hw_cost += cold_per_node * total_nodes
                if hw_cost < budget and getRuntime(d - 1, mesh, inst_name) / runtime > SPEEDUP_THRESHOLD:
                    return d if runtime * tinyda_iters < available_runtime else 0
            return 0
        best_d, best_cost = 0, None
        for d in range(1, int(max_npc) + 1):
            runtime = getRuntime(d, mesh, inst_name)
            if runtime * tinyda_iters >= available_runtime:      # deadline infeasible
                continue
            total_nodes = d * chains
            duration = runtime * tinyda_iters
            hw_cost = getEstimate(cost_per_sec_node * runtime, tinyda_iters, 1, total_nodes)
            if cold_per_node:
                hw_cost += cold_per_node * total_nodes
            if hw_cost >= budget:                                # budget infeasible
                continue
            lic_cost = (self.metrics.calculate_license_cost(
                            software_id, total_nodes * cores_per_node, duration)
                        if software_id else 0.0)
            total = hw_cost + lic_cost
            if best_cost is None or total < best_cost:
                best_d, best_cost = d, total
        return best_d

    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]],
                          budget: float, available_runtime: float, request, mesh,
                          software_id: Optional[int] = None) -> List[tuple[Instance, int]]:
        """
        Advanced moldable resource allocation (3-tier strategy from PLAIN fcfs_optimized)

        FIXED: Added missing "global view" of resource constraints:
        - Runtime feasibility: if speedup_runtime * iterations < available_runtime
        - Nodes_per_chain moldability optimization
        - Speedup threshold checking (SPEEDUP_THRESHOLD = 1.4)
        - Instance closeness checking (15% tolerance)

        Tier 1: Moldable on-prem allocation (optimize nodes_per_chain)
        Tier 2: Moldable cloud allocation with instance closeness
        Tier 3: Fallback to any available instances with speedup check
        """
        # 1. If on-prem is already allocated, allocate possible on-prem instances
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            free_slots = instance.getFreeSlots()
            to_be_used = 0
            # At least 1 node per chain/moldability
            if free_slots >= request['count']:
                max_npc = request['chains'] - request['count'] + free_slots // request['chains']
                # License-cost-aware DEPTH (was solver-blind speedup search).
                nodes_per_chain = self._best_depth_total(
                    max_npc, mesh, instance.name, instance.cores,
                    request['chains'], request['tinyda-iterations'],
                    budget, available_runtime, instance.cost_per_second,
                    software_id, cold_per_node=0.0)
                if nodes_per_chain > 0:
                    to_be_used = (nodes_per_chain * request['chains']) - (request['chains'] - request['count'])
                    print(f'Moldable onprem with {nodes_per_chain} nodes per chain '
                          f'(cost-optimal, sw={software_id})')
            return [(instance, to_be_used)]

        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        # We consider 3 cases:
        # 1. Instances of the same type (res/on-dem)
        # 2. Instances with almost the same runtime (res/on-dem)
        # 3. Any idle instances if speedup makes sense
        cur_instances = set()
        cur_instance_runtimes = []
        for inst_tuple in current_resources:
            cur_instances.add(inst_tuple[0].name)
            cur_instance_runtimes.append(getRuntime(1, mesh, inst_tuple[0].name))
        free_slots = 0
        instances_to_be_used = []  # [instObj, count]
        ondemandFlag = False
        for inst in instances:
            free_nodes = inst.getFreeSlots()
            if free_nodes and (inst.name in cur_instances or self.checkCloseness(inst, cur_instance_runtimes, mesh)):
                free_slots += free_nodes
                instances_to_be_used.append((inst, free_nodes))
                if isinstance(inst, CloudOnDemandInstance): ondemandFlag = True

        to_be_used, nodes_per_chain = 0, 0
        if free_slots:
            print(f'Going into moldable cloud with {free_slots}')
            # Cost model (unchanged): slowest instance drives runtime, priciest drives cost.
            slow_name = instances_to_be_used[-1][0].name
            slow_cpn  = instances_to_be_used[-1][0].cores
            dear_cps  = instances_to_be_used[0][0].cost_per_second
            # Cap lifted 4 -> MAX_NODES_PER_CHAIN so Abaqus's deep small-mesh optimum is reachable.
            max_npc = min((request['chains'] - request['count'] + free_slots) // request['chains'],
                          MAX_NODES_PER_CHAIN)
            # License-cost-aware DEPTH (was solver-blind speedup search).
            nodes_per_chain = self._best_depth_total(
                max_npc, mesh, slow_name, slow_cpn,
                request['chains'], request['tinyda-iterations'],
                budget, available_runtime, dear_cps, software_id,
                cold_per_node=(COLD_START_TIME * dear_cps if ondemandFlag else 0.0))
            if nodes_per_chain > 0:
                to_be_used = (nodes_per_chain * request['chains']) - (request['chains'] - request['count'])
                print(f'Alloted moldable cloud with {nodes_per_chain} nodes per chain '
                      f'(cost-optimal, sw={software_id})')

        # Extract last n to_be_used nodes from instances_to_be_used - cheaper
        acquired_instances = []
        while to_be_used > 0:
            cur_node, cur_count = instances_to_be_used.pop()
            count = min(cur_count, to_be_used)
            acquired_instances.append((cur_node, count))
            cur_count -= count
            if cur_count > 0: instances_to_be_used.append((cur_node, cur_count))
            to_be_used -= count

        # If moldabiliy couldn't be handled, take at least available instances till budget allows
        # 3. Allocate any reserved/on-demand if we have budget and if speedup makes sense
        if not acquired_instances:  # account for existing resources
            max_cur_runtime = max(cur_instance_runtimes)
            cost_per_node_iteration = current_resources[-1][0].cost_per_second * max_cur_runtime
            budget -= getEstimate(cost_per_node_iteration, request['tinyda-iterations'], 1, request['chains'] - request['count'])

            node_count = request['count'] if request['count'] > 0 else request['chains'] + request['count']
            current_node_count = request['chains'] - request['count']
            addedColdStartCost = False

            for inst in instances:
                if budget < MIN_INSTANCE_COST or node_count == 0: break
                free_nodes = inst.getFreeSlots()
                if not free_nodes: continue
                to_be_used = 1
                speedup_runtime = getRuntime(1, mesh, inst.name)
                if speedup_runtime > max_cur_runtime * 1.2:
                    if free_nodes > 1:
                        to_be_used = 2  # get 2 nodes
                        speedup_runtime = getRuntime(2, mesh, inst.name)
                    else: continue
                total_cost = inst.cost_per_second * speedup_runtime * request['tinyda-iterations'] * to_be_used
                if isinstance(inst, CloudOnDemandInstance) or addedColdStartCost:
                    total_cost += COLD_START_TIME * inst.cost_per_second
                    if not addedColdStartCost:
                        total_cost += COLD_START_TIME * current_node_count * inst.cost_per_second
                if budget - total_cost > 0:
                    budget -= total_cost
                    addedColdStartCost = True
                else: continue
                acquired_instances.append((inst, to_be_used))
                current_node_count += to_be_used
                node_count -= 1
            else: print(f'moldable cloud free alloc with {current_node_count} for {request["chains"]}')

        # 4. Cannot allocate anything
        return acquired_instances

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


class Scheduler_LA_Elastic(Scheduler_LA):
    """The elastic licence policies (FCFS-LAMF, EDF-LAMF and, through
    EDF-LAMF, HSM) share the release side of licence-aware negotiation
    (B7.2). Their admission and scale-up methods differ and stay per policy."""

    def freeResourcesWithLicenses(self, instances, request, backend, license_pool: Optional[str]):
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
            response_instances, backend, request.get('client-ip', None)
        )

        return actual_licenses_released

    def _feasibilityChains(self, request) -> int:
        """The chain count the licence-feasibility search sizes its tokens with.
        FCFS-LAMF sizes with one chain, EDF-LAMF (and HSM) with the workflow's
        chain count; each policy states its own (B7.3)."""
        raise NotImplementedError

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
        # GLOBAL VIEW: Pass available_runtime for deadline feasibility check.
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
                        chains=self._feasibilityChains(request)
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
