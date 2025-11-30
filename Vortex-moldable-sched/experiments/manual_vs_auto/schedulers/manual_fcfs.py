"""
Manual Intervention FCFS Scheduler.

In manual mode, each workflow iteration is submitted as a SEPARATE job.
After an iteration completes, there's a human delay (hours) before
the engineer reviews results and submits the next iteration.

Key characteristics:
- Each iteration enters the queue independently
- Human delay between iterations (lognormal distribution)
- FCFS scheduling for each job
- No cross-iteration optimization
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from collections import deque
import heapq
import sys
from pathlib import Path

# Handle imports for both package and standalone execution
try:
    from ..models.workflow import SeisSolWorkflow, IterationConfig
    from ..models.human_delay import HumanDelayModel, HumanDelayConfig, WorkHoursConfig
    from .resource_manager import OnPremResourceManager, ResourceManagerConfig
    from .auto_fcfs import calculate_wave_runtime
except ImportError:
    # Add parent to path for standalone execution
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from models.workflow import SeisSolWorkflow, IterationConfig
    from models.human_delay import HumanDelayModel, HumanDelayConfig, WorkHoursConfig
    from schedulers.resource_manager import OnPremResourceManager, ResourceManagerConfig
    from schedulers.auto_fcfs import calculate_wave_runtime


@dataclass
class Job:
    """Represents a single job (one iteration of a workflow)."""
    job_id: str
    workflow_id: str
    iteration_idx: int
    submit_time: float
    nodes_needed: int
    runtime: float
    mesh: int

    # Timing tracking
    queue_enter_time: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def wait_time(self) -> float:
        """Time spent waiting in queue."""
        return self.start_time - self.queue_enter_time

    @property
    def turnaround_time(self) -> float:
        """Total time from submit to completion."""
        return self.end_time - self.submit_time


@dataclass
class WorkflowState:
    """Tracks state of a workflow across its iterations."""
    workflow: SeisSolWorkflow
    current_iteration: int = 0
    submit_time: float = 0.0
    first_job_start_time: float = 0.0
    last_job_end_time: float = 0.0
    total_compute_time: float = 0.0
    total_queue_wait_time: float = 0.0
    total_human_delay: float = 0.0
    iteration_end_times: List[float] = field(default_factory=list)
    completed: bool = False


@dataclass
class Event:
    """Simulation event."""
    time: float
    event_type: str  # 'job_submit', 'job_complete', 'workflow_submit'
    data: dict

    def __lt__(self, other):
        return self.time < other.time


class ManualFCFSScheduler:
    """
    Manual intervention FCFS scheduler.

    Simulates the manual workflow pattern where engineers must
    intervene between iterations to analyze results and submit
    the next iteration as a new job.
    """

    def __init__(
        self,
        resource_manager: OnPremResourceManager,
        delay_model: HumanDelayModel,
        system_delay: float = 5.0
    ):
        """
        Initialize the manual scheduler.

        Args:
            resource_manager: Resource manager for the cluster
            delay_model: Human delay model for inter-iteration delays
            system_delay: Minimum system delay in seconds (default 5s)
        """
        self.resource_manager = resource_manager
        self.delay_model = delay_model
        self.system_delay = system_delay

        # Job queue (FCFS order)
        self.job_queue: deque[Job] = deque()

        # Event queue (priority queue by time)
        self.event_queue: List[Event] = []

        # Workflow state tracking
        self.workflow_states: Dict[str, WorkflowState] = {}

        # Running jobs
        self.running_jobs: Dict[str, Job] = {}

        # Completed jobs (for metrics)
        self.completed_jobs: List[Job] = []

        # Current simulation time
        self.current_time: float = 0.0

    def submit_workflow(self, workflow: SeisSolWorkflow):
        """
        Submit a workflow to the scheduler.

        This creates the initial job submission for iteration 0.

        Args:
            workflow: The workflow to submit
        """
        # Track workflow state
        self.workflow_states[workflow.id] = WorkflowState(
            workflow=workflow,
            submit_time=workflow.submit_time
        )

        # Submit first iteration as a job
        self._submit_iteration_job(workflow.id, 0, workflow.submit_time)

    def _submit_iteration_job(self, workflow_id: str, iteration_idx: int, submit_time: float):
        """
        Submit a single iteration as a job.

        In manual mode, each iteration requests its own node allocation.
        The runtime is calculated based on wave-based execution to match
        the automated mode's calculation for fair comparison.

        Args:
            workflow_id: Parent workflow ID
            iteration_idx: Iteration index
            submit_time: Time when job is submitted
        """
        state = self.workflow_states[workflow_id]
        workflow = state.workflow

        if iteration_idx >= workflow.num_iterations:
            return  # No more iterations

        iter_config = workflow.iterations[iteration_idx]
        job_id = f"{workflow_id}-iter{iteration_idx}"

        # In manual mode, each iteration requests exactly what it needs
        nodes_needed = iter_config.get_total_nodes()

        # Calculate runtime using wave-based execution
        # For manual mode: nodes_allocated = nodes_needed (exact fit)
        runtime = calculate_wave_runtime(
            allocated_nodes=nodes_needed,
            chains=iter_config.chains,
            mesh=workflow.mesh,
            tinyda_iterations=iter_config.tinyda_iterations
        )

        job = Job(
            job_id=job_id,
            workflow_id=workflow_id,
            iteration_idx=iteration_idx,
            submit_time=submit_time,
            nodes_needed=nodes_needed,
            runtime=runtime,
            mesh=workflow.mesh,
            queue_enter_time=submit_time
        )

        # Schedule job submit event
        self._schedule_event(Event(
            time=submit_time,
            event_type='job_submit',
            data={'job': job}
        ))

    def _schedule_event(self, event: Event):
        """Add an event to the event queue."""
        heapq.heappush(self.event_queue, event)

    def _try_start_job(self, job: Job) -> bool:
        """
        Try to start a job if resources are available.

        Args:
            job: Job to start

        Returns:
            True if job started, False if waiting
        """
        if self.resource_manager.allocate(
            job.job_id,
            job.nodes_needed,
            self.current_time,
            job.workflow_id,
            job.iteration_idx
        ):
            # Job started
            job.start_time = self.current_time
            self.running_jobs[job.job_id] = job

            # Update workflow state
            state = self.workflow_states[job.workflow_id]
            if job.iteration_idx == 0:
                state.first_job_start_time = self.current_time

            # Schedule completion event
            completion_time = self.current_time + job.runtime
            self._schedule_event(Event(
                time=completion_time,
                event_type='job_complete',
                data={'job_id': job.job_id}
            ))

            return True
        return False

    def _process_job_complete(self, job_id: str):
        """
        Process job completion.

        Releases resources and schedules next iteration (with human delay).
        """
        if job_id not in self.running_jobs:
            return

        job = self.running_jobs.pop(job_id)
        job.end_time = self.current_time

        # Release resources
        self.resource_manager.release(job_id, self.current_time)

        # Update workflow state
        state = self.workflow_states[job.workflow_id]
        state.current_iteration = job.iteration_idx + 1
        state.last_job_end_time = self.current_time
        state.total_compute_time += job.runtime
        state.total_queue_wait_time += job.wait_time
        state.iteration_end_times.append(self.current_time)

        # Add to completed jobs
        self.completed_jobs.append(job)

        # Check if more iterations
        workflow = state.workflow
        if state.current_iteration < workflow.num_iterations:
            # Schedule next iteration with human delay
            human_delay = self.delay_model.sample_delay(self.current_time)
            state.total_human_delay += human_delay

            next_submit_time = self.current_time + human_delay
            self._submit_iteration_job(
                job.workflow_id,
                state.current_iteration,
                next_submit_time
            )
        else:
            # Workflow completed
            state.completed = True

        # Try to start waiting jobs
        self._try_start_waiting_jobs()

    def _try_start_waiting_jobs(self):
        """Try to start jobs from the waiting queue."""
        started = True
        while started and self.job_queue:
            started = False
            # Check each job in FCFS order
            for i, job in enumerate(self.job_queue):
                if self._try_start_job(job):
                    self.job_queue.remove(job)
                    started = True
                    break

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
        self.job_queue.clear()
        self.event_queue.clear()
        self.workflow_states.clear()
        self.running_jobs.clear()
        self.completed_jobs.clear()
        self.current_time = 0.0

        # Submit all workflows
        for workflow in workflows:
            self.submit_workflow(workflow)

        # Process events
        while self.event_queue:
            event = heapq.heappop(self.event_queue)
            self.current_time = event.time

            if event.event_type == 'job_submit':
                job = event.data['job']
                # Try to start job, or add to queue
                if not self._try_start_job(job):
                    self.job_queue.append(job)

            elif event.event_type == 'job_complete':
                self._process_job_complete(event.data['job_id'])

        return self._collect_results()

    def _collect_results(self) -> dict:
        """Collect and return simulation results."""
        results = {
            'mode': 'manual',
            'workflows': {},
            'total_makespan': 0.0,
            'total_compute_time': 0.0,
            'total_human_delay': 0.0,
            'total_queue_wait_time': 0.0,
            'average_utilization': self.resource_manager.get_average_utilization(),
            'utilization_history': self.resource_manager.get_utilization_history()
        }

        for wf_id, state in self.workflow_states.items():
            wf_result = {
                'workflow_id': wf_id,
                'submit_time': state.submit_time,
                'completion_time': state.last_job_end_time,
                'turnaround_time': state.last_job_end_time - state.submit_time,
                'compute_time': state.total_compute_time,
                'human_delay': state.total_human_delay,
                'queue_wait_time': state.total_queue_wait_time,
                'num_iterations': state.workflow.num_iterations,
                'completed': state.completed
            }
            results['workflows'][wf_id] = wf_result

            # Update totals
            if state.completed:
                results['total_compute_time'] += state.total_compute_time
                results['total_human_delay'] += state.total_human_delay
                results['total_queue_wait_time'] += state.total_queue_wait_time

        # Calculate makespan
        if results['workflows']:
            end_times = [wf['completion_time'] for wf in results['workflows'].values() if wf['completed']]
            start_times = [wf['submit_time'] for wf in results['workflows'].values()]
            if end_times and start_times:
                results['total_makespan'] = max(end_times) - min(start_times)

        return results


def create_manual_scheduler_from_config(config: dict, seed: int = 42) -> ManualFCFSScheduler:
    """
    Create a ManualFCFSScheduler from experiment configuration.

    Args:
        config: Experiment configuration dictionary
        seed: Random seed

    Returns:
        Configured ManualFCFSScheduler instance
    """
    try:
        from .resource_manager import create_resource_manager_from_config
        from ..models.human_delay import create_delay_model_from_config
    except ImportError:
        from schedulers.resource_manager import create_resource_manager_from_config
        from models.human_delay import create_delay_model_from_config

    resource_manager = create_resource_manager_from_config(config)
    delay_model = create_delay_model_from_config(config, seed)

    auto_config = config.get('auto_delay', {})
    system_delay = auto_config.get('system_delay_seconds', 5.0)

    return ManualFCFSScheduler(
        resource_manager=resource_manager,
        delay_model=delay_model,
        system_delay=system_delay
    )


if __name__ == "__main__":
    # Test manual scheduler
    from ..models.workflow import WorkflowGenerator
    from .resource_manager import ResourceManagerConfig
    from ..models.human_delay import HumanDelayConfig, WorkHoursConfig

    print("Manual FCFS Scheduler Test")
    print("=" * 60)

    # Create resource manager
    rm_config = ResourceManagerConfig(total_nodes=148, cores_per_node=48)
    rm = OnPremResourceManager(rm_config)

    # Create delay model
    delay_config = HumanDelayConfig(
        median_hours=3.0,
        sigma=0.9,
        work_hours=WorkHoursConfig(enabled=False)  # Disable for testing
    )
    delay_model = HumanDelayModel(delay_config, seed=42)

    # Create scheduler
    scheduler = ManualFCFSScheduler(rm, delay_model)

    # Generate test workflows
    generator = WorkflowGenerator(seed=42, num_workflows=3, iteration_range=(2, 4))
    workflows = generator.generate_workflows()

    print(f"\nGenerated {len(workflows)} workflows:")
    for wf in workflows:
        print(f"  {wf.id}: {wf.num_iterations} iterations, mesh {wf.mesh}")

    # Run simulation
    print("\nRunning simulation...")
    results = scheduler.run_simulation(workflows)

    print(f"\nResults:")
    print(f"  Total makespan: {results['total_makespan']/3600:.2f} hours")
    print(f"  Total compute time: {results['total_compute_time']/3600:.2f} hours")
    print(f"  Total human delay: {results['total_human_delay']/3600:.2f} hours")
    print(f"  Total queue wait: {results['total_queue_wait_time']/3600:.2f} hours")
    print(f"  Average utilization: {results['average_utilization']:.2%}")

    print("\nPer-workflow results:")
    for wf_id, wf_result in results['workflows'].items():
        print(f"  {wf_id}:")
        print(f"    Turnaround: {wf_result['turnaround_time']/3600:.2f}h")
        print(f"    Compute: {wf_result['compute_time']/3600:.2f}h")
        print(f"    Human delay: {wf_result['human_delay']/3600:.2f}h")
