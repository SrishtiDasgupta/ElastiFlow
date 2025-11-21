"""
License-Aware Metrics Calculation Module

Computes performance metrics for LAMF (License-Aware Moldable FCFS) scheduler.
Fixes issues in original metrics.py:
- Corrected cost calculation (accumulate actual usage, not differential)
- Fixed resource utilization (use actual total resources, not constant)
- Proper budget/deadline miss rate calculations
"""

import pandas as pd
import time

from config.constants_LA import RESOURCE_UTILIZATION_POLLING, TOTAL_WORKFLOWS
from resource_manager.instance import CloudReservedInstance, OnPremInstance
from resource_manager.resource_manager import ResourceManager

# ============================================================================
# License Cost Constants (based on Henkel & Treiber 2015 and JSSPP 2025)
# ============================================================================

# Annual costs converted to cost per second = Annual / (365.25 * 24 * 3600)
SECONDS_PER_YEAR = 365.25 * 24 * 3600  # 31,557,600 seconds

# LS-Dyna: €1000 per token per year, T(n) = n
LSDYNA_COST_PER_TOKEN_SEC = 1000.0 / SECONDS_PER_YEAR  # ~31.7 µ€/s

# Abaqus: €2500 per token per year, T(n) = 5 × n^0.422
ABAQUS_COST_PER_TOKEN_SEC = 2500.0 / SECONDS_PER_YEAR  # ~79.3 µ€/s

# Ansys: MEBA (solver) + HPC Workgroup licenses
ANSYS_MEBA_COST_SEC = 14000.0 / SECONDS_PER_YEAR       # ~476 µ€/s
ANSYS_WORKGROUP_COST_SEC = 1700.0 / SECONDS_PER_YEAR   # ~54 µ€/s

# Software ID mapping (from constants_LA.py)
SOFTWARE_ID_ANSYS = 1
SOFTWARE_ID_ABAQUS = 2
SOFTWARE_ID_LSDYNA = 3


