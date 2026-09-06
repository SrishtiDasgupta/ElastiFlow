import time
from typing import List

from elastiflow.config.constants_HPO import AVG_WORKFLOW_ITERATIONS, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, RESOURCE_REQUEST_TIMEOUT
from elastiflow.scripts.create_instance_HPO import createWorkerInstances, deleteInstanceFromIp
from elastiflow.scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from elastiflow.utils.metrics_HPO import MetricsHPO
from elastiflow.utils.resource import getEstimate
from elastiflow.resource_manager.instance import Instance, OnPremInstance
from elastiflow.utils.request import getConfig, sendRequest
from elastiflow.scheduler.scheduler import Scheduler

class Scheduler_HPO(Scheduler):
    """
    HPO-specific base scheduler class
    Uses HPO constants, executor, and runtime functions

    Since B7.1 a subclass of `Scheduler`: the constructor, run,
    allocateResources, sendWorkflowForExecution, sendNewResources,
    sendFreedResources, checkResources and purgeWorkflow are inherited
    (the HPO log lines keep their "HPO " prefix through `log_prefix`); the
    HPO layer overrides completion, negotiation and on-demand termination,
    which bind HPO's own timeout, cost floor and provisioning module.
    """

    metrics_class = MetricsHPO
    log_prefix = 'HPO '

    def processJobCompletion(self, backend):
        print('HPO Scheduler started listening to completed jobs...')
        while True:
            try:
                data = backend.completions.peek()
                if data:
                    data = eval(data)
                    wf_id = data.get('wf-id')

                    # Guard against duplicate completion messages (e.g., TCP retry).
                    # Without this, returnResources().pop() on an already-completed
                    # workflow would KeyError and crash this thread permanently.
                    if not self.resource_manager.getWorkflow(wf_id):
                        print(f'[WARN] Duplicate completion for {wf_id}, ignoring')
                        backend.completions.pop()
                        backend.sleep(1)
                        continue

                    # Safety-net: terminate on-demand instances from scheduler side.
                    # The executor's finally block should have done this already,
                    # but if the executor crashed or never reached cleanup, this
                    # ensures on-demand instances don't run forever.
                    self._terminate_ondemand_instances(wf_id, data.get('hosts'), backend)

                    self.resource_manager.returnResources(wf_id)
                    self.metrics.updateDataframe(wf_id, {'exec_start_time': data.get('start-time'), 'finish_time': data.get('finish-time'), 'complete': data.get('complete')})
                    print(f'{wf_id} workflow freed at {backend.now()}')
                    with open('workflow_status.log', 'a') as f:
                        f.write(f'{wf_id} COMPLETED at {backend.now()}\n')
                    backend.completions.pop()
            except Exception as e:
                print(f'[ERROR] processJobCompletion exception: {e}')
                import traceback
                traceback.print_exc()
                # Don't crash the thread — skip this message and continue
                try:
                    backend.completions.pop()
                except Exception:
                    pass
            backend.sleep(60) # NOTE: polling interval

    def _terminate_ondemand_instances(self, wf_id, hosts, backend):
        """
        Safety-net termination of on-demand instances.
        Called from processJobCompletion before returnResources pops the workflow.
        Uses hosts dict from the completion message; falls back to stored workflow data.
        Idempotent: if executor already terminated them, deleteInstanceFromIp finds nothing.
        """
        if backend.simulated:
            return

        # Collect on-demand IPs from completion message hosts
        ondemand_ips = []
        if hosts:
            for name, val in hosts.get('on-demand', {}).items():
                if isinstance(val, (tuple, list)) and len(val) >= 2:
                    ondemand_ips.extend(val[1])

        # Fallback: extract from stored workflow data (before returnResources pops it)
        if not ondemand_ips:
            wf_data = self.resource_manager.getWorkflow(wf_id)
            if wf_data:
                instances = wf_data[0]  # [(instance_obj, count, [ips])]
                for instance, count, ips in instances:
                    if instance.type == 'on-demand' and ips:
                        ondemand_ips.extend(ips)

        if ondemand_ips:
            print(f"[SAFETY-NET] Terminating on-demand instances for {wf_id}: {ondemand_ips}")
            try:
                deleteInstanceFromIp(ondemand_ips, backend)
            except Exception as e:
                print(f"[ERROR] Safety-net termination failed for {wf_id}: {e}")

    # request = {"wf-id", "count", "iteration": ind, "tinyda-iterations", "client-ip", "request-time"}
    def allocateNewResources(self, request, backend):
        if backend.now() - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return

        (instances, budget, _, start_time, model) = self.resource_manager.getWorkflow(request['wf-id'])
        free_resources = self.resource_manager.getResources()
        used_budget = self.metrics.computeCost(request['wf-id'], backend.now())
        available_budget = max(0, budget - used_budget) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        alloc_instances = self.checkNewResources(free_resources, instances, available_budget, request, model) # {obj: count}
        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances) # alloc_resource = {obj: (count, [ips])}
        self.sendNewResources(request['wf-id'], ips, alloc_resources, backend, request.get('client-ip', None))

    # request = {"wf-id", "count", "request-time", "client-ip"}
    def freeResources(self, request, backend):
        if backend.now() - request['request-time'] > RESOURCE_REQUEST_TIMEOUT:
            return
        (instances, _, deadline, _, _) = self.resource_manager.getWorkflow(request['wf-id'])

        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        # Only free resources if next iteration can happen in available time
        available_time = max(0, deadline - backend.now()) / max((AVG_WORKFLOW_ITERATIONS - request['iteration']), 1)
        freed_count = 0
        to_free_instances = []
        if available_time > MIN_ITERATION_RUNTIME:
            # Free the last n instances
            for i in range(len(instances)-1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count']-freed_count, count)
                to_free_instances.append((instance, to_free, ips[-to_free:])) # last n vals
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break
            self.resource_manager.returnResources(request['wf-id'], to_free_instances)

            # Terminate freed on-demand EC2 instances immediately (don't wait for workflow end)
            if not backend.simulated:
                for instance, count, ips in to_free_instances:
                    if instance.type == 'on-demand' and ips:
                        print(f"[SCALE-DOWN] Terminating {len(ips)} freed on-demand instances: {ips}")
                        try:
                            deleteInstanceFromIp(ips, backend)
                        except Exception as e:
                            print(f"[ERROR] Scale-down termination failed: {e}")

        # Free resources
        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, backend, request.get('client-ip', None))

    # request = {"wf-id": wf_id, "count": n, "iteration": ind, "tinyda-iterations": m}
    # current_resources = {obj: (count, ip)}
    def checkNewResources(self, resources: List[Instance], current_resources: List[tuple[Instance, int, List]], budget: float, request, model) -> List[tuple[Instance, int]]:
        """
        HPO version: uses model parameter instead of mesh
        Uses HPO-specific runtime functions
        """
        if budget < MIN_INSTANCE_COST:
            return []

        # 1. If on-prem is already allocated, allocate possible on-prem instances
        instance = current_resources[0][0]
        if isinstance(instance, OnPremInstance):
            # Use HPO runtime function - assume g5 for on-prem
            runtime = getRuntime_g5(1, model, request['tinyda-iterations'])
            cost_per_iteration = (runtime / 3600) * instance.cost_per_second  # cost_per_second is $/hour
            instance_cost = getEstimate(cost_per_iteration, 1)  # Already includes tinyda-iterations in runtime
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / instance_cost))
            return [(instance, to_be_used)]

        acquired_count = 0
        acquired_instances = []

        # 2. Allocate possible reserved/on-demand if not case 1
        instances = list(filter(lambda x: not isinstance(x, OnPremInstance), resources))
        for instance in instances:
            # Determine instance type and use appropriate runtime function
            if 'g5' in instance.name.lower():
                runtime = getRuntime_g5(1, model, request['tinyda-iterations'])
            else:  # g4dn
                runtime = getRuntime_g4(1, model, request['tinyda-iterations'])

            cost_per_iteration = (runtime / 3600) * instance.cost_per_second  # cost_per_second is $/hour
            instance_cost = getEstimate(cost_per_iteration, 1)  # Already includes tinyda-iterations
            to_be_used = min(request['count']-acquired_count, instance.getFreeSlots(), int(budget/instance_cost))

            if to_be_used:
                acquired_count += to_be_used
                budget -= to_be_used * instance_cost
                acquired_instances.append((instance, to_be_used))

                if acquired_count == request['count'] or budget < MIN_INSTANCE_COST:
                    return acquired_instances

        # 3. Else, do not allocate

        return acquired_instances

    # Purge and return if workflow has been purged
    def getInstanceTypeForHPO(self, instance_name):
        """Map instance names to HPO instance types"""
        if 'g4dn' in instance_name:
            return 'g4'
        elif 'g5' in instance_name:
            return 'g5'
        elif 'on-prem' in instance_name:
            return 'g4'  # On-prem uses g4dn.xlarge instances
        else:
            return 'unknown'


