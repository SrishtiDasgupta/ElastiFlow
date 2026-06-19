"""
Visualization generation for manual vs automated comparison.

Creates publication-quality plots for:
1. Bar chart metrics comparison (3 modes)
2. Utilization time series (3 modes)
3. Box plots for turnaround distributions (3 modes)
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

try:
    from ..simulation.simulate_comparison import ComparisonResult
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from simulation.simulate_comparison import ComparisonResult


# Color scheme for three modes
COLORS = {
    'manual_strict': '#E74C3C',      # Red
    'manual_flexible': '#F39C12',    # Orange
    'automated': '#2ECC71',          # Green
    'compute': '#3498DB',            # Blue
    'delay': '#E74C3C',              # Red
    'queue_wait': '#9B59B6',         # Purple
    'idle': '#95A5A6',               # Gray
}


def plot_metric_comparison(
    result: ComparisonResult,
    output_dir: str,
    filename: str = "metric_comparison.png",
    figsize: Tuple[int, int] = (14, 10),
    dpi: int = 150
) -> str:
    """
    Create bar chart comparing key metrics between all three modes.
    """
    summary = result.get_summary()

    fig, axes = plt.subplots(2, 2, figsize=figsize)
    fig.suptitle('Manual (Strict) vs Manual (Flexible) vs Automated Workflow Comparison',
                 fontsize=14, fontweight='bold')

    modes = ['Manual\n(Strict)', 'Manual\n(Flexible)', 'Automated']
    colors = [COLORS['manual_strict'], COLORS['manual_flexible'], COLORS['automated']]

    # 1. Makespan comparison
    ax = axes[0, 0]
    makespans = [
        summary['manual']['makespan_hours'],
        summary['manual_flexible']['makespan_hours'],
        summary['automated']['makespan_hours']
    ]
    bars = ax.bar(modes, makespans, color=colors)
    ax.set_ylabel('Hours')
    ax.set_title('Total Makespan')
    # Add values on bars
    for bar, val in zip(bars, makespans):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'{val:.1f}h', ha='center', va='bottom', fontsize=9)
    # Add improvement annotations
    imp_strict = summary['improvement']['makespan_pct']
    imp_flex = summary['improvement']['makespan_flexible_pct']
    ax.annotate(f'{imp_strict:.1f}% ↓', xy=(2, makespans[2]),
                xytext=(2.3, makespans[0] * 0.7),
                fontsize=9, ha='center', color=COLORS['manual_strict'],
                arrowprops=dict(arrowstyle='->', color=COLORS['manual_strict'], lw=0.5))

    # 2. Time breakdown (stacked bar)
    ax = axes[0, 1]
    width = 0.6
    x = np.array([0, 1, 2])

    compute = [
        summary['manual']['compute_hours'],
        summary['manual_flexible']['compute_hours'],
        summary['automated']['compute_hours']
    ]
    delay = [
        summary['manual']['human_delay_hours'],
        summary['manual_flexible']['human_delay_hours'],
        summary['automated']['system_delay_seconds'] / 3600
    ]
    queue = [
        summary['manual']['queue_wait_hours'],
        summary['manual_flexible']['queue_wait_hours'],
        summary['automated']['queue_wait_hours']
    ]

    ax.bar(x, compute, width, label='Compute', color=COLORS['compute'])
    ax.bar(x, delay, width, bottom=compute, label='Delay', color=COLORS['delay'])
    ax.bar(x, queue, width, bottom=[c+d for c,d in zip(compute, delay)],
           label='Queue Wait', color=COLORS['queue_wait'])

    ax.set_xticks(x)
    ax.set_xticklabels(modes)
    ax.set_ylabel('Hours')
    ax.set_title('Time Breakdown')
    ax.legend(loc='upper right', fontsize=8)

    # 3. Resource utilization
    ax = axes[1, 0]
    utils = [
        summary['manual']['utilization'] * 100,
        summary['manual_flexible']['utilization'] * 100,
        summary['automated']['utilization'] * 100
    ]
    bars = ax.bar(modes, utils, color=colors)
    ax.set_ylabel('Utilization (%)')
    ax.set_title('Average Resource Utilization')
    ax.set_ylim(0, 100)
    # Add values on bars
    for bar, val in zip(bars, utils):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2,
                f'{val:.1f}%', ha='center', va='bottom', fontsize=9)

    # 4. Summary table
    ax = axes[1, 1]
    ax.axis('off')

    table_data = [
        ['Metric', 'Manual\n(Strict)', 'Manual\n(Flexible)', 'Automated'],
        ['Makespan',
         f"{summary['manual']['makespan_hours']:.1f}h",
         f"{summary['manual_flexible']['makespan_hours']:.1f}h",
         f"{summary['automated']['makespan_hours']:.1f}h"],
        ['Compute',
         f"{summary['manual']['compute_hours']:.1f}h",
         f"{summary['manual_flexible']['compute_hours']:.1f}h",
         f"{summary['automated']['compute_hours']:.1f}h"],
        ['Delay',
         f"{summary['manual']['human_delay_hours']:.1f}h",
         f"{summary['manual_flexible']['human_delay_hours']:.1f}h",
         f"{summary['automated']['system_delay_seconds']:.0f}s"],
        ['Queue Wait',
         f"{summary['manual']['queue_wait_hours']:.1f}h",
         f"{summary['manual_flexible']['queue_wait_hours']:.1f}h",
         f"{summary['automated']['queue_wait_hours']:.1f}h"],
        ['Utilization',
         f"{summary['manual']['utilization']*100:.1f}%",
         f"{summary['manual_flexible']['utilization']*100:.1f}%",
         f"{summary['automated']['utilization']*100:.1f}%"],
        ['Improvement',
         'baseline',
         f"{summary['improvement']['makespan_pct'] - summary['improvement']['makespan_flexible_pct']:.1f}%",
         f"{summary['improvement']['makespan_pct']:.1f}%"],
    ]

    table = ax.table(cellText=table_data[1:], colLabels=table_data[0],
                     loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(9)
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
    figsize: Tuple[int, int] = (16, 5),
    dpi: int = 150
) -> str:
    """
    Plot resource utilization over time for all three modes.
    """
    fig, axes = plt.subplots(1, 3, figsize=figsize, sharey=True)
    fig.suptitle('Resource Utilization Over Time', fontsize=14, fontweight='bold')

    modes_data = [
        ('Manual (Strict)', result.manual_results, COLORS['manual_strict']),
        ('Manual (Flexible)', result.manual_flexible_results, COLORS['manual_flexible']),
        ('Automated', result.auto_results, COLORS['automated']),
    ]

    for ax, (title, results, color) in zip(axes, modes_data):
        history = results.get('utilization_history', [])
        if history:
            times = [t / 3600 for t, _, _ in history]
            utils = [used / total * 100 if total > 0 else 0 for _, used, total in history]
            ax.fill_between(times, utils, alpha=0.3, color=color)
            ax.plot(times, utils, color=color, linewidth=1.5)
        ax.set_xlabel('Time (hours)')
        if ax == axes[0]:
            ax.set_ylabel('Utilization (%)')
        ax.set_title(title)
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
    figsize: Tuple[int, int] = (12, 6),
    dpi: int = 150
) -> str:
    """
    Plot turnaround time distribution comparison for all three modes.
    """
    fig, ax = plt.subplots(figsize=figsize)

    # Extract turnaround times
    manual_strict_turnarounds = [
        wf['turnaround_time'] / 3600
        for wf in result.manual_results['workflows'].values()
        if wf['completed']
    ]
    manual_flexible_turnarounds = [
        wf['turnaround_time'] / 3600
        for wf in result.manual_flexible_results['workflows'].values()
        if wf['completed']
    ]
    auto_turnarounds = [
        wf['turnaround_time'] / 3600
        for wf in result.auto_results['workflows'].values()
        if wf['completed']
    ]

    # Box plot
    bp = ax.boxplot(
        [manual_strict_turnarounds, manual_flexible_turnarounds, auto_turnarounds],
        labels=['Manual\n(Strict)', 'Manual\n(Flexible)', 'Automated'],
        patch_artist=True
    )

    # Color the boxes
    colors = [COLORS['manual_strict'], COLORS['manual_flexible'], COLORS['automated']]
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)

    ax.set_ylabel('Turnaround Time (hours)')
    ax.set_title('Workflow Turnaround Time Distribution')
    ax.grid(True, alpha=0.3, axis='y')

    # Add mean markers
    means = [
        np.mean(manual_strict_turnarounds),
        np.mean(manual_flexible_turnarounds),
        np.mean(auto_turnarounds)
    ]
    ax.scatter([1, 2, 3], means, marker='D', color='black', s=50, zorder=3, label='Mean')

    # Add statistics annotation
    stats_text = (
        f"Manual (Strict): mean={np.mean(manual_strict_turnarounds):.1f}h, median={np.median(manual_strict_turnarounds):.1f}h\n"
        f"Manual (Flexible): mean={np.mean(manual_flexible_turnarounds):.1f}h, median={np.median(manual_flexible_turnarounds):.1f}h\n"
        f"Automated: mean={np.mean(auto_turnarounds):.1f}h, median={np.median(auto_turnarounds):.1f}h"
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


def plot_delay_comparison(
    result: ComparisonResult,
    output_dir: str,
    filename: str = "delay_comparison.png",
    figsize: Tuple[int, int] = (10, 6),
    dpi: int = 150
) -> str:
    """
    Plot comparison of delay times across modes.
    Shows that human delay is the bottleneck.
    """
    summary = result.get_summary()

    fig, ax = plt.subplots(figsize=figsize)

    modes = ['Manual\n(Strict)', 'Manual\n(Flexible)', 'Automated']
    delays = [
        summary['manual']['human_delay_hours'],
        summary['manual_flexible']['human_delay_hours'],
        summary['automated']['system_delay_seconds'] / 3600
    ]
    colors = [COLORS['manual_strict'], COLORS['manual_flexible'], COLORS['automated']]

    bars = ax.bar(modes, delays, color=colors)
    ax.set_ylabel('Total Delay Time (hours)')
    ax.set_title('Inter-Iteration Delay: Human vs System')

    # Add values on bars
    for bar, val in zip(bars, delays):
        if val > 0.1:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                    f'{val:.1f}h', ha='center', va='bottom', fontsize=10)
        else:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                    f'{val*3600:.0f}s', ha='center', va='bottom', fontsize=10)

    # Add annotation showing the difference
    ax.annotate(
        f'Human delay dominates:\n{delays[0]:.1f}h vs {delays[2]*3600:.0f}s',
        xy=(2, delays[2]), xytext=(1.5, delays[0] * 0.5),
        fontsize=10, ha='center',
        bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.7),
        arrowprops=dict(arrowstyle='->', color='gray')
    )

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

    paths['delay_comparison'] = plot_delay_comparison(
        result, output_dir, f"{timestamp}_delay_comparison.png", dpi=dpi
    )

    return paths


if __name__ == "__main__":
    # Test plot generation
    print("Plot Generation Test")
    print("=" * 60)

    from simulation.simulate_comparison import ComparisonSimulator

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
            'mean_interarrival_seconds': 300
        },
        'human_delay': {
            'median_hours': 3.0,
            'sigma': 0.9,
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

    print("Running simulation...")
    simulator = ComparisonSimulator(config, seed=42)
    result = simulator.run_comparison()

    print("Generating plots...")
    output_dir = "/tmp/manual_vs_auto_plots"
    paths = generate_all_plots(result, output_dir)

    print(f"\nGenerated plots:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
