"""
License-Aware Scheduler Base Class

Extends the standard scheduler with license management capabilities.
Integrates with the existing license manager for dual-resource (compute + license) scheduling.
"""

from abc import ABC, abstractmethod
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
    AVG_WORKFLOW_ITERATIONS, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, MIN_RUNTIME,
    RESOURCE_REQUEST_TIMEOUT, SPEEDUP_THRESHOLD, COLD_START_TIME, DEADLINE_BUFFER
)
from elastiflow.executor import executeWorklow, processNewResources
from elastiflow.scripts.speedup import getRuntime
from elastiflow.utils.metrics_LA import MetricsLA as Metrics
from elastiflow.utils.resource import getEstimate
from elastiflow.resource_manager.instance import Instance, OnPremInstance, CloudOnDemandInstance
from elastiflow.resource_manager.license.manager import LicenseManager
from elastiflow.resource_manager.license.exceptions import InsufficientTokens, LicenseError
from elastiflow.utils.request import ExecutorRequest, getConfig, getExecutor, sendRequest
from elastiflow.utils.sim import peekElement, removeElement
from elastiflow.execution.backend import backend_for


class Scheduler_LA(ABC):
    """
    License-Aware Scheduler Base Class

    Provides license management on top of standard resource scheduling.
    All license-aware schedulers should inherit from this class.
    """

    def __init__(self, queue, finish_queue, resource_request_queue):
        self.queue = queue
        self.finish_queue = finish_queue
        self.resource_request_queue = resource_request_queue
        self.metrics = Metrics()

        # License management - will use resource_manager's license_manager
        # (initialized in subclass after resource_manager is created)
        self.license_manager = None
        self.license_holds = {}  # {wf_id: [hold_ids]}

    @abstractmethod
    def run(self, queue):
        pass

    def allocateResources(self, constraints):
        """
        Allocate compute resources (base method)

        Override this in child classes to add license awareness
        """
        ips, alloc_resources = {}, []
        instances = self.resource_manager.getResources()
        count, instances = self.checkResources(instances, constraints['min_instances'])
        if count == constraints['min_instances']:
            ips, alloc_resources = self.resource_manager.allocateResources(instances)
        return ips, alloc_resources

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

    def sendWorkflowForExecution(self, wf_plan, ips, sim, deadline, license_holds=None):
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

        executor, on_demand_type = getExecutor(ips, sim)
        if on_demand_type:
            request['hosts']['on-demand'][on_demand_type] = (
                request['hosts']['on-demand'][on_demand_type][0],
                [executor]
            )

        if sim:
            sim.process(executeWorklow, request, sim)
        else:
            sendRequest(executor, getConfig('executor-incoming-port'), request)

    def processJobCompletion(self, sim=None, mb=None):
        """
        Process workflow completions and release BOTH compute and licenses
        """
        backend = backend_for(sim)
        print('Scheduler started listening to completed jobs...')
        while True:
            data = peekElement(mb, self.finish_queue)
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
                removeElement(mb, self.finish_queue)

            backend.sleep(60)

    def allocateNewResources(self, request, sim):
        """
        Allocate new resources for moldable workflows

        Base implementation (extended in child classes with license checks)
        """
        backend = backend_for(sim)
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

        self.sendNewResources(request['wf-id'], ips, alloc_resources, sim, request.get('client-ip', None))

    def sendNewResources(self, wf_id, ips, alloc_resources, sim, client_ip, license_holds=None):
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

        if sim:
            sim.process(processNewResources, new_req)
        else:
            sendRequest(client_ip, getConfig('executor-incoming-port'), new_req)

        if alloc_resources:
            self.resource_manager.updateWorkflowResources(wf_id, alloc_resources)
            # Metrics tracking now happens in fcfs_optimized_LA.py before calling sendNewResources()
            # self.metrics.updateResources(wf_id, alloc_resources, backend.now())  # OLD signature - removed

    def freeResources(self, request, sim):
        """
        Free resources (compute only in base class)

        Override in child classes to handle license freeing
        """
        backend = backend_for(sim)
        if backend.now() - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        # LA workflows always return 7-value tuples
        instances, _, deadline, _, _, software_id, license_holds = \
            self.resource_manager.getWorkflow(request['wf-id'])

        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        available_time = max(0, deadline - backend.now()) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        freed_count = 0
        to_free_instances = []

        if available_time > MIN_ITERATION_RUNTIME:
            # Free the last n instances (LIFO)
            for i in range(len(instances)-1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count']-freed_count, count)
                to_free_instances.append((instance, to_free, ips[-to_free:]))
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break

            self.resource_manager.returnResources(request['wf-id'], to_free_instances)

        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, sim, request.get('client-ip', None))

    def sendFreedResources(self, wf_id, to_free_instances, instances, response_instances, sim, client_ip):
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

        if sim:
            sim.process(processNewResources, new_req)
        else:
            sendRequest(client_ip, getConfig('executor-incoming-port'), new_req)

        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            # Metrics tracking now happens in fcfs_optimized_LA.py before calling freeResourcesWithLicenses()
            # self.metrics.updateResources(wf_id, to_free_instances, None, backend.now())  # OLD signature - removed

    def checkResources(self, instances: List[Instance], min_instances: int) -> tuple[int, List[tuple[Instance, int]]]:
        """
        Check compute resource availability

        Same as base scheduler
        """
        currently_acquired = 0
        acquired_instances = []

        for instance in instances:
            # Allocate on-prem only if it can be fully allocated
            if isinstance(instance, OnPremInstance):
                if instance.getFreeSlots() >= min_instances:
                    acquired_instances = [(instance, min_instances)]
                    return (min_instances, acquired_instances)
                else:
                    continue

            # Check if enough nodes are available
            to_be_used = min(min_instances-currently_acquired, instance.getFreeSlots())
            if to_be_used:
                currently_acquired += to_be_used
                acquired_instances.append((instance, to_be_used))
                if currently_acquired == min_instances:
                    break

        return (currently_acquired, acquired_instances)

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

    def purgeWorkflow(self, wf_plan, sim) -> bool:
        """
        Check if workflow should be purged

        Same as base scheduler
        """
        backend = backend_for(sim)
        runtime = MIN_ITERATION_RUNTIME + getEstimate(MIN_RUNTIME, 1 + wf_plan['constraints']['tinydaIterations'])
        if backend.now() + runtime > wf_plan['submit_time'] + wf_plan['constraints']['deadline']:
            print(f"Workflow {wf_plan['id']} can no longer be executed, discarding it at {backend.now()}")
            return True
        return False
