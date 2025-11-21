"""
License-Aware Scheduler Base Class

Extends the standard scheduler with license management capabilities.
Integrates with the existing license manager for dual-resource (compute + license) scheduling.
"""

from abc import ABC, abstractmethod
import time
from typing import List, Tuple, Optional

from config.constants import AVG_WORKFLOW_ITERATIONS, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, MIN_RUNTIME, RESOURCE_REQUEST_TIMEOUT
from executor import executeWorklow, processNewResources
from scripts.speedup import getRuntime
from utils.metrics_LA import MetricsLA as Metrics
from utils.resource import getEstimate
from resource_manager.instance import Instance, OnPremInstance
from resource_manager.license.manager import LicenseManager
from resource_manager.license.exceptions import InsufficientTokens, LicenseError
from utils.request import ExecutorRequest, getConfig, getExecutor, sendRequest
from utils.sim import getTime, peekElement, removeElement


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
            hold_id = self.license_manager.hold(
                pool=license_pool,
                amount=licenses_needed,
                owner=f"wf-{constraints.get('wf_id', 'unknown')}",
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

                # Release compute resources
                self.resource_manager.returnResources(wf_id)

                # Release licenses (NEW)
                self.releaseLicensesForWorkflow(wf_id)

                self.metrics.updateDataframe(
                    wf_id,
                    {
                        'exec_start_time': data.get('start-time'),
                        'finish_time': data.get('finish-time'),
                        'complete': data.get('complete')
                    }
                )
                print(f'{wf_id} workflow freed at {getTime(sim)}')
                removeElement(mb, self.finish_queue)

            (sim or time).sleep(60)

    def allocateNewResources(self, request, sim):
        """
        Allocate new resources for moldable workflows

        Base implementation (extended in child classes with license checks)
        """
        if getTime(sim) - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        # LA workflows always return 7-value tuples
        instances, budget, _, start_time, mesh, software_id, license_holds = \
            self.resource_manager.getWorkflow(request['wf-id'])

        free_resources = self.resource_manager.getResources()
        used_budget = self.metrics.computeCost(request['wf-id'], getTime(sim))
        available_budget = max(0, budget - used_budget) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)

        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, mesh)
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
            # self.metrics.updateResources(wf_id, alloc_resources, getTime(sim))  # OLD signature - removed

    def freeResources(self, request, sim):
        """
        Free resources (compute only in base class)

        Override in child classes to handle license freeing
        """
        if getTime(sim) - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        # LA workflows always return 7-value tuples
        instances, _, deadline, _, _, software_id, license_holds = \
            self.resource_manager.getWorkflow(request['wf-id'])

        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        available_time = max(0, deadline - getTime(sim)) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
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
            # self.metrics.updateResources(wf_id, to_free_instances, None, getTime(sim))  # OLD signature - removed

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

    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, request, mesh) -> List[tuple[Instance, int]]:
        """
        Check new resource availability for moldable scaling

        Same as base scheduler (override in child classes for license checks)
        """
        if budget < MIN_INSTANCE_COST:
            return []

        # 1. If on-prem is already allocated, allocate possible on-prem instances
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            cost_per_iteration = instance.cost_per_second * getRuntime(1, mesh, instance.name)
            instance_cost = getEstimate(cost_per_iteration, request['tinyda-iterations'])
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / instance_cost))
            return [(instance, to_be_used)]

        acquired_count = 0
        acquired_instances = []

        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        for instance in instances:
            cost_per_iteration = instance.cost_per_second * getRuntime(1, mesh, instance.name)
            instance_cost = getEstimate(cost_per_iteration, request['tinyda-iterations'], 1)
            to_be_used = min(request['count']-acquired_count, instance.getFreeSlots(), int(budget/instance_cost))

            if to_be_used:
                acquired_count += to_be_used
                budget -= to_be_used * instance_cost
                acquired_instances.append((instance, to_be_used))

                if acquired_count == request['count'] or budget < MIN_INSTANCE_COST:
                    return acquired_instances

        return acquired_instances

    def purgeWorkflow(self, wf_plan, sim) -> bool:
        """
        Check if workflow should be purged

        Same as base scheduler
        """
        runtime = MIN_ITERATION_RUNTIME + getEstimate(MIN_RUNTIME, 1 + wf_plan['constraints']['tinydaIterations'])
        if getTime(sim) + runtime > wf_plan['submit_time'] + wf_plan['constraints']['deadline']:
            print(f"Workflow {wf_plan['id']} can no longer be executed, discarding it at {getTime(sim)}")
            return True
        return False
