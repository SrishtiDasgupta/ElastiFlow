"""
Metrics collection and statistical analysis for manual vs automated comparison.

Provides comprehensive metrics across three categories:
1. Makespan (total completion time)
2. Per-workflow turnaround time
3. Resource utilization
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from scipy import stats


@dataclass
class PerWorkflowMetrics:
    """Metrics for a single workflow."""
    workflow_id: str
    mode: str  # 'manual' or 'automated'
    submit_time: float
    completion_time: float
    turnaround_time: float
    compute_time: float
    delay_time: float  # Human delay (manual) or system delay (auto)
    queue_wait_time: float
    num_iterations: int
    completed: bool


@dataclass
class AggregateMetrics:
    """Aggregate metrics across all workflows."""
    mode: str
    num_workflows: int
    num_completed: int

    # Makespan metrics
    total_makespan: float
    makespan_hours: float

    # Turnaround metrics
    mean_turnaround: float
    median_turnaround: float
    std_turnaround: float
    min_turnaround: float
    max_turnaround: float
    p25_turnaround: float
    p75_turnaround: float
    p95_turnaround: float

    # Time breakdown
    total_compute_time: float
    total_delay_time: float
    total_queue_wait_time: float

    # Utilization metrics
    average_utilization: float
    peak_utilization: float

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'mode': self.mode,
            'num_workflows': self.num_workflows,
            'num_completed': self.num_completed,
            'makespan_hours': self.makespan_hours,
            'mean_turnaround_hours': self.mean_turnaround / 3600,
            'median_turnaround_hours': self.median_turnaround / 3600,
            'std_turnaround_hours': self.std_turnaround / 3600,
            'p95_turnaround_hours': self.p95_turnaround / 3600,
            'total_compute_hours': self.total_compute_time / 3600,
            'total_delay_hours': self.total_delay_time / 3600,
            'total_queue_wait_hours': self.total_queue_wait_time / 3600,
            'average_utilization': self.average_utilization,
            'peak_utilization': self.peak_utilization,
        }


@dataclass
class ComparisonStats:
    """Statistical comparison between manual and automated modes."""
    # Improvement percentages
    makespan_improvement_pct: float
    turnaround_improvement_pct: float
    utilization_improvement_ppt: float

    # Absolute differences
    makespan_diff_hours: float
    turnaround_diff_hours: float
    delay_diff_hours: float

    # Statistical tests (for multiple replications)
    makespan_t_stat: Optional[float] = None
    makespan_p_value: Optional[float] = None
    turnaround_t_stat: Optional[float] = None
    turnaround_p_value: Optional[float] = None

    # Effect sizes
    makespan_cohens_d: Optional[float] = None
    turnaround_cohens_d: Optional[float] = None


class MetricsCollector:
    """
    Collects and analyzes metrics from simulation results.
    """

    def __init__(self):
        self.per_workflow_metrics: List[PerWorkflowMetrics] = []

    def extract_metrics_from_results(
        self,
        results: dict,
        mode: str
    ) -> Tuple[List[PerWorkflowMetrics], AggregateMetrics]:
        """
        Extract metrics from simulation results.

        Args:
            results: Simulation results dictionary
            mode: 'manual' or 'automated'

        Returns:
            Tuple of (per-workflow metrics list, aggregate metrics)
        """
        per_workflow = []

        for wf_id, wf_data in results['workflows'].items():
            # Determine delay type based on mode
            if mode == 'manual':
                delay_time = wf_data.get('human_delay', 0)
            else:
                delay_time = wf_data.get('system_delay', 0)

            metrics = PerWorkflowMetrics(
                workflow_id=wf_id,
                mode=mode,
                submit_time=wf_data['submit_time'],
                completion_time=wf_data['completion_time'],
                turnaround_time=wf_data['turnaround_time'],
                compute_time=wf_data['compute_time'],
                delay_time=delay_time,
                queue_wait_time=wf_data['queue_wait_time'],
                num_iterations=wf_data['num_iterations'],
                completed=wf_data['completed']
            )
            per_workflow.append(metrics)

        # Calculate aggregate metrics
        aggregate = self._calculate_aggregate_metrics(per_workflow, results, mode)

        return per_workflow, aggregate

    def _calculate_aggregate_metrics(
        self,
        per_workflow: List[PerWorkflowMetrics],
        results: dict,
        mode: str
    ) -> AggregateMetrics:
        """Calculate aggregate metrics from per-workflow data."""
        completed = [m for m in per_workflow if m.completed]
        turnarounds = [m.turnaround_time for m in completed]

        if not turnarounds:
            turnarounds = [0.0]

        # Calculate utilization from history
        util_history = results.get('utilization_history', [])
        peak_util = 0.0
        if util_history:
            utilizations = [used / total for _, used, total in util_history if total > 0]
            peak_util = max(utilizations) if utilizations else 0.0

        return AggregateMetrics(
            mode=mode,
            num_workflows=len(per_workflow),
            num_completed=len(completed),
            total_makespan=results['total_makespan'],
            makespan_hours=results['total_makespan'] / 3600,
            mean_turnaround=np.mean(turnarounds),
            median_turnaround=np.median(turnarounds),
            std_turnaround=np.std(turnarounds),
            min_turnaround=np.min(turnarounds),
            max_turnaround=np.max(turnarounds),
            p25_turnaround=np.percentile(turnarounds, 25),
            p75_turnaround=np.percentile(turnarounds, 75),
            p95_turnaround=np.percentile(turnarounds, 95),
            total_compute_time=results['total_compute_time'],
            total_delay_time=results.get('total_human_delay', 0) + results.get('total_system_delay', 0),
            total_queue_wait_time=results['total_queue_wait_time'],
            average_utilization=results['average_utilization'],
            peak_utilization=peak_util,
        )

    def compare_modes(
        self,
        manual_metrics: AggregateMetrics,
        auto_metrics: AggregateMetrics
    ) -> ComparisonStats:
        """
        Compare metrics between manual and automated modes.

        Args:
            manual_metrics: Aggregate metrics from manual mode
            auto_metrics: Aggregate metrics from automated mode

        Returns:
            ComparisonStats with improvement percentages and differences
        """
        # Improvement percentages (positive means auto is better)
        makespan_imp = 0.0
        if manual_metrics.total_makespan > 0:
            makespan_imp = (
                manual_metrics.total_makespan - auto_metrics.total_makespan
            ) / manual_metrics.total_makespan * 100

        turnaround_imp = 0.0
        if manual_metrics.mean_turnaround > 0:
            turnaround_imp = (
                manual_metrics.mean_turnaround - auto_metrics.mean_turnaround
            ) / manual_metrics.mean_turnaround * 100

        util_imp = (
            auto_metrics.average_utilization - manual_metrics.average_utilization
        ) * 100  # In percentage points

        return ComparisonStats(
            makespan_improvement_pct=makespan_imp,
            turnaround_improvement_pct=turnaround_imp,
            utilization_improvement_ppt=util_imp,
            makespan_diff_hours=(manual_metrics.total_makespan - auto_metrics.total_makespan) / 3600,
            turnaround_diff_hours=(manual_metrics.mean_turnaround - auto_metrics.mean_turnaround) / 3600,
            delay_diff_hours=(manual_metrics.total_delay_time - auto_metrics.total_delay_time) / 3600,
        )


def compute_replication_statistics(
    manual_makespans: List[float],
    auto_makespans: List[float],
    manual_turnarounds: List[float],
    auto_turnarounds: List[float]
) -> dict:
    """
    Compute statistical tests across multiple replications.

    Args:
        manual_makespans: List of makespan values from manual replications
        auto_makespans: List of makespan values from automated replications
        manual_turnarounds: List of average turnaround times from manual replications
        auto_turnarounds: List of average turnaround times from automated replications

    Returns:
        Dictionary with statistical test results
    """
    results = {}

    # Paired t-test for makespan (same workflows in each pair)
    if len(manual_makespans) >= 2 and len(auto_makespans) >= 2:
        t_stat, p_value = stats.ttest_rel(manual_makespans, auto_makespans)
        results['makespan_t_stat'] = t_stat
        results['makespan_p_value'] = p_value

        # Cohen's d effect size
        diff = np.array(manual_makespans) - np.array(auto_makespans)
        results['makespan_cohens_d'] = np.mean(diff) / np.std(diff, ddof=1) if np.std(diff) > 0 else 0

    # Paired t-test for turnaround
    if len(manual_turnarounds) >= 2 and len(auto_turnarounds) >= 2:
        t_stat, p_value = stats.ttest_rel(manual_turnarounds, auto_turnarounds)
        results['turnaround_t_stat'] = t_stat
        results['turnaround_p_value'] = p_value

        diff = np.array(manual_turnarounds) - np.array(auto_turnarounds)
        results['turnaround_cohens_d'] = np.mean(diff) / np.std(diff, ddof=1) if np.std(diff) > 0 else 0

    # Confidence intervals (95%)
    if manual_makespans:
        results['manual_makespan_mean'] = np.mean(manual_makespans)
        results['manual_makespan_std'] = np.std(manual_makespans, ddof=1)
        results['manual_makespan_ci_95'] = stats.t.interval(
            0.95,
            len(manual_makespans) - 1,
            loc=np.mean(manual_makespans),
            scale=stats.sem(manual_makespans)
        ) if len(manual_makespans) > 1 else (np.mean(manual_makespans), np.mean(manual_makespans))

    if auto_makespans:
        results['auto_makespan_mean'] = np.mean(auto_makespans)
        results['auto_makespan_std'] = np.std(auto_makespans, ddof=1)
        results['auto_makespan_ci_95'] = stats.t.interval(
            0.95,
            len(auto_makespans) - 1,
            loc=np.mean(auto_makespans),
            scale=stats.sem(auto_makespans)
        ) if len(auto_makespans) > 1 else (np.mean(auto_makespans), np.mean(auto_makespans))

    # Improvement statistics
    improvements = np.array(manual_makespans) - np.array(auto_makespans)
    results['improvement_mean_hours'] = np.mean(improvements) / 3600
    results['improvement_std_hours'] = np.std(improvements, ddof=1) / 3600 if len(improvements) > 1 else 0
    if len(improvements) > 1:
        results['improvement_ci_95'] = tuple(x / 3600 for x in stats.t.interval(
            0.95,
            len(improvements) - 1,
            loc=np.mean(improvements),
            scale=stats.sem(improvements)
        ))

    return results


if __name__ == "__main__":
    # Test metrics collection
    print("Metrics Collection Test")
    print("=" * 60)

    # Create sample results
    manual_results = {
        'mode': 'manual',
        'total_makespan': 100000,
        'total_compute_time': 50000,
        'total_human_delay': 40000,
        'total_queue_wait_time': 10000,
        'average_utilization': 0.6,
        'utilization_history': [(0, 100, 148), (1000, 120, 148)],
        'workflows': {
            'wf-001': {
                'submit_time': 0,
                'completion_time': 50000,
                'turnaround_time': 50000,
                'compute_time': 25000,
                'human_delay': 20000,
                'queue_wait_time': 5000,
                'num_iterations': 4,
                'completed': True
            }
        }
    }

    auto_results = {
        'mode': 'automated',
        'total_makespan': 60000,
        'total_compute_time': 50000,
        'total_system_delay': 100,
        'total_queue_wait_time': 9900,
        'average_utilization': 0.85,
        'utilization_history': [(0, 100, 148), (1000, 140, 148)],
        'workflows': {
            'wf-001': {
                'submit_time': 0,
                'completion_time': 30000,
                'turnaround_time': 30000,
                'compute_time': 25000,
                'system_delay': 50,
                'queue_wait_time': 4950,
                'num_iterations': 4,
                'completed': True
            }
        }
    }

    collector = MetricsCollector()

    manual_per_wf, manual_agg = collector.extract_metrics_from_results(manual_results, 'manual')
    auto_per_wf, auto_agg = collector.extract_metrics_from_results(auto_results, 'automated')

    print("\nManual Mode Aggregate Metrics:")
    for k, v in manual_agg.to_dict().items():
        print(f"  {k}: {v}")

    print("\nAutomated Mode Aggregate Metrics:")
    for k, v in auto_agg.to_dict().items():
        print(f"  {k}: {v}")

    comparison = collector.compare_modes(manual_agg, auto_agg)
    print("\nComparison:")
    print(f"  Makespan improvement: {comparison.makespan_improvement_pct:.1f}%")
    print(f"  Turnaround improvement: {comparison.turnaround_improvement_pct:.1f}%")
    print(f"  Utilization improvement: {comparison.utilization_improvement_ppt:.1f} ppt")
