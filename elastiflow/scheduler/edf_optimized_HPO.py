"""
EDF_Optimized_HPO: Moldable EDF Scheduler for HPO Workflows

Combines EDF (Earliest Deadline First) queue ordering with moldable
resource allocation from fcfs_optimized_HPO.py. Adds deadline-urgency-based
graduated scaling from the LA EDF pattern.

Key features:
- EDF heap ordering for deadline-based priority
- Moldable resource reallocation between HPO iterations
- Deadline urgency boost: CRITICAL (<30%) -> 2.0x, WARNING (<50%) -> 1.5x, Regular -> 1.2x
- Cluster isolation (on-prem vs cloud, no cross-cluster migration)
- Instance type locking (no g5<->g4 switching within workflow)
"""

import heapq
from collections import deque
import math
import threading
import time
from typing import List

from elastiflow.config.constants_HPO import (COLD_START_TIME, WORKFLOW_POLLING, MIN_TRIALS, MAX_TRIALS,
                                  DEADLINE_BUFFER, MIN_INSTANCE_COST, SPEEDUP_THRESHOLD,
                                  OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR)
from elastiflow.scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from elastiflow.scripts.create_instance_HPO import createWorkerInstances, deleteInstanceFromIp
from elastiflow.resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
import os
from elastiflow.resource_manager.resource_manager import ResourceManager

_HPO_RESOURCES_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from elastiflow.utils.resource import getConstraintsFromWorkflow, getEstimate
from elastiflow.utils.request import ExecutorRequest, sendRequest, getConfig
from elastiflow.utils import negotiation_log
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO


