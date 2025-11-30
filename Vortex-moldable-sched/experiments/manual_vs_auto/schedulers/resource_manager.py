"""
Resource Manager for the on-prem HPC cluster.

Manages allocation and release of the 148-node homogeneous cluster.
Implements simple FCFS resource allocation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque


@dataclass
class AllocationRecord:
    """Record of a resource allocation."""
    job_id: str
    nodes_allocated: int
    allocation_time: float
    workflow_id: str
    iteration_idx: int


@dataclass
class ResourceManagerConfig:
    """Configuration for the resource manager."""
    total_nodes: int = 148
    cores_per_node: int = 48


class OnPremResourceManager:
    """
    Manages the on-prem HPC cluster resources.

    Simple FCFS allocation: if enough nodes are available, allocate immediately.
    If not, job waits in queue until resources are freed.
    """

    def __init__(self, config: ResourceManagerConfig):
        """
        Initialize the resource manager.

        Args:
            config: Resource configuration
        """
        self.total_nodes = config.total_nodes
        self.cores_per_node = config.cores_per_node
        self.available_nodes = config.total_nodes

        # Track allocations by job_id
        self.allocations: Dict[str, AllocationRecord] = {}

        # Queue of waiting jobs: (job_id, nodes_needed, request_time, callback)
        self.waiting_queue: deque = deque()

        # Utilization tracking
        self.utilization_history: List[Tuple[float, int, int]] = []  # (time, used, total)

    def can_allocate(self, nodes_needed: int) -> bool:
        """Check if allocation is possible."""
        return nodes_needed <= self.available_nodes

    def allocate(
        self,
        job_id: str,
        nodes_needed: int,
        current_time: float,
        workflow_id: str = "",
        iteration_idx: int = 0
    ) -> bool:
        """
        Attempt to allocate nodes to a job.

        Args:
            job_id: Unique identifier for the job/iteration
            nodes_needed: Number of nodes required
            current_time: Current simulation time
            workflow_id: Parent workflow ID
            iteration_idx: Iteration index within workflow

        Returns:
            True if allocation successful, False if must wait
        """
        if nodes_needed > self.total_nodes:
            raise ValueError(
                f"Job {job_id} requests {nodes_needed} nodes, "
                f"but cluster only has {self.total_nodes} nodes"
            )

        if self.can_allocate(nodes_needed):
            self.available_nodes -= nodes_needed
            self.allocations[job_id] = AllocationRecord(
                job_id=job_id,
                nodes_allocated=nodes_needed,
                allocation_time=current_time,
                workflow_id=workflow_id,
                iteration_idx=iteration_idx
            )
            self._record_utilization(current_time)
            return True
        return False

    def release(self, job_id: str, current_time: float) -> int:
        """
        Release nodes allocated to a job.

        Args:
            job_id: Job identifier to release
            current_time: Current simulation time

        Returns:
            Number of nodes freed, 0 if job not found
        """
        if job_id not in self.allocations:
            return 0

        record = self.allocations.pop(job_id)
        self.available_nodes += record.nodes_allocated
        self._record_utilization(current_time)
        return record.nodes_allocated

    def get_allocated_nodes(self, job_id: str) -> int:
        """Get number of nodes allocated to a job."""
        if job_id in self.allocations:
            return self.allocations[job_id].nodes_allocated
        return 0

    def get_used_nodes(self) -> int:
        """Get total number of nodes currently in use."""
        return self.total_nodes - self.available_nodes

    def get_utilization(self) -> float:
        """Get current utilization as a fraction."""
        return self.get_used_nodes() / self.total_nodes

    def _record_utilization(self, current_time: float):
        """Record utilization at current time."""
        self.utilization_history.append(
            (current_time, self.get_used_nodes(), self.total_nodes)
        )

    def get_utilization_history(self) -> List[Tuple[float, int, int]]:
        """Get utilization history for analysis."""
        return self.utilization_history.copy()

    def get_average_utilization(self) -> float:
        """
        Calculate average utilization over the simulation.

        Uses time-weighted average.
        """
        if len(self.utilization_history) < 2:
            return 0.0

        total_time = 0.0
        weighted_util = 0.0

        for i in range(len(self.utilization_history) - 1):
            t1, used1, total1 = self.utilization_history[i]
            t2, _, _ = self.utilization_history[i + 1]

            duration = t2 - t1
            utilization = used1 / total1

            weighted_util += utilization * duration
            total_time += duration

        if total_time > 0:
            return weighted_util / total_time
        return 0.0

    def get_status(self) -> dict:
        """Get current resource status."""
        return {
            'total_nodes': self.total_nodes,
            'available_nodes': self.available_nodes,
            'used_nodes': self.get_used_nodes(),
            'utilization': self.get_utilization(),
            'active_jobs': len(self.allocations),
            'active_job_ids': list(self.allocations.keys())
        }

    def reset(self):
        """Reset the resource manager to initial state."""
        self.available_nodes = self.total_nodes
        self.allocations.clear()
        self.waiting_queue.clear()
        self.utilization_history.clear()


def create_resource_manager_from_config(config: dict) -> OnPremResourceManager:
    """
    Create a ResourceManager from experiment configuration.

    Args:
        config: Experiment configuration dictionary

    Returns:
        Configured OnPremResourceManager instance
    """
    res_config = config.get('resources', {})
    rm_config = ResourceManagerConfig(
        total_nodes=res_config.get('total_nodes', 148),
        cores_per_node=res_config.get('cores_per_node', 48)
    )
    return OnPremResourceManager(rm_config)


if __name__ == "__main__":
    # Test resource manager
    print("Resource Manager Test")
    print("=" * 60)

    config = ResourceManagerConfig(total_nodes=148, cores_per_node=48)
    rm = OnPremResourceManager(config)

    print(f"\nInitial status: {rm.get_status()}")

    # Simulate some allocations
    print("\nAllocating jobs...")

    # Job 1: 20 nodes
    success = rm.allocate("job-001", 20, 0.0, "wf-001", 0)
    print(f"  job-001 (20 nodes): {'SUCCESS' if success else 'WAITING'}")
    print(f"    Available: {rm.available_nodes}")

    # Job 2: 50 nodes
    success = rm.allocate("job-002", 50, 10.0, "wf-002", 0)
    print(f"  job-002 (50 nodes): {'SUCCESS' if success else 'WAITING'}")
    print(f"    Available: {rm.available_nodes}")

    # Job 3: 100 nodes (should fail - only 78 available)
    success = rm.allocate("job-003", 100, 20.0, "wf-003", 0)
    print(f"  job-003 (100 nodes): {'SUCCESS' if success else 'WAITING'}")
    print(f"    Available: {rm.available_nodes}")

    # Release job 1
    print("\nReleasing job-001...")
    freed = rm.release("job-001", 100.0)
    print(f"  Freed {freed} nodes")
    print(f"  Available: {rm.available_nodes}")

    # Now job 3 should succeed
    success = rm.allocate("job-003", 100, 100.0, "wf-003", 0)
    print(f"\nRetrying job-003 (100 nodes): {'SUCCESS' if success else 'WAITING'}")
    print(f"  Available: {rm.available_nodes}")

    print(f"\nFinal status: {rm.get_status()}")
    print(f"Average utilization: {rm.get_average_utilization():.2%}")
