"""
CSV export functionality for simulation results.

Supports three modes: manual (strict), manual (flexible), and automated.
"""

import csv
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

try:
    from ..simulation.simulate_comparison import ComparisonResult
    from ..metrics.comparison_metrics import MetricsCollector, AggregateMetrics
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from simulation.simulate_comparison import ComparisonResult
    from metrics.comparison_metrics import MetricsCollector, AggregateMetrics


def export_per_workflow_results(
    result: ComparisonResult,
    output_dir: str,
    prefix: str = ""
) -> str:
    """
    Export per-workflow results to CSV.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        prefix: Optional prefix for filename

    Returns:
        Path to created CSV file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{prefix}per_workflow_results.csv" if prefix else "per_workflow_results.csv"
    filepath = output_path / filename

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'workflow_id', 'mode', 'submit_time', 'completion_time',
            'turnaround_time', 'compute_time', 'delay_time', 'queue_wait_time',
            'num_iterations', 'completed',
            'turnaround_hours', 'compute_hours', 'delay_hours', 'queue_wait_hours'
        ])

        # Manual (strict) results
        for wf_id, wf_data in result.manual_results['workflows'].items():
            delay = wf_data.get('human_delay', 0)
            writer.writerow([
                wf_id, 'manual_strict',
                wf_data['submit_time'], wf_data['completion_time'],
                wf_data['turnaround_time'], wf_data['compute_time'],
                delay, wf_data['queue_wait_time'],
                wf_data['num_iterations'], wf_data['completed'],
                wf_data['turnaround_time'] / 3600,
                wf_data['compute_time'] / 3600,
                delay / 3600,
                wf_data['queue_wait_time'] / 3600
            ])

        # Manual (flexible) results
        for wf_id, wf_data in result.manual_flexible_results['workflows'].items():
            delay = wf_data.get('human_delay', 0)
            writer.writerow([
                wf_id, 'manual_flexible',
                wf_data['submit_time'], wf_data['completion_time'],
                wf_data['turnaround_time'], wf_data['compute_time'],
                delay, wf_data['queue_wait_time'],
                wf_data['num_iterations'], wf_data['completed'],
                wf_data['turnaround_time'] / 3600,
                wf_data['compute_time'] / 3600,
                delay / 3600,
                wf_data['queue_wait_time'] / 3600
            ])

        # Automated results
        for wf_id, wf_data in result.auto_results['workflows'].items():
            delay = wf_data.get('system_delay', 0)
            writer.writerow([
                wf_id, 'automated',
                wf_data['submit_time'], wf_data['completion_time'],
                wf_data['turnaround_time'], wf_data['compute_time'],
                delay, wf_data['queue_wait_time'],
                wf_data['num_iterations'], wf_data['completed'],
                wf_data['turnaround_time'] / 3600,
                wf_data['compute_time'] / 3600,
                delay / 3600,
                wf_data['queue_wait_time'] / 3600
            ])

    return str(filepath)


def export_aggregate_results(
    result: ComparisonResult,
    output_dir: str,
    prefix: str = ""
) -> str:
    """
    Export aggregate comparison results to CSV.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        prefix: Optional prefix for filename

    Returns:
        Path to created CSV file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{prefix}aggregate_results.csv" if prefix else "aggregate_results.csv"
    filepath = output_path / filename

    summary = result.get_summary()

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow(['metric', 'manual_strict', 'manual_flexible', 'automated', 'improvement_vs_strict', 'improvement_vs_flexible', 'unit'])

        # Metrics
        writer.writerow([
            'makespan',
            summary['manual']['makespan_hours'],
            summary['manual_flexible']['makespan_hours'],
            summary['automated']['makespan_hours'],
            summary['improvement']['makespan_pct'],
            summary['improvement']['makespan_flexible_pct'],
            'hours / %'
        ])

        writer.writerow([
            'compute_time',
            summary['manual']['compute_hours'],
            summary['manual_flexible']['compute_hours'],
            summary['automated']['compute_hours'],
            0,
            0,
            'hours'
        ])

        writer.writerow([
            'delay_time',
            summary['manual']['human_delay_hours'],
            summary['manual_flexible']['human_delay_hours'],
            summary['automated']['system_delay_seconds'] / 3600,
            summary['manual']['human_delay_hours'] - summary['automated']['system_delay_seconds'] / 3600,
            summary['manual_flexible']['human_delay_hours'] - summary['automated']['system_delay_seconds'] / 3600,
            'hours'
        ])

        writer.writerow([
            'queue_wait_time',
            summary['manual']['queue_wait_hours'],
            summary['manual_flexible']['queue_wait_hours'],
            summary['automated']['queue_wait_hours'],
            summary['manual']['queue_wait_hours'] - summary['automated']['queue_wait_hours'],
            summary['manual_flexible']['queue_wait_hours'] - summary['automated']['queue_wait_hours'],
            'hours'
        ])

        writer.writerow([
            'utilization',
            summary['manual']['utilization'] * 100,
            summary['manual_flexible']['utilization'] * 100,
            summary['automated']['utilization'] * 100,
            (summary['automated']['utilization'] - summary['manual']['utilization']) * 100,
            (summary['automated']['utilization'] - summary['manual_flexible']['utilization']) * 100,
            '% / ppt'
        ])

    return str(filepath)


