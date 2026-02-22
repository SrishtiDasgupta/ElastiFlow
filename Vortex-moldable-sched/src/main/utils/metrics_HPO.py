"""
HPO Metrics Calculation Module

Computes performance metrics for HPO (Hyperparameter Optimization) schedulers.
Follows the MetricsLA pattern but without license tracking:
- Imports TOTAL_WORKFLOWS from constants_HPO (10, not 400)
- Dynamic resource utilization computation
- Per-model breakdown (vgg19, wide_resnet, convnext)
- Moldability effectiveness tracking
- Unique file prefix per scheduler to prevent output overwriting
"""

import pandas as pd
import time

from config.constants_HPO import RESOURCE_UTILIZATION_POLLING, TOTAL_WORKFLOWS
from resource_manager.instance import CloudReservedInstance, OnPremInstance
from resource_manager.resource_manager import ResourceManager


class MetricsHPO:
    """
    Metrics collection and calculation for HPO scheduling.

    Tracks:
    - Workflow execution times (submit, start, finish)
    - Resource allocations and costs
    - Budget and deadline constraints
    - Resource utilization over time
    - Per-model performance (vgg19, wide_resnet101_2, convnext_large)
    - Moldability effectiveness (scale-up/down stats)
    """

    def __init__(self):
        self.df = {}
        self.free_resources = []
        self.collectFlag = True
        self.total_resources = 0  # Will be computed dynamically

        # Moldability effectiveness tracking
        self.scale_up_attempts = 0
        self.scale_up_successes = 0
        self.scale_up_failures = 0
        self.scale_up_failures_by_reason = {
            'insufficient_compute': 0,
            'budget_exhausted': 0,
            'time_exhausted': 0
        }

        # Track scale-up attempts per workflow
        self.scale_up_attempts_per_workflow = {}

        self.scale_down_attempts = 0
        self.scale_down_successes = 0
        self.scale_down_blocked = 0
        self.scale_down_blocked_by_reason = {
            'late_iteration': 0,
            'time_progress': 0,
            'budget_or_time_progress': 0
        }

        self.total_instances_added = 0
        self.total_cores_added = 0
        self.total_instances_removed = 0
        self.total_cores_removed = 0

    def addToDataframe(self, id, wf, submit_time: float):
        """
        Add a new workflow to tracking.

        Args:
            id: Workflow identifier
            wf: Workflow tuple (instances, budget, deadline, sched_start_time, model)
            submit_time: Time workflow was submitted
        """
        instances = self.processInstances(wf[0], wf[3])
        data = {
            'instances': instances,
            'budget': wf[1],
            'deadline': wf[2],
            'sched_start_time': wf[3],
            'submit_time': submit_time,
            'model': wf[4] if len(wf) > 4 else 'unknown'  # HPO stores model name at index 4
        }
        self.df[id] = data

    def updateDataframe(self, id, obj):
        """
        Update workflow data with additional fields.

        Args:
            id: Workflow identifier
            obj: Dictionary of fields to update
        """
        data = self.df[id]
        for key in obj:
            data[key] = obj[key]
        self.df[id] = data

    def processInstances(self, instances, start_time, finish_time=None):
        """
        Convert instance list to tracking format.

        Args:
            instances: List of (instance_obj, count, ips) tuples
            start_time: Allocation start time
            finish_time: Allocation end time

        Returns:
            Dictionary mapping instance objects to [(count, start, end)] lists
        """
        new_instances = {}
        for instance, count, ips in instances:
            new_instances[instance] = [(count, start_time, finish_time)]
        return new_instances

    def updateResources(self, id, instances, start_time, finish_time=None):
        """
        Update resource allocation tracking (base version).

        Args:
            id: Workflow identifier
            instances: List of (instance_obj, count, ips) tuples
            start_time: Allocation start time
            finish_time: Allocation end time (for freed resources)
        """
        new_instances = self.processInstances(instances, start_time, finish_time)
        for instance in new_instances:
            self.df[id]['instances'][instance] = self.df[id]['instances'].get(instance, []) + new_instances[instance]

    def computeCost(self, id, wf_finish_time):
        """
        Compute hardware cost for a workflow (no license cost).

        Args:
            id: Workflow identifier
            wf_finish_time: Workflow completion time

        Returns:
            float: Hardware cost
        """
        cost = 0
        instances = self.df[id]['instances']  # {obj: [(count, start, end), ...]}
        for instance in instances:
            instance_list = instances[instance]
            cost_per_hour = instance.getCostPerSecond()  # Misnamed: actually $/hour
            for count, start_time, finish_time in instance_list:
                if start_time:  # Newly added resource
                    hours = (wf_finish_time - start_time) / 3600
                    cost += cost_per_hour * count * hours
                if finish_time:  # Freed resource
                    hours = (wf_finish_time - finish_time) / 3600
                    cost -= cost_per_hour * count * hours
        return cost

    def recordScaleUpAttempt(self, success, reason=None, instances_added=0, cores_added=0, workflow_id=None):
        """
        Record a scale-up attempt and its outcome.

        Args:
            success: True if scale-up succeeded, False otherwise
            reason: Reason for failure (if applicable)
            instances_added: Number of instances successfully added
            cores_added: Number of cores successfully added
            workflow_id: Workflow identifier (to track attempts per workflow)
        """
        self.scale_up_attempts += 1

        if workflow_id:
            if workflow_id not in self.scale_up_attempts_per_workflow:
                self.scale_up_attempts_per_workflow[workflow_id] = 0
            self.scale_up_attempts_per_workflow[workflow_id] += 1

        if success:
            self.scale_up_successes += 1
            self.total_instances_added += instances_added
            self.total_cores_added += cores_added
        else:
            self.scale_up_failures += 1
            if reason and reason in self.scale_up_failures_by_reason:
                self.scale_up_failures_by_reason[reason] += 1

    def recordScaleDownAttempt(self, success, blocked_reason=None, instances_removed=0, cores_removed=0):
        """
        Record a scale-down attempt and its outcome.

        Args:
            success: True if scale-down succeeded, False if blocked
            blocked_reason: Reason for blocking (if applicable)
            instances_removed: Number of instances freed
            cores_removed: Number of cores freed
        """
        self.scale_down_attempts += 1

        if success:
            self.scale_down_successes += 1
            self.total_instances_removed += instances_removed
            self.total_cores_removed += cores_removed
        else:
            self.scale_down_blocked += 1
            if blocked_reason and blocked_reason in self.scale_down_blocked_by_reason:
                self.scale_down_blocked_by_reason[blocked_reason] += 1

    def collectResourceUtilization(self, sim, rm: ResourceManager):
        """
        Continuously collect resource utilization data.

        Args:
            sim: Simulus simulator instance
            rm: ResourceManager instance
        """
        while self.collectFlag:
            resources = rm.getResources()
            onprem_free, cloud_free = 0, 0

            for instance in resources:
                if isinstance(instance, OnPremInstance):
                    onprem_free += instance.getFreeSlots()
                elif isinstance(instance, CloudReservedInstance):
                    cloud_free += instance.getFreeSlots()

            timestamp = (sim and sim.now) or time.time()
            self.free_resources.append((timestamp, onprem_free, cloud_free))

            (sim or time).sleep(RESOURCE_UTILIZATION_POLLING)

    def computeResourceUtilization(self):
        """
        Compute average resource utilization percentage.

        Dynamically computes total from maximum observed free resources
        (equals total capacity). Same approach as MetricsLA.

        Returns:
            Average utilization percentage (0-100)
        """
        n = len(self.free_resources)
        if n == 0:
            return 0.0

        # Dynamically determine total resources from max observed free
        max_onprem = max(entry[1] for entry in self.free_resources)
        max_cloud = max(entry[2] for entry in self.free_resources)
        total_resources = max_onprem + max_cloud

        if total_resources == 0:
            return 0.0

        # Find start and stop indices where resources are actually in use
        start_idx = 0
        stop_idx = n - 1

        for i in range(n):
            free = self.free_resources[i][1] + self.free_resources[i][2]
            if free < total_resources:
                start_idx = i
                break

        for i in range(n - 1, -1, -1):
            free = self.free_resources[i][1] + self.free_resources[i][2]
            if free < total_resources:
                stop_idx = i
                break

        total_free = 0
        for i in range(start_idx, stop_idx + 1):
            total_free += self.free_resources[i][1] + self.free_resources[i][2]

        num_samples = stop_idx - start_idx + 1
        if num_samples == 0:
            return 0.0

        average_free = total_free / num_samples
        average_used = total_resources - average_free
        utilization_pct = (average_used / total_resources) * 100

        return utilization_pct

    def _infer_scheduler_type(self, file_prefix: str) -> str:
        """
        Infer scheduler type from file prefix.

        Returns:
            'FCFS_Static', 'FCFS_Moldable', 'EDF_Static', 'EDF_Moldable', or 'UNKNOWN'
        """
        if not file_prefix:
            return 'UNKNOWN'

        # Check most specific patterns first
        if 'FCFS_Moldable' in file_prefix:
            return 'FCFS_Moldable'
        elif 'FCFS_Static' in file_prefix:
            return 'FCFS_Static'
        elif 'EDF_Moldable' in file_prefix:
            return 'EDF_Moldable'
        elif 'EDF_Static' in file_prefix:
            return 'EDF_Static'
        else:
            return 'UNKNOWN'

    def computeMetrics(self, file_prefix=''):
        """
        Compute and display all performance metrics.

        Args:
            file_prefix: Prefix for output files (e.g., 'FCFS_Static_HPO_')

        Output files:
            {file_prefix}results.csv  - Per-workflow results
            {file_prefix}resources.csv - Resource utilization time series
            {file_prefix}results.out  - Human-readable summary
        """
        scheduler_type = self._infer_scheduler_type(file_prefix)

        print('\n' + '='*70)
        print(f'COMPUTING HPO METRICS ({scheduler_type})')
        print('='*70)

        self.collectFlag = False

        # Save resource utilization data
        resource_filename = f'{file_prefix}resources.csv' if file_prefix else 'resources.csv'
        resource_df = pd.DataFrame(self.free_resources, columns=['Timestamp', 'On-prem', 'Cloud'])
        resource_df.to_csv(resource_filename)

        # Initialize accumulators
        makespan = 0
        waitTime = 0
        cost = 0
        budget_miss = 0
        deadline_miss = 0
        overall_miss = 0
        executed_workflows = 0
        wasted_cost = 0
        wasted_time = 0

        # Per-model accumulators
        model_stats = {}  # {model: {count, flowtime, cost, deadline_miss, budget_miss}}

        # Process each workflow
        for wf_id in self.df:
            wf_data = self.df[wf_id]

            if wf_data.get('complete', False):
                executed_workflows += 1

                # Flowtime = finish_time - submit_time
                flowtime = wf_data['finish_time'] - wf_data['submit_time']
                makespan += flowtime

                # Wait time = exec_start_time - submit_time
                wait = wf_data['exec_start_time'] - wf_data['submit_time']
                waitTime += wait

                # Compute cost (hardware only, no license)
                wf_cost = self.computeCost(wf_id, wf_data['finish_time'])
                wf_data['cost'] = wf_cost
                cost += wf_cost

                # Check budget violation
                budget_violated = wf_cost > wf_data['budget']
                budget_miss += int(budget_violated)

                # Check deadline violation
                deadline_violated = wf_data['finish_time'] > wf_data['deadline']
                deadline_miss += int(deadline_violated)

                # Overall miss = deadline OR budget violated
                overall_miss += int(budget_violated or deadline_violated)

                # Per-model tracking
                model = wf_data.get('model', 'unknown')
                if model not in model_stats:
                    model_stats[model] = {
                        'count': 0, 'flowtime': 0, 'cost': 0,
                        'deadline_miss': 0, 'budget_miss': 0
                    }
                model_stats[model]['count'] += 1
                model_stats[model]['flowtime'] += flowtime
                model_stats[model]['cost'] += wf_cost
                model_stats[model]['deadline_miss'] += int(deadline_violated)
                model_stats[model]['budget_miss'] += int(budget_violated)

            else:
                # Incomplete workflow
                wasted_cost += self.computeCost(wf_id, wf_data['finish_time'])
                wasted_time += wf_data['finish_time'] - wf_data.get('exec_start_time', wf_data['finish_time'])

        # Print results
        print(f'\nTotal workflows: {TOTAL_WORKFLOWS}')
        print(f'Executed workflows: {executed_workflows}')
        print(f'Incomplete workflows: {TOTAL_WORKFLOWS - executed_workflows}')

        # Prepare summary lines for .out file
        summary_lines = []
        summary_lines.append(f'Scheduler: {scheduler_type}')
        summary_lines.append(f'Total workflows = {TOTAL_WORKFLOWS}')
        summary_lines.append(f'Executed workflows = {executed_workflows}')
        summary_lines.append(f'Incomplete workflows = {TOTAL_WORKFLOWS - executed_workflows}')

        if executed_workflows > 0:
            avg_flowtime = round(makespan / executed_workflows, 4)
            avg_cost = round(cost / executed_workflows, 4)
            avg_wait_time = round(waitTime / executed_workflows, 4)
            avg_utilization = round(self.computeResourceUtilization(), 4)

            deadline_rate = round((deadline_miss + TOTAL_WORKFLOWS - executed_workflows) / TOTAL_WORKFLOWS, 4)
            budget_rate = round(budget_miss / TOTAL_WORKFLOWS, 4)
            overall_rate = round((overall_miss + TOTAL_WORKFLOWS - executed_workflows) / TOTAL_WORKFLOWS, 4)

            print(f'\n--- Performance Metrics ---')
            print(f'Average Flowtime: {avg_flowtime} seconds')
            print(f'Average Cost: ${avg_cost}')
            print(f'Average Wait Time: {avg_wait_time} seconds')
            print(f'Average Resource Utilization: {avg_utilization}%')

            print(f'\n--- Constraint Violations ---')
            print(f'Deadline miss rate: {deadline_rate} ({deadline_miss}/{executed_workflows} executed)')
            print(f'Budget miss rate: {budget_rate} ({budget_miss}/{executed_workflows} executed)')
            print(f'Overall miss rate: {overall_rate}')

            summary_lines.append(f'Average Flowtime = {avg_flowtime}')
            summary_lines.append(f'Average Cost = {avg_cost}')
            summary_lines.append(f'Average Wait Time = {avg_wait_time}')
            summary_lines.append(f'Average Resource Utilization = {avg_utilization}')
            summary_lines.append(f'Deadline miss rate = {deadline_rate}')
            summary_lines.append(f'Budget miss rate = {budget_rate}')
            summary_lines.append(f'Overall miss rate = {overall_rate}')

            # Per-model breakdown
            if model_stats:
                print(f'\n--- Per-Model Breakdown ---')
                summary_lines.append('')
                summary_lines.append('--- Per-Model Breakdown ---')
                for model in sorted(model_stats.keys()):
                    stats = model_stats[model]
                    n = stats['count']
                    avg_ft = round(stats['flowtime'] / n, 2)
                    avg_c = round(stats['cost'] / n, 4)
                    dl_miss = stats['deadline_miss']
                    bg_miss = stats['budget_miss']
                    print(f'  {model} ({n} workflows):')
                    print(f'    Avg Flowtime: {avg_ft}s, Avg Cost: ${avg_c}')
                    print(f'    Deadline misses: {dl_miss}, Budget misses: {bg_miss}')
                    summary_lines.append(f'{model}: count={n}, avg_flowtime={avg_ft}, avg_cost={avg_c}, deadline_miss={dl_miss}, budget_miss={bg_miss}')

            if TOTAL_WORKFLOWS - executed_workflows > 0:
                wasted_time_hours = round(wasted_time / 3600, 2)
                wasted_cost_total = round(wasted_cost, 2)
                print(f'\n--- Incomplete Workflow Costs ---')
                print(f'Time spent on incomplete workflows: {wasted_time_hours} hours')
                print(f'Wasted cost on incomplete workflows: ${wasted_cost_total}')
                summary_lines.append(f'Time spent on incomplete workflows = {wasted_time_hours} hours')
                summary_lines.append(f'Wasted cost on incomplete workflows = {wasted_cost_total}')

            # Moldability effectiveness report
            is_static = scheduler_type in ['FCFS_Static', 'EDF_Static']

            if is_static:
                print(f'\n--- Moldability Status ---')
                print(f'Scheduler Type: Non-Moldable ({scheduler_type})')
                print(f'Resources allocated once at workflow start (no dynamic scaling)')
                summary_lines.append(f'Moldability: Static (no dynamic scaling)')
            else:
                print(f'\n--- Moldability Effectiveness ---')
                print(f'Scheduler Type: {scheduler_type}')
                print(f'Scale-up attempts: {self.scale_up_attempts}')
                summary_lines.append(f'Scale-up attempts = {self.scale_up_attempts}')

                if self.scale_up_attempts > 0:
                    success_rate = round(self.scale_up_successes / self.scale_up_attempts * 100, 1)
                    print(f'  Successes: {self.scale_up_successes} ({success_rate}%)')
                    print(f'  Failures: {self.scale_up_failures}')
                    if self.scale_up_failures > 0:
                        print(f'    - Insufficient compute: {self.scale_up_failures_by_reason["insufficient_compute"]}')
                        print(f'    - Budget exhausted: {self.scale_up_failures_by_reason["budget_exhausted"]}')
                        print(f'    - Time exhausted: {self.scale_up_failures_by_reason["time_exhausted"]}')
                    print(f'  Total instances added: {self.total_instances_added}')
                    print(f'  Total cores added: {self.total_cores_added}')
                    summary_lines.append(f'Scale-up successes = {self.scale_up_successes} ({success_rate}%)')

                print(f'\nScale-down attempts: {self.scale_down_attempts}')
                summary_lines.append(f'Scale-down attempts = {self.scale_down_attempts}')

                if self.scale_down_attempts > 0:
                    success_rate = round(self.scale_down_successes / self.scale_down_attempts * 100, 1)
                    print(f'  Successes: {self.scale_down_successes} ({success_rate}%)')
                    print(f'  Blocked: {self.scale_down_blocked}')
                    if self.scale_down_blocked > 0:
                        print(f'    - Late iteration: {self.scale_down_blocked_by_reason["late_iteration"]}')
                        print(f'    - Time progress (>70%): {self.scale_down_blocked_by_reason["time_progress"]}')
                        print(f'    - Budget or time progress (>50%): {self.scale_down_blocked_by_reason["budget_or_time_progress"]}')
                    print(f'  Total instances removed: {self.total_instances_removed}')
                    print(f'  Total cores removed: {self.total_cores_removed}')
                    summary_lines.append(f'Scale-down successes = {self.scale_down_successes} ({success_rate}%)')

                # Net moldability impact
                if self.scale_up_attempts > 0 or self.scale_down_attempts > 0:
                    net_instances = self.total_instances_added - self.total_instances_removed
                    net_cores = self.total_cores_added - self.total_cores_removed
                    print(f'\nNet moldability impact:')
                    print(f'  Net instances: {net_instances:+d}')
                    print(f'  Net cores: {net_cores:+d}')
                    summary_lines.append(f'Net instances = {net_instances:+d}')
                    summary_lines.append(f'Net cores = {net_cores:+d}')

                    # Effectiveness assessment
                    failure_rate = self.scale_up_failures / self.scale_up_attempts if self.scale_up_attempts > 0 else 0.0
                    if failure_rate > 0.5:
                        print(f'  WARNING: High scale-up failure rate ({failure_rate*100:.1f}%)')
                    elif self.scale_up_successes > 0:
                        print(f'  Moldability active: {self.scale_up_successes} successful scale-ups')

                # Scale-up opportunity analysis
                workflows_with_no_scale_ups = 0
                deadline_misses_with_no_scale_ups = 0

                for wf_id in self.df:
                    wf_data = self.df[wf_id]
                    scale_up_count = self.scale_up_attempts_per_workflow.get(wf_id, 0)
                    if scale_up_count == 0:
                        workflows_with_no_scale_ups += 1
                        if wf_data.get('complete', False) and wf_data['finish_time'] > wf_data['deadline']:
                            deadline_misses_with_no_scale_ups += 1

                if executed_workflows > 0:
                    print(f'\n--- Scale-Up Opportunity Analysis ---')
                    print(f'Workflows with 0 scale-up attempts: {workflows_with_no_scale_ups} ({workflows_with_no_scale_ups/executed_workflows*100:.1f}%)')
                    if deadline_misses_with_no_scale_ups > 0:
                        print(f'Deadline misses with 0 scale-ups: {deadline_misses_with_no_scale_ups}')
                    avg_scale_ups = self.scale_up_attempts / executed_workflows
                    print(f'Average scale-up attempts per workflow: {avg_scale_ups:.2f}')

        else:
            print('\n[WARNING] No workflows completed!')
            summary_lines.append('WARNING: No workflows completed')

        print('='*70 + '\n')

        # Save summary to .out file
        out_filename = f'{file_prefix}results.out' if file_prefix else 'results.out'
        with open(out_filename, 'w') as f:
            f.write('\n'.join(summary_lines) + '\n')

        # Save detailed results to CSV
        results_filename = f'{file_prefix}results.csv' if file_prefix else 'results.csv'
        df = pd.DataFrame(self.df).T
        df.to_csv(results_filename)

        exit()
