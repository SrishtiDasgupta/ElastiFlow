"""
Workflow data models for SeisSol iterative workflows.

Defines the structure for workflows with multiple iterations,
where each iteration can have different chain counts and TinyDA iterations.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import random
import yaml
from pathlib import Path
import sys

# Handle imports for both package and standalone execution
try:
    from .runtime import get_iteration_runtime
except ImportError:
    # Running as standalone script
    from runtime import get_iteration_runtime


@dataclass
class IterationConfig:
    """Configuration for a single workflow iteration."""
    chains: int                 # Number of parallel MCMC chains
    tinyda_iterations: int      # Number of TinyDA iterations per chain
    nodes_per_chain: int        # Nodes allocated to each chain

    def get_total_nodes(self) -> int:
        """Total nodes needed for this iteration."""
        return self.chains * self.nodes_per_chain

    def __post_init__(self):
        if self.chains < 1:
            raise ValueError(f"chains must be >= 1, got {self.chains}")
        if self.tinyda_iterations < 1:
            raise ValueError(f"tinyda_iterations must be >= 1, got {self.tinyda_iterations}")
        if self.nodes_per_chain < 1:
            raise ValueError(f"nodes_per_chain must be >= 1, got {self.nodes_per_chain}")


@dataclass
class SeisSolWorkflow:
    """
    Represents a SeisSol iterative workflow.

    A workflow consists of multiple iterations (optimization rounds),
    where each iteration runs multiple chains in parallel, and each
    chain performs TinyDA iterations.
    """
    id: str
    submit_time: float                          # Simulation time when workflow is submitted
    mesh: int                                   # Mesh size: 500, 750, or 1000
    iterations: List[IterationConfig]           # Per-iteration configuration
    budget: float = 1000.0                      # Budget constraint (dollars)
    deadline: float = 86400.0                   # Deadline constraint (seconds, default 24h)

    def __post_init__(self):
        if self.mesh not in [500, 750, 1000]:
            raise ValueError(f"mesh must be 500, 750, or 1000, got {self.mesh}")
        if len(self.iterations) < 1:
            raise ValueError("workflow must have at least 1 iteration")

    @property
    def num_iterations(self) -> int:
        """Number of workflow iterations."""
        return len(self.iterations)

    def get_total_nodes_for_iteration(self, iter_idx: int) -> int:
        """Get total nodes needed for a specific iteration."""
        if iter_idx < 0 or iter_idx >= len(self.iterations):
            raise IndexError(f"iteration index {iter_idx} out of range")
        return self.iterations[iter_idx].get_total_nodes()

    def get_iteration_runtime(self, iter_idx: int) -> float:
        """
        Get estimated runtime in seconds for a specific iteration.

        Args:
            iter_idx: Index of the iteration (0-based)

        Returns:
            Runtime in seconds
        """
        if iter_idx < 0 or iter_idx >= len(self.iterations):
            raise IndexError(f"iteration index {iter_idx} out of range")

        cfg = self.iterations[iter_idx]
        return get_iteration_runtime(
            nodes_per_chain=cfg.nodes_per_chain,
            mesh=self.mesh,
            tinyda_iterations=cfg.tinyda_iterations
        )

    def get_total_runtime(self) -> float:
        """Get total estimated runtime for all iterations (without delays)."""
        return sum(self.get_iteration_runtime(i) for i in range(len(self.iterations)))

    def get_max_nodes_needed(self) -> int:
        """Get maximum nodes needed across all iterations."""
        return max(cfg.get_total_nodes() for cfg in self.iterations)


@dataclass
class WorkflowGenerator:
    """
    Generates synthetic SeisSol workflows for simulation.

    Uses configurable ranges for workflow parameters.
    """
    seed: int = 42
    num_workflows: int = 50
    iteration_range: tuple = (2, 6)
    mesh_sizes: List[int] = field(default_factory=lambda: [500, 750, 1000])
    mesh_distribution: List[float] = field(default_factory=lambda: [0.33, 0.34, 0.33])
    chains_range: tuple = (2, 8)
    nodes_per_chain_range: tuple = (1, 4)
    tinyda_iterations_range: tuple = (1, 10)
    mean_interarrival_seconds: float = 3600.0

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def generate_workflows(self) -> List[SeisSolWorkflow]:
        """Generate a list of synthetic workflows."""
        workflows = []
        current_time = 0.0

        for i in range(self.num_workflows):
            # Generate workflow
            wf = self._generate_single_workflow(f"wf-{i:04d}", current_time)
            workflows.append(wf)

            # Generate interarrival time (exponential distribution)
            interarrival = self.rng.expovariate(1.0 / self.mean_interarrival_seconds)
            current_time += interarrival

        return workflows

    def _generate_single_workflow(self, wf_id: str, submit_time: float) -> SeisSolWorkflow:
        """Generate a single workflow with random parameters."""
        # Select mesh size based on distribution
        mesh = self.rng.choices(self.mesh_sizes, weights=self.mesh_distribution, k=1)[0]

        # Generate number of iterations
        num_iterations = self.rng.randint(*self.iteration_range)

        # Generate iteration configurations
        iterations = []
        for _ in range(num_iterations):
            chains = self.rng.randint(*self.chains_range)
            nodes_per_chain = self.rng.randint(*self.nodes_per_chain_range)
            tinyda_iters = self.rng.randint(*self.tinyda_iterations_range)

            iterations.append(IterationConfig(
                chains=chains,
                tinyda_iterations=tinyda_iters,
                nodes_per_chain=nodes_per_chain
            ))

        # Calculate reasonable budget and deadline based on workflow complexity
        wf = SeisSolWorkflow(
            id=wf_id,
            submit_time=submit_time,
            mesh=mesh,
            iterations=iterations
        )

        # Set budget/deadline with some slack
        total_runtime = wf.get_total_runtime()
        wf.deadline = total_runtime * 2.0  # 2x runtime as deadline
        wf.budget = total_runtime * 0.01   # Rough cost estimate

        return wf


def load_config(config_path: str) -> dict:
    """Load experiment configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def create_generator_from_config(config: dict) -> WorkflowGenerator:
    """Create a WorkflowGenerator from experiment config."""
    wf_config = config.get('workflows', {})
    exp_config = config.get('experiment', {})

    return WorkflowGenerator(
        seed=exp_config.get('seed', 42),
        num_workflows=wf_config.get('num_workflows', 50),
        iteration_range=tuple(wf_config.get('iteration_range', [2, 6])),
        mesh_sizes=wf_config.get('mesh_sizes', [500, 750, 1000]),
        mesh_distribution=wf_config.get('mesh_distribution', [0.33, 0.34, 0.33]),
        chains_range=tuple(wf_config.get('chains_range', [2, 8])),
        nodes_per_chain_range=tuple(wf_config.get('nodes_per_chain_range', [1, 4])),
        tinyda_iterations_range=tuple(wf_config.get('tinyda_iterations_range', [1, 10])),
        mean_interarrival_seconds=wf_config.get('mean_interarrival_seconds', 3600.0)
    )


if __name__ == "__main__":
    # Test workflow generation
    print("Workflow Generation Test")
    print("=" * 60)

    generator = WorkflowGenerator(seed=42, num_workflows=5)
    workflows = generator.generate_workflows()

    for wf in workflows:
        print(f"\n{wf.id}:")
        print(f"  Submit time: {wf.submit_time:.0f}s")
        print(f"  Mesh: {wf.mesh}")
        print(f"  Iterations: {wf.num_iterations}")
        print(f"  Max nodes: {wf.get_max_nodes_needed()}")
        print(f"  Total runtime: {wf.get_total_runtime():.0f}s ({wf.get_total_runtime()/3600:.2f}h)")

        for i, iter_cfg in enumerate(wf.iterations):
            runtime = wf.get_iteration_runtime(i)
            print(f"    Iter {i}: {iter_cfg.chains} chains x {iter_cfg.nodes_per_chain} nodes, "
                  f"{iter_cfg.tinyda_iterations} TinyDA iters -> {runtime:.0f}s")