def export_utilization_timeseries(
    result: ComparisonResult,
    output_dir: str,
    prefix: str = ""
) -> str:
    """
    Export resource utilization time series to CSV.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        prefix: Optional prefix for filename

    Returns:
        Path to created CSV file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{prefix}utilization_timeseries.csv" if prefix else "utilization_timeseries.csv"
    filepath = output_path / filename

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow(['mode', 'time_seconds', 'time_hours', 'used_nodes', 'total_nodes', 'utilization'])

        # Manual (strict) utilization
        for time, used, total in result.manual_results.get('utilization_history', []):
            util = used / total if total > 0 else 0
            writer.writerow(['manual_strict', time, time / 3600, used, total, util])

        # Manual (flexible) utilization
        for time, used, total in result.manual_flexible_results.get('utilization_history', []):
            util = used / total if total > 0 else 0
            writer.writerow(['manual_flexible', time, time / 3600, used, total, util])

        # Automated utilization
        for time, used, total in result.auto_results.get('utilization_history', []):
            util = used / total if total > 0 else 0
            writer.writerow(['automated', time, time / 3600, used, total, util])

    return str(filepath)


def export_replication_results(
    results: List[ComparisonResult],
    output_dir: str,
    prefix: str = ""
) -> str:
    """
    Export results from multiple replications to CSV.

    Args:
        results: List of ComparisonResult from multiple replications
        output_dir: Output directory path
        prefix: Optional prefix for filename

    Returns:
        Path to created CSV file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = f"{prefix}replication_results.csv" if prefix else "replication_results.csv"
    filepath = output_path / filename

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'replication', 'num_workflows',
            'manual_strict_makespan_hours', 'manual_flexible_makespan_hours', 'auto_makespan_hours',
            'manual_strict_compute_hours', 'manual_flexible_compute_hours', 'auto_compute_hours',
            'manual_strict_delay_hours', 'manual_flexible_delay_hours', 'auto_delay_seconds',
            'manual_strict_utilization', 'manual_flexible_utilization', 'auto_utilization',
            'makespan_improvement_vs_strict_pct', 'makespan_improvement_vs_flexible_pct',
            'turnaround_improvement_pct'
        ])

        for i, result in enumerate(results):
            summary = result.get_summary()
            writer.writerow([
                i + 1, summary['num_workflows'],
                summary['manual']['makespan_hours'],
                summary['manual_flexible']['makespan_hours'],
                summary['automated']['makespan_hours'],
                summary['manual']['compute_hours'],
                summary['manual_flexible']['compute_hours'],
                summary['automated']['compute_hours'],
                summary['manual']['human_delay_hours'],
                summary['manual_flexible']['human_delay_hours'],
                summary['automated']['system_delay_seconds'],
                summary['manual']['utilization'],
                summary['manual_flexible']['utilization'],
                summary['automated']['utilization'],
                summary['improvement']['makespan_pct'],
                summary['improvement']['makespan_flexible_pct'],
                summary['improvement']['turnaround_pct']
            ])

    return str(filepath)


def export_all_results(
    result: ComparisonResult,
    output_dir: str,
    include_timeseries: bool = True
) -> Dict[str, str]:
    """
    Export all results to CSV files.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        include_timeseries: Whether to export utilization time series

    Returns:
        Dictionary mapping result type to file path
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{timestamp}_"

    paths = {
        'per_workflow': export_per_workflow_results(result, output_dir, prefix),
        'aggregate': export_aggregate_results(result, output_dir, prefix),
    }

    if include_timeseries:
        paths['utilization'] = export_utilization_timeseries(result, output_dir, prefix)

    return paths


if __name__ == "__main__":
    # Test CSV export
    print("CSV Export Test")
    print("=" * 60)

    from simulation.simulate_comparison import ComparisonSimulator

    config = {
        'experiment': {'seed': 42},
        'resources': {'total_nodes': 148, 'cores_per_node': 48},
        'workflows': {
            'num_workflows': 5,
            'iteration_range': [2, 4],
            'mesh_sizes': [500, 750, 1000],
            'mesh_distribution': [0.33, 0.34, 0.33],
            'chains_range': [2, 6],
            'nodes_per_chain_range': [1, 3],
            'tinyda_iterations_range': [1, 5],
            'mean_interarrival_seconds': 300
        },
        'human_delay': {
            'median_hours': 2.0,
            'sigma': 0.8,
            'min_hours': 0.5,
            'max_hours': 12.0,
            'work_hours': {'enabled': False}
        },
        'auto_delay': {'system_delay_seconds': 5.0},
        'manual_flexible': {
            'min_nodes_per_job': 1,
            'max_scale_factor': 2.0
        }
    }

    simulator = ComparisonSimulator(config, seed=42)
    result = simulator.run_comparison()

    output_dir = "/tmp/manual_vs_auto_test"
    paths = export_all_results(result, output_dir)

    print(f"\nExported files:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
