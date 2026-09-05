"""
Runtime calculation module for SeisSol workflows.

Integrates with the existing speedup.py runtime models for on-prem HPC cluster.
Uses the hpcOnpremRuntime() function which models SuperMUC-like cluster performance.
"""

import numpy as np
import sys
from pathlib import Path

# Import runtime functions from existing speedup.py
try:
    from elastiflow.scripts.speedup import hpcOnpremRuntime, tinyDaOverhead
except ImportError:
    # Fallback: define locally if import fails
    tinyDaOverhead = {1000: 4.713, 750: 8.378, 500: 25.799}

    def hpcOnpremRuntime(nodes: int, mesh: int) -> float:
        """
        Runtime model for on-prem HPC cluster (SuperMUC-like).

        Args:
            nodes: Number of nodes per chain
            mesh: Mesh size (500, 750, or 1000)

        Returns:
            Runtime in seconds for one SeisSol trial
        """
        runtimes1 = {1000: 109, 750: 149, 500: 469.41}
        runtimes2 = {1000: 64.01, 750: 87.09, 500: 245.7}

        if nodes == 1:
            return runtimes1[mesh]
        elif nodes == 2:
            return runtimes2[mesh]
        else:
            # Exponential speedup model for >2 nodes
            a = 1.45170288e+04
            b = -2.52000045e+00
            c = -5.90571174e+00
            d = 5.67339488e+01
            return a * np.exp(b * nodes / 8.0 + c * mesh / 1000.0) + d


def get_seissol_runtime(nodes_per_chain: int, mesh: int) -> float:
    """
    Get SeisSol runtime for given node allocation and mesh size.

    Args:
        nodes_per_chain: Number of nodes allocated to each chain
        mesh: Mesh size (500, 750, or 1000)

    Returns:
        Runtime in seconds for one SeisSol trial (without TinyDA overhead)
    """
    if nodes_per_chain <= 0:
        return 0.0
    return hpcOnpremRuntime(nodes_per_chain, mesh)


def get_tinyda_overhead(mesh: int) -> float:
    """
    Get TinyDA overhead per iteration for given mesh size.

    Args:
        mesh: Mesh size (500, 750, or 1000)

    Returns:
        TinyDA overhead in seconds
    """
    return tinyDaOverhead.get(mesh, 10.0)  # Default 10s if mesh not found


def get_iteration_runtime(
    nodes_per_chain: int,
    mesh: int,
    tinyda_iterations: int
) -> float:
    """
    Calculate total runtime for one workflow iteration.

    All chains run in parallel, each on nodes_per_chain nodes.
    Runtime = (seissol_runtime + tinyda_overhead) * tinyda_iterations

    Args:
        nodes_per_chain: Number of nodes allocated to each chain
        mesh: Mesh size (500, 750, or 1000)
        tinyda_iterations: Number of TinyDA iterations in this workflow iteration

    Returns:
        Total runtime in seconds for the iteration
    """
    if nodes_per_chain <= 0 or tinyda_iterations <= 0:
        return 0.0

    # Base SeisSol runtime for given node count
    base_runtime = get_seissol_runtime(nodes_per_chain, mesh)

    # TinyDA overhead per iteration
    overhead = get_tinyda_overhead(mesh)

    # Total: (base + overhead) * tinyda_iterations
    return (base_runtime + overhead) * tinyda_iterations


def get_runtime_table(mesh: int, max_nodes: int = 8) -> dict:
    """
    Generate a runtime lookup table for different node counts.

    Useful for debugging and understanding the speedup curve.

    Args:
        mesh: Mesh size (500, 750, or 1000)
        max_nodes: Maximum number of nodes to calculate

    Returns:
        Dictionary mapping node count to runtime
    """
    table = {}
    for nodes in range(1, max_nodes + 1):
        table[nodes] = get_seissol_runtime(nodes, mesh)
    return table


# Runtime lookup tables for quick reference
RUNTIME_TABLES = {
    500: {1: 469.41, 2: 245.7},   # Slowest mesh
    750: {1: 149.0, 2: 87.09},
    1000: {1: 109.0, 2: 64.01},   # Fastest mesh
}


if __name__ == "__main__":
    # Test runtime calculations
    print("SeisSol Runtime Model Test")
    print("=" * 50)

    for mesh in [500, 750, 1000]:
        print(f"\nMesh {mesh}:")
        print(f"  TinyDA overhead: {get_tinyda_overhead(mesh):.2f}s")
        for nodes in [1, 2, 3, 4]:
            runtime = get_seissol_runtime(nodes, mesh)
            print(f"  {nodes} node(s): {runtime:.2f}s")

    print("\n\nExample iteration runtime:")
    # 4 chains, 2 nodes/chain, mesh 750, 5 TinyDA iterations
    runtime = get_iteration_runtime(
        nodes_per_chain=2,
        mesh=750,
        tinyda_iterations=5
    )
    print(f"  2 nodes/chain, mesh 750, 5 TinyDA iters: {runtime:.2f}s ({runtime/60:.2f}m)")