class EDF_Optimized_HPO(Scheduler_HPO):
    """
    Moldable EDF scheduler for HPO workflows.

    EDF ordering + moldable resource reallocation + deadline urgency boost.
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_trial',
                 resource_config=None, file_prefix=None):
        self.resource_manager = ResourceManager(resource_config or _HPO_RESOURCES_DEFAULT)
        self.file_prefix = file_prefix or 'EDF_Moldable_HPO_'
        func = lambda x: self.getHPOInstanceCost(x) + (COLD_START_TIME * 0.001 if isinstance(x, CloudOnDemandInstance) else 0)
        self.resource_manager.sortResourcesByFunction(func)

        # EDF heap setup
        self.workflow_heap = []
        self.resource_request_heap = []
        self.workflow_counter = 0
        self.resource_request_counter = 0
        self._heap_log_counter = 0   # for periodic [HEAP] visibility log

        super().__init__(queue, finish_queue, resource_request_queue)

    def getHPOInstanceCost(self, instance):
        """Get cost per hour for HPO instances (from resources YAML)"""
        return instance.cost_per_second  # $/hour from YAML (field is misnamed)

    def run(self, backend):
        print(f'Starting HPO Moldable EDF scheduler...')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')
        print(f'  - Moldable: Dynamic resource reallocation between iterations')
        print(f'  - Deadline urgency boost: CRITICAL 2.0x, WARNING 1.5x, Regular 1.2x')

        # Start a thread to periodically compute resource utilization
        backend.spawn(self.metrics.collectResourceUtilization, backend, self.resource_manager)

        while True:

            # === PHASE 1: Process resource requests sorted by deadline ===
            resource_requests = backend.resource_requests.pop_many(None)

            if resource_requests:
                t_obs = time.time()
                for _raw in resource_requests:
                    try:
                        _rd = eval(_raw)
                        _rt_label = 'grow' if _rd.get('request') == ExecutorRequest.REQUEST_RESOURCE.value else 'shrink'
                        negotiation_log.log('scheduler',
                                            wf_id=_rd.get('wf-id', '?'),
                                            iter_idx=_rd.get('iteration', ''),
                                            request_type=_rt_label,
                                            t_scheduler_request_observed=t_obs)
                    except Exception as _e:
                        print(f'[negotiation_log] obs parse fail: {_e}')
                self.processResourceRequestsByDeadline(resource_requests)

            resource_request = self.peekWorkflow(self.resource_request_heap)

            if resource_request:
                start = time.time()
                if backend.now() - resource_request['request-time'] > 300:  # 5 min timeout
                    self.popWorkflow(self.resource_request_heap)
                    # NOTE: do NOT pop from resource_request_queue. getAllElements
                    # already drained it when we heap-pushed (same data5-bug pattern).
                    continue
                self.processMoldableRequestHPO(resource_request, backend)
                print(f"  HPO EDF Moldable resource processing overhead: {time.time() - start}")
                self.popWorkflow(self.resource_request_heap)
                # NOTE: see above — queue was already drained, do not LPOP here.
                continue

            # === PHASE 2: Schedule new workflows in EDF order ===
            workflows = backend.workflows.pop_many(None)

            if workflows:
                # Defensive log: which wfs just left the queue?
                try:
                    drained_ids = [eval(w).get('id', '?') for w in workflows]
                except Exception as e:
                    drained_ids = [f'<eval-error: {e}>']
                print(f"[SCHED] Drained {len(workflows)} wf(s) from queue: {drained_ids}")
                self.processWorkflowsByDeadline(workflows)

            wf_plan = self.peekWorkflow(self.workflow_heap)

            if wf_plan:

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    self.popWorkflow(self.workflow_heap)
                    # NOTE: do NOT pop from wf_queue here. getAllElements already drained
                    # the wf out of the queue when we heap-pushed it. A trailing
                    # queue.pop() here LPOPs whatever happens to be at the head right
                    # now — which, if a new wf was pushed during a slow allocation,
                    # silently discards that new wf (the data5-disappears bug from R3/R7).
                    self.metrics.computeMetrics(file_prefix=self.file_prefix)
                    break

                # Moldable scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesMoldableHPO(constraints, backend)
                    print(f"{wf_plan['id']} EDF moldable allocation (deadline={constraints['deadline']:.1f}s): ", ips)

                    if ips:
                        self.popWorkflow(self.workflow_heap)
                        # NOTE: see END branch above — wf_queue.pop() here would
                        # silently discard any newly-arrived wf. The wf was already
                        # drained from the queue when it landed in the heap.
                        start_time = backend.now()
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, backend, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO EDF moldable resources to allocate, waiting...')

            # Periodic heap visibility (~ every 60s assuming WORKFLOW_POLLING=5s)
            self._heap_log_counter += 1
            if self._heap_log_counter >= 12:
                heap_ids = [entry[2] for entry in self.workflow_heap]
                print(f"[HEAP] size={len(self.workflow_heap)} ids={heap_ids}")
                self._heap_log_counter = 0

            backend.sleep(WORKFLOW_POLLING)

    # =========================================================================
    # EDF HEAP MANAGEMENT
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
        """Sort resource requests by deadline (EDF ordering) — urgent workflows get resources first"""
        for req in requests:
            req_dict = eval(req)
            # Get workflow deadline for ordering
            try:
                wf = self.resource_manager.getWorkflow(req_dict['wf-id'])
                deadline = wf[2] if wf else float('inf')
            except Exception:
                deadline = float('inf')

            heapq.heappush(self.resource_request_heap, (deadline, self.resource_request_counter, req_dict['wf-id'], req_dict))
            self.resource_request_counter += 1

    def peekWorkflow(self, heap):
        """Peek at top of heap without removing"""
        return heap and heap[0][3]

    def popWorkflow(self, heap):
        """Remove top of heap"""
        try:
            heapq.heappop(heap)
        except Exception as e:
            print(f'HEAP POP ERROR: {e}')
            print(heap)

    # =========================================================================
    # HPO MOLDABLE RESOURCE ALLOCATION (from fcfs_optimized_HPO.py)
    # =========================================================================

    def allocateResourcesMoldableHPO(self, constraints, backend=None):
        """
        Moldable HPO resource allocation with resilient fallback
        """
        model = constraints['mesh']
        budget = constraints['budget']
        deadline_duration = constraints['deadline_duration']
        trials = constraints['chains']
        epochs = constraints['tinydaIterations']

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
                        print(f"EDF Moldable: Allocated {num_hosts_requested} on-prem {instance.name}")
                        break
                    elif slots_available > 0:
                        selected_instances = [(instance, slots_available)]
                        remaining -= slots_available
                        print(f"EDF Moldable: Partial on-prem {slots_available}/{num_hosts_requested}")
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
                                print(f"EDF Moldable: {slots_available} on-demand {optimal_type} (${cost:.2f})")
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
                                    print(f"EDF Moldable: {slots_available} on-demand {alt_type} (${cost:.2f})")
                                    if g5_remaining == 0:
                                        break

                g5_allocated = num_hosts_requested - g5_remaining

                # --- Pick winner: whichever type satisfies more hosts ---
                if g5_allocated > g4_allocated:
                    selected_instances = g5_selected
                    remaining = g5_remaining
                    print(f"EDF Moldable: {optimal_type} partial ({g4_allocated}), using {alt_type} ({g5_allocated}/{num_hosts_requested})")
                else:
                    selected_instances = g4_selected
                    remaining = g4_remaining
                    if g4_allocated > 0:
                        print(f"EDF Moldable: {optimal_type} partial ({g4_allocated}/{num_hosts_requested}), {alt_type} no better ({g5_allocated})")
            else:
                selected_instances = g4_selected
                remaining = 0

        # RESILIENT: Proceed with what we got
        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"EDF Moldable degraded: requested {num_hosts_requested}, got {allocated_hosts}")

        if allocated_hosts == 0:
            print(f"EDF Moldable: Failed to allocate any hosts (both types exhausted)")
            return None, None

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
            print(f"EDF Moldable: on-demand instance creation failed, returning resources")
            self.resource_manager.returnResources("_failed_alloc", alloc_resources)
            return None, None

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts (moldable version)
        Returns: (instance_type, num_hosts)

        Availability-aware: uses ACTUAL cost based on which slots are free.
        If reserved g4 is taken, g4 cost = on-demand price.
        If reserved g5 is free, g5 cost = reserved price.
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
            # moldable starts identical to static at iter 0. The optimizer's score
            # function otherwise prefers num_hosts=1 because cost is roughly flat
            # under perfect parallelism — that would make moldable strictly slower
            # than static at iter 0, defeating the cap=1.0 intent.
            if MOLDABLE_INITIAL_CAP >= 1.0 and total_free >= 1:
                num_hosts_range = [min(max_initial, total_free)]
            else:
                num_hosts_range = range(1, min(max_initial, total_free) + 1)

            for num_hosts in num_hosts_range:
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
                    cost_rate += use * slot_cost
                    hosts_remaining -= use
                    if hosts_remaining == 0:
                        break

                cost = (runtime / 3600) * cost_rate

                if runtime <= deadline and cost <= budget:
                    score = cost + (runtime / deadline) * 0.1
                    if score < best_score:
                        best_score = score
                        best_num_hosts = num_hosts
                        best_instance_type = inst_type
                        best_cost = cost
                        best_runtime = runtime

        if best_instance_type is None:
            for inst_type, slots in type_slots.items():
                if sum(free for _, free in slots) > 0:
                    print(f"No configuration meets constraints, defaulting to: 1 x {inst_type}")
                    return inst_type, 1
            print(f"No free slots available, defaulting to g4")
            return 'g4', 1

        print(f"EDF Moldable selected: {best_num_hosts} x {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
        return (best_instance_type, best_num_hosts)

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

    def getInstanceTypeForHPO(self, instance_name):
        """Map instance names to HPO instance types"""
        if 'g4dn' in instance_name:
            return 'g4'
        elif 'g5' in instance_name:
            return 'g5'
        elif 'on-prem' in instance_name:
            return 'g4'
        else:
            return 'unknown'

    # =========================================================================
    # MOLDABLE REQUEST PROCESSING WITH DEADLINE URGENCY BOOST
    # =========================================================================

    def processMoldableRequestHPO(self, request, backend):
        """
        Process moldable resource requests between HPO optimization rounds.
        Adds deadline-urgency-based graduated scaling from LA EDF pattern.

        Urgency modes:
        - CRITICAL (<30% time remaining): 2.0x budget boost, aggressive scale-up
        - WARNING (<50% time remaining): 1.5x budget boost
        - Regular (falling behind): 1.2x budget boost
        """
        wf_id = request['wf-id']
        (instances, budget, deadline, start_time, model) = self.resource_manager.getWorkflow(wf_id)

        ind = request['iteration']
        # Full remaining time — used for scale-up feasibility. Parallelism shrinks per-iteration
        # runtime, so the original DFACTOR slice (designed to PACE iterations within the deadline)
        # is too tight for the scale-up gate in HPO workloads where per-iter runtimes are short
        # (5-10 min) relative to the deadline (1-2 hr). Decoupling: full time for scale-up,
        # paced slice for scale-down.
        available_time = max(0, deadline - DEADLINE_BUFFER - backend.now())
        paced_available_time = available_time * OPTIM_FCFS_DFACTOR[ind]

        # Current allocation
        current_instance = instances[0][0]
        current_instance_type = current_instance.name
        current_trials = sum(count for _, count, _ in instances)

        # === DEADLINE URGENCY ASSESSMENT ===
        elapsed_time = backend.now() - start_time
        total_time = deadline - start_time
        time_progress = elapsed_time / total_time if total_time > 0 else 0.0

        used_budget = self.metrics.computeCost(wf_id, backend.now())
        budget_progress = used_budget / budget if budget > 0 else 0.0

        time_remaining = deadline - backend.now()
        deadline_urgency = time_remaining / total_time if total_time > 0 else 0.0

        urgency_mode = 'NORMAL'
        skip_scale_down = False
        force_scale_up = False

        # CRITICAL: <30% time remaining
        if deadline_urgency < 0.30:
            print(f"  DEADLINE CRITICAL for {wf_id}: only {time_remaining:.1f}s ({deadline_urgency*100:.1f}%) remaining")
            urgency_mode = 'CRITICAL'
            skip_scale_down = True
            force_scale_up = True

        # WARNING: <50% time remaining and falling behind
        elif deadline_urgency < 0.50 and time_progress > budget_progress + 0.03:
            print(f"  DEADLINE WARNING for {wf_id}: {deadline_urgency*100:.1f}% time left")
            urgency_mode = 'WARNING'
            skip_scale_down = True
            force_scale_up = True

        # Falling behind
        elif time_progress > budget_progress + 0.03:
            print(f"  EARLY SCALE-UP for {wf_id}: time {time_progress*100:.1f}% > budget {budget_progress*100:.1f}%")
            skip_scale_down = True
            force_scale_up = True

        # === SCALE DOWN CHECK ===
        trials_per_instance = 3
        request['count'] = None
        min_needed_trials = request['chains']
        min_needed_instances = min_needed_trials

        if current_instance_type.startswith('g4dn') or 'on-prem' in current_instance_type:
            runtime_per_trial = getRuntime_g4(1, model, request['tinyda-iterations'])
        else:
            runtime_per_trial = getRuntime_g5(1, model, request['tinyda-iterations'])

        while trials_per_instance > 0 and not skip_scale_down:
            runtime = trials_per_instance * runtime_per_trial
            if runtime < paced_available_time:
                min_needed_instances = min_needed_trials // trials_per_instance + bool(min_needed_trials % trials_per_instance)
                if current_trials > min_needed_instances:
                    request['count'] = current_trials - min_needed_instances
                    self.freeResources(instances, request, backend)
                    return
                else:
                    break
            else:
                trials_per_instance -= 1

        # === SCALE UP CHECK WITH DEADLINE URGENCY BOOST ===
        used_budget = self.metrics.computeCost(wf_id, backend.now())

        # Graduated boost factor based on urgency
        if urgency_mode == 'CRITICAL':
            boost_factor = 2.0
            print(f"  CRITICAL BOOST: 2.0x budget allocation for {wf_id}")
        elif urgency_mode == 'WARNING':
            boost_factor = 1.5
            print(f"  WARNING BOOST: 1.5x budget allocation for {wf_id}")
        elif force_scale_up:
            boost_factor = 1.2
            print(f"  SCALE-UP BOOST: 1.2x budget allocation for {wf_id}")
        else:
            boost_factor = 1.0

        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind] * boost_factor

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            if force_scale_up:
                # Request additional instances scaled by urgency
                if urgency_mode == 'CRITICAL':
                    additional = max(1, int(current_trials * 0.50))
                elif urgency_mode == 'WARNING':
                    additional = max(1, int(current_trials * 0.40))
                else:
                    additional = max(1, int(current_trials * 0.30))
                request['count'] = additional
                print(f"  Requesting +{additional} instances (current: {current_trials})")
            else:
                request['count'] = min_needed_instances - current_trials

        # Moldable opportunity: even if we CAN finish with current instances,
        # try to scale up to max parallelism (1 instance per trial) if budget allows.
        if request['count'] <= 0 and current_trials < request['chains']:
            potential_extra = request['chains'] - current_trials
            seq_runtime = runtime_per_trial * math.ceil(request['chains'] / max(current_trials, 1))
            par_runtime = runtime_per_trial * math.ceil(request['chains'] / (current_trials + potential_extra))
            if seq_runtime > 0 and par_runtime < seq_runtime:
                print(f"EDF Moldable opportunity: {wf_id} can scale {current_trials}→{current_trials + potential_extra} "
                      f"(speedup {seq_runtime/par_runtime:.1f}x, {seq_runtime:.0f}s→{par_runtime:.0f}s)")
                request['count'] = potential_extra

        # Allocate new resources (same instance type only, homogeneous)
        alloc_instances = self.checkNewResourcesHPO(
            free_resources,
            instances,
            available_budget,
            available_time,
            request,
            model,
            current_instance_type,
            backend
        )

        ips, alloc_resources = self.resource_manager.allocateResources(alloc_instances)

        if ips:
            ips = self.createOnDemandWorkers(ips, backend)
            self._syncOnDemandIPs(ips, alloc_resources)
            # If all on-demand creation failed, return the slots
            total_ips = sum(len(ip_list) for _, (_, ip_list) in ips.get('on-demand', {}).items())
            on_demand_requested = sum(count for _, (count, _) in ips.get('on-demand', {}).items())
            if on_demand_requested > 0 and total_ips == 0:
                print(f"[SCALE-UP] On-demand creation failed, returning reserved slots")
                self.resource_manager.returnResources("_failed_scaleup", alloc_resources)
                alloc_instances = []
                ips = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}

        # Record scale-up metrics
        if alloc_instances:
            instances_added = sum(count for _, count, *_ in alloc_instances)
            cores_added = sum(count * inst.cores for inst, count, *_ in alloc_instances)
            self.metrics.recordScaleUpAttempt(
                success=True,
                instances_added=instances_added,
                cores_added=cores_added,
                workflow_id=wf_id
            )
        else:
            # Determine failure reason
            free_compute = any(r.getFreeSlots() > 0 for r in free_resources)
            if not free_compute:
                self.metrics.recordScaleUpAttempt(success=False, reason='insufficient_compute', workflow_id=wf_id)
            elif available_budget <= 0:
                self.metrics.recordScaleUpAttempt(success=False, reason='budget_exhausted', workflow_id=wf_id)
            elif available_time <= 0:
                self.metrics.recordScaleUpAttempt(success=False, reason='time_exhausted', workflow_id=wf_id)
            else:
                self.metrics.recordScaleUpAttempt(success=False, reason='insufficient_compute', workflow_id=wf_id)

        self.sendNewResources(request['wf-id'], ips, alloc_resources, backend, request.get('client-ip', None), iter_idx=request.get('iteration'))

    def checkNewResourcesHPO(self, resources, current_resources, budget, available_runtime, request, model, instance_type_filter, backend):
        """
        Allocate additional resources for moldable HPO with cluster isolation.
        Identical to fcfs_optimized_HPO version.
        """
        if budget < MIN_INSTANCE_COST:
            return []

        if instance_type_filter.startswith('g4dn') or 'on-prem' in instance_type_filter:
            runtime_func = getRuntime_g4
        else:
            runtime_func = getRuntime_g5

        instance = current_resources[0][0]

        # CASE 1: On-prem workflow - scale ONLY within on-prem
        if isinstance(instance, OnPremInstance):
            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = (runtime / 3600) * instance.cost_per_second
            to_be_used = min(instance.getFreeSlots(), request['count'], int(budget / cost_per_instance))

            if to_be_used > 0:
                print(f"EDF Moldable: Scaling on-prem workflow, adding {to_be_used} on-prem instances")
                return [(instance, to_be_used)]
            else:
                print(f"EDF Moldable: On-prem workflow cannot scale (no free slots or budget)")
                return []

        # CASE 2: Cloud workflow - scale ONLY within cloud (reserved + on-demand)
        available_instances = []
        for inst in resources:
            if (not isinstance(inst, OnPremInstance) and
                inst.name == instance_type_filter and
                inst.getFreeSlots() > 0):
                priority = 0 if inst.type == 'reserved' else 1
                available_instances.append((priority, inst))

        available_instances.sort(key=lambda x: x[0])

        acquired_count = 0
        acquired_instances = []
        trials_needed = request['count']

        for priority, inst in available_instances:
            if acquired_count >= trials_needed or budget < MIN_INSTANCE_COST:
                break

            runtime = runtime_func(1, model, request['tinyda-iterations'])
            cost_per_instance = (runtime / 3600) * inst.cost_per_second

            if inst.type == 'on-demand':
                cost_per_instance += (COLD_START_TIME / 3600) * inst.cost_per_second

            current_trials = request['chains'] - request['count'] + acquired_count
            new_trials = current_trials + 1

            current_runtime = runtime * (request['chains'] / max(current_trials, 1))
            new_runtime = runtime * (request['chains'] / new_trials)
            speedup = current_runtime / new_runtime if new_runtime > 0 else 0

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
                print(f"EDF Moldable: Cloud workflow {wf_id} cannot scale (no free {instance_type_filter} slots)")
            elif budget < MIN_INSTANCE_COST:
                print(f"EDF Moldable: Cloud workflow {wf_id} cannot scale (budget exhausted, ${budget:.4f} remaining)")
            elif available_runtime <= 0:
                print(f"EDF Moldable: Cloud workflow {wf_id} cannot scale (deadline exhausted)")
            else:
                print(f"EDF Moldable: Cloud workflow {wf_id} cannot scale (speedup not justified, budget=${budget:.4f}, time={available_runtime:.0f}s)")

        return acquired_instances

    def freeResources(self, instances, request, backend):
        """Free excess resources when deadline allows (LIFO strategy)"""
        response_instances = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        freed_count = 0
        to_free_instances = []

        if request['count'] > 0:
            for i in range(len(instances) - 1, -1, -1):
                instance, count, ips = instances[i]
                to_free = min(request['count'] - freed_count, count)
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
        print(f"{wf_id} EDF allocated additional resources: ", ips)
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
        print(f"EDF Scheduler freeing {response_instances} for {wf_id}")
        backend.notify_resources(new_req, client_ip)
        negotiation_log.log('scheduler',
                            wf_id=wf_id, iter_idx=iter_idx if iter_idx is not None else '',
                            request_type='shrink',
                            granted_count=negotiation_log.count_hosts(response_instances),
                            t_scheduler_reply_sent=time.time())
        if to_free_instances:
            self.resource_manager.updateFreedResources(wf_id, instances)
            self.metrics.updateResources(wf_id, to_free_instances, None, backend.now())

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

    def sendWorkflowForExecutionHPO(self, wf_plan, ips, backend, deadline):
        """
        HPO-specific workflow execution with dedicated executor design
        """
        on_prem_hosts = ips.get('on-prem', {})
        on_prem_ips = []
        for name, (count, ip_list) in on_prem_hosts.items():
            on_prem_ips.extend(ip_list)

        if on_prem_ips:
            executor_ip = on_prem_ips[0]
            print(f"HPO EDF Moldable Workflow {wf_plan['id']}: Using on-prem executor at {executor_ip}")
        else:
            # First allocated cloud IP acts as executor (reserved preferred)
            cloud_ips = []
            for name, (count, ip_list) in ips.get('reserved', {}).items():
                cloud_ips.extend(ip_list)
            for name, (count, ip_list) in ips.get('on-demand', {}).items():
                cloud_ips.extend(ip_list)
            executor_ip = cloud_ips[0]
            print(f"HPO EDF Moldable Workflow {wf_plan['id']}: Using cloud executor at {executor_ip}")

        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,
            "executor-ip": executor_ip,
            "deadline": deadline,
            "moldable": True
        }

        print(f"  Workers: {ips}")

        backend.start_workflow(request, executor_ip)
