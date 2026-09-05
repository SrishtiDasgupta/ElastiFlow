import pandas as pd
import time
from collections import defaultdict

# Import from constants for non-LA simulations
from elastiflow.config.constants import RESOURCE_UTILIZATION_POLLING, TOTAL_RESOURCES, TOTAL_WORKFLOWS

from elastiflow.resource_manager.instance import (
    CloudOnDemandInstance, CloudReservedInstance, OnPremInstance,
)
from elastiflow.resource_manager.resource_manager import ResourceManager


def _tier_for(instance):
    """Return one of 'on_prem' / 'reserved' / 'on_demand' for the given
    instance object. Used for cost-split and per-tier utilisation
    accounting throughout this module."""
    if isinstance(instance, OnPremInstance):
        return 'on_prem'
    if isinstance(instance, CloudReservedInstance):
        return 'reserved'
    if isinstance(instance, CloudOnDemandInstance):
        return 'on_demand'
    return 'unknown'


class Metrics():

    def __init__(self):
        self.df = {}
        # free_resources timeseries: list of
        # (timestamp, on_prem_free, reserved_free, on_demand_free)
        # — used by computeResourceUtilization to produce per-tier averages.
        self.free_resources = []
        # capacity (max possible busy lanes) per tier, captured once when
        # collectResourceUtilization starts so we can divide free counts
        # to recover percentages.
        self.capacity = {'on_prem': 0, 'reserved': 0, 'on_demand': 0}
        # ---------------- Scaling-decision counters (filled by
        # recordScaleUpAttempt / recordScaleDownAttempt at the moldable
        # negotiation hooks inside processFreeRequest).
        self.scale_up_attempts = 0          # every REQUEST_RESOURCE that reaches scale-up
        self.scale_up_granted = 0           # any nodes granted (full or partial)
        self.scale_up_full = 0              # nodes granted == nodes requested
        self.scale_up_partial = 0           # 0 < granted < requested
        self.scale_up_denied = 0            # nothing granted
        self.scale_up_nodes_requested = 0
        self.scale_up_nodes_granted = 0
        self.scale_up_by_iter = defaultdict(int)
        self.scale_up_grants_by_iter = defaultdict(int)
        self.scale_up_grants_by_tier = {'on_prem': 0, 'reserved': 0, 'on_demand': 0}
        self.scale_up_grants_by_instance = defaultdict(int)
        self.scale_down_attempts = 0        # every time processFreeRequest decided to scale-down
        self.scale_down_executed = 0        # actually freed > 0 nodes
        self.scale_down_nodes_freed = 0
        self.scale_down_by_iter = defaultdict(int)

        # Executor intent vs scheduler decision — captures the cases where
        # the workflow engine *asked* for more nodes but the scheduler's
        # rich packing-density probe decided to scale down (or do nothing)
        # instead, or vice versa.
        # Codes for executor intent:
        #   'up'   = REQUEST_RESOURCE event (executor wanted more nodes)
        #   'down' = FREE_RESOURCE event    (executor wanted to release nodes)
        # Cross-tab: (executor_intent, scheduler_outcome) where outcome is
        # one of 'up_granted', 'up_partial', 'up_denied', 'down_freed',
        # 'down_noop', 'up_overridden_to_down'.
        self.executor_intent_count = {'up': 0, 'down': 0}
        self.outcome_matrix = defaultdict(int)  # (intent, outcome) -> count

        # Per-path scaling stats — was scale-up attempted on the on-prem
        # path or on the cloud path? Workflows that start on slurm only
        # ever consider on-prem for scale-up; workflows that start in the
        # cloud only ever consider cloud. So the path distribution maps
        # back to the workflow's initial-allocation tier.
        self.scale_up_on_prem_attempts = 0
        self.scale_up_on_prem_granted = 0
        self.scale_up_cloud_attempts = 0
        self.scale_up_cloud_granted = 0
        self.collectFlag = True
        self.output_file = 'results'  # default output filename (without extension)

    def set_output_file(self, filename):
        """Set the output filename for CSV results (without extension)"""
        self.output_file = filename

    def addToDataframe(self, id, wf, submit_time: float):
        instances = self.processInstances(wf[0], wf[3])
        data = {
            'instances': instances,
            'budget': wf[1],
            'deadline': wf[2],
            'sched_start_time': wf[3],
            'submit_time': submit_time
        }
        self.df[id] = data

    def updateDataframe(self, id, obj):
        data = self.df[id]
        for key in obj:
            data[key] = obj[key]
        self.df[id] = data

    def updateResources(self, id, instances, start_time, finish_time=None):
        new_instances = self.processInstances(instances, start_time, finish_time)
        for instance in new_instances:
            self.df[id]['instances'][instance] = self.df[id]['instances'].get(instance, []) + new_instances[instance]

    # wf[0] = instances : [(instanceObj, count, ips)]
    # store instance in the form (count, start_time, finish_time)
    def processInstances(self, instances, start_time, finish_time=None):
        new_instances = {}
        for instance, count, ips in instances:
            new_instances[instance] = [(count, start_time, finish_time)]
        return new_instances

    def computeCost(self, id, wf_finish_time):
        """Total cost for a workflow up to wf_finish_time (no tier split)."""
        cost = 0
        instances = self.df[id]['instances']
        for instance in instances:
            for count, start_time, finish_time in instances[instance]:
                if start_time:
                    cost += instance.getCostPerSecond() * count * (wf_finish_time - start_time)
                if finish_time:
                    cost -= instance.getCostPerSecond() * count * (wf_finish_time - finish_time)
        return cost

    def computeCostBreakdown(self, id, wf_finish_time):
        """Per-tier cost decomposition for one workflow up to wf_finish_time.
        Returns {'on_prem': $, 'reserved': $, 'on_demand': $, 'total': $}."""
        split = {'on_prem': 0.0, 'reserved': 0.0, 'on_demand': 0.0}
        instances = self.df[id]['instances']
        for instance in instances:
            tier = _tier_for(instance)
            if tier == 'unknown':
                continue
            for count, start_time, finish_time in instances[instance]:
                cps = instance.getCostPerSecond()
                if start_time:
                    split[tier] += cps * count * (wf_finish_time - start_time)
                if finish_time:
                    split[tier] -= cps * count * (wf_finish_time - finish_time)
        split['total'] = split['on_prem'] + split['reserved'] + split['on_demand']
        return split

    # -------- Scaling-decision recording hooks (called by Scheduler.processFreeRequest)
    def _intent_from(self, request):
        """Decode the executor's original intent from the request's
        'request' field. Returns 'up' for REQUEST_RESOURCE, 'down' for
        FREE_RESOURCE, or 'unknown' if the field isn't present."""
        from elastiflow.utils.request import ExecutorRequest
        rt = request.get('request')
        if rt == ExecutorRequest.REQUEST_RESOURCE.value:
            return 'up'
        if rt == ExecutorRequest.FREE_RESOURCE.value:
            return 'down'
        return 'unknown'

    def recordScaleUpAttempt(self, request, granted_instances, path='unknown'):
        """Called once per scale-up decision at the negotiation point.
        `granted_instances` is the list returned by checkNewResources*:
            [(instance_obj, count), ...]  — empty means denied.
        `path` is 'on_prem' / 'cloud' to track per-path success rates.
        Counts are aggregated by iteration, tier, instance name, and the
        executor-intent cross-tab."""
        iteration = request.get('iteration', 0)
        requested = max(0, request.get('count') or 0)
        intent = self._intent_from(request)
        self.scale_up_attempts += 1
        self.scale_up_by_iter[iteration] += 1
        self.scale_up_nodes_requested += requested
        if intent in self.executor_intent_count:
            self.executor_intent_count[intent] += 1

        if path == 'on_prem':
            self.scale_up_on_prem_attempts += 1
        elif path == 'cloud':
            self.scale_up_cloud_attempts += 1

        total_granted = sum(c for _, c in granted_instances if c > 0)
        if total_granted > 0:
            self.scale_up_granted += 1
            self.scale_up_grants_by_iter[iteration] += 1
            self.scale_up_nodes_granted += total_granted
            if path == 'on_prem':
                self.scale_up_on_prem_granted += 1
            elif path == 'cloud':
                self.scale_up_cloud_granted += 1
            if requested == 0 or total_granted >= requested:
                self.scale_up_full += 1
                self.outcome_matrix[(intent, 'up_granted_full')] += 1
            else:
                self.scale_up_partial += 1
                self.outcome_matrix[(intent, 'up_granted_partial')] += 1
            for inst_obj, count in granted_instances:
                if count <= 0:
                    continue
                tier = _tier_for(inst_obj)
                if tier in self.scale_up_grants_by_tier:
                    self.scale_up_grants_by_tier[tier] += count
                self.scale_up_grants_by_instance[getattr(inst_obj, 'name', 'unknown')] += count
        else:
            self.scale_up_denied += 1
            self.outcome_matrix[(intent, 'up_denied')] += 1

    def recordScaleDownAttempt(self, request, nodes_freed):
        """Called once per scale-down decision (proactive packing-density
        probe inside processFreeRequest). `nodes_freed` is how many nodes
        the scheduler actually released — could be 0 if the executor's
        held count happened to already match the target. The
        outcome_matrix entry records (executor_intent, scheduler_outcome)
        so we can see when the scheduler overrode the executor."""
        iteration = request.get('iteration', 0)
        intent = self._intent_from(request)
        self.scale_down_attempts += 1
        self.scale_down_by_iter[iteration] += 1
        if intent in self.executor_intent_count and not self._already_counted_intent_for(request):
            self.executor_intent_count[intent] += 1

        if nodes_freed and nodes_freed > 0:
            self.scale_down_executed += 1
            self.scale_down_nodes_freed += nodes_freed
            self.outcome_matrix[(intent, 'down_freed')] += 1
        else:
            self.outcome_matrix[(intent, 'down_noop')] += 1

    def _already_counted_intent_for(self, request):
        """Guard so we don't double-count executor intent if both
        recordScaleUp and recordScaleDown happen to be called for the same
        request (shouldn't normally happen, but safe)."""
        # Currently processFreeRequest only calls one of the two; this is
        # a placeholder for future-proofing.
        return False

    def computeMetrics(self):
        print('Computing metrics...')
        self.collectFlag = False
        resource_df = pd.DataFrame(
            self.free_resources,
            columns=['Timestamp', 'On-prem', 'Reserved', 'On-demand'],
        )
        resource_df.to_csv(f'{self.output_file}_resources.csv')

        # Aggregators
        sum_flowtime, sum_wait, sum_cost = 0, 0, 0
        sum_cost_on_prem = 0.0
        sum_cost_reserved = 0.0
        sum_cost_on_demand = 0.0
        budget_miss, deadline_miss, overall_miss = 0, 0, 0
        executed_workflows = 0
        wasted_cost, wasted_time = 0, 0
        # batch makespan = max(finish_time) - min(submit_time) across
        # completed workflows; very different from sum-of-flowtimes (which
        # avg_flowtime collapses) — this is the wall-clock duration of
        # the whole batch.
        first_submit = None
        last_finish = None

        for wf in self.df:
            wf_data = self.df[wf]
            if wf_data.get('complete', False):
                executed_workflows += 1
                sum_flowtime += wf_data['finish_time'] - wf_data['submit_time']
                sum_wait += wf_data['exec_start_time'] - wf_data['submit_time']
                breakdown = self.computeCostBreakdown(wf, wf_data['finish_time'])
                wf_data['cost'] = breakdown['total']
                wf_data['cost_on_prem'] = breakdown['on_prem']
                wf_data['cost_reserved'] = breakdown['reserved']
                wf_data['cost_on_demand'] = breakdown['on_demand']
                sum_cost += breakdown['total']
                sum_cost_on_prem += breakdown['on_prem']
                sum_cost_reserved += breakdown['reserved']
                sum_cost_on_demand += breakdown['on_demand']
                budget = breakdown['total'] > wf_data['budget']
                deadline = wf_data['finish_time'] > wf_data['deadline']
                budget_miss += budget
                deadline_miss += deadline
                overall_miss += (budget or deadline)
                if first_submit is None or wf_data['submit_time'] < first_submit:
                    first_submit = wf_data['submit_time']
                if last_finish is None or wf_data['finish_time'] > last_finish:
                    last_finish = wf_data['finish_time']
            else:
                wasted_cost += self.computeCost(wf, wf_data['finish_time'])
                wasted_time += wf_data['finish_time'] - wf_data.get('exec_start_time', wf_data['finish_time'])

        avg_flowtime = round(sum_flowtime / executed_workflows, 4) if executed_workflows > 0 else 0
        avg_cost = round(sum_cost / executed_workflows, 4) if executed_workflows > 0 else 0
        avg_wait_time = round(sum_wait / executed_workflows, 4) if executed_workflows > 0 else 0
        avg_cost_on_prem = round(sum_cost_on_prem / executed_workflows, 4) if executed_workflows > 0 else 0
        avg_cost_reserved = round(sum_cost_reserved / executed_workflows, 4) if executed_workflows > 0 else 0
        avg_cost_on_demand = round(sum_cost_on_demand / executed_workflows, 4) if executed_workflows > 0 else 0
        batch_makespan = round(last_finish - first_submit, 4) if (first_submit is not None and last_finish is not None) else 0

        # Resource utilization (fixed to be a percentage)
        util_overall, util_on_prem, util_reserved, util_on_demand = self.computeResourceUtilization()

        deadline_miss_rate = round((deadline_miss + TOTAL_WORKFLOWS - executed_workflows) / TOTAL_WORKFLOWS, 4)
        budget_miss_rate = round(budget_miss / TOTAL_WORKFLOWS, 4)
        overall_miss_rate = round((overall_miss + TOTAL_WORKFLOWS - executed_workflows) / TOTAL_WORKFLOWS, 4)
        wasted_time_hours = round(wasted_time / (60 * 60), 2)
        wasted_cost_total = round(wasted_cost, 2)

        cost_pct_on_prem = round(100 * sum_cost_on_prem / sum_cost, 2) if sum_cost > 0 else 0
        cost_pct_reserved = round(100 * sum_cost_reserved / sum_cost, 2) if sum_cost > 0 else 0
        cost_pct_on_demand = round(100 * sum_cost_on_demand / sum_cost, 2) if sum_cost > 0 else 0

        lines = [
            f'Total workflows = {TOTAL_WORKFLOWS}',
            f'Executed workflows = {executed_workflows}',
            f'Average Flowtime = {avg_flowtime}',
            f'Batch Makespan = {batch_makespan}',
            f'Average Cost = {avg_cost}',
            f'  - Average Cost on-prem = {avg_cost_on_prem}',
            f'  - Average Cost reserved-cloud = {avg_cost_reserved}',
            f'  - Average Cost on-demand-cloud = {avg_cost_on_demand}',
            f'  - Cost %% on-prem = {cost_pct_on_prem}%',
            f'  - Cost %% reserved-cloud = {cost_pct_reserved}%',
            f'  - Cost %% on-demand-cloud = {cost_pct_on_demand}%',
            f'Total Cost = {round(sum_cost, 2)}',
            f'  - Total Cost on-prem = {round(sum_cost_on_prem, 2)}',
            f'  - Total Cost reserved-cloud = {round(sum_cost_reserved, 2)}',
            f'  - Total Cost on-demand-cloud = {round(sum_cost_on_demand, 2)}',
            f'Average Wait Time = {avg_wait_time}',
            f'Average Resource Utilization (overall) = {util_overall}%',
            f'  - Util on-prem = {util_on_prem}%',
            f'  - Util reserved-cloud = {util_reserved}%',
            f'  - Util on-demand-cloud = {util_on_demand}%',
            f'Deadline miss rate = {deadline_miss_rate}',
            f'Budget miss rate = {budget_miss_rate}',
            f'Overall miss rate = {overall_miss_rate}',
            f'Time spent on incomplete workflows = {wasted_time_hours} hours',
            f'Wasted cost on incomplete workflows = {wasted_cost_total}',
        ]

        # ---------------- Scaling decisions section
        su_full_pct = round(100 * self.scale_up_full / self.scale_up_attempts, 2) if self.scale_up_attempts else 0
        su_partial_pct = round(100 * self.scale_up_partial / self.scale_up_attempts, 2) if self.scale_up_attempts else 0
        su_denied_pct = round(100 * self.scale_up_denied / self.scale_up_attempts, 2) if self.scale_up_attempts else 0
        sd_exec_pct = round(100 * self.scale_down_executed / self.scale_down_attempts, 2) if self.scale_down_attempts else 0

        scaling_lines = [
            '',
            f'--- Scaling decisions ---',
            f'Scale-up attempts = {self.scale_up_attempts}',
            f'  - Granted in full  = {self.scale_up_full} ({su_full_pct}%)',
            f'  - Granted partial  = {self.scale_up_partial} ({su_partial_pct}%)',
            f'  - Denied (no grant) = {self.scale_up_denied} ({su_denied_pct}%)',
            f'Scale-up nodes requested (total) = {self.scale_up_nodes_requested}',
            f'Scale-up nodes granted (total)   = {self.scale_up_nodes_granted}',
            f'Scale-up grants by tier:',
            f'  - on-prem        = {self.scale_up_grants_by_tier["on_prem"]} nodes',
            f'  - reserved-cloud = {self.scale_up_grants_by_tier["reserved"]} nodes',
            f'  - on-demand      = {self.scale_up_grants_by_tier["on_demand"]} nodes',
            f'Scale-up grants by instance type:',
        ]
        for name in sorted(self.scale_up_grants_by_instance):
            scaling_lines.append(f'  - {name} = {self.scale_up_grants_by_instance[name]} nodes')
        scaling_lines.append(f'Scale-up attempts by iteration:')
        for it in sorted(self.scale_up_by_iter):
            granted = self.scale_up_grants_by_iter.get(it, 0)
            scaling_lines.append(f'  - iter {it}: attempted {self.scale_up_by_iter[it]}, granted {granted}')
        scaling_lines += [
            f'Scale-down attempts = {self.scale_down_attempts}',
            f'  - Executed (>=1 node freed) = {self.scale_down_executed} ({sd_exec_pct}%)',
            f'  - Total nodes freed         = {self.scale_down_nodes_freed}',
            f'Scale-down attempts by iteration:',
        ]
        for it in sorted(self.scale_down_by_iter):
            scaling_lines.append(f'  - iter {it}: {self.scale_down_by_iter[it]}')

        # ---------------- Per-path scale-up success rates
        # (on-prem path vs cloud path inside checkNewResources*)
        op_pct = round(100 * self.scale_up_on_prem_granted / self.scale_up_on_prem_attempts, 2) if self.scale_up_on_prem_attempts else 0
        cl_pct = round(100 * self.scale_up_cloud_granted / self.scale_up_cloud_attempts, 2) if self.scale_up_cloud_attempts else 0
        scaling_lines += [
            '',
            '--- Scale-up success by path ---',
            f'On-prem path:  attempts {self.scale_up_on_prem_attempts}, granted {self.scale_up_on_prem_granted} ({op_pct}%)',
            f'Cloud path:    attempts {self.scale_up_cloud_attempts}, granted {self.scale_up_cloud_granted} ({cl_pct}%)',
        ]

        # ---------------- Executor intent vs scheduler decision
        # ("Did the engine ask for what the workflow needed, and did the
        # scheduler honour it?")
        scaling_lines += [
            '',
            '--- Executor intent vs scheduler decision ---',
            f'Executor REQUEST_RESOURCE events (wanted scale-up)   = {self.executor_intent_count["up"]}',
            f'Executor FREE_RESOURCE events    (wanted scale-down) = {self.executor_intent_count["down"]}',
            'Outcome cross-tab (executor_intent → scheduler_outcome):',
        ]
        # Render in a tidy block, grouped by executor intent
        outcome_order = ['up_granted_full', 'up_granted_partial', 'up_denied', 'down_freed', 'down_noop']
        for intent in ('up', 'down', 'unknown'):
            seen_any = any((intent, o) in self.outcome_matrix for o in outcome_order)
            if not seen_any:
                continue
            scaling_lines.append(f'  executor wanted "{intent}":')
            for o in outcome_order:
                c = self.outcome_matrix.get((intent, o), 0)
                if c > 0:
                    scaling_lines.append(f'    → scheduler decided "{o}" = {c}')

        # Key behavioural signal: how often did the scheduler scale DOWN
        # when the executor asked for UP? (the "scheduler override" rate)
        up_intent = self.executor_intent_count.get('up', 0)
        override_to_down = self.outcome_matrix.get(('up', 'down_freed'), 0) + self.outcome_matrix.get(('up', 'down_noop'), 0)
        override_pct = round(100 * override_to_down / up_intent, 2) if up_intent else 0
        scaling_lines.append(
            f'Scheduler-override rate (REQUEST_RESOURCE → scheduler scaled down or noop) = {override_to_down}/{up_intent} ({override_pct}%)'
        )

        lines = lines + scaling_lines

        for line in lines:
            print(line.replace('%%', '%'))

        # Persist summary
        with open(f'{self.output_file}.out', 'w') as f:
            for line in lines:
                f.write(line.replace('%%', '%') + '\n')

        df = pd.DataFrame(self.df).T
        df.to_csv(f'{self.output_file}.csv')
        exit()

    def collectResourceUtilization(self, sim, rm: ResourceManager):
        # First sample also captures fleet capacity (assumes capacity is
        # constant over the run — on-demand "capacity" here is the maximum
        # provisionable, used as the denominator for the OD utilisation %).
        first = True
        while self.collectFlag:
            resources = rm.getResources()
            on_prem_free = reserved_free = on_demand_free = 0
            on_prem_cap = reserved_cap = on_demand_cap = 0
            for instance in resources:
                tier = _tier_for(instance)
                free = instance.getFreeSlots()
                # On the very first sample no workflow has been allocated
                # yet, so free == capacity per instance. Use that to seed
                # the per-tier capacity; on subsequent samples we only
                # touch the free counts.
                if tier == 'on_prem':
                    on_prem_free += free
                    if first:
                        on_prem_cap += free
                elif tier == 'reserved':
                    reserved_free += free
                    if first:
                        reserved_cap += free
                elif tier == 'on_demand':
                    on_demand_free += free
                    if first:
                        on_demand_cap += free
            if first:
                self.capacity = {
                    'on_prem': on_prem_cap,
                    'reserved': reserved_cap,
                    'on_demand': on_demand_cap,
                }
                first = False
            self.free_resources.append((
                (sim and sim.now) or time.time(),
                on_prem_free, reserved_free, on_demand_free,
            ))
            (sim or time).sleep(RESOURCE_UTILIZATION_POLLING)

    def computeResourceUtilization(self):
        """Return (overall_pct, on_prem_pct, reserved_pct, on_demand_pct)
        as the time-averaged fraction of each tier's capacity that was busy
        across the campaign's active window."""
        n = len(self.free_resources)
        if n == 0:
            return 0.0, 0.0, 0.0, 0.0

        # Find the active window: from the first sample where ANY tier had
        # something busy, to the last such sample. Frames the average over
        # the period the system was actually doing work (matches Kavitha's
        # convention).
        def busy(sample):
            _, op, rs, od = sample
            cap_busy = (self.capacity['on_prem'] - op) + (self.capacity['reserved'] - rs) + (self.capacity['on_demand'] - od)
            return cap_busy > 0

        start = next((i for i in range(n) if busy(self.free_resources[i])), 0)
        stop = next((i for i in range(n - 1, -1, -1) if busy(self.free_resources[i])), n - 1)

        if stop < start:
            return 0.0, 0.0, 0.0, 0.0

        on_prem_busy = reserved_busy = on_demand_busy = 0
        samples = stop - start + 1
        for i in range(start, stop + 1):
            _, op, rs, od = self.free_resources[i]
            on_prem_busy += (self.capacity['on_prem'] - op)
            reserved_busy += (self.capacity['reserved'] - rs)
            on_demand_busy += (self.capacity['on_demand'] - od)

        def pct(busy_sum, cap):
            return round(100 * busy_sum / (cap * samples), 4) if cap > 0 else 0.0

        on_prem_pct = pct(on_prem_busy, self.capacity['on_prem'])
        reserved_pct = pct(reserved_busy, self.capacity['reserved'])
        on_demand_pct = pct(on_demand_busy, self.capacity['on_demand'])

        total_cap = self.capacity['on_prem'] + self.capacity['reserved'] + self.capacity['on_demand']
        total_busy = on_prem_busy + reserved_busy + on_demand_busy
        overall_pct = pct(total_busy, total_cap) if total_cap > 0 else 0.0

        return overall_pct, on_prem_pct, reserved_pct, on_demand_pct
