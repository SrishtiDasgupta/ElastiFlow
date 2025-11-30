"""
Automated Feedback-driven FCFS Scheduler with Fixed Allocation.

In automated mode, a workflow is submitted once and resources are allocated
ONCE at the start. All iterations run on this fixed allocation, adapting
chain execution to fit the available nodes.

Key characteristics:
- Single workflow submission
- Fixed resource allocation for entire workflow duration
- Automatic iteration triggering with minimal system delay (~5s)
- Wave-based execution: if chains > nodes, run in multiple waves
- If nodes > chains, each chain gets more nodes (faster execution)
- FCFS scheduling for workflow execution slots
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from collections import deque
import heapq
import math
import sys
from pathlib import Path

# Handle imports for both package and standalone execution
try:
    from ..models.workflow import SeisSolWorkflow
    from ..models.runtime import get_iteration_runtime, get_seissol_runtime, get_tinyda_overhead
    from .resource_manager import OnPremResourceManager, ResourceManagerConfig
except ImportError:
    # Add parent to path for standalone execution
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from models.workflow import SeisSolWorkflow
    from models.runtime import get_iteration_runtime, get_seissol_runtime, get_tinyda_overhead
    from schedulers.resource_manager import OnPremResourceManager, ResourceManagerConfig


def calculate_wave_runtime(
    allocated_nodes: int,
    chains: int,
    mesh: int,
    tinyda_iterations: int
) -> float:
    """
    Calculate runtime for an iteration given fixed node allocation.

    Adapts execution to fit allocated nodes:
    - If chains <= allocated_nodes: run all in parallel, distribute nodes among chains
    - If chains > allocated_nodes: run in waves, 1 node per chain

    Args:
        allocated_nodes: Fixed number of nodes allocated to this workflow
        chains: Number of chains needed for this iteration
        mesh: Mesh size (500, 750, 1000)
        tinyda_iterations: Number of TinyDA iterations per chain

    Returns:
        Total runtime in seconds for this iteration
    """
    if allocated_nodes <= 0 or chains <= 0:
        return 0.0

    if chains <= allocated_nodes:
        # Can run all chains in parallel
        # Distribute nodes among chains (some chains may get more nodes)
        nodes_per_chain = allocated_nodes // chains
        # All chains run in parallel, runtime determined by nodes_per_chain
        single_chain_runtime = get_seissol_runtime(nodes_per_chain, mesh)
        overhead = get_tinyda_overhead(mesh)
        runtime = (single_chain_runtime + overhead) * tinyda_iterations
        return runtime
    else:
        # Need multiple waves (more chains than nodes)
        # Each chain gets 1 node
        nodes_per_chain = 1
        chains_per_wave = allocated_nodes
        num_waves = math.ceil(chains / chains_per_wave)

        single_chain_runtime = get_seissol_runtime(nodes_per_chain, mesh)
        overhead = get_tinyda_overhead(mesh)
        single_wave_runtime = (single_chain_runtime + overhead) * tinyda_iterations

        return num_waves * single_wave_runtime


@dataclass
class WorkflowExecution:
    """Tracks execution of a workflow through all its iterations."""
    workflow: SeisSolWorkflow
    allocated_nodes: int = 0          # Fixed allocation for entire workflow
    current_iteration: int = 0
    submit_time: float = 0.0
    start_time: float = 0.0           # When workflow started (resources allocated)
    end_time: float = 0.0             # When last iteration completed
    queue_enter_time: float = 0.0
    total_compute_time: float = 0.0
    total_system_delay: float = 0.0
    iteration_start_times: List[float] = field(default_factory=list)
    iteration_end_times: List[float] = field(default_factory=list)
    completed: bool = False
    running: bool = False

    @property
    def wait_time(self) -> float:
        """Time spent waiting before workflow started."""
        return self.start_time - self.queue_enter_time if self.start_time > 0 else 0.0

    @property
    def turnaround_time(self) -> float:
        """Total time from submit to completion."""
        return self.end_time - self.submit_time if self.completed else 0.0


@dataclass
class Event:
    """Simulation event."""
    time: float
    event_type: str  # 'workflow_submit', 'iteration_complete'
    data: dict

    def __lt__(self, other):
        return self.time < other.time


class AutoFCFSScheduler:
    """
    Automated feedback-driven FCFS scheduler with fixed allocation.

    Resources are allocated ONCE when workflow starts and held until
    all iterations complete. Chain execution adapts to fit the allocation.
    """

    def __init__(
        self,
        resource_manager: OnPremResourceManager,
        system_delay: float = 5.0
    ):
        """
        Initialize the automated scheduler.

        Args:
            resource_manager: Resource manager for the cluster
            system_delay: System processing delay in seconds between iterations
        """
        self.resource_manager = resource_manager
        self.system_delay = system_delay

        # Workflow queue (FCFS order)
        self.workflow_queue: deque[str] = deque()

        # Event queue (priority queue by time)
        self.event_queue: List[Event] = []

        # Workflow execution tracking
        self.executions: Dict[str, WorkflowExecution] = {}

        # Current simulation time
        self.current_time: float = 0.0

    def submit_workflow(self, workflow: SeisSolWorkflow):
        """
        Submit a workflow for automated execution.

        Args:
            workflow: The workflow to submit
        """
        execution = WorkflowExecution(
            workflow=workflow,
            submit_time=workflow.submit_time,
            queue_enter_time=workflow.submit_time
        )
        self.executions[workflow.id] = execution

        # Schedule workflow submit event
        self._schedule_event(Event(
            time=workflow.submit_time,
            event_type='workflow_submit',
            data={'workflow_id': workflow.id}
        ))

    def _schedule_event(self, event: Event):
        """Add an event to the event queue."""
        heapq.heappush(self.event_queue, event)

    def _get_workflow_node_request(self, workflow: SeisSolWorkflow) -> int:
        """
        Determine how many nodes to request for a workflow.

        Uses the maximum nodes needed across all iterations as the request.
        This ensures all iterations can run without re-allocation.

        Args:
            workflow: The workflow

        Returns:
            Number of nodes to request
        """
        return workflow.get_max_nodes_needed()

    def _try_start_workflow(self, workflow_id: str) -> bool:
        """
        Try to start a workflow if resources are available.

        Allocates resources ONCE for the entire workflow duration.

        Args:
            workflow_id: Workflow to start

        Returns:
            True if workflow started, False if waiting
        """
        execution = self.executions[workflow_id]
        workflow = execution.workflow

        # Request nodes based on workflow's maximum requirement
        nodes_requested = self._get_workflow_node_request(workflow)

        if self.resource_manager.allocate(
            workflow_id,  # Single allocation ID for entire workflow
            nodes_requested,
            self.current_time,
            workflow_id,
            -1  # -1 indicates workflow-level allocation
        ):
            # Workflow started - resources allocated for duration
            execution.allocated_nodes = nodes_requested
            execution.running = True
            execution.start_time = self.current_time

            # Start first iteration immediately
            self._start_iteration(workflow_id)
            return True
        return False

    def _start_iteration(self, workflow_id: str):
        """
        Start the current iteration of a workflow.

        Uses the fixed allocation, adapting chain execution as needed.
        """
        execution = self.executions[workflow_id]
        workflow = execution.workflow
        iter_idx = execution.current_iteration

        if iter_idx >= workflow.num_iterations:
            return

        iter_config = workflow.iterations[iter_idx]

        # Record iteration start
        execution.iteration_start_times.append(self.current_time)

        # Calculate runtime with wave-based execution
        runtime = calculate_wave_runtime(
            allocated_nodes=execution.allocated_nodes,
            chains=iter_config.chains,
            mesh=workflow.mesh,
            tinyda_iterations=iter_config.tinyda_iterations
        )

        completion_time = self.current_time + runtime

        self._schedule_event(Event(
            time=completion_time,
            event_type='iteration_complete',
            data={'workflow_id': workflow_id, 'runtime': runtime}
        ))

    def _process_iteration_complete(self, workflow_id: str, runtime: float):
        """
        Process iteration completion.

        Does NOT release resources - they're held until workflow completes.
        Immediately starts next iteration with minimal system delay.
        """
        execution = self.executions[workflow_id]
        workflow = execution.workflow

        # Record iteration end
        execution.iteration_end_times.append(self.current_time)
        execution.total_compute_time += runtime

        # Move to next iteration
        execution.current_iteration += 1

        # Check if more iterations
        if execution.current_iteration < workflow.num_iterations:
            # Add system delay, then start next iteration
            execution.total_system_delay += self.system_delay

            # Schedule next iteration start after system delay
            next_start_time = self.current_time + self.system_delay

            # We use a small trick: schedule a pseudo-event that triggers iteration start
            self._schedule_event(Event(
                time=next_start_time,
                event_type='iteration_start',
                data={'workflow_id': workflow_id}
            ))
        else:
            # Workflow completed - NOW release resources
            execution.completed = True
            execution.running = False
            execution.end_time = self.current_time

            # Release the fixed allocation
            self.resource_manager.release(workflow_id, self.current_time)

            # Try to start waiting workflows
            self._try_start_waiting_workflows()

    def _try_start_waiting_workflows(self):
        """Try to start workflows from the waiting queue."""
        while self.workflow_queue:
            workflow_id = self.workflow_queue[0]
            if self._try_start_workflow(workflow_id):
                self.workflow_queue.popleft()
            else:
                break  # First workflow can't start, stop trying

    def run_simulation(self, workflows: List[SeisSolWorkflow]) -> dict:
        """
        Run the complete simulation.

        Args:
            workflows: List of workflows to simulate

        Returns:
            Simulation results dictionary
        """
        # Reset state
        self.resource_manager.reset()
        self.workflow_queue.clear()
        self.event_queue.clear()
        self.executions.clear()
        self.current_time = 0.0

        # Submit all workflows
        for workflow in workflows:
            self.submit_workflow(workflow)

        # Process events
        while self.event_queue:
            event = heapq.heappop(self.event_queue)
            self.current_time = event.time

            if event.event_type == 'workflow_submit':
                workflow_id = event.data['workflow_id']
                # Try to start workflow, or add to queue
                if not self._try_start_workflow(workflow_id):
                    self.workflow_queue.append(workflow_id)

            elif event.event_type == 'iteration_complete':
                self._process_iteration_complete(
                    event.data['workflow_id'],
                    event.data['runtime']
                )

            elif event.event_type == 'iteration_start':
                # Start next iteration (after system delay)
                self._start_iteration(event.data['workflow_id'])

        return self._collect_results()

    def _collect_results(self) -> dict:
        """Collect and return simulation results."""
        results = {
            'mode': 'automated',
            'workflows': {},
            'total_makespan': 0.0,
            'total_compute_time': 0.0,
            'total_system_delay': 0.0,
            'total_queue_wait_time': 0.0,
            'average_utilization': self.resource_manager.get_average_utilization(),
            'utilization_history': self.resource_manager.get_utilization_history()
        }

        for wf_id, execution in self.executions.items():
            wf_result = {
                'workflow_id': wf_id,
                'submit_time': execution.submit_time,
                'completion_time': execution.end_time,
                'turnaround_time': execution.turnaround_time,
                'compute_time': execution.total_compute_time,
                'system_delay': execution.total_system_delay,
                'queue_wait_time': execution.wait_time,
                'num_iterations': execution.workflow.num_iterations,
                'allocated_nodes': execution.allocated_nodes,
                'completed': execution.completed
            }
            results['workflows'][wf_id] = wf_result

            # Update totals
            if execution.completed:
                results['total_compute_time'] += execution.total_compute_time
                results['total_system_delay'] += execution.total_system_delay
                results['total_queue_wait_time'] += execution.wait_time

        # Calculate makespan
        if results['workflows']:
            end_times = [wf['completion_time'] for wf in results['workflows'].values() if wf['completed']]
            start_times = [wf['submit_time'] for wf in results['workflows'].values()]
            if end_times and start_times:
                results['total_makespan'] = max(end_times) - min(start_times)

        return results


def create_auto_scheduler_from_config(config: dict) -> AutoFCFSScheduler:
    """
    Create an AutoFCFSScheduler from experiment configuration.

    Args:
        config: Experiment configuration dictionary

    Returns:
        Configured AutoFCFSScheduler instance
    """
    try:
        from .resource_manager import create_resource_manager_from_config
    except ImportError:
        from schedulers.resource_manager import create_resource_manager_from_config

    resource_manager = create_resource_manager_from_config(config)

    auto_config = config.get('auto_delay', {})
    system_delay = auto_config.get('system_delay_seconds', 5.0)

    return AutoFCFSScheduler(
        resource_manager=resource_manager,
        system_delay=system_delay
    )


if __name__ == "__main__":
    # Test automated scheduler with fixed allocation
    try:
        from models.workflow import WorkflowGenerator
        from schedulers.resource_manager import ResourceManagerConfig
    except ImportError:
        from ..models.workflow import WorkflowGenerator
        from .resource_manager import ResourceManagerConfig

    print("Automated FCFS Scheduler Test (Fixed Allocation)")
    print("=" * 60)

    # Create resource manager
    rm_config = ResourceManagerConfig(total_nodes=148, cores_per_node=48)
    rm = OnPremResourceManager(rm_config)

    # Create scheduler
    scheduler = AutoFCFSScheduler(rm, system_delay=5.0)

    # Generate test workflows
    generator = WorkflowGenerator(seed=42, num_workflows=3, iteration_range=(2, 4))
    workflows = generator.generate_workflows()

    print(f"\nGenerated {len(workflows)} workflows:")
    for wf in workflows:
        print(f"  {wf.id}: {wf.num_iterations} iterations, mesh {wf.mesh}, max_nodes={wf.get_max_nodes_needed()}")
        for i, it in enumerate(wf.iterations):
            print(f"    Iter {i}: {it.chains} chains x {it.nodes_per_chain} nodes = {it.get_total_nodes()} nodes")

    # Run simulation
    print("\nRunning simulation...")
    results = scheduler.run_simulation(workflows)

    print(f"\nResults:")
    print(f"  Total makespan: {results['total_makespan']/3600:.2f} hours")
    print(f"  Total compute time: {results['total_compute_time']/3600:.2f} hours")
    print(f"  Total system delay: {results['total_system_delay']:.0f} seconds")
    print(f"  Total queue wait: {results['total_queue_wait_time']/3600:.2f} hours")
    print(f"  Average utilization: {results['average_utilization']:.2%}")

    print("\nPer-workflow results:")
    for wf_id, wf_result in results['workflows'].items():
        print(f"  {wf_id}:")
        print(f"    Allocated nodes: {wf_result['allocated_nodes']}")
        print(f"    Turnaround: {wf_result['turnaround_time']/3600:.2f}h")
        print(f"    Compute: {wf_result['compute_time']/3600:.2f}h")
        print(f"    System delay: {wf_result['system_delay']:.0f}s")

    # Test wave calculation
    print("\n" + "=" * 60)
    print("Wave Calculation Examples:")
    print("=" * 60)

    # Example 1: 4 nodes, 2 chains -> 2 nodes/chain
    runtime = calculate_wave_runtime(4, 2, 750, 5)
    print(f"  4 nodes, 2 chains, mesh 750, 5 tinyda: {runtime:.1f}s ({runtime/60:.1f}m)")

    # Example 2: 4 nodes, 6 chains -> 2 waves
    runtime = calculate_wave_runtime(4, 6, 750, 5)
    print(f"  4 nodes, 6 chains, mesh 750, 5 tinyda: {runtime:.1f}s ({runtime/60:.1f}m) [2 waves]")

    # Example 3: 4 nodes, 7 chains -> 2 waves (4+3)
    runtime = calculate_wave_runtime(4, 7, 750, 5)
    print(f"  4 nodes, 7 chains, mesh 750, 5 tinyda: {runtime:.1f}s ({runtime/60:.1f}m) [2 waves]")

    # Example 4: 5 nodes, 3 chains -> 1 node/chain (2 nodes idle)
    runtime = calculate_wave_runtime(5, 3, 750, 5)
    print(f"  5 nodes, 3 chains, mesh 750, 5 tinyda: {runtime:.1f}s ({runtime/60:.1f}m) [1 node/chain]")
