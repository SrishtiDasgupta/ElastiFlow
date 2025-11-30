"""
Visualization generation for manual vs automated comparison.

Creates publication-quality plots for:
1. Gantt chart comparison
2. Bar chart metrics comparison
3. Utilization time series
4. Box plots for distributions
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from ..simulation.simulate_comparison import ComparisonResult


# Color scheme
COLORS = {
    'manual': '#E74C3C',       # Red
    'automated': '#2ECC71',    # Green
    'compute': '#3498DB',      # Blue
    'delay': '#E74C3C',        # Red
    'queue_wait': '#F39C12',   # Orange
    'idle': '#95A5A6',         # Gray
}


def plot_metric_comparison(
    result: ComparisonResult,
    output_dir: str,
    filename: str = "metric_comparison.png",
    figsize: Tuple[int, int] = (12, 8),
    dpi: int = 150
) -> str:
    """
    Create bar chart comparing key metrics between modes.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        filename: Output filename
        figsize: Figure size
        dpi: DPI for output

    Returns:
        Path to created figure
    """
    summary = result.get_summary()

    fig, axes = plt.subplots(2, 2, figsize=figsize)
    fig.suptitle('Manual vs Automated Workflow Comparison', fontsize=14, fontweight='bold')

    # 1. Makespan comparison
    ax = axes[0, 0]
    modes = ['Manual', 'Automated']
    makespans = [summary['manual']['makespan_hours'], summary['automated']['makespan_hours']]
    bars = ax.bar(modes, makespans, color=[COLORS['manual'], COLORS['automated']])
    ax.set_ylabel('Hours')
    ax.set_title('Total Makespan')
    # Add improvement annotation
    imp = summary['improvement']['makespan_pct']
    ax.annotate(f'{imp:.1f}% reduction', xy=(1, makespans[1]), xytext=(1.3, makespans[0] * 0.8),
                fontsize=10, ha='center', arrowprops=dict(arrowstyle='->', color='gray'))

    # 2. Time breakdown (stacked bar)
    ax = axes[0, 1]
    width = 0.6
    manual_breakdown = [
        summary['manual']['compute_hours'],
        summary['manual']['human_delay_hours'],
        summary['manual']['queue_wait_hours']
    ]
    auto_breakdown = [
        summary['automated']['compute_hours'],
        summary['automated']['system_delay_seconds'] / 3600,
        summary['automated']['queue_wait_hours']
    ]

    x = np.array([0, 1])
    bottom_manual = 0
    bottom_auto = 0

    colors = [COLORS['compute'], COLORS['delay'], COLORS['queue_wait']]
    labels = ['Compute', 'Delay', 'Queue Wait']

    for i, (m_val, a_val, color, label) in enumerate(zip(manual_breakdown, auto_breakdown, colors, labels)):
        ax.bar([0], [m_val], width, bottom=[bottom_manual], color=color, label=label if i == 0 else None)
        ax.bar([1], [a_val], width, bottom=[bottom_auto], color=color)
        bottom_manual += m_val
        bottom_auto += a_val

    ax.set_xticks([0, 1])
    ax.set_xticklabels(['Manual', 'Automated'])
    ax.set_ylabel('Hours')
    ax.set_title('Time Breakdown')

    # Create legend
    compute_patch = mpatches.Patch(color=COLORS['compute'], label='Compute')
    delay_patch = mpatches.Patch(color=COLORS['delay'], label='Delay')
    queue_patch = mpatches.Patch(color=COLORS['queue_wait'], label='Queue Wait')
    ax.legend(handles=[compute_patch, delay_patch, queue_patch], loc='upper right')

    # 3. Resource utilization
    ax = axes[1, 0]
    utils = [summary['manual']['utilization'] * 100, summary['automated']['utilization'] * 100]
    bars = ax.bar(modes, utils, color=[COLORS['manual'], COLORS['automated']])
    ax.set_ylabel('Utilization (%)')
    ax.set_title('Average Resource Utilization')
    ax.set_ylim(0, 100)
    # Add values on bars
    for bar, val in zip(bars, utils):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2,
                f'{val:.1f}%', ha='center', va='bottom')

    # 4. Summary table
    ax = axes[1, 1]
    ax.axis('off')

    table_data = [
        ['Metric', 'Manual', 'Automated', 'Improvement'],
        ['Makespan', f"{summary['manual']['makespan_hours']:.1f}h",
         f"{summary['automated']['makespan_hours']:.1f}h",
         f"{summary['improvement']['makespan_pct']:.1f}%"],
        ['Avg Turnaround', '-', '-', f"{summary['improvement']['turnaround_pct']:.1f}%"],
        ['Utilization', f"{summary['manual']['utilization']*100:.1f}%",
         f"{summary['automated']['utilization']*100:.1f}%",
         f"+{summary['improvement']['utilization_ppt']:.1f}ppt"],
        ['Human Delay', f"{summary['manual']['human_delay_hours']:.1f}h",
         f"{summary['automated']['system_delay_seconds']:.0f}s", 'N/A'],
    ]

    table = ax.table(cellText=table_data[1:], colLabels=table_data[0],
                     loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.5)

    # Style header row
    for i in range(4):
        table[(0, i)].set_facecolor('#E8E8E8')
        table[(0, i)].set_text_props(fontweight='bold')

    plt.tight_layout()

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filepath = output_path / filename
    plt.savefig(filepath, dpi=dpi, bbox_inches='tight')
    plt.close()

    return str(filepath)


def plot_utilization_timeseries(
    result: ComparisonResult,
    output_dir: str,
    filename: str = "utilization_timeseries.png",
    figsize: Tuple[int, int] = (14, 6),
    dpi: int = 150
) -> str:
    """
    Plot resource utilization over time for both modes.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        filename: Output filename
        figsize: Figure size
        dpi: DPI for output

    Returns:
        Path to created figure
    """
    fig, axes = plt.subplots(1, 2, figsize=figsize, sharey=True)
    fig.suptitle('Resource Utilization Over Time', fontsize=14, fontweight='bold')

    # Manual mode
    ax = axes[0]
    manual_history = result.manual_results.get('utilization_history', [])
    if manual_history:
        times = [t / 3600 for t, _, _ in manual_history]
        utils = [used / total * 100 if total > 0 else 0 for _, used, total in manual_history]
        ax.fill_between(times, utils, alpha=0.3, color=COLORS['manual'])
        ax.plot(times, utils, color=COLORS['manual'], linewidth=1.5)
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Utilization (%)')
    ax.set_title('Manual Mode')
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)

    # Automated mode
    ax = axes[1]
    auto_history = result.auto_results.get('utilization_history', [])
    if auto_history:
        times = [t / 3600 for t, _, _ in auto_history]
        utils = [used / total * 100 if total > 0 else 0 for _, used, total in auto_history]
        ax.fill_between(times, utils, alpha=0.3, color=COLORS['automated'])
        ax.plot(times, utils, color=COLORS['automated'], linewidth=1.5)
    ax.set_xlabel('Time (hours)')
    ax.set_title('Automated Mode')
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filepath = output_path / filename
    plt.savefig(filepath, dpi=dpi, bbox_inches='tight')
    plt.close()

    return str(filepath)


def plot_turnaround_distribution(
    result: ComparisonResult,
    output_dir: str,
    filename: str = "turnaround_distribution.png",
    figsize: Tuple[int, int] = (10, 6),
    dpi: int = 150
) -> str:
    """
    Plot turnaround time distribution comparison.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        filename: Output filename
        figsize: Figure size
        dpi: DPI for output

    Returns:
        Path to created figure
    """
    fig, ax = plt.subplots(figsize=figsize)

    # Extract turnaround times
    manual_turnarounds = [
        wf['turnaround_time'] / 3600
        for wf in result.manual_results['workflows'].values()
        if wf['completed']
    ]
    auto_turnarounds = [
        wf['turnaround_time'] / 3600
        for wf in result.auto_results['workflows'].values()
        if wf['completed']
    ]

    # Box plot
    bp = ax.boxplot(
        [manual_turnarounds, auto_turnarounds],
        labels=['Manual', 'Automated'],
        patch_artist=True
    )

    # Color the boxes
    bp['boxes'][0].set_facecolor(COLORS['manual'])
    bp['boxes'][0].set_alpha(0.6)
    bp['boxes'][1].set_facecolor(COLORS['automated'])
    bp['boxes'][1].set_alpha(0.6)

    ax.set_ylabel('Turnaround Time (hours)')
    ax.set_title('Workflow Turnaround Time Distribution')
    ax.grid(True, alpha=0.3, axis='y')

    # Add mean markers
    ax.scatter([1], [np.mean(manual_turnarounds)], marker='D', color='black', s=50, zorder=3, label='Mean')
    ax.scatter([2], [np.mean(auto_turnarounds)], marker='D', color='black', s=50, zorder=3)

    # Add statistics annotation
    stats_text = (
        f"Manual: mean={np.mean(manual_turnarounds):.1f}h, median={np.median(manual_turnarounds):.1f}h\n"
        f"Auto: mean={np.mean(auto_turnarounds):.1f}h, median={np.median(auto_turnarounds):.1f}h"
    )
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filepath = output_path / filename
    plt.savefig(filepath, dpi=dpi, bbox_inches='tight')
    plt.close()

    return str(filepath)


def generate_all_plots(
    result: ComparisonResult,
    output_dir: str,
    dpi: int = 150
) -> Dict[str, str]:
    """
    Generate all visualization plots.

    Args:
        result: ComparisonResult from simulation
        output_dir: Output directory path
        dpi: DPI for output

    Returns:
        Dictionary mapping plot type to file path
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    paths = {}

    paths['metric_comparison'] = plot_metric_comparison(
        result, output_dir, f"{timestamp}_metric_comparison.png", dpi=dpi
    )

    paths['utilization'] = plot_utilization_timeseries(
        result, output_dir, f"{timestamp}_utilization_timeseries.png", dpi=dpi
    )

    paths['turnaround'] = plot_turnaround_distribution(
        result, output_dir, f"{timestamp}_turnaround_distribution.png", dpi=dpi
    )

    return paths


if __name__ == "__main__":
    # Test plot generation
    print("Plot Generation Test")
    print("=" * 60)

    from ..simulation.simulate_comparison import ComparisonSimulator

    config = {
        'experiment': {'seed': 42},
        'resources': {'total_nodes': 148, 'cores_per_node': 48},
        'workflows': {
            'num_workflows': 10,
            'iteration_range': [2, 5],
            'mesh_sizes': [500, 750, 1000],
            'mesh_distribution': [0.33, 0.34, 0.33],
            'chains_range': [2, 6],
            'nodes_per_chain_range': [1, 3],
            'tinyda_iterations_range': [1, 5],
            'mean_interarrival_seconds': 1800
        },
        'human_delay': {
            'median_hours': 3.0,
            'sigma': 0.9,
            'min_hours': 0.5,
            'max_hours': 12.0,
            'work_hours': {'enabled': False}
        },
        'auto_delay': {'system_delay_seconds': 5.0}
    }

    print("Running simulation...")
    simulator = ComparisonSimulator(config, seed=42)
    result = simulator.run_comparison()

    print("Generating plots...")
    output_dir = "/tmp/manual_vs_auto_plots"
    paths = generate_all_plots(result, output_dir)

    print(f"\nGenerated plots:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
