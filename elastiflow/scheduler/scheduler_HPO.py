import math
import time
from typing import List

from elastiflow.config.constants_HPO import AVG_WORKFLOW_ITERATIONS, COLD_START_TIME, MIN_INSTANCE_COST, MIN_ITERATION_RUNTIME, RESOURCE_REQUEST_TIMEOUT, SPEEDUP_THRESHOLD
from elastiflow.scripts.create_instance_HPO import createWorkerInstances, deleteInstanceFromIp
from elastiflow.scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from elastiflow.utils.metrics_HPO import MetricsHPO
from elastiflow.utils.resource import getEstimate
from elastiflow.resource_manager.instance import Instance, OnPremInstance
from elastiflow.utils import negotiation_log
from elastiflow.utils.request import ExecutorRequest, getConfig, sendRequest
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
    request_timeout = RESOURCE_REQUEST_TIMEOUT
    policy_label = ''           # 'EDF ' in the EDF policies: their log lines say so
    mode_label = ''             # 'Moldable ' in the elastic layer
    moldable_request = False    # the elastic layer flags its start requests

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

    def sendWorkflowForExecutionHPO(self, wf_plan, ips, backend, deadline):
        """
        HPO-specific workflow execution with dedicated executor design
        Routes to on-prem executor OR creates cloud executor based on worker allocation
        """
        # Determine executor based on worker allocation
        # If on-prem workers → use on-prem executor (manually started)
        # If cloud workers → create dedicated cloud executor

        # ips['on-prem'] is a dict: {'on-prem': (count, [ip_list])}
        on_prem_hosts = ips.get('on-prem', {})
        on_prem_ips = []
        for name, (count, ip_list) in on_prem_hosts.items():
            on_prem_ips.extend(ip_list)

        if on_prem_ips:
            executor_ip = on_prem_ips[0]  # Head node IP
            print(f"HPO {self.policy_label}{self.mode_label}Workflow {wf_plan['id']}: Using on-prem executor at {executor_ip}")
        else:
            # First allocated cloud IP acts as executor (reserved preferred)
            cloud_ips = []
            for name, (count, ip_list) in ips.get('reserved', {}).items():
                cloud_ips.extend(ip_list)
            for name, (count, ip_list) in ips.get('on-demand', {}).items():
                cloud_ips.extend(ip_list)
            executor_ip = cloud_ips[0]
            print(f"HPO {self.policy_label}{self.mode_label}Workflow {wf_plan['id']}: Using cloud executor at {executor_ip}")

        # Prepare request with separated executor and worker instances
        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,  # Worker instances only
            "executor-ip": executor_ip,  # Dedicated executor (on-prem or cloud)
            "deadline": deadline
        }

        print(f"  Workers: {ips}")

        if self.moldable_request:
            request["moldable"] = True  # Enable moldable features (the elastic layer)

        # Send to executor
        backend.start_workflow(request, executor_ip)


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

    def allocateResourcesHPO(self, constraints, backend=None):
        """
        HPO-specific resource allocation with resilient fallback
        Tries to allocate optimal number of hosts, degrades gracefully if unavailable
        """
        model = constraints['mesh']
        budget = constraints['budget']
        deadline_duration = constraints['deadline_duration']  # Duration in seconds (not absolute timestamp)
        trials = constraints['chains']
        epochs = constraints['tinydaIterations']

        # Get optimal instance type AND number of hosts
        optimal_type, num_hosts_requested = self.selectOptimalInstanceType(
            budget, deadline_duration, model, trials, epochs
        )

        instances = self.resource_manager.getResources()
        selected_instances = []
        remaining = num_hosts_requested

        # Check if on-prem can satisfy (any free slots of the right type)
        on_prem_available = False
        for instance in instances:
            if isinstance(instance, OnPremInstance) and \
               self.getInstanceTypeForHPO(instance.name) == optimal_type:
                if instance.getFreeSlots() > 0:
                    on_prem_available = True
                    break

        if on_prem_available:
            # ON-PREM PATH: only allocate on-prem (skip cloud entirely)
            for instance in instances:
                if isinstance(instance, OnPremInstance) and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = instance.getFreeSlots()
                    if slots_available >= remaining:
                        selected_instances = [(instance, remaining)]
                        remaining = 0
                        print(f"Allocated {num_hosts_requested} on-prem {instance.name} (workflow locked to on-prem)")
                        break
                    elif slots_available > 0:
                        selected_instances = [(instance, slots_available)]
                        remaining -= slots_available
                        print(f"Allocated {slots_available} on-prem {instance.name} (degraded from {num_hosts_requested})")
                        break
        else:
            # CLOUD PATH: reserved then on-demand (skip on-prem entirely)
            for instance in instances:
                if instance.type == 'reserved' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        selected_instances.append((instance, slots_available))
                        remaining -= slots_available
                        if remaining == 0:
                            break

            if remaining > 0:
                runtime_func = getRuntime_g5 if optimal_type == 'g5' else getRuntime_g4

                allocated_so_far = num_hosts_requested - remaining
                total_hosts = allocated_so_far + remaining

                if total_hosts >= trials:
                    workers_per_trial = total_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    batches = math.ceil(trials / total_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                for instance in instances:
                    if instance.type == 'on-demand' and \
                       self.getInstanceTypeForHPO(instance.name) == optimal_type:
                        slots_available = min(remaining, instance.getFreeSlots())
                        if slots_available > 0:
                            cost = (runtime / 3600) * instance.cost_per_second * slots_available
                            if cost < budget:
                                selected_instances.append((instance, slots_available))
                                remaining -= slots_available
                                print(f"Allocated {slots_available} on-demand {optimal_type} (cost: ${cost:.2f})")
                                if remaining == 0:
                                    break

        # RESILIENT: Always proceed with what we got (never fail)
        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"⚠️  Degraded allocation: requested {num_hosts_requested}, got {allocated_hosts} hosts")
            print(f"   Workflow will run with reduced parallelism (some trials sequential)")

        if allocated_hosts == 0:
            print(f"❌ Failed to allocate any hosts of type {optimal_type}")
            return None, None

        # Allocate and create instances
        ips, alloc_resources = self.resource_manager.allocateResources(selected_instances)
        ips = self.createOnDemandWorkers(ips, backend)

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts
        Returns: (instance_type, num_hosts)

        Considers ALL available instance types (on-prem, g4, g5) with actual costs
        Explores different host counts to find best cost/performance tradeoff
        No instance type switching - stays within one type for entire workflow
        """
        instances = self.resource_manager.getResources()

        # Build list of unique instance types to evaluate
        instance_types = {}
        for instance in instances:
            inst_type = self.getInstanceTypeForHPO(instance.name)
            if inst_type not in instance_types:
                # Store cheapest cost for this type (prioritize reserved/on-prem over on-demand)
                cost = instance.cost_per_second
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                instance_types[inst_type] = {
                    'cost_per_second': cost,
                    'runtime_func': runtime_func,
                    'name': instance.name
                }
            else:
                # Update if we found a cheaper instance of same type
                if instance.cost_per_second < instance_types[inst_type]['cost_per_second']:
                    instance_types[inst_type]['cost_per_second'] = instance.cost_per_second
                    instance_types[inst_type]['name'] = instance.name

        best_instance_type = None
        best_num_hosts = 1
        best_score = float('inf')
        best_cost = 0
        best_runtime = 0

        # Evaluate each instance type
        for inst_type, info in instance_types.items():
            runtime_func = info['runtime_func']
            cost_per_second = info['cost_per_second']

            # Try different num_hosts for this instance type
            for num_hosts in range(1, trials + 1):
                # Calculate actual runtime based on execution pattern
                if num_hosts >= trials:
                    # Parallel execution: each trial gets (num_hosts // trials) workers
                    workers_per_trial = num_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    # Sequential batches: some trials must wait
                    batches = math.ceil(trials / num_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                # Calculate cost for this configuration (cost_per_second is actually $/hour)
                cost = (runtime / 3600) * cost_per_second * num_hosts

                # Check if meets constraints
                if runtime <= deadline and cost <= budget:
                    # Score: minimize cost with slight preference for faster completion
                    score = cost + (runtime / deadline) * 0.1
                    if score < best_score:
                        best_score = score
                        best_num_hosts = num_hosts
                        best_instance_type = inst_type
                        best_cost = cost
                        best_runtime = runtime

        if best_instance_type is None:
            # No solution found within constraints - return cheapest option
            cheapest_type = min(instance_types.items(), key=lambda x: x[1]['cost_per_second'])[0]
            print(f"⚠️  No configuration meets constraints, defaulting to cheapest: 1 × {cheapest_type}")
            return cheapest_type, 1

        print(f"Selected: {best_num_hosts} × {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
        return (best_instance_type, best_num_hosts)


class Scheduler_HPO_Elastic(Scheduler_HPO):
    """The elastic HPO policies (Elastic-FCFS, Elastic-EDF): on-demand workers
    created and released between rounds, with the IP bookkeeping that needs
    (B7.2); admission, instance selection, scale-up and the executor messages
    (B7.3). The request loop stays per policy."""

    mode_label = 'Moldable '
    moldable_request = True

    def _runtimeFunctionFor(self, instance_type):
        """The runtime model checkNewResourcesHPO uses for a cloud scale-up of
        this instance type. The two elastic policies differ for on-prem
        workflows: Elastic-FCFS models them with g5, Elastic-EDF with g4; each
        states its own (B7.3)."""
        raise NotImplementedError

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

    def allocateResourcesMoldableHPO(self, constraints, backend=None):
        """
        Moldable HPO resource allocation with resilient fallback
        Same logic as static but prepares for moldable adjustments between rounds
        """
        model = constraints['mesh']
        budget = constraints['budget']
        deadline_duration = constraints['deadline_duration']  # Duration in seconds (not absolute timestamp)
        trials = constraints['chains']
        epochs = constraints['tinydaIterations']

        # Get optimal instance type AND number of hosts
        optimal_type, num_hosts_requested = self.selectOptimalInstanceType(
            budget, deadline_duration, model, trials, epochs
        )

        instances = self.resource_manager.getResources()
        selected_instances = []
        remaining = num_hosts_requested

        # Check if on-prem can satisfy (always prefer on-prem over cloud)
        # On-prem is checked regardless of optimal_type since it's local infrastructure
        on_prem_available = False
        for instance in instances:
            if isinstance(instance, OnPremInstance) and instance.getFreeSlots() > 0:
                on_prem_available = True
                break

        if on_prem_available:
            # ON-PREM PATH: only allocate on-prem (skip cloud entirely)
            # On-prem is always g4-type hardware, used regardless of optimizer's cloud choice
            for instance in instances:
                if isinstance(instance, OnPremInstance):
                    slots_available = instance.getFreeSlots()
                    if slots_available >= remaining:
                        selected_instances = [(instance, remaining)]
                        remaining = 0
                        print(f"{self.policy_label}Moldable: Allocated {num_hosts_requested} on-prem {instance.name}")
                        break
                    elif slots_available > 0:
                        selected_instances = [(instance, slots_available)]
                        remaining -= slots_available
                        print(f"{self.policy_label}Moldable: Partial on-prem {slots_available}/{num_hosts_requested}")
                        break
        else:
            # CLOUD PATH: try optimal_type first, fallback to alt_type if partial

            # --- Attempt 1: optimal_type (e.g. g4) reserved → on-demand ---
            g4_selected = []
            g4_remaining = num_hosts_requested

            for instance in instances:
                if instance.type == 'reserved' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(g4_remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        g4_selected.append((instance, slots_available))
                        g4_remaining -= slots_available
                        if g4_remaining == 0:
                            break

            if g4_remaining > 0:
                runtime_func = getRuntime_g5 if optimal_type == 'g5' else getRuntime_g4

                total_hosts = num_hosts_requested
                if total_hosts >= trials:
                    workers_per_trial = total_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    batches = math.ceil(trials / total_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                for instance in instances:
                    if instance.type == 'on-demand' and \
                       self.getInstanceTypeForHPO(instance.name) == optimal_type:
                        slots_available = min(g4_remaining, instance.getFreeSlots())
                        if slots_available > 0:
                            cost = (runtime / 3600) * instance.cost_per_second * slots_available
                            if cost < budget:
                                g4_selected.append((instance, slots_available))
                                g4_remaining -= slots_available
                                print(f"{self.policy_label}Moldable: {slots_available} on-demand {optimal_type} (${cost:.2f})")
                                if g4_remaining == 0:
                                    break

            g4_allocated = num_hosts_requested - g4_remaining

            # --- Attempt 2: alt_type — only if optimal couldn't fully satisfy ---
            if g4_remaining > 0:
                alt_type = 'g5' if optimal_type == 'g4' else 'g4'
                g5_selected = []
                g5_remaining = num_hosts_requested  # try for FULL request

                for instance in instances:
                    if instance.type == 'reserved' and \
                       self.getInstanceTypeForHPO(instance.name) == alt_type:
                        slots_available = min(g5_remaining, instance.getFreeSlots())
                        if slots_available > 0:
                            g5_selected.append((instance, slots_available))
                            g5_remaining -= slots_available
                            if g5_remaining == 0:
                                break

                if g5_remaining > 0:
                    alt_runtime_func = getRuntime_g5 if alt_type == 'g5' else getRuntime_g4

                    total_hosts = num_hosts_requested
                    if total_hosts >= trials:
                        workers_per_trial = total_hosts // trials
                        alt_runtime = alt_runtime_func(workers_per_trial, model, epochs)
                    else:
                        batches = math.ceil(trials / total_hosts)
                        alt_runtime = batches * alt_runtime_func(1, model, epochs)

                    for instance in instances:
                        if instance.type == 'on-demand' and \
                           self.getInstanceTypeForHPO(instance.name) == alt_type:
                            slots_available = min(g5_remaining, instance.getFreeSlots())
                            if slots_available > 0:
                                cost = (alt_runtime / 3600) * instance.cost_per_second * slots_available
                                if cost < budget:
                                    g5_selected.append((instance, slots_available))
                                    g5_remaining -= slots_available
                                    print(f"{self.policy_label}Moldable: {slots_available} on-demand {alt_type} (${cost:.2f})")
                                    if g5_remaining == 0:
                                        break

                g5_allocated = num_hosts_requested - g5_remaining

                # --- Pick winner: whichever type satisfies more hosts ---
                if g5_allocated > g4_allocated:
                    selected_instances = g5_selected
                    remaining = g5_remaining
                    print(f"{self.policy_label}Moldable: {optimal_type} partial ({g4_allocated}), using {alt_type} ({g5_allocated}/{num_hosts_requested})")
                else:
                    selected_instances = g4_selected
                    remaining = g4_remaining
                    if g4_allocated > 0:
                        print(f"{self.policy_label}Moldable: {optimal_type} partial ({g4_allocated}/{num_hosts_requested}), {alt_type} no better ({g5_allocated})")
            else:
                selected_instances = g4_selected
                remaining = 0

        # RESILIENT: Proceed with what we got
        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"{self.policy_label}Moldable degraded: requested {num_hosts_requested}, got {allocated_hosts}")

        if allocated_hosts == 0:
            print(f"{self.policy_label}Moldable: Failed to allocate any hosts (both types exhausted)")
            return None, None

        # Allocate and create instances
        ips, alloc_resources = self.resource_manager.allocateResources(selected_instances)
        ips = self.createOnDemandWorkers(ips, backend)
        self._syncOnDemandIPs(ips, alloc_resources)

        # Verify we have usable IPs (on-demand creation may have failed)
        total_ips = sum(
            len(ip_list)
            for category in ['on-prem', 'reserved', 'on-demand']
            for _, (_, ip_list) in ips.get(category, {}).items()
        )
        if total_ips == 0:
            print(f"{self.policy_label}Moldable: on-demand instance creation failed, returning resources")
            self.resource_manager.returnResources("_failed_alloc", alloc_resources)
            return None, None

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts (moldable version)
        Returns: (instance_type, num_hosts)

        Availability-aware: uses ACTUAL cost based on which slots are free.
        If reserved g4 is taken, g4 cost = on-demand price ($0.526/hr).
        If reserved g5 is free, g5 cost = reserved price ($0.435/hr).
        This ensures paid-for reserved capacity is used before on-demand.
        """
        instances = self.resource_manager.getResources()

        # Build availability info per instance type (cloud only):
        # On-prem is excluded here — allocateResourcesMoldableHPO handles on-prem-first
        # logic separately. Including on-prem's TCO cost ($0.84) would make the optimizer
        # pick cloud g5 ($0.435 reserved) over on-prem, which is wrong.
        type_slots = {}  # {type: [(cost_per_hour, free_slots), ...]}
        type_runtime = {}  # {type: runtime_func}
        for instance in instances:
            if isinstance(instance, OnPremInstance):
                continue  # Skip on-prem; handled by allocation logic
            inst_type = self.getInstanceTypeForHPO(instance.name)
            if inst_type not in type_slots:
                type_slots[inst_type] = []
                type_runtime[inst_type] = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
            free = instance.getFreeSlots()
            if free > 0:
                type_slots[inst_type].append((instance.cost_per_second, free))

        # Sort each type's slots by cost (cheapest first: on-prem/reserved before on-demand)
        for inst_type in type_slots:
            type_slots[inst_type].sort(key=lambda x: x[0])

        best_instance_type = None
        best_num_hosts = 1
        best_score = float('inf')
        best_cost = 0
        best_runtime = 0

        from elastiflow.config.constants_HPO import MOLDABLE_INITIAL_CAP
        max_initial = max(1, math.ceil(trials * MOLDABLE_INITIAL_CAP))

        # Evaluate each instance type
        for inst_type, slots in type_slots.items():
            runtime_func = type_runtime[inst_type]
            total_free = sum(free for _, free in slots)

            # When cap >= 1.0, pin initial allocation to chains (or pool max) so
            # moldable starts identical to static at iter 0. See edf_optimized_HPO.py
            # for full rationale.
            if MOLDABLE_INITIAL_CAP >= 1.0 and total_free >= 1:
                num_hosts_range = [min(max_initial, total_free)]
            else:
                num_hosts_range = range(1, min(max_initial, total_free) + 1)

            # Only try up to available free slots (can't allocate more than exists)
            for num_hosts in num_hosts_range:
                # Calculate runtime
                if num_hosts >= trials:
                    workers_per_trial = num_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    batches = math.ceil(trials / num_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                # Calculate ACTUAL cost: assign to cheapest free slots first
                hosts_remaining = num_hosts
                cost_rate = 0  # total $/hour across all assigned instances
                for slot_cost, slot_free in slots:
                    use = min(hosts_remaining, slot_free)
                    cost_rate += use * slot_cost  # cost_per_second is $/hour
                    hosts_remaining -= use
                    if hosts_remaining == 0:
                        break

                cost = (runtime / 3600) * cost_rate

                # Check if meets constraints
                if runtime <= deadline and cost <= budget:
                    score = cost + (runtime / deadline) * 0.1
                    if score < best_score:
                        best_score = score
                        best_num_hosts = num_hosts
                        best_instance_type = inst_type
                        best_cost = cost
                        best_runtime = runtime

        if best_instance_type is None:
            # No solution found within constraints - return cheapest option
            # Fall back to any type that has free slots
            for inst_type, slots in type_slots.items():
                if sum(free for _, free in slots) > 0:
                    print(f"⚠️  No configuration meets constraints, defaulting to: 1 × {inst_type}")
                    return inst_type, 1
            # Absolute fallback
            print(f"⚠️  No free slots available, defaulting to g4")
            return 'g4', 1

        print(f"{self.policy_label}Moldable selected: {best_num_hosts} × {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
        return (best_instance_type, best_num_hosts)

    def checkNewResourcesHPO(self, resources, current_resources, budget, available_runtime, request, model, instance_type_filter, backend):
        """
        Allocate additional resources for moldable HPO with cluster isolation
        - On-prem workflows scale ONLY within on-prem
        - Cloud workflows scale ONLY within cloud (reserved + on-demand)
        - NO cross-cluster migration
        """
        if budget < MIN_INSTANCE_COST:
            return []

        # Determine runtime function
        runtime_func = self._runtimeFunctionFor(instance_type_filter)   # the policy's choice for on-prem (B7.3)

        # Check current cluster
        instance = current_resources[0][0]

        # CASE 1: On-prem workflow - scale ONLY within on-prem
        if isinstance(instance, OnPremInstance):
            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = (runtime / 3600) * instance.cost_per_second
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / cost_per_instance))

            if to_be_used > 0:
                print(f"{self.policy_label}Moldable: Scaling on-prem workflow, adding {to_be_used} on-prem instances")
                return [(instance, to_be_used)]
            else:
                print(f"{self.policy_label}Moldable: On-prem workflow cannot scale (no free on-prem slots or budget)")
                return []

        # CASE 2: Cloud workflow - scale ONLY within cloud (reserved + on-demand)
        # Filter out on-prem instances (cluster isolation!)
        available_instances = []
        for inst in resources:
            if (not isinstance(inst, OnPremInstance) and
                inst.name == instance_type_filter and
                inst.getFreeSlots() > 0):

                # Priority: reserved first, on-demand second (cost-based)
                priority = 0 if inst.type == 'reserved' else 1
                available_instances.append((priority, inst))

        # Sort by priority (reserved before on-demand)
        available_instances.sort(key=lambda x: x[0])

        # Allocate resources within budget
        acquired_count = 0
        acquired_instances = []
        trials_needed = request['count']

        for priority, inst in available_instances:
            if acquired_count >= trials_needed or budget < MIN_INSTANCE_COST:
                break

            # Calculate cost (cost_per_second is actually $/hour)
            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = (runtime / 3600) * inst.cost_per_second

            # Add cold start for on-demand
            if inst.type == 'on-demand':
                cost_per_instance += (COLD_START_TIME / 3600) * inst.cost_per_second

            # Check speedup justification
            current_trials = request['chains'] - request['count'] + acquired_count
            new_trials = current_trials + 1

            current_runtime = runtime * (request['chains'] / max(current_trials, 1))
            new_runtime = runtime * (request['chains'] / new_trials)
            speedup = current_runtime / new_runtime if new_runtime > 0 else 0

            # Allocate if justified
            if (cost_per_instance < budget and
                speedup > SPEEDUP_THRESHOLD and
                new_runtime < available_runtime):

                to_be_used = min(trials_needed - acquired_count, inst.getFreeSlots())
                acquired_instances.append((inst, to_be_used))
                acquired_count += to_be_used
                budget -= to_be_used * cost_per_instance

        if not acquired_instances:
            wf_id = request.get('wf-id', '?')
            if not available_instances:
                print(f"{self.policy_label}Moldable: Cloud workflow {wf_id} cannot scale (no free {instance_type_filter} slots)")
            elif budget < MIN_INSTANCE_COST:
                print(f"{self.policy_label}Moldable: Cloud workflow {wf_id} cannot scale (budget exhausted, ${budget:.4f} remaining)")
            elif available_runtime <= 0:
                print(f"{self.policy_label}Moldable: Cloud workflow {wf_id} cannot scale (deadline exhausted)")
            else:
                print(f"{self.policy_label}Moldable: Cloud workflow {wf_id} cannot scale (speedup not justified, budget=${budget:.4f}, time={available_runtime:.0f}s)")

        return acquired_instances

    def sendNewResources(self, wf_id, ips, alloc_resources, backend, client_ip, iter_idx=None):
        """Send new resources to executor.
        If send fails (executor dead/unreachable), terminate any on-demand instances
        that were just created to prevent leaks."""
        new_req = {
            "request": ExecutorRequest.REQUEST_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": ips,
        }
        print(f"{wf_id} {self.policy_label}allocated additional resources: ", ips)
        send_ok = True
        send_ok = backend.notify_resources(new_req, client_ip)
        negotiation_log.log('scheduler',
                            wf_id=wf_id, iter_idx=iter_idx if iter_idx is not None else '',
                            request_type='grow',
                            granted_count=negotiation_log.count_hosts(ips),
                            t_scheduler_reply_sent=time.time())

        if not send_ok and not backend.simulated:
            # Executor is dead — terminate any on-demand instances we just created
            leaked_ips = []
            for name, val in ips.get('on-demand', {}).items():
                if isinstance(val, (tuple, list)) and len(val) >= 2:
                    leaked_ips.extend(val[1])
            if leaked_ips:
                print(f"[CLEANUP] Send to executor failed for {wf_id}, terminating on-demand scale-up instances: {leaked_ips}")
                try:
                    deleteInstanceFromIp(leaked_ips, backend)
                except Exception as e:
                    print(f"[CLEANUP] Failed to terminate leaked scale-up instances: {e}")
            # Return allocated slots to resource manager
            if alloc_resources:
                self.resource_manager.returnResources(wf_id + "_send_failed", alloc_resources)
            return

        if alloc_resources:
            self.resource_manager.updateWorkflowResources(wf_id, alloc_resources)
            self.metrics.updateResources(wf_id, alloc_resources, backend.now())

    def sendFreedResources(self, wf_id, to_free_instances, instances, response_instances, backend, client_ip, iter_idx=None):
        """Send freed resources notification to executor"""
        new_req = {
            "request": ExecutorRequest.FREE_RESOURCE.value,
            "initial-alloc": False,
            "wf-id": wf_id,
            "hosts": response_instances
        }
        print(f"{self.policy_label}Scheduler freeing {response_instances} for {wf_id} ")
        backend.notify_resources(new_req, client_ip)
        negotiation_log.log('scheduler',
                            wf_id=wf_id, iter_idx=iter_idx if iter_idx is not None else '',
                            request_type='shrink',
                            granted_count=negotiation_log.count_hosts(response_instances),
                            t_scheduler_reply_sent=time.time())
        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            self.metrics.updateResources(wf_id, to_free_instances, None, backend.now())
