"""
Comparison Simulation Runner.

Runs both manual and automated simulations with identical workflows
and compares the results.
"""

import yaml
import copy
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

# Handle imports for both package and standalone execution
try:
    from ..models.workflow import (
        SeisSolWorkflow,
        WorkflowGenerator,
        create_generator_from_config
    )
    from ..models.human_delay import create_delay_model_from_config
    from ..schedulers.resource_manager import (
        OnPremResourceManager,
        ResourceManagerConfig,
        create_resource_manager_from_config
    )
    from ..schedulers.manual_fcfs import ManualFCFSScheduler
    from ..schedulers.auto_fcfs import AutoFCFSScheduler
except ImportError:
    # Add parent to path for standalone execution
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from models.workflow import (
        SeisSolWorkflow,
        WorkflowGenerator,
        create_generator_from_config
    )
    from models.human_delay import create_delay_model_from_config
    from schedulers.resource_manager import (
        OnPremResourceManager,
        ResourceManagerConfig,
        create_resource_manager_from_config
    )
    from schedulers.manual_fcfs import ManualFCFSScheduler
    from schedulers.auto_fcfs import AutoFCFSScheduler


@dataclass
class ComparisonResult:
    """Results from comparing manual vs automated modes."""
    manual_results: dict
    auto_results: dict
    workflows: List[SeisSolWorkflow]
    config: dict

    @property
    def makespan_improvement(self) -> float:
        """Makespan improvement (manual - auto) / manual."""
        if self.manual_results['total_makespan'] > 0:
            return (
                self.manual_results['total_makespan'] -
                self.auto_results['total_makespan']
            ) / self.manual_results['total_makespan']
        return 0.0

    @property
    def turnaround_improvement(self) -> float:
        """Average turnaround time improvement."""
        manual_avg = self._get_average_turnaround(self.manual_results)
        auto_avg = self._get_average_turnaround(self.auto_results)
        if manual_avg > 0:
            return (manual_avg - auto_avg) / manual_avg
        return 0.0

    @property
    def utilization_improvement(self) -> float:
        """Utilization improvement."""
        return (
            self.auto_results['average_utilization'] -
            self.manual_results['average_utilization']
        )

    def _get_average_turnaround(self, results: dict) -> float:
        """Calculate average turnaround time from results."""
        turnarounds = [
            wf['turnaround_time']
            for wf in results['workflows'].values()
            if wf['completed']
        ]
        return sum(turnarounds) / len(turnarounds) if turnarounds else 0.0

    def get_summary(self) -> dict:
        """Get summary of comparison results."""
        return {
            'num_workflows': len(self.workflows),
            'manual': {
                'makespan_hours': self.manual_results['total_makespan'] / 3600,
                'compute_hours': self.manual_results['total_compute_time'] / 3600,
                'human_delay_hours': self.manual_results['total_human_delay'] / 3600,
                'queue_wait_hours': self.manual_results['total_queue_wait_time'] / 3600,
                'utilization': self.manual_results['average_utilization'],
            },
            'automated': {
                'makespan_hours': self.auto_results['total_makespan'] / 3600,
                'compute_hours': self.auto_results['total_compute_time'] / 3600,
                'system_delay_seconds': self.auto_results['total_system_delay'],
                'queue_wait_hours': self.auto_results['total_queue_wait_time'] / 3600,
                'utilization': self.auto_results['average_utilization'],
            },
            'improvement': {
                'makespan_pct': self.makespan_improvement * 100,
                'turnaround_pct': self.turnaround_improvement * 100,
                'utilization_ppt': self.utilization_improvement * 100,
            }
        }


