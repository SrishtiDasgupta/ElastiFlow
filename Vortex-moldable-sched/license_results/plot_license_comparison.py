"""
License-Aware Moldable FCFS (LAMF) vs Baseline Comparison Plots

Generates must-have visualizations for PhD thesis:
1. Time Series: License Utilization by Pool
2. Dual-Axis: License-Compute Coupling
3. Box Plot: Cost/Flowtime/Wait Time Distribution
4. Grouped Bar: Violation Rates

Author: Generated for LAMF Performance Analysis
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from scipy import stats
from pathlib import Path

# ============================================================================
# Configuration
# ============================================================================

# File paths
DATA_DIR = Path(__file__).parent
PLOTS_DIR = DATA_DIR / 'plots'

BASELINE_LICENSE = DATA_DIR / 'Baseline_400_license_usage.csv'
LAMF_LICENSE = DATA_DIR / 'LAMF_400_license_usage.csv'
BASELINE_RESOURCES = DATA_DIR / 'Baseline_400_resources.csv'
LAMF_RESOURCES = DATA_DIR / 'LAMF_400_resources.csv'
BASELINE_RESULTS = DATA_DIR / 'Baseline_400_results.csv'
LAMF_RESULTS = DATA_DIR / 'LAMF_400_results.csv'

# Plotting style
plt.style.use('seaborn-v0_8-paper')
sns.set_palette("husl")

# Colors
COLOR_BASELINE = '#2E86AB'  # Blue
COLOR_LAMF = '#F77F00'      # Orange

# Figure settings
FIGURE_DPI = 300
FIGURE_SIZE = (10, 6)
FONT_SIZE = 12

plt.rcParams.update({
    'font.size': FONT_SIZE,
    'axes.labelsize': FONT_SIZE,
    'axes.titlesize': FONT_SIZE + 2,
    'xtick.labelsize': FONT_SIZE - 1,
    'ytick.labelsize': FONT_SIZE - 1,
    'legend.fontsize': FONT_SIZE - 1,
    'figure.dpi': FIGURE_DPI,
})

# ============================================================================
# Data Loading
# ============================================================================

def load_data():
    """Load all CSV files and return DataFrames."""
    print("Loading data...")

    baseline_license = pd.read_csv(BASELINE_LICENSE)
    lamf_license = pd.read_csv(LAMF_LICENSE)
    baseline_resources = pd.read_csv(BASELINE_RESOURCES)
    lamf_resources = pd.read_csv(LAMF_RESOURCES)
    baseline_results = pd.read_csv(BASELINE_RESULTS)
    lamf_results = pd.read_csv(LAMF_RESULTS)

    # Convert timestamp to hours (relative to simulation start)
    for df in [baseline_license, lamf_license]:
        # Filter out non-simulation timestamps (Unix timestamps)
        df_filtered = df[df['Timestamp'] < 1e9].copy()
        df.drop(df.index, inplace=True)
        df[df_filtered.columns] = df_filtered
        df['Time_Hours'] = df['Timestamp'] / 3600.0

    for df in [baseline_resources, lamf_resources]:
        df_filtered = df[df['Timestamp'] < 1e9].copy()
        df.drop(df.index, inplace=True)
        df[df_filtered.columns] = df_filtered
        df['Time_Hours'] = df['Timestamp'] / 3600.0

    print(f"✓ Loaded {len(baseline_license)} license samples (Baseline)")
    print(f"✓ Loaded {len(lamf_license)} license samples (LAMF)")
    print(f"✓ Loaded {len(baseline_results)} workflow results (Baseline)")
    print(f"✓ Loaded {len(lamf_results)} workflow results (LAMF)")

    return {
        'baseline_license': baseline_license,
        'lamf_license': lamf_license,
        'baseline_resources': baseline_resources,
        'lamf_resources': lamf_resources,
        'baseline_results': baseline_results,
        'lamf_results': lamf_results
    }

# ============================================================================
# Plot 1: Time Series - License Utilization by Pool
# ============================================================================

def plot_license_utilization_timeseries(data):
    """
    Create time series plots of license utilization for each pool.
    3 subplots (ANSYS, ABAQUS, LSDYNA) comparing Baseline vs LAMF.
    """
    print("\n[1/4] Generating License Utilization Time Series...")

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    pools = ['ANSYS', 'ABAQUS', 'LSDYNA']

    for idx, pool in enumerate(pools):
        ax = axes[idx]

        # Filter data for this pool
        baseline_pool = data['baseline_license'][data['baseline_license']['Pool'] == pool]
        lamf_pool = data['lamf_license'][data['lamf_license']['Pool'] == pool]

        # Plot Baseline
        ax.plot(baseline_pool['Time_Hours'], baseline_pool['Utilization_Percent'],
                color=COLOR_BASELINE, linewidth=2, label='Baseline', linestyle='-')

        # Plot LAMF
        ax.plot(lamf_pool['Time_Hours'], lamf_pool['Utilization_Percent'],
                color=COLOR_LAMF, linewidth=2, label='LAMF', linestyle='--')

        # Saturation line
        ax.axhline(100, color='red', linestyle=':', linewidth=1, alpha=0.5, label='Saturation')

        # Styling
        ax.set_ylabel(f'{pool}\nUtilization (%)', fontweight='bold')
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(loc='upper right', framealpha=0.9)
        ax.set_ylim(0, max(110, baseline_pool['Utilization_Percent'].max(), lamf_pool['Utilization_Percent'].max()) * 1.05)

        # Statistics
        baseline_avg = baseline_pool['Utilization_Percent'].mean()
        lamf_avg = lamf_pool['Utilization_Percent'].mean()
        ax.text(0.02, 0.95, f'Avg: Baseline={baseline_avg:.1f}%, LAMF={lamf_avg:.1f}%',
                transform=ax.transAxes, fontsize=10, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    axes[-1].set_xlabel('Simulation Time (hours)', fontweight='bold')
    fig.suptitle('License Utilization Over Time by Pool: Baseline vs LAMF',
                 fontsize=14, fontweight='bold', y=0.995)

    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '1_license_utilization_timeseries.png'
    output_pdf = PLOTS_DIR / '1_license_utilization_timeseries.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Plot 2: Dual-Axis - License-Compute Coupling
# ============================================================================

def plot_license_compute_coupling(data):
    """
    Dual-axis plot showing correlation between license and compute utilization.
    Left Y-axis: Average license utilization
    Right Y-axis: Compute utilization
    """
    print("\n[2/4] Generating License-Compute Coupling Plot...")

    fig, ax1 = plt.subplots(figsize=(12, 6))

    # Compute average license utilization per timestamp
    baseline_license_avg = data['baseline_license'].groupby('Time_Hours')['Utilization_Percent'].mean()
    lamf_license_avg = data['lamf_license'].groupby('Time_Hours')['Utilization_Percent'].mean()

    # Compute resource utilization (percentage of allocated resources)
    # Assuming max observed free resources = total capacity
    baseline_res = data['baseline_resources']
    lamf_res = data['lamf_resources']

    total_onprem = max(baseline_res['On-prem'].max(), lamf_res['On-prem'].max())
    total_cloud = max(baseline_res['Cloud'].max(), lamf_res['Cloud'].max())
    total_resources = total_onprem + total_cloud

    baseline_res['Compute_Util'] = 100 * (1 - (baseline_res['On-prem'] + baseline_res['Cloud']) / total_resources)
    lamf_res['Compute_Util'] = 100 * (1 - (lamf_res['On-prem'] + lamf_res['Cloud']) / total_resources)

    # Plot license utilization (left Y-axis)
    ax1.plot(baseline_license_avg.index, baseline_license_avg.values,
             color=COLOR_BASELINE, linewidth=2.5, label='Baseline License', linestyle='-')
    ax1.plot(lamf_license_avg.index, lamf_license_avg.values,
             color=COLOR_LAMF, linewidth=2.5, label='LAMF License', linestyle='-')

    ax1.set_xlabel('Simulation Time (hours)', fontweight='bold')
    ax1.set_ylabel('Average License Utilization (%)', color='black', fontweight='bold')
    ax1.tick_params(axis='y', labelcolor='black')
    ax1.grid(True, alpha=0.3, linestyle='--')

    # Create second Y-axis for compute utilization
    ax2 = ax1.twinx()
    ax2.plot(baseline_res['Time_Hours'], baseline_res['Compute_Util'],
             color=COLOR_BASELINE, linewidth=2, label='Baseline Compute', linestyle=':', alpha=0.7)
    ax2.plot(lamf_res['Time_Hours'], lamf_res['Compute_Util'],
             color=COLOR_LAMF, linewidth=2, label='LAMF Compute', linestyle=':', alpha=0.7)

    ax2.set_ylabel('Compute Utilization (%)', color='gray', fontweight='bold')
    ax2.tick_params(axis='y', labelcolor='gray')

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', framealpha=0.9)

    # Correlation statistics
    baseline_corr = np.corrcoef(baseline_license_avg.values,
                                 baseline_res['Compute_Util'][:len(baseline_license_avg)])[0, 1]
    lamf_corr = np.corrcoef(lamf_license_avg.values,
                            lamf_res['Compute_Util'][:len(lamf_license_avg)])[0, 1]

    ax1.text(0.98, 0.02, f'Correlation:\nBaseline: {baseline_corr:.3f}\nLAMF: {lamf_corr:.3f}',
             transform=ax1.transAxes, fontsize=11, verticalalignment='bottom',
             horizontalalignment='right',
             bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.title('License-Compute Resource Coupling: Baseline vs LAMF',
              fontsize=14, fontweight='bold', pad=20)
    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '2_license_compute_coupling.png'
    output_pdf = PLOTS_DIR / '2_license_compute_coupling.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Plot 3: Box Plot - Cost/Flowtime/Wait Time Distribution
# ============================================================================

def plot_performance_boxplot(data):
    """
    Box plots comparing cost, flowtime, and wait time distributions.
    Shows median, quartiles, outliers, and statistical significance.
    """
    print("\n[3/4] Generating Performance Metrics Box Plot...")

    baseline_results = data['baseline_results']
    lamf_results = data['lamf_results']

    # Filter completed workflows only
    baseline_complete = baseline_results[baseline_results['complete'] == True]
    lamf_complete = lamf_results[lamf_results['complete'] == True]

    # Calculate wait time (exec_start_time - submit_time)
    baseline_complete = baseline_complete.copy()
    lamf_complete = lamf_complete.copy()
    baseline_complete['wait_time'] = baseline_complete['exec_start_time'] - baseline_complete['submit_time']
    lamf_complete['wait_time'] = lamf_complete['exec_start_time'] - lamf_complete['submit_time']

    # Calculate flowtime (finish_time - submit_time)
    baseline_complete['flowtime'] = baseline_complete['finish_time'] - baseline_complete['submit_time']
    lamf_complete['flowtime'] = lamf_complete['finish_time'] - lamf_complete['submit_time']

    # Prepare data
    metrics = ['cost', 'flowtime', 'wait_time']
    labels = ['Cost ($)', 'Flowtime (s)', 'Wait Time (s)']

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    for idx, (metric, label) in enumerate(zip(metrics, labels)):
        ax = axes[idx]

        baseline_values = baseline_complete[metric].dropna()
        lamf_values = lamf_complete[metric].dropna()

        # Create box plot
        box_data = [baseline_values, lamf_values]
        positions = [1, 2]
        bp = ax.boxplot(box_data, positions=positions, widths=0.6,
                        patch_artist=True, showfliers=True,
                        boxprops=dict(linewidth=1.5),
                        medianprops=dict(color='red', linewidth=2),
                        whiskerprops=dict(linewidth=1.5),
                        capprops=dict(linewidth=1.5))

        # Color boxes
        bp['boxes'][0].set_facecolor(COLOR_BASELINE)
        bp['boxes'][0].set_alpha(0.7)
        bp['boxes'][1].set_facecolor(COLOR_LAMF)
        bp['boxes'][1].set_alpha(0.7)

        # Labels
        ax.set_ylabel(label, fontweight='bold')
        ax.set_xticks([1, 2])
        ax.set_xticklabels(['Baseline', 'LAMF'])
        ax.grid(True, alpha=0.3, axis='y', linestyle='--')

        # Statistical test (t-test)
        t_stat, p_value = stats.ttest_ind(baseline_values, lamf_values)
        significance = '***' if p_value < 0.001 else ('**' if p_value < 0.01 else ('*' if p_value < 0.05 else 'ns'))

        # Add statistics text
        baseline_median = baseline_values.median()
        lamf_median = lamf_values.median()
        improvement = ((baseline_median - lamf_median) / baseline_median) * 100

        stats_text = f'Median:\nB: {baseline_median:.2f}\nL: {lamf_median:.2f}\nΔ: {improvement:+.1f}%\np={p_value:.4f} {significance}'
        ax.text(0.98, 0.98, stats_text, transform=ax.transAxes,
                fontsize=9, verticalalignment='top', horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.7))

    fig.suptitle('Performance Metrics Distribution: Baseline vs LAMF',
                 fontsize=14, fontweight='bold', y=0.98)
    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '3_cost_flowtime_wait_boxplot.png'
    output_pdf = PLOTS_DIR / '3_cost_flowtime_wait_boxplot.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Plot 4: Grouped Bar - Violation Rates
# ============================================================================

def plot_violation_rates(data):
    """
    Grouped bar chart showing deadline, budget, and overall violation rates.
    """
    print("\n[4/4] Generating Violation Rates Bar Chart...")

    baseline_results = data['baseline_results']
    lamf_results = data['lamf_results']

    # Filter completed workflows
    baseline_complete = baseline_results[baseline_results['complete'] == True]
    lamf_complete = lamf_results[lamf_results['complete'] == True]

    total_workflows = 400  # From constants

    # Calculate violation rates
    def calc_violations(df):
        deadline_violations = (df['finish_time'] > df['deadline']).sum()
        budget_violations = (df['cost'] > df['budget']).sum()
        # Overall = workflows that violated either deadline OR budget
        overall_violations = ((df['finish_time'] > df['deadline']) | (df['cost'] > df['budget'])).sum()

        # Include incomplete workflows in violations
        incomplete = total_workflows - len(df)

        return {
            'deadline_rate': (deadline_violations + incomplete) / total_workflows,
            'budget_rate': budget_violations / total_workflows,
            'overall_rate': (overall_violations + incomplete) / total_workflows
        }

    baseline_violations = calc_violations(baseline_complete)
    lamf_violations = calc_violations(lamf_complete)

    # Prepare data
    metrics = ['Deadline Miss', 'Budget Miss', 'Overall Miss']
    baseline_rates = [baseline_violations['deadline_rate'] * 100,
                      baseline_violations['budget_rate'] * 100,
                      baseline_violations['overall_rate'] * 100]
    lamf_rates = [lamf_violations['deadline_rate'] * 100,
                  lamf_violations['budget_rate'] * 100,
                  lamf_violations['overall_rate'] * 100]

    # Create plot
    fig, ax = plt.subplots(figsize=(10, 6))

    x = np.arange(len(metrics))
    width = 0.35

    bars1 = ax.bar(x - width/2, baseline_rates, width, label='Baseline',
                   color=COLOR_BASELINE, alpha=0.8, edgecolor='black', linewidth=1.5)
    bars2 = ax.bar(x + width/2, lamf_rates, width, label='LAMF',
                   color=COLOR_LAMF, alpha=0.8, edgecolor='black', linewidth=1.5)

    # Labels and styling
    ax.set_ylabel('Violation Rate (%)', fontweight='bold')
    ax.set_xlabel('Constraint Type', fontweight='bold')
    ax.set_title('SLA Violation Rates: Baseline vs LAMF', fontsize=14, fontweight='bold', pad=20)
    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')
    ax.set_ylim(0, max(max(baseline_rates), max(lamf_rates)) * 1.2)

    # Add value labels on bars
    def autolabel(bars):
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f'{height:.1f}%',
                       xy=(bar.get_x() + bar.get_width() / 2, height),
                       xytext=(0, 3),
                       textcoords="offset points",
                       ha='center', va='bottom', fontsize=10, fontweight='bold')

    autolabel(bars1)
    autolabel(bars2)

    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '4_violation_rates.png'
    output_pdf = PLOTS_DIR / '4_violation_rates.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Plot 5: Stacked Bar - Hardware vs License Cost Contribution
# ============================================================================

def plot_cost_breakdown_stacked(data):
    """
    Stacked bar chart showing hardware vs license cost contribution.
    Compares Baseline vs LAMF total costs with breakdown.
    """
    print("\n[5/7] Generating Cost Breakdown Stacked Bar Chart...")

    baseline_results = data['baseline_results']
    lamf_results = data['lamf_results']

    # Filter completed workflows only
    baseline_complete = baseline_results[baseline_results['complete'] == True]
    lamf_complete = lamf_results[lamf_results['complete'] == True]

    # Check if cost breakdown columns exist
    if 'hardware_cost' not in baseline_complete.columns or 'license_cost' not in baseline_complete.columns:
        print("  ⚠ Warning: Cost breakdown columns not found in CSV. Skipping plot.")
        print("  → Re-run simulation to generate CSV files with hardware_cost and license_cost columns.")
        return

    # Calculate totals
    baseline_hw_total = baseline_complete['hardware_cost'].sum()
    baseline_lic_total = baseline_complete['license_cost'].sum()
    lamf_hw_total = lamf_complete['hardware_cost'].sum()
    lamf_lic_total = lamf_complete['license_cost'].sum()

    # Prepare data
    schedulers = ['Baseline', 'LAMF']
    hardware_costs = [baseline_hw_total, lamf_hw_total]
    license_costs = [baseline_lic_total, lamf_lic_total]

    # Create plot
    fig, ax = plt.subplots(figsize=(10, 7))

    x = np.arange(len(schedulers))
    width = 0.5

    # Stacked bars
    bars1 = ax.bar(x, hardware_costs, width, label='Hardware Cost',
                   color='#3B7EA1', alpha=0.9, edgecolor='black', linewidth=1.5)
    bars2 = ax.bar(x, license_costs, width, bottom=hardware_costs, label='License Cost',
                   color='#FDB462', alpha=0.9, edgecolor='black', linewidth=1.5)

    # Labels and styling
    ax.set_ylabel('Total Cost (€)', fontweight='bold', fontsize=13)
    ax.set_xlabel('Scheduler', fontweight='bold', fontsize=13)
    ax.set_title('Cost Breakdown: Hardware vs License (Baseline vs LAMF)',
                 fontsize=14, fontweight='bold', pad=20)
    ax.set_xticks(x)
    ax.set_xticklabels(schedulers, fontsize=12)
    ax.legend(loc='upper right', framealpha=0.9, fontsize=11)
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')

    # Add value labels on bars
    for i, scheduler in enumerate(schedulers):
        hw_cost = hardware_costs[i]
        lic_cost = license_costs[i]
        total = hw_cost + lic_cost

        # Hardware label
        ax.text(i, hw_cost/2, f'€{hw_cost:.2f}', ha='center', va='center',
                fontsize=10, fontweight='bold', color='white')

        # License label
        ax.text(i, hw_cost + lic_cost/2, f'€{lic_cost:.2f}', ha='center', va='center',
                fontsize=10, fontweight='bold', color='white')

        # Total label
        ax.text(i, total, f'Total: €{total:.2f}', ha='center', va='bottom',
                fontsize=10, fontweight='bold')

        # Percentage breakdown
        hw_pct = (hw_cost / total) * 100 if total > 0 else 0
        lic_pct = (lic_cost / total) * 100 if total > 0 else 0

    # Add percentage annotation
    baseline_total = hardware_costs[0] + license_costs[0]
    lamf_total = hardware_costs[1] + license_costs[1]
    baseline_lic_pct = (license_costs[0] / baseline_total * 100) if baseline_total > 0 else 0
    lamf_lic_pct = (license_costs[1] / lamf_total * 100) if lamf_total > 0 else 0

    stats_text = f'License Cost Percentage:\nBaseline: {baseline_lic_pct:.1f}%\nLAMF: {lamf_lic_pct:.1f}%'
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
            fontsize=11, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '5_cost_breakdown_stacked.png'
    output_pdf = PLOTS_DIR / '5_cost_breakdown_stacked.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Plot 6: Box Plot - Hardware vs License Cost Distribution
# ============================================================================

def plot_cost_components_boxplot(data):
    """
    Box plots comparing hardware cost vs license cost distributions per workflow.
    Shows the distribution of costs across workflows.
    """
    print("\n[6/7] Generating Hardware vs License Cost Distribution Box Plot...")

    baseline_results = data['baseline_results']
    lamf_results = data['lamf_results']

    # Filter completed workflows only
    baseline_complete = baseline_results[baseline_results['complete'] == True]
    lamf_complete = lamf_results[lamf_results['complete'] == True]

    # Check if cost breakdown columns exist
    if 'hardware_cost' not in baseline_complete.columns or 'license_cost' not in baseline_complete.columns:
        print("  ⚠ Warning: Cost breakdown columns not found in CSV. Skipping plot.")
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Plot 1: Hardware Cost Distribution
    ax = axes[0]
    baseline_hw = baseline_complete['hardware_cost'].dropna()
    lamf_hw = lamf_complete['hardware_cost'].dropna()

    box_data = [baseline_hw, lamf_hw]
    positions = [1, 2]
    bp = ax.boxplot(box_data, positions=positions, widths=0.6,
                    patch_artist=True, showfliers=True,
                    boxprops=dict(linewidth=1.5),
                    medianprops=dict(color='red', linewidth=2),
                    whiskerprops=dict(linewidth=1.5),
                    capprops=dict(linewidth=1.5))

    bp['boxes'][0].set_facecolor(COLOR_BASELINE)
    bp['boxes'][0].set_alpha(0.7)
    bp['boxes'][1].set_facecolor(COLOR_LAMF)
    bp['boxes'][1].set_alpha(0.7)

    ax.set_ylabel('Hardware Cost (€)', fontweight='bold')
    ax.set_title('Hardware Cost per Workflow', fontweight='bold')
    ax.set_xticks([1, 2])
    ax.set_xticklabels(['Baseline', 'LAMF'])
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')

    # Add median values
    baseline_hw_median = baseline_hw.median()
    lamf_hw_median = lamf_hw.median()
    ax.text(0.98, 0.98, f'Median:\nB: €{baseline_hw_median:.4f}\nL: €{lamf_hw_median:.4f}',
            transform=ax.transAxes, fontsize=10, verticalalignment='top',
            horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.7))

    # Plot 2: License Cost Distribution
    ax = axes[1]
    baseline_lic = baseline_complete['license_cost'].dropna()
    lamf_lic = lamf_complete['license_cost'].dropna()

    box_data = [baseline_lic, lamf_lic]
    bp = ax.boxplot(box_data, positions=positions, widths=0.6,
                    patch_artist=True, showfliers=True,
                    boxprops=dict(linewidth=1.5),
                    medianprops=dict(color='red', linewidth=2),
                    whiskerprops=dict(linewidth=1.5),
                    capprops=dict(linewidth=1.5))

    bp['boxes'][0].set_facecolor(COLOR_BASELINE)
    bp['boxes'][0].set_alpha(0.7)
    bp['boxes'][1].set_facecolor(COLOR_LAMF)
    bp['boxes'][1].set_alpha(0.7)

    ax.set_ylabel('License Cost (€)', fontweight='bold')
    ax.set_title('License Cost per Workflow', fontweight='bold')
    ax.set_xticks([1, 2])
    ax.set_xticklabels(['Baseline', 'LAMF'])
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')

    # Add median values
    baseline_lic_median = baseline_lic.median()
    lamf_lic_median = lamf_lic.median()
    ax.text(0.98, 0.98, f'Median:\nB: €{baseline_lic_median:.4f}\nL: €{lamf_lic_median:.4f}',
            transform=ax.transAxes, fontsize=10, verticalalignment='top',
            horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.7))

    fig.suptitle('Cost Component Distribution: Hardware vs License',
                 fontsize=14, fontweight='bold', y=0.98)
    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '6_hardware_license_cost_boxplot.png'
    output_pdf = PLOTS_DIR / '6_hardware_license_cost_boxplot.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Plot 7: Grouped Bar - Cost Breakdown by Software Type
# ============================================================================

def plot_cost_by_software(data):
    """
    Grouped bar chart showing cost breakdown by software type (ANSYS, ABAQUS, LSDYNA).
    Compares hardware vs license costs for each software.
    """
    print("\n[7/7] Generating Cost Breakdown by Software Type...")

    baseline_results = data['baseline_results']
    lamf_results = data['lamf_results']

    # Filter completed workflows only
    baseline_complete = baseline_results[baseline_results['complete'] == True]
    lamf_complete = lamf_results[lamf_results['complete'] == True]

    # Check if required columns exist
    if 'hardware_cost' not in baseline_complete.columns or 'software_id' not in baseline_complete.columns:
        print("  ⚠ Warning: Required columns not found in CSV. Skipping plot.")
        return

    # Software ID mapping (from metrics_LA.py)
    SOFTWARE_NAMES = {1: 'ANSYS', 2: 'ABAQUS', 3: 'LSDYNA', 0: 'Unlicensed'}

    def aggregate_by_software(df):
        """Aggregate costs by software type."""
        costs_by_sw = {}
        for sw_id in [1, 2, 3]:  # Only licensed software
            sw_df = df[df['software_id'] == sw_id]
            if len(sw_df) > 0:
                costs_by_sw[sw_id] = {
                    'hardware': sw_df['hardware_cost'].sum(),
                    'license': sw_df['license_cost'].sum(),
                    'count': len(sw_df)
                }
            else:
                costs_by_sw[sw_id] = {'hardware': 0, 'license': 0, 'count': 0}
        return costs_by_sw

    baseline_by_sw = aggregate_by_software(baseline_complete)
    lamf_by_sw = aggregate_by_software(lamf_complete)

    # Prepare data for plotting
    software_names = ['ANSYS', 'ABAQUS', 'LSDYNA']
    software_ids = [1, 2, 3]

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Plot 1: Baseline
    ax = axes[0]
    hw_costs = [baseline_by_sw[sw_id]['hardware'] for sw_id in software_ids]
    lic_costs = [baseline_by_sw[sw_id]['license'] for sw_id in software_ids]
    counts = [baseline_by_sw[sw_id]['count'] for sw_id in software_ids]

    x = np.arange(len(software_names))
    width = 0.35

    bars1 = ax.bar(x - width/2, hw_costs, width, label='Hardware',
                   color='#3B7EA1', alpha=0.8, edgecolor='black', linewidth=1.5)
    bars2 = ax.bar(x + width/2, lic_costs, width, label='License',
                   color='#FDB462', alpha=0.8, edgecolor='black', linewidth=1.5)

    ax.set_ylabel('Total Cost (€)', fontweight='bold')
    ax.set_xlabel('Software Type', fontweight='bold')
    ax.set_title('Baseline: Cost by Software Type', fontsize=13, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(software_names)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')

    # Add workflow counts as annotations
    for i, (hw, lic, count) in enumerate(zip(hw_costs, lic_costs, counts)):
        ax.text(i, max(hw, lic) * 1.05, f'n={count}', ha='center', va='bottom',
                fontsize=9, fontweight='bold')

    # Plot 2: LAMF
    ax = axes[1]
    hw_costs = [lamf_by_sw[sw_id]['hardware'] for sw_id in software_ids]
    lic_costs = [lamf_by_sw[sw_id]['license'] for sw_id in software_ids]
    counts = [lamf_by_sw[sw_id]['count'] for sw_id in software_ids]

    bars1 = ax.bar(x - width/2, hw_costs, width, label='Hardware',
                   color='#3B7EA1', alpha=0.8, edgecolor='black', linewidth=1.5)
    bars2 = ax.bar(x + width/2, lic_costs, width, label='License',
                   color='#FDB462', alpha=0.8, edgecolor='black', linewidth=1.5)

    ax.set_ylabel('Total Cost (€)', fontweight='bold')
    ax.set_xlabel('Software Type', fontweight='bold')
    ax.set_title('LAMF: Cost by Software Type', fontsize=13, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(software_names)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.grid(True, alpha=0.3, axis='y', linestyle='--')

    # Add workflow counts
    for i, (hw, lic, count) in enumerate(zip(hw_costs, lic_costs, counts)):
        ax.text(i, max(hw, lic) * 1.05, f'n={count}', ha='center', va='bottom',
                fontsize=9, fontweight='bold')

    fig.suptitle('Cost Breakdown by Software Type: Baseline vs LAMF',
                 fontsize=14, fontweight='bold', y=0.98)
    plt.tight_layout()

    # Save
    output_png = PLOTS_DIR / '7_cost_by_software.png'
    output_pdf = PLOTS_DIR / '7_cost_by_software.pdf'
    plt.savefig(output_png, dpi=FIGURE_DPI, bbox_inches='tight')
    plt.savefig(output_pdf, bbox_inches='tight')
    plt.close()

    print(f"  ✓ Saved: {output_png.name} and {output_pdf.name}")

# ============================================================================
# Main Execution
# ============================================================================

def main():
    """Main execution function."""
    print("="*70)
    print("LAMF vs Baseline Visualization Generator")
    print("="*70)

    # Ensure plots directory exists
    PLOTS_DIR.mkdir(exist_ok=True)

    # Load data
    data = load_data()

    # Generate plots
    print("\nGenerating must-have plots...")
    plot_license_utilization_timeseries(data)
    plot_license_compute_coupling(data)
    plot_performance_boxplot(data)
    plot_violation_rates(data)

    # Generate cost breakdown plots (requires updated CSV with hardware_cost and license_cost)
    print("\nGenerating cost breakdown plots...")
    plot_cost_breakdown_stacked(data)
    plot_cost_components_boxplot(data)
    plot_cost_by_software(data)

    print("\n" + "="*70)
    print("✓ All plots generated successfully!")
    print(f"✓ Output directory: {PLOTS_DIR}")
    print("="*70)

if __name__ == '__main__':
    main()
