#!/usr/bin/env python3
"""
Main CLI entry point for Manual vs Automated Workflow Comparison Experiment.

Usage:
    python run_experiment.py                    # Run with default config
    python run_experiment.py --config my_config.yaml
    python run_experiment.py --num-workflows 100 --replications 30
    python run_experiment.py --no-plots --output-dir ./results
"""

import argparse
import sys
import yaml
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src" / "main"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from manual_vs_auto.simulation.simulate_comparison import (
    ComparisonSimulator,
    ComparisonResult,
    print_results,
    load_config
)
from manual_vs_auto.output.csv_exporter import export_all_results, export_replication_results
from manual_vs_auto.output.plot_generator import generate_all_plots
from manual_vs_auto.metrics.comparison_metrics import compute_replication_statistics


def get_default_config() -> dict:
    """Return default experiment configuration."""
    return {
        'experiment': {
            'name': 'manual_vs_auto_seissol',
            'seed': 42,
            'num_replications': 1
        },
        'resources': {
            'total_nodes': 148,
            'cores_per_node': 48
        },
        'workflows': {
            'num_workflows': 50,
            'iteration_range': [2, 6],
            'mesh_sizes': [500, 750, 1000],
            'mesh_distribution': [0.33, 0.34, 0.33],
            'chains_range': [2, 8],
            'nodes_per_chain_range': [1, 4],
            'tinyda_iterations_range': [1, 10],
            'mean_interarrival_seconds': 3600
        },
        'human_delay': {
            'distribution': 'lognormal',
            'median_hours': 3.0,
            'sigma': 0.9,
            'min_hours': 0.5,
            'max_hours': 24.0,
            'work_hours': {
                'enabled': True,
                'start': 9,
                'end': 17
            }
        },
        'auto_delay': {
            'system_delay_seconds': 5.0
        },
        'output': {
            'dir': 'output/',
            'generate_plots': True,
            'csv_export': True
        }
    }


def run_single_experiment(config: dict, verbose: bool = True) -> ComparisonResult:
    """Run a single experiment and return results."""
    seed = config.get('experiment', {}).get('seed', 42)
    simulator = ComparisonSimulator(config, seed)

    if verbose:
        print("Running simulation...")

    result = simulator.run_comparison()

    if verbose:
        print_results(result)

    return result


def run_multiple_replications(
    config: dict,
    num_replications: int,
    verbose: bool = True
) -> tuple:
    """
    Run multiple replications and compute statistics.

    Returns:
        Tuple of (results list, statistics dict)
    """
    seed = config.get('experiment', {}).get('seed', 42)
    simulator = ComparisonSimulator(config, seed)

    if verbose:
        print(f"Running {num_replications} replications...")

    results = simulator.run_multiple_replications(num_replications)

    # Collect data for statistics
    manual_makespans = [r.manual_results['total_makespan'] for r in results]
    auto_makespans = [r.auto_results['total_makespan'] for r in results]

    manual_turnarounds = []
    auto_turnarounds = []
    for r in results:
        m_turns = [wf['turnaround_time'] for wf in r.manual_results['workflows'].values() if wf['completed']]
        a_turns = [wf['turnaround_time'] for wf in r.auto_results['workflows'].values() if wf['completed']]
        if m_turns:
            manual_turnarounds.append(sum(m_turns) / len(m_turns))
        if a_turns:
            auto_turnarounds.append(sum(a_turns) / len(a_turns))

    stats = compute_replication_statistics(
        manual_makespans, auto_makespans,
        manual_turnarounds, auto_turnarounds
    )

    if verbose:
        print("\n" + "=" * 70)
        print("STATISTICAL ANALYSIS (across replications)")
        print("=" * 70)
        print(f"\nMakespan:")
        print(f"  Manual mean: {stats['manual_makespan_mean']/3600:.2f}h (+/- {stats['manual_makespan_std']/3600:.2f}h)")
        print(f"  Auto mean:   {stats['auto_makespan_mean']/3600:.2f}h (+/- {stats['auto_makespan_std']/3600:.2f}h)")
        print(f"  Improvement: {stats['improvement_mean_hours']:.2f}h")

        if 'makespan_p_value' in stats:
            print(f"\n  t-statistic: {stats['makespan_t_stat']:.3f}")
            print(f"  p-value:     {stats['makespan_p_value']:.6f}")
            print(f"  Cohen's d:   {stats['makespan_cohens_d']:.3f}")

            if stats['makespan_p_value'] < 0.05:
                print("  Result: STATISTICALLY SIGNIFICANT (p < 0.05)")
            else:
                print("  Result: NOT statistically significant")

    return results, stats