class ComparisonSimulator:
    """
    Runs comparison simulations between manual and automated modes.

    Ensures fair comparison by using:
    - Identical workflows
    - Same resource configuration
    - Same random seeds for workflow generation
    """

    def __init__(self, config: dict, seed: int = 42):
        """
        Initialize the comparison simulator.

        Args:
            config: Experiment configuration dictionary
            seed: Random seed for reproducibility
        """
        self.config = config
        self.seed = seed

    def generate_workflows(self) -> List[SeisSolWorkflow]:
        """Generate workflows for simulation."""
        generator = create_generator_from_config(self.config)
        generator.seed = self.seed
        generator.rng = __import__('random').Random(self.seed)
        return generator.generate_workflows()

    def run_manual_simulation(
        self,
        workflows: List[SeisSolWorkflow]
    ) -> dict:
        """
        Run simulation with manual intervention scheduler.

        Args:
            workflows: List of workflows to simulate

        Returns:
            Simulation results
        """
        # Create fresh resource manager
        res_config = self.config.get('resources', {})
        rm_config = ResourceManagerConfig(
            total_nodes=res_config.get('total_nodes', 148),
            cores_per_node=res_config.get('cores_per_node', 48)
        )
        resource_manager = OnPremResourceManager(rm_config)

        # Create delay model
        delay_model = create_delay_model_from_config(self.config, self.seed)

        # Create scheduler
        auto_config = self.config.get('auto_delay', {})
        system_delay = auto_config.get('system_delay_seconds', 5.0)

        scheduler = ManualFCFSScheduler(
            resource_manager=resource_manager,
            delay_model=delay_model,
            system_delay=system_delay
        )

        # Deep copy workflows to avoid state pollution
        workflows_copy = [self._copy_workflow(wf) for wf in workflows]

        return scheduler.run_simulation(workflows_copy)

    def run_auto_simulation(
        self,
        workflows: List[SeisSolWorkflow]
    ) -> dict:
        """
        Run simulation with automated scheduler.

        Args:
            workflows: List of workflows to simulate

        Returns:
            Simulation results
        """
        # Create fresh resource manager
        res_config = self.config.get('resources', {})
        rm_config = ResourceManagerConfig(
            total_nodes=res_config.get('total_nodes', 148),
            cores_per_node=res_config.get('cores_per_node', 48)
        )
        resource_manager = OnPremResourceManager(rm_config)

        # Create scheduler
        auto_config = self.config.get('auto_delay', {})
        system_delay = auto_config.get('system_delay_seconds', 5.0)

        scheduler = AutoFCFSScheduler(
            resource_manager=resource_manager,
            system_delay=system_delay
        )

        # Deep copy workflows to avoid state pollution
        workflows_copy = [self._copy_workflow(wf) for wf in workflows]

        return scheduler.run_simulation(workflows_copy)

    def _copy_workflow(self, workflow: SeisSolWorkflow) -> SeisSolWorkflow:
        """Create a deep copy of a workflow."""
        try:
            from ..models.workflow import IterationConfig
        except ImportError:
            from models.workflow import IterationConfig

        iterations = [
            IterationConfig(
                chains=ic.chains,
                tinyda_iterations=ic.tinyda_iterations,
                nodes_per_chain=ic.nodes_per_chain
            )
            for ic in workflow.iterations
        ]

        return SeisSolWorkflow(
            id=workflow.id,
            submit_time=workflow.submit_time,
            mesh=workflow.mesh,
            iterations=iterations,
            budget=workflow.budget,
            deadline=workflow.deadline
        )

    def run_comparison(self) -> ComparisonResult:
        """
        Run both simulations and compare results.

        Returns:
            ComparisonResult with both results and analysis
        """
        # Generate workflows (same for both)
        workflows = self.generate_workflows()

        print(f"Generated {len(workflows)} workflows")
        print("Running manual simulation...")
        manual_results = self.run_manual_simulation(workflows)

        print("Running automated simulation...")
        auto_results = self.run_auto_simulation(workflows)

        return ComparisonResult(
            manual_results=manual_results,
            auto_results=auto_results,
            workflows=workflows,
            config=self.config
        )

    def run_multiple_replications(
        self,
        num_replications: int = 30
    ) -> List[ComparisonResult]:
        """
        Run multiple replications for statistical analysis.

        Args:
            num_replications: Number of replications to run

        Returns:
            List of ComparisonResult for each replication
        """
        results = []
        base_seed = self.seed

        for rep in range(num_replications):
            print(f"\nReplication {rep + 1}/{num_replications}")
            self.seed = base_seed + rep

            result = self.run_comparison()
            results.append(result)

        self.seed = base_seed  # Restore original seed
        return results


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def run_experiment(config_path: str, num_replications: int = 1) -> List[ComparisonResult]:
    """
    Run the complete experiment.

    Args:
        config_path: Path to configuration YAML file
        num_replications: Number of replications

    Returns:
        List of ComparisonResult
    """
    config = load_config(config_path)
    seed = config.get('experiment', {}).get('seed', 42)

    simulator = ComparisonSimulator(config, seed)

    if num_replications == 1:
        return [simulator.run_comparison()]
    else:
        return simulator.run_multiple_replications(num_replications)


