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

from elastiflow.config.constants_HPO import (
    COLD_START_TIME,
    MIN_TRIALS,
    MAX_TRIALS,
    DEADLINE_BUFFER,
    OPTIM_FCFS_BFACTOR,
    OPTIM_FCFS_DFACTOR,
)
from elastiflow.scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from elastiflow.resource_manager.instance import CloudOnDemandInstance, Instance
import os
from elastiflow.resource_manager.resource_manager import ResourceManager

_HPO_RESOURCES_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from elastiflow.utils.resource import getEstimate
from elastiflow.utils.request import ExecutorRequest, sendRequest, getConfig
from elastiflow.utils import negotiation_log
from elastiflow.scheduler.scheduler import EDFOrderingMixin
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO_Elastic


class EDF_Optimized_HPO(EDFOrderingMixin, Scheduler_HPO_Elastic):
    """
    Moldable EDF scheduler for HPO workflows.

    EDF ordering + moldable resource reallocation + deadline urgency boost.
    """

    policy_label = 'EDF '

    def _runtimeFunctionFor(self, instance_type):
        # Elastic-EDF: on-prem workflows scale with the g4 runtime model (on-prem is g4 hardware).
        return getRuntime_g4 if instance_type.startswith('g4dn') or 'on-prem' in instance_type else getRuntime_g5

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

    def printBanner(self, backend):
        print(f'Starting HPO Moldable EDF scheduler...')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')
        print(f'  - Moldable: Dynamic resource reallocation between iterations')
        print(f'  - Deadline urgency boost: CRITICAL 2.0x, WARNING 1.5x, Regular 1.2x')

    def serviceResourceRequests(self, backend) -> bool:
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
        if not resource_request:
            return False
        start = time.time()
        if backend.now() - resource_request['request-time'] > 300:  # 5 min timeout
            self.popWorkflow(self.resource_request_heap)
            # NOTE: do NOT pop from resource_request_queue. getAllElements
            # already drained it when we heap-pushed (same data5-bug pattern).
            return True
        self.processMoldableRequestHPO(resource_request, backend)
        print(f"  HPO EDF Moldable resource processing overhead: {time.time() - start}")
        self.popWorkflow(self.resource_request_heap)
        # NOTE: see above — queue was already drained, do not LPOP here.
        return True

    def nextWorkflow(self, backend):
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

        return self.peekWorkflow(self.workflow_heap)

    def dropWorkflow(self, backend):
        self.popWorkflow(self.workflow_heap)
        # NOTE: do NOT pop from wf_queue here. getAllElements already drained
        # the wf out of the queue when we heap-pushed it. A trailing
        # queue.pop() here LPOPs whatever happens to be at the head right
        # now — which, if a new wf was pushed during a slow allocation,
        # silently discards that new wf (the data5-disappears bug from R3/R7).

    def printAllocation(self, wf_plan, ips, backend, constraints=None):
        print(f"{wf_plan['id']} EDF moldable allocation (deadline={constraints['deadline']:.1f}s): ", ips)

    def endCycle(self, backend):
        # Periodic heap visibility (~ every 60s assuming WORKFLOW_POLLING=5s)
        self._heap_log_counter += 1
        if self._heap_log_counter >= 12:
            heap_ids = [entry[2] for entry in self.workflow_heap]
            print(f"[HEAP] size={len(self.workflow_heap)} ids={heap_ids}")
            self._heap_log_counter = 0

    # =========================================================================
    # EDF HEAP MANAGEMENT
    # =========================================================================

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

    # =========================================================================
    # HPO MOLDABLE RESOURCE ALLOCATION (from fcfs_optimized_HPO.py)
    # =========================================================================

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