class MetricsLA:
    """
    Metrics collection and calculation for License-Aware scheduling.

    Tracks:
    - Workflow execution times (submit, start, finish)
    - Resource allocations and costs
    - Budget and deadline constraints
    - Resource utilization over time
    """

    def __init__(self):
        self.df = {}
        self.free_resources = []
        self.collectFlag = True
        self.total_resources = 0  # Will be computed dynamically

        # License utilization tracking (per pool)
        # Format: [(timestamp, pool_name, total_tokens, allocated_tokens, utilization%), ...]
        self.license_utilization = []

        # Moldability effectiveness tracking
        self.scale_up_attempts = 0
        self.scale_up_successes = 0
        self.scale_up_failures = 0
        self.scale_up_failures_by_reason = {
            'insufficient_compute': 0,
            'insufficient_licenses': 0,
            'budget_exhausted': 0,
            'time_exhausted': 0
        }

        # Track scale-up attempts per workflow (to detect missed opportunities)
        # Format: {workflow_id: num_scale_up_attempts}
        self.scale_up_attempts_per_workflow = {}

        self.scale_down_attempts = 0
        self.scale_down_successes = 0
        self.scale_down_blocked = 0
        self.scale_down_blocked_by_reason = {
            'license_pool_saturated': 0,
            'late_iteration': 0,
            'time_progress': 0,
            'budget_or_time_progress': 0
        }

        self.total_instances_added = 0
        self.total_cores_added = 0
        self.total_instances_removed = 0
        self.total_cores_removed = 0
        self.total_licenses_acquired = 0
        self.total_licenses_released = 0

    def calculate_license_tokens(self, software_id, cores):
        """
        Calculate number of license tokens required based on software type and cores.

        Based on formulas from Henkel & Treiber 2015 and JSSPP 2025 Section 2.2:
        - LS-Dyna: T(n) = n
        - Abaqus: T(n) = 5 × n^0.422
        - Ansys: T_meba = 1, T_workgroup = n - 4 (for n > 4)

        Args:
            software_id: Software identifier (1=ANSYS, 2=ABAQUS, 3=LSDYNA, 0=unlicensed)
            cores: Number of CPU cores allocated

        Returns:
            Number of license tokens required

        Raises:
            ValueError: If cores is negative or invalid
        """
        # Defensive check: cores must be non-negative
        if cores < 0:
            raise ValueError(f"Invalid cores value: {cores}. Cores cannot be negative. "
                           f"This indicates an accounting bug in instance allocation/deallocation.")

        if software_id == SOFTWARE_ID_LSDYNA:  # LS-Dyna
            return float(cores)

        elif software_id == SOFTWARE_ID_ABAQUS:  # Abaqus
            # Power law formula: for negative cores, this would produce complex numbers
            return 5.0 * (cores ** 0.422)

        elif software_id == SOFTWARE_ID_ANSYS:  # Ansys (workgroup model)
            # 1 MEBA token (covers up to 4 cores) + workgroup licenses for additional cores
            return 1.0 + max(0, cores - 4)

        else:  # Unlicensed or unknown
            return 0.0

    def calculate_license_cost(self, software_id, cores, duration):
        """
        Calculate total license cost for a given allocation.

        Total cost = tokens × cost_per_second × duration

        Args:
            software_id: Software identifier
            cores: Number of CPU cores allocated
            duration: Duration in seconds

        Returns:
            License cost in euros

        Raises:
            ValueError: If cores or duration is negative
        """
        # Defensive checks
        if cores < 0:
            raise ValueError(f"Invalid cores value: {cores}. Cores cannot be negative.")
        if duration < 0:
            raise ValueError(f"Invalid duration value: {duration}. Duration cannot be negative.")

        if software_id == 0 or cores == 0 or duration <= 0:
            return 0.0

        tokens = self.calculate_license_tokens(software_id, cores)

        if software_id == SOFTWARE_ID_LSDYNA:
            cost_per_token_sec = LSDYNA_COST_PER_TOKEN_SEC
            return tokens * cost_per_token_sec * duration

        elif software_id == SOFTWARE_ID_ABAQUS:
            cost_per_token_sec = ABAQUS_COST_PER_TOKEN_SEC
            return tokens * cost_per_token_sec * duration

        elif software_id == SOFTWARE_ID_ANSYS:
            # MEBA (1 token) + Workgroup licenses (n-4 tokens)
            meba_cost = ANSYS_MEBA_COST_SEC * duration
            workgroup_tokens = max(0, cores - 4)
            workgroup_cost = workgroup_tokens * ANSYS_WORKGROUP_COST_SEC * duration
            return meba_cost + workgroup_cost

        return 0.0

    def addToDataframe(self, id, wf, submit_time: float):
        """
        Add a new workflow to tracking.

        Args:
            id: Workflow identifier
            wf: Workflow tuple (instances, budget, deadline, sched_start_time, mesh, software_id, license_holds)
            submit_time: Time workflow was submitted
        """
        instances = self.processInstances(wf[0], wf[3])
        data = {
            'instances': instances,
            'budget': wf[1],
            'deadline': wf[2],
            'sched_start_time': wf[3],
            'submit_time': submit_time,
            'software_id': wf[5] if len(wf) > 5 else 0  # Extract software_id from workflow tuple
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

    def updateResources(self, id, instance_obj, count, timestamp, action='add'):
        """
        Update resource allocation for a moldable workflow.

        For moldable workflows that scale up/down, this tracks each allocation
        segment with its own start and end times.

        Args:
            id: Workflow identifier
            instance_obj: Instance object being allocated/freed
            count: Number of instances
            timestamp: Time of the allocation change
            action: 'add' for scale-up, 'remove' for scale-down

        Example usage:
            # Scale up: add 3 instances at t=100
            updateResources('wf1', m5_large, 3, 100, 'add')
            # Result: [(3, 100, None)]

            # Scale down: remove 2 instances at t=200
            updateResources('wf1', m5_large, 2, 200, 'remove')
            # Result: [(3, 100, 200), (1, 200, None)]
        """
        # Handle zero count as no-op (valid case when cur_count == min_needed_count)
        if count == 0:
            print(f"  ⚠ [Metrics] Skipping {action} with count=0 for {instance_obj.name} (no-op)")
            return

        # Defensive validation: count cannot be negative
        if count < 0:
            raise ValueError(f"Invalid count: {count}. Instance count cannot be negative. "
                           f"This indicates a bug in scheduler allocation logic.")

        if id not in self.df:
            return

        # Ensure instance tracking exists
        if instance_obj not in self.df[id]['instances']:
            self.df[id]['instances'][instance_obj] = []

        allocations = self.df[id]['instances'][instance_obj]

        if action == 'add':
            # Add new allocation starting at timestamp
            allocations.append((count, timestamp, None))
            print(f"  📊 [Metrics] Added {count} {instance_obj.name} instances at t={timestamp:.1f}s")

        elif action == 'remove':
            # Find most recent open allocation and close it
            # Then create a new allocation with reduced count
            for i in range(len(allocations) - 1, -1, -1):
                alloc_count, start, finish = allocations[i]

                if finish is None:  # Open allocation
                    if alloc_count > count:
                        # Partial removal: close old, open new with reduced count
                        allocations[i] = (alloc_count, start, timestamp)
                        remaining = alloc_count - count
                        allocations.append((remaining, timestamp, None))
                        print(f"  📊 [Metrics] Removed {count} {instance_obj.name} instances at t={timestamp:.1f}s, {remaining} remaining")
                        break
                    elif alloc_count == count:
                        # Full removal: just close the allocation
                        allocations[i] = (alloc_count, start, timestamp)
                        print(f"  📊 [Metrics] Removed all {count} {instance_obj.name} instances at t={timestamp:.1f}s")
                        break
                    else:
                        # Remove this allocation entirely and continue to next
                        allocations[i] = (alloc_count, start, timestamp)
                        count -= alloc_count
                        # Continue to remove more from earlier allocations

    def recordScaleUpAttempt(self, success, reason=None, instances_added=0, cores_added=0, licenses_acquired=0, workflow_id=None):
        """
        Record a scale-up attempt and its outcome.

        Args:
            success: True if scale-up succeeded, False otherwise
            reason: Reason for failure (if applicable): 'insufficient_compute',
                   'insufficient_licenses', 'budget_exhausted', 'time_exhausted'
            instances_added: Number of instances successfully added
            cores_added: Number of cores successfully added
            licenses_acquired: Number of license tokens acquired
            workflow_id: Workflow identifier (to track attempts per workflow)
        """
        self.scale_up_attempts += 1

        # Track per-workflow attempts
        if workflow_id:
            if workflow_id not in self.scale_up_attempts_per_workflow:
                self.scale_up_attempts_per_workflow[workflow_id] = 0
            self.scale_up_attempts_per_workflow[workflow_id] += 1

        if success:
            self.scale_up_successes += 1
            self.total_instances_added += instances_added
            self.total_cores_added += cores_added
            self.total_licenses_acquired += licenses_acquired
        else:
            self.scale_up_failures += 1
            if reason and reason in self.scale_up_failures_by_reason:
                self.scale_up_failures_by_reason[reason] += 1

    def recordScaleDownAttempt(self, success, blocked_reason=None, instances_removed=0, cores_removed=0, licenses_released=0):
        """
        Record a scale-down attempt and its outcome.

        Args:
            success: True if scale-down succeeded, False if blocked
            blocked_reason: Reason for blocking (if applicable): 'license_pool_saturated',
                          'late_iteration', 'time_progress'
            instances_removed: Number of instances freed
            cores_removed: Number of cores freed
            licenses_released: Number of license tokens released
        """
        self.scale_down_attempts += 1

        if success:
            self.scale_down_successes += 1
            self.total_instances_removed += instances_removed
            self.total_cores_removed += cores_removed
            self.total_licenses_released += licenses_released
        else:
            self.scale_down_blocked += 1
            if blocked_reason and blocked_reason in self.scale_down_blocked_by_reason:
                self.scale_down_blocked_by_reason[blocked_reason] += 1

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

    def computeCost(self, id, wf_finish_time):
        """
        Compute hardware cost for a workflow.

        Hardware cost = Σ(instance_cost_per_sec × instance_count × duration)

        License costs are tracked separately for reporting but not included in the
        returned value, as budgets are based on hardware costs only.

        Args:
            id: Workflow identifier
            wf_finish_time: Workflow completion time

        Returns:
            float: Hardware cost in euros
        """
        hardware_cost = 0.0
        license_cost = 0.0

        instances = self.df[id]['instances']  # {instance_obj: [(count, start, end), ...]}
        software_id = self.df[id].get('software_id', 0)  # Get software type

        for instance_obj in instances:
            instance_list = instances[instance_obj]
            hw_cost_per_second = instance_obj.getCostPerSecond()
            cores_per_instance = instance_obj.cores  # Get cores from instance object

            for count, start_time, finish_time in instance_list:
                # Determine actual resource usage duration
                if finish_time is not None:
                    # Resources were explicitly released at finish_time
                    duration = finish_time - start_time
                else:
                    # Resources held until workflow completion
                    duration = wf_finish_time - start_time

                # Hardware cost: instance_rate × instance_count × duration
                allocation_hw_cost = hw_cost_per_second * count * duration
                hardware_cost += allocation_hw_cost

                # License cost: based on total cores allocated (for reporting only)
                total_cores = cores_per_instance * count
                allocation_lic_cost = self.calculate_license_cost(software_id, total_cores, duration)
                license_cost += allocation_lic_cost

        # Store license cost for end-of-run reporting
        self.df[id]['license_cost'] = license_cost

        return hardware_cost

    def computeCurrentCost(self, id, current_time):
        """
        Compute hardware cost for an in-progress workflow up to current_time.

        Unlike computeCost(), this method is designed to handle workflows
        that are still executing. It treats current_time as the end time
        for any allocations that haven't been explicitly released yet.

        License costs are tracked separately for reporting but not included in the
        returned value, as budgets are based on hardware costs only.

        Args:
            id: Workflow identifier
            current_time: Current simulation time

        Returns:
            float: Hardware cost accumulated so far in euros
        """
        hardware_cost = 0.0
        license_cost = 0.0

        # Check if workflow exists in tracking
        if id not in self.df:
            return 0.0

        instances = self.df[id]['instances']  # {instance_obj: [(count, start, end), ...]}
        software_id = self.df[id].get('software_id', 0)  # Get software type

        for instance_obj in instances:
            instance_list = instances[instance_obj]
            hw_cost_per_second = instance_obj.getCostPerSecond()
            cores_per_instance = instance_obj.cores

            for count, start_time, finish_time in instance_list:
                # Skip allocations that haven't started yet
                if start_time is None or start_time > current_time:
                    continue

                # Determine actual resource usage duration up to now
                if finish_time is not None:
                    # Resources were explicitly released
                    if finish_time <= current_time:
                        # Already released - use actual duration
                        duration = finish_time - start_time
                    else:
                        # Release scheduled for future - use current time
                        duration = current_time - start_time
                else:
                    # Resources still held - calculate cost up to current time
                    duration = current_time - start_time

                # Hardware cost
                allocation_hw_cost = hw_cost_per_second * count * duration
                hardware_cost += allocation_hw_cost

                # License cost (for tracking only)
                total_cores = cores_per_instance * count
                allocation_lic_cost = self.calculate_license_cost(software_id, total_cores, duration)
                license_cost += allocation_lic_cost

        # Store current license cost for tracking
        self.df[id]['current_license_cost'] = license_cost

        return hardware_cost

    def computeMetrics(self, file_prefix=''):
        """
        Compute and display all performance metrics.

        Args:
            file_prefix: Optional prefix for output files (e.g., 'LAMF_400_' or 'Baseline_400_')

        Metrics calculated:
        - Average Flowtime (completion time - submit time)
        - Average Cost
        - Average Wait Time (exec start - submit time)
        - Average Resource Utilization
        - Deadline Miss Rate
        - Budget Miss Rate
        - Overall Miss Rate (deadline OR budget violated)
        - Incomplete workflow statistics
        """
        print('\n' + '='*70)
        print('COMPUTING LAMF METRICS')
        print('='*70)

        self.collectFlag = False

        # Save resource utilization data
        resource_filename = f'{file_prefix}resources.csv' if file_prefix else 'resources.csv'
        resource_df = pd.DataFrame(self.free_resources, columns=['Timestamp', 'On-prem', 'Cloud'])
        resource_df.to_csv(resource_filename)

        # Save license utilization data
        if self.license_utilization:
            license_filename = f'{file_prefix}license_usage.csv' if file_prefix else 'license_usage.csv'
            license_df = pd.DataFrame(
                self.license_utilization,
                columns=['Timestamp', 'Pool', 'Total_Tokens', 'Allocated_Tokens', 'Utilization_Percent']
            )
            license_df.to_csv(license_filename, index=False)
            print(f'\n📊 License usage data saved to {license_filename} ({len(self.license_utilization)} samples)')

        # Initialize accumulators
        makespan = 0
        waitTime = 0
        cost = 0
        hardware_cost_total = 0
        license_cost_total = 0
        budget_miss = 0
        deadline_miss = 0
        overall_miss = 0
        executed_workflows = 0
        wasted_cost = 0
        wasted_hardware_cost = 0
        wasted_license_cost = 0
        wasted_time = 0

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

                # Compute hardware cost (license cost is stored separately)
                wf_hw_cost = self.computeCost(wf_id, wf_data['finish_time'])
                wf_lic_cost = wf_data.get('license_cost', 0.0)  # Stored by computeCost()
                wf_cost = wf_hw_cost + wf_lic_cost  # Total cost for reporting

                wf_data['cost'] = wf_cost
                wf_data['hardware_cost'] = wf_hw_cost
                # license_cost already stored by computeCost()

                cost += wf_cost
                hardware_cost_total += wf_hw_cost
                license_cost_total += wf_lic_cost

                # Check budget violation (only against hardware cost, as budgets are hardware-only)
                budget_violated = wf_hw_cost > wf_data['budget']
                budget_miss += int(budget_violated)

                # Check deadline violation
                deadline_violated = wf_data['finish_time'] > wf_data['deadline']
                deadline_miss += int(deadline_violated)

                # Overall miss = deadline OR budget violated
                overall_miss += int(budget_violated or deadline_violated)

            else:
                # Incomplete workflow
                wf_wasted_hw = self.computeCost(wf_id, wf_data['finish_time'])
                wf_wasted_lic = wf_data.get('license_cost', 0.0)  # Stored by computeCost()
                wf_wasted_cost = wf_wasted_hw + wf_wasted_lic

                wasted_cost += wf_wasted_cost
                wasted_hardware_cost += wf_wasted_hw
                wasted_license_cost += wf_wasted_lic
                wasted_time += wf_data['finish_time'] - wf_data.get('exec_start_time', wf_data['finish_time'])

        # Print results
        print(f'\nTotal workflows: {TOTAL_WORKFLOWS}')
        print(f'Executed workflows: {executed_workflows}')
        print(f'Incomplete workflows: {TOTAL_WORKFLOWS - executed_workflows}')

        if executed_workflows > 0:
            print(f'\n--- Performance Metrics ---')
            print(f'Average Flowtime: {round(makespan/executed_workflows, 4)} seconds')
            print(f'Average Cost: €{round(cost/executed_workflows, 4)}')
            print(f'  - Average Hardware Cost: €{round(hardware_cost_total/executed_workflows, 4)}')
            print(f'  - Average License Cost: €{round(license_cost_total/executed_workflows, 4)}')
            if cost > 0:
                lic_pct = (license_cost_total / cost) * 100
                print(f'  - License Cost %: {round(lic_pct, 2)}%')
            print(f'Average Wait Time: {round(waitTime/executed_workflows, 4)} seconds')

            # Total costs summary (budgets are hardware-only)
            print(f'\n--- Total Costs Summary ---')
            print(f'Total Hardware Cost: €{round(hardware_cost_total, 2)}')
            print(f'Total License Cost (additional): €{round(license_cost_total, 2)}')
            print(f'Total Combined Cost: €{round(cost, 2)}')
            if hardware_cost_total > 0:
                overhead_pct = (license_cost_total / hardware_cost_total) * 100
                print(f'License Cost Overhead: {round(overhead_pct, 2)}% of hardware cost')

            # Resource utilization
            avg_utilization = self.computeResourceUtilization()
            print(f'Average Resource Utilization: {round(avg_utilization, 4)}%')

            # License utilization per pool
            license_stats = self.computeLicenseUtilization()
            if license_stats:
                print(f'\n--- License Utilization by Pool ---')
                for pool_name in sorted(license_stats.keys()):
                    stats = license_stats[pool_name]
                    avg_util = stats['average_utilization']
                    peak = stats['peak_allocated']
                    total = stats['total_tokens']
                    peak_pct = (peak / total * 100) if total > 0 else 0
                    print(f'{pool_name}:')
                    print(f'  Average Utilization: {round(avg_util, 2)}%')
                    print(f'  Peak Allocated: {peak}/{total} tokens ({round(peak_pct, 2)}%)')
                    print(f'  Samples: {stats["samples"]}')

            print(f'\n--- Constraint Violations ---')
            deadline_rate = round((deadline_miss + TOTAL_WORKFLOWS - executed_workflows) / TOTAL_WORKFLOWS, 4)
            budget_rate = round(budget_miss / TOTAL_WORKFLOWS, 4)
            overall_rate = round((overall_miss + TOTAL_WORKFLOWS - executed_workflows) / TOTAL_WORKFLOWS, 4)

            print(f'Deadline miss rate: {deadline_rate} ({deadline_miss}/{executed_workflows} executed)')
            print(f'Budget miss rate: {budget_rate} ({budget_miss}/{executed_workflows} executed)')
            print(f'Overall miss rate: {overall_rate}')

            if TOTAL_WORKFLOWS - executed_workflows > 0:
                print(f'\n--- Incomplete Workflow Costs ---')
                print(f'Time spent on incomplete workflows: {round(wasted_time / 3600, 2)} hours')
                print(f'Wasted cost on incomplete workflows: €{round(wasted_cost, 2)}')
                print(f'  - Wasted Hardware Cost: €{round(wasted_hardware_cost, 2)}')
                print(f'  - Wasted License Cost: €{round(wasted_license_cost, 2)}')

            # Moldability effectiveness report
            print(f'\n--- Moldability Effectiveness ---')
            print(f'Scale-up attempts: {self.scale_up_attempts}')
            if self.scale_up_attempts > 0:
                success_rate = round(self.scale_up_successes / self.scale_up_attempts * 100, 1)
                print(f'  Successes: {self.scale_up_successes} ({success_rate}%)')
                print(f'  Failures: {self.scale_up_failures}')
                if self.scale_up_failures > 0:
                    print(f'    - Insufficient compute: {self.scale_up_failures_by_reason["insufficient_compute"]}')
                    print(f'    - Insufficient licenses: {self.scale_up_failures_by_reason["insufficient_licenses"]}')
                    print(f'    - Budget exhausted: {self.scale_up_failures_by_reason["budget_exhausted"]}')
                    print(f'    - Time exhausted: {self.scale_up_failures_by_reason["time_exhausted"]}')
                print(f'  Total instances added: {self.total_instances_added}')
                print(f'  Total cores added: {self.total_cores_added}')
                print(f'  Total licenses acquired: {self.total_licenses_acquired}')

            print(f'\nScale-down attempts: {self.scale_down_attempts}')
            if self.scale_down_attempts > 0:
                success_rate = round(self.scale_down_successes / self.scale_down_attempts * 100, 1)
                print(f'  Successes: {self.scale_down_successes} ({success_rate}%)')
                print(f'  Blocked: {self.scale_down_blocked}')
                if self.scale_down_blocked > 0:
                    print(f'    - License pool saturated: {self.scale_down_blocked_by_reason["license_pool_saturated"]}')
                    print(f'    - Late iteration (>3): {self.scale_down_blocked_by_reason["late_iteration"]}')
                    print(f'    - Time progress (>70%): {self.scale_down_blocked_by_reason["time_progress"]}')
                    print(f'    - Budget or time progress (>50%): {self.scale_down_blocked_by_reason["budget_or_time_progress"]}')
                print(f'  Total instances removed: {self.total_instances_removed}')
                print(f'  Total cores removed: {self.total_cores_removed}')
                print(f'  Total licenses released: {self.total_licenses_released}')

            # Net moldability benefit analysis
            if self.scale_up_attempts > 0 or self.scale_down_attempts > 0:
                net_instances = self.total_instances_added - self.total_instances_removed
                net_cores = self.total_cores_added - self.total_cores_removed
                net_licenses = self.total_licenses_acquired - self.total_licenses_released
                print(f'\nNet moldability impact:')
                print(f'  Net instances: {net_instances:+d}')
                print(f'  Net cores: {net_cores:+d}')
                print(f'  Net licenses: {net_licenses:+d}')

                # Assess whether moldability is helping or hurting
                if self.scale_up_attempts > 0:
                    failure_rate = self.scale_up_failures / self.scale_up_attempts
                    if failure_rate > 0.5:
                        print(f'  ⚠ WARNING: High scale-up failure rate ({failure_rate*100:.1f}%) - moldability may be counterproductive')
                    elif self.scale_up_successes > 0 and self.total_instances_added > 0:
                        print(f'  ✓ Moldability appears effective - {self.scale_up_successes} successful scale-ups')

            # Analyze missed scale-up opportunities
            workflows_with_no_scale_ups = 0
            workflows_with_few_scale_ups = 0  # < 2 attempts
            deadline_misses_with_no_scale_ups = 0

            for wf_id in self.df:
                wf_data = self.df[wf_id]
                scale_up_count = self.scale_up_attempts_per_workflow.get(wf_id, 0)

                if scale_up_count == 0:
                    workflows_with_no_scale_ups += 1
                    # Check if this workflow missed deadline
                    if wf_data.get('complete', False):
                        if wf_data['finish_time'] > wf_data['deadline']:
                            deadline_misses_with_no_scale_ups += 1
                elif scale_up_count < 2:
                    workflows_with_few_scale_ups += 1

            if executed_workflows > 0:
                print(f'\n--- Scale-Up Opportunity Analysis ---')
                print(f'Workflows with 0 scale-up attempts: {workflows_with_no_scale_ups} ({workflows_with_no_scale_ups/executed_workflows*100:.1f}%)')
                print(f'Workflows with <2 scale-up attempts: {workflows_with_few_scale_ups} ({workflows_with_few_scale_ups/executed_workflows*100:.1f}%)')
                if deadline_misses_with_no_scale_ups > 0:
                    print(f'⚠ Deadline misses with 0 scale-ups: {deadline_misses_with_no_scale_ups}')
                    print(f'  → These workflows may have benefited from scale-up attempts')

                avg_scale_ups_per_workflow = self.scale_up_attempts / executed_workflows if executed_workflows > 0 else 0
                print(f'Average scale-up attempts per workflow: {avg_scale_ups_per_workflow:.2f}')

        else:
            print('\n[WARNING] No workflows completed!')

        print('='*70 + '\n')

        # Save detailed results
        results_filename = f'{file_prefix}results.csv' if file_prefix else 'results.csv'
        df = pd.DataFrame(self.df).T
        df.to_csv(results_filename)

        exit()

    def collectResourceUtilization(self, sim, rm: ResourceManager, license_manager=None):
        """
        Continuously collect resource utilization data and license pool utilization.

        Args:
            sim: Simulus simulator instance
            rm: ResourceManager instance
            license_manager: LicenseManager instance (optional)
        """
        while self.collectFlag:
            resources = rm.getResources()
            onprem_free, cloud_free = 0, 0

            # Count free slots by resource type
            for instance in resources:
                if isinstance(instance, OnPremInstance):
                    onprem_free += instance.getFreeSlots()
                elif isinstance(instance, CloudReservedInstance):
                    cloud_free += instance.getFreeSlots()

            timestamp = (sim and sim.now) or time.time()
            self.free_resources.append((timestamp, onprem_free, cloud_free))

            # Collect license pool utilization if license_manager available
            if license_manager:
                for pool_name in ['ANSYS', 'ABAQUS', 'LSDYNA']:
                    try:
                        pool_status = license_manager.get_pool_status(pool_name)
                        total = pool_status['total']
                        allocated = pool_status['allocated']
                        utilization = (allocated / total * 100) if total > 0 else 0.0
                        self.license_utilization.append((timestamp, pool_name, total, allocated, utilization))
                    except Exception as e:
                        # Silently skip if pool doesn't exist
                        pass

            (sim or time).sleep(RESOURCE_UTILIZATION_POLLING)

    def computeResourceUtilization(self):
        """
        Compute average resource utilization percentage.

        FIXED: Original implementation used hardcoded TOTAL_RESOURCES=13,
        causing negative utilization. Now dynamically computes total from
        maximum observed free resources (equals total capacity).

        Returns:
            Average utilization percentage (0-100)
        """
        n = len(self.free_resources)
        if n == 0:
            return 0.0

        # Dynamically determine total resources from max observed free
        # (at start, all resources are free)
        max_onprem = max(entry[1] for entry in self.free_resources)
        max_cloud = max(entry[2] for entry in self.free_resources)
        total_resources = max_onprem + max_cloud

        if total_resources == 0:
            return 0.0

        # Find start and stop indices where resources are actually in use
        start_idx = 0
        stop_idx = n - 1

        # Find first point where resources are allocated
        for i in range(n):
            free = self.free_resources[i][1] + self.free_resources[i][2]
            if free < total_resources:
                start_idx = i
                break

        # Find last point where resources are allocated
        for i in range(n - 1, -1, -1):
            free = self.free_resources[i][1] + self.free_resources[i][2]
            if free < total_resources:
                stop_idx = i
                break

        # Calculate total free resources during active period
        total_free = 0
        for i in range(start_idx, stop_idx + 1):
            total_free += self.free_resources[i][1] + self.free_resources[i][2]

        # Utilization = (total capacity - average free) / total capacity
        num_samples = stop_idx - start_idx + 1
        if num_samples == 0:
            return 0.0

        average_free = total_free / num_samples
        average_used = total_resources - average_free
        utilization_pct = (average_used / total_resources) * 100

        return utilization_pct

    def computeLicenseUtilization(self):
        """
        Compute average license utilization per pool.

        Returns:
            Dictionary mapping pool name to average utilization percentage
        """
        if not self.license_utilization:
            return {}

        pool_stats = {}

        # Group data by pool
        for timestamp, pool_name, total, allocated, utilization in self.license_utilization:
            if pool_name not in pool_stats:
                pool_stats[pool_name] = {
                    'utilizations': [],
                    'total_tokens': total,
                    'peak_allocated': 0,
                    'samples': 0
                }

            pool_stats[pool_name]['utilizations'].append(utilization)
            pool_stats[pool_name]['peak_allocated'] = max(pool_stats[pool_name]['peak_allocated'], allocated)
            pool_stats[pool_name]['samples'] += 1

        # Calculate averages
        results = {}
        for pool_name, stats in pool_stats.items():
            avg_utilization = sum(stats['utilizations']) / len(stats['utilizations'])
            results[pool_name] = {
                'average_utilization': avg_utilization,
                'peak_allocated': stats['peak_allocated'],
                'total_tokens': stats['total_tokens'],
                'samples': stats['samples']
            }

        return results