def print_results(result: ComparisonResult):
    """Print formatted comparison results."""
    summary = result.get_summary()

    print("\n" + "=" * 70)
    print("COMPARISON RESULTS: Manual vs Automated Workflows")
    print("=" * 70)

    print(f"\nWorkflows simulated: {summary['num_workflows']}")

    print("\n--- MANUAL MODE ---")
    print(f"  Makespan:        {summary['manual']['makespan_hours']:.2f} hours")
    print(f"  Compute time:    {summary['manual']['compute_hours']:.2f} hours")
    print(f"  Human delay:     {summary['manual']['human_delay_hours']:.2f} hours")
    print(f"  Queue wait:      {summary['manual']['queue_wait_hours']:.2f} hours")
    print(f"  Utilization:     {summary['manual']['utilization']:.1%}")

    print("\n--- AUTOMATED MODE ---")
    print(f"  Makespan:        {summary['automated']['makespan_hours']:.2f} hours")
    print(f"  Compute time:    {summary['automated']['compute_hours']:.2f} hours")
    print(f"  System delay:    {summary['automated']['system_delay_seconds']:.0f} seconds")
    print(f"  Queue wait:      {summary['automated']['queue_wait_hours']:.2f} hours")
    print(f"  Utilization:     {summary['automated']['utilization']:.1%}")

    print("\n--- IMPROVEMENT (Automated vs Manual) ---")
    print(f"  Makespan:        {summary['improvement']['makespan_pct']:+.1f}%")
    print(f"  Turnaround:      {summary['improvement']['turnaround_pct']:+.1f}%")
    print(f"  Utilization:     {summary['improvement']['utilization_ppt']:+.1f} ppt")

    # Calculate time saved
    time_saved = summary['manual']['makespan_hours'] - summary['automated']['makespan_hours']
    print(f"\n  Time saved:      {time_saved:.1f} hours")

    print("=" * 70)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Find config file
    config_path = Path(__file__).parent.parent / "config" / "experiment_config.yaml"

    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        print("Using default configuration...")

        # Default config for testing
        config = {
            'experiment': {'seed': 42},
            'resources': {'total_nodes': 148, 'cores_per_node': 48},
            'workflows': {
                'num_workflows': 10,
                'iteration_range': [2, 6],
                'mesh_sizes': [500, 750, 1000],
                'mesh_distribution': [0.33, 0.34, 0.33],
                'chains_range': [2, 8],
                'nodes_per_chain_range': [1, 4],
                'tinyda_iterations_range': [1, 10],
                'mean_interarrival_seconds': 3600
            },
            'human_delay': {
                'median_hours': 3.0,
                'sigma': 0.9,
                'min_hours': 0.5,
                'max_hours': 24.0,
                'work_hours': {'enabled': False}
            },
            'auto_delay': {'system_delay_seconds': 5.0}
        }

        simulator = ComparisonSimulator(config, seed=42)
        result = simulator.run_comparison()
    else:
        results = run_experiment(str(config_path), num_replications=1)
        result = results[0]

    print_results(result)
