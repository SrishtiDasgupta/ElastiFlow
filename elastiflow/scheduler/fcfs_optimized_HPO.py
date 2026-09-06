from collections import deque
import math
import threading
import time
from typing import List

from elastiflow.config.constants_HPO import (
    COLD_START_TIME,
    WORKFLOW_POLLING,
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
from elastiflow.utils.resource import getConstraintsFromWorkflow, getEstimate
from elastiflow.utils.request import ExecutorRequest, sendRequest, getConfig
from elastiflow.utils import negotiation_log
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO_Elastic

# HPO-specific Moldable FCFS Scheduler with Dedicated Executor Design
# Supports full cross-type instance switching and elastic scaling
class FCFS_Optimized_HPO(Scheduler_HPO_Elastic):

    def _runtimeFunctionFor(self, instance_type):
        # Elastic-FCFS: only g4dn types use the g4 runtime model; on-prem workflows scale with g5's.
        return getRuntime_g4 if instance_type.startswith('g4dn') else getRuntime_g5

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_trial',
                 resource_config=None, file_prefix=None):
        self.resource_manager = ResourceManager(resource_config or _HPO_RESOURCES_DEFAULT)
        self.file_prefix = file_prefix or 'FCFS_Moldable_HPO_'
        # HPO-specific sorting: prioritize by trial cost + cold start penalty
        func = lambda x: self.getHPOInstanceCost(x) + (COLD_START_TIME * 0.001 if isinstance(x, CloudOnDemandInstance) else 0)
        self.resource_manager.sortResourcesByFunction(func)
        super().__init__(queue, finish_queue, resource_request_queue)

    def run(self, backend):
        print(f'Starting HPO Moldable FCFS scheduler...')

        # Start a thread to periodically compute resource utilization
        backend.spawn(self.metrics.collectResourceUtilization, backend, self.resource_manager)

        while True:

            # Check queue for moldable resource requests
            resource_request = backend.resource_requests.peek()

            if resource_request:
                # Process moldable resource requests
                start = time.time()
                resource_request = eval(resource_request)
                try:
                    _rt_label = 'grow' if resource_request.get('request') == ExecutorRequest.REQUEST_RESOURCE.value else 'shrink'
                    negotiation_log.log('scheduler',
                                        wf_id=resource_request.get('wf-id', '?'),
                                        iter_idx=resource_request.get('iteration', ''),
                                        request_type=_rt_label,
                                        t_scheduler_request_observed=time.time())
                except Exception as _e:
                    print(f'[negotiation_log] obs parse fail: {_e}')
                if backend.now() - resource_request['request-time'] > 300:  # 5 min timeout
                    backend.resource_requests.pop()
                    continue
                self.processMoldableRequestHPO(resource_request, backend)
                print(f"HPO Moldable resource processing overhead: {time.time() - start}")
                backend.resource_requests.pop()
                continue

            # Check the queue for new jobs
            workflow_plan = backend.workflows.peek()

            if workflow_plan:
                wf_plan = eval(workflow_plan) # Convert string back to dictionary

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    backend.workflows.pop()
                    self.metrics.computeMetrics(file_prefix=self.file_prefix)
                    break

                # Moldable scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesMoldableHPO(constraints, backend)
                    print(f"{wf_plan['id']} moldable allocation: ", ips)

                    # Remove the element if we found the resources needed.
                    if ips:
                        backend.workflows.pop()
                        # NOTE: We start billing at this point
                        start_time = backend.now()
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, backend, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO moldable resources to allocate, waiting...')

            backend.sleep(WORKFLOW_POLLING)

    def processMoldableRequestHPO(self, request, backend):
        """
        Process moldable resource requests between HPO optimization rounds
        Decides: scale up, scale down, or maintain allocation (NO instance type switching)
        """
        wf_id = request['wf-id']
        (instances, budget, deadline, start_time, model) = self.resource_manager.getWorkflow(wf_id)

        # Calculate iteration-weighted constraints
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
        current_instance_type = current_instance.name  # Locked to this type (no switching!)
        current_trials = sum(count for _, count, _ in instances)

        # Check if can reduce resources (deadline not tight)
        trials_per_instance = 3
        request['count'] = None
        min_needed_trials = request['chains']
        min_needed_instances = min_needed_trials  # default: 1 instance per trial

        # Determine runtime function
        if current_instance_type.startswith('g4dn'):
            runtime_per_trial = getRuntime_g4(1, model, request['tinyda-iterations'])
        else:
            runtime_per_trial = getRuntime_g5(1, model, request['tinyda-iterations'])

        while trials_per_instance > 0:
            runtime = trials_per_instance * runtime_per_trial
            if runtime < paced_available_time:
                min_needed_instances = min_needed_trials // trials_per_instance + bool(min_needed_trials % trials_per_instance)
                if current_trials > min_needed_instances:
                    # Free excess instances
                    request['count'] = current_trials - min_needed_instances
                    self.freeResources(instances, request, backend)
                    return
                else:
                    # Need more instances
                    break
            else:
                trials_per_instance -= 1

        # Allocate additional resources if needed
        used_budget = self.metrics.computeCost(wf_id, backend.now())
        available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]

        free_resources = self.resource_manager.getResources()

        if request['count'] is None:
            request['count'] = min_needed_instances - current_trials

        # Moldable opportunity: even if we CAN finish with current instances,
        # try to scale up to max parallelism (1 instance per trial) if budget allows.
        # This is the core moldable benefit: start lean, scale up for speedup.
        if request['count'] <= 0 and current_trials < request['chains']:
            potential_extra = request['chains'] - current_trials
            # Calculate speedup from going parallel
            seq_runtime = runtime_per_trial * math.ceil(request['chains'] / max(current_trials, 1))
            par_runtime = runtime_per_trial * math.ceil(request['chains'] / (current_trials + potential_extra))
            if seq_runtime > 0 and par_runtime < seq_runtime:
                print(f"Moldable opportunity: {wf_id} can scale {current_trials}→{current_trials + potential_extra} "
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

        # Create actual on-demand instances if allocated
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