# ---------------------------------------------------------------------------
# Module-level helpers for instance classification.
#
# getFamily()      : GPU-architecture compatibility key. Two instances share a
#                    family iff they carry the same GPU and may participate in
#                    the same binding under a future Policy B bracket.
# getInstanceKey() : Full inventory key (== Instance.name). Selection scoring
#                    and resize lock both operate on this key, so multiple
#                    sizes within a family are kept distinct end-to-end.
#
# Both are pure functions of the instance name and have no callers yet that
# change behaviour — they exist so the registry in
# scripts/speedup_HPO_runtime.py and the deferred bracket predicate in
# docs/history/POLICY_B_TODO.md can be wired in additively.
# ---------------------------------------------------------------------------
def getFamily(instance_name: str) -> str:
    if 'g4dn' in instance_name or 'on-prem' in instance_name:
        return 'g4'
    if 'g5' in instance_name:
        return 'g5'
    return 'unknown'


def getInstanceKey(instance_name: str) -> str:
    return instance_name

class Scheduler_HPO_Static(Scheduler_HPO):
    """The static HPO policies (FCFS-ST, EDF-ST): one allocation at admission,
    on-demand workers created once (B7.2)."""

    def createOnDemandWorkers(self, ips, backend):
        """
        Create actual on-demand worker instances for allocated virtual slots
        ResourceManager.allocateResources() returns empty IP lists for on-demand,
        this method creates the actual instances and updates the IP lists.
        Multiple instance types are created in parallel.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        to_create = {itype: count for itype, (count, ip_list) in ips.get('on-demand', {}).items()
                     if count > 0 and len(ip_list) == 0}

        if not to_create:
            return ips

        def _create(instance_type, count):
            print(f"Creating {count} on-demand {instance_type} worker instances...")
            worker_ips = createWorkerInstances(instance_type, count, backend)
            print(f"Created {count} on-demand {instance_type} workers: {worker_ips}")
            return instance_type, count, worker_ips

        with ThreadPoolExecutor(max_workers=len(to_create)) as pool:
            futures = [pool.submit(_create, itype, cnt) for itype, cnt in to_create.items()]
            for future in as_completed(futures):
                instance_type, count, worker_ips = future.result()
                ips['on-demand'][instance_type] = (count, worker_ips)

        return ips


class Scheduler_HPO_Elastic(Scheduler_HPO):
    """The elastic HPO policies (Elastic-FCFS, Elastic-EDF): on-demand workers
    created and released between rounds, with the IP bookkeeping that needs
    (B7.2). Admission, scale-up and the request loop stay per policy."""

    def _syncOnDemandIPs(self, ips, alloc_resources):
        """Sync actual on-demand IPs from ips dict back into alloc_resources list.
        After createOnDemandWorkers(), ips has real EC2 IPs but alloc_resources
        still has empty lists. Syncs BOTH count and IPs to match actual creation
        (partial creation may return fewer instances than requested)."""
        for i, (instance, count, ip_list) in enumerate(alloc_resources):
            if instance.type == 'on-demand' and len(ip_list) == 0:
                actual_ips = ips.get('on-demand', {}).get(instance.name, (0, []))[1]
                actual_count = len(actual_ips)
                # Release over-reserved slots if partial creation
                if actual_count < count:
                    over_reserved = count - actual_count
                    instance.freeResources(over_reserved, [])
                    print(f"[SYNC] Released {over_reserved} phantom on-demand slots for {instance.name}")
                alloc_resources[i] = (instance, actual_count, list(actual_ips))
        return alloc_resources

    def createOnDemandWorkers(self, ips, backend):
        """Create actual on-demand worker instances for allocated virtual slots.
        Multiple instance types are created in parallel.
        Terminates any successfully created instances if other threads fail.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        to_create = {itype: count for itype, (count, ip_list) in ips.get('on-demand', {}).items()
                     if count > 0 and len(ip_list) == 0}

        if not to_create:
            return ips

        def _create(instance_type, count):
            print(f"Creating {count} on-demand {instance_type} worker instances...")
            worker_ips = createWorkerInstances(instance_type, count, backend)
            print(f"Created {count} on-demand {instance_type} workers: {worker_ips}")
            return instance_type, count, worker_ips

        created_ips = []  # Track all created IPs for rollback on failure
        try:
            with ThreadPoolExecutor(max_workers=len(to_create)) as pool:
                futures = [pool.submit(_create, itype, cnt) for itype, cnt in to_create.items()]
                for future in as_completed(futures):
                    instance_type, count, worker_ips = future.result()
                    created_ips.extend(worker_ips)
                    ips['on-demand'][instance_type] = (count, worker_ips)
        except Exception as e:
            print(f"[ERROR] On-demand instance creation failed: {e}")
            # Terminate any instances that were successfully created
            if created_ips and not backend.simulated:
                print(f"[CLEANUP] Rolling back {len(created_ips)} successfully created instances: {created_ips}")
                try:
                    deleteInstanceFromIp(created_ips, backend)
                except Exception as cleanup_err:
                    print(f"[CLEANUP] Rollback termination failed: {cleanup_err}")
            # Zero out all on-demand IPs so caller sees creation failed
            for itype in to_create:
                ips['on-demand'][itype] = (ips['on-demand'][itype][0], [])

        return ips

    def freeResources(self, instances, request, backend):
        """
        Free excess resources when deadline allows
        Uses LIFO strategy: frees most recently allocated instances first
        (typically on-demand instances allocated last)
        """
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []

        if request['count'] > 0:
            # LIFO: Iterate from END of instances list (most recently allocated)
            for i in range(len(instances) - 1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count'] - freed_count, count)
                # Free last IPs from this instance (LIFO within instance)
                to_free_instances.append((instance, to_free, ips[-to_free:]))
                instances[i] = (instance, count - to_free, ips[:-to_free])
                response_instances[instance.type][instance.name] = (to_free, ips[-to_free:])
                freed_count += to_free
                if freed_count == request['count']:
                    break

            self.resource_manager.returnResources(request['wf-id'], to_free_instances)

            # Record scale-down metrics
            cores_freed = sum(inst.cores * count for inst, count, _ in to_free_instances)
            self.metrics.recordScaleDownAttempt(
                success=True,
                instances_removed=freed_count,
                cores_removed=cores_freed
            )

        # Notify executor FIRST so it stops using freed IPs immediately.
        # Instance termination happens AFTER to avoid blocking the response
        # (termination can take 5+ minutes, exceeding executor's 210s timeout).
        self.sendFreedResources(request['wf-id'], to_free_instances, instances, response_instances, backend, request.get('client-ip', None), iter_idx=request.get('iteration'))

        # Terminate freed on-demand EC2 instances AFTER notifying executor
        if request['count'] > 0 and not backend.simulated:
            for instance, count, ips in to_free_instances:
                if instance.type == 'on-demand' and ips:
                    print(f"[SCALE-DOWN] Terminating {len(ips)} freed on-demand instances: {ips}")
                    try:
                        deleteInstanceFromIp(ips, backend)
                    except Exception as e:
                        print(f"[ERROR] Scale-down termination failed: {e}")

    def getHPOInstanceCost(self, instance):
        """Get cost per hour for HPO instances (from resources YAML)"""
        return instance.cost_per_second  # $/hour from YAML (field is misnamed)