def main():
    parser = argparse.ArgumentParser(
        description='Manual vs Automated Workflow Comparison Experiment',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default configuration
  python run_experiment.py

  # Use custom config file
  python run_experiment.py --config my_config.yaml

  # Run with 100 workflows
  python run_experiment.py --num-workflows 100

  # Run 30 replications for statistical significance
  python run_experiment.py --replications 30

  # Specify output directory
  python run_experiment.py --output-dir ./results

  # Run without generating plots
  python run_experiment.py --no-plots

  # Quick test with few workflows
  python run_experiment.py --num-workflows 5 --quick
        """
    )

    parser.add_argument(
        '--config', '-c',
        type=str,
        help='Path to experiment configuration YAML file'
    )

    parser.add_argument(
        '--num-workflows', '-n',
        type=int,
        help='Override number of workflows to simulate'
    )

    parser.add_argument(
        '--replications', '-r',
        type=int,
        default=1,
        help='Number of replications to run (default: 1)'
    )

    parser.add_argument(
        '--seed', '-s',
        type=int,
        help='Random seed for reproducibility'
    )

    parser.add_argument(
        '--output-dir', '-o',
        type=str,
        help='Output directory for results'
    )

    parser.add_argument(
        '--no-plots',
        action='store_true',
        help='Skip plot generation'
    )

    parser.add_argument(
        '--no-csv',
        action='store_true',
        help='Skip CSV export'
    )

    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress detailed output'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick test mode (disable work hours constraint)'
    )

    args = parser.parse_args()

    # Load or create configuration
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: Config file not found: {args.config}")
            sys.exit(1)
        config = load_config(str(config_path))
    else:
        # Try to find default config
        default_config_path = Path(__file__).parent.parent / "config" / "experiment_config.yaml"
        if default_config_path.exists():
            config = load_config(str(default_config_path))
        else:
            config = get_default_config()

    # Apply command-line overrides
    if args.num_workflows:
        config['workflows']['num_workflows'] = args.num_workflows

    if args.seed:
        config['experiment']['seed'] = args.seed

    if args.output_dir:
        config['output']['dir'] = args.output_dir

    if args.no_plots:
        config['output']['generate_plots'] = False

    if args.no_csv:
        config['output']['csv_export'] = False

    if args.quick:
        config['human_delay']['work_hours']['enabled'] = False

    # Create output directory
    output_dir = config.get('output', {}).get('dir', 'output/')
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir) / timestamp
    output_path.mkdir(parents=True, exist_ok=True)

    verbose = not args.quiet

    print("=" * 70)
    print("MANUAL VS AUTOMATED WORKFLOW COMPARISON EXPERIMENT")
    print("=" * 70)
    print(f"\nConfiguration:")
    print(f"  Workflows: {config['workflows']['num_workflows']}")
    print(f"  Iterations per workflow: {config['workflows']['iteration_range']}")
    print(f"  Human delay (median): {config['human_delay']['median_hours']} hours")
    print(f"  Replications: {args.replications}")
    print(f"  Output: {output_path}")

    # Run experiment(s)
    if args.replications == 1:
        result = run_single_experiment(config, verbose)
        results = [result]
        stats = None
    else:
        results, stats = run_multiple_replications(config, args.replications, verbose)
        result = results[0]  # Use first for detailed output

    # Export results
    if config.get('output', {}).get('csv_export', True):
        print("\nExporting CSV results...")
        csv_paths = export_all_results(result, str(output_path))
        for name, path in csv_paths.items():
            print(f"  {name}: {path}")

        if args.replications > 1:
            rep_path = export_replication_results(results, str(output_path))
            print(f"  replications: {rep_path}")

    # Generate plots
    if config.get('output', {}).get('generate_plots', True):
        print("\nGenerating plots...")
        try:
            plot_paths = generate_all_plots(result, str(output_path))
            for name, path in plot_paths.items():
                print(f"  {name}: {path}")
        except Exception as e:
            print(f"  Warning: Could not generate plots: {e}")

    # Save configuration used
    config_save_path = output_path / "experiment_config_used.yaml"
    with open(config_save_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    print(f"\nConfiguration saved to: {config_save_path}")

    print(f"\n{'='*70}")
    print("EXPERIMENT COMPLETE")
    print(f"{'='*70}")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()
