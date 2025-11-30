# Manual vs Automated Workflow Comparison Experiment

This experiment compares **manual intervention workflows** with **automated feedback-driven workflows** for SeisSol-style iterative scientific computing.

## Overview

### Problem Statement

In traditional HPC workflows, engineers must manually intervene between iterations:
1. Submit first iteration as a job
2. Wait for completion
3. **Manual delay** (hours): Analyze results, decide on next parameters
4. Submit next iteration as a new job
5. Repeat until convergence

This introduces significant delays (hours to days) between iterations.

### Solution: Automated Feedback-driven Workflows

Automated workflows trigger iterations automatically:
1. Submit entire workflow once
2. **Resources allocated once** for entire workflow duration
3. System executes iteration
4. **System delay** (~5 seconds): Process feedback, trigger next iteration
5. Repeat automatically until completion
6. Resources released only after workflow completes

## Key Differences Between Modes

| Aspect | Manual Mode | Automated Mode |
|--------|-------------|----------------|
| Resource allocation | Per-iteration (re-queue each time) | Once for entire workflow |
| Delay between iterations | Human delay (hours, lognormal) | System delay (5 seconds) |
| Queue wait | Each iteration waits independently | Only initial wait |
| Resource sizing | Exact fit per iteration | Max needed across all iterations |
| Execution model | chains × nodes_per_chain per iteration | Wave-based (adapts to fixed allocation) |

### Wave-Based Execution in Automated Mode

When resources are allocated once, iterations adapt to the fixed allocation:

| Scenario | Behavior |
|----------|----------|
| 4 nodes, 2 chains | 2 nodes/chain (faster execution) |
| 4 nodes, 6 chains | 2 waves: 4 chains, then 2 chains (1 node/chain) |
| 4 nodes, 7 chains | 2 waves: 4 chains, then 3 chains |
| 5 nodes, 3 chains | 1 node/chain (2 nodes idle) |

## Expected Results

With a 3-hour median human delay and 2-6 iterations per workflow:

| Scenario | Manual Makespan | Automated Makespan | Improvement |
|----------|-----------------|-------------------|-------------|
| 2 iterations | ~10h | ~4h | ~60% |
| 4 iterations | ~20h | ~8h | ~60% |
| 6 iterations | ~30h | ~12h | ~60% |

## Quick Start

```bash
# Navigate to experiment directory
cd experiments/manual_vs_auto

# Run with default configuration (50 workflows)
python scripts/run_experiment.py

# Quick test with few workflows (no work-hour constraints)
python scripts/run_experiment.py --num-workflows 5 --quick

# Run 30 replications for statistical significance
python scripts/run_experiment.py --replications 30

# Use custom configuration
python scripts/run_experiment.py --config config/experiment_config.yaml
```

## File Structure

```
experiments/manual_vs_auto/
├── config/
│   └── experiment_config.yaml    # Main configuration
├── models/
│   ├── runtime.py                # SeisSol runtime integration
│   ├── workflow.py               # Workflow data structures
│   └── human_delay.py            # Lognormal delay model
├── schedulers/
│   ├── resource_manager.py       # 148-node cluster manager
│   ├── manual_fcfs.py            # Manual intervention scheduler
│   └── auto_fcfs.py              # Automated feedback scheduler (fixed allocation)
├── simulation/
│   └── simulate_comparison.py    # Main simulation orchestrator
├── metrics/
│   └── comparison_metrics.py     # Metrics and statistics
├── output/
│   ├── csv_exporter.py           # CSV export
│   └── plot_generator.py         # Visualization
├── scripts/
│   └── run_experiment.py         # CLI entry point
├── README.md                     # This file
└── THESIS_MOTIVATION.md          # Thesis framing and suggested text
```

## Configuration

Edit `config/experiment_config.yaml` to customize:

### Resource Configuration
```yaml
resources:
  total_nodes: 148        # On-prem cluster nodes
  cores_per_node: 48
```

### Workflow Parameters
```yaml
workflows:
  num_workflows: 50
  iteration_range: [2, 6]
  mesh_sizes: [500, 750, 1000]  # SeisSol mesh sizes
  chains_range: [2, 8]          # MCMC chains per iteration
  nodes_per_chain_range: [1, 4] # Nodes allocated per chain
```

### Human Delay Model
```yaml
human_delay:
  distribution: lognormal
  median_hours: 3.0       # Typical response time
  sigma: 0.9              # Variability
  work_hours:
    enabled: true         # Enable 9-5 work hour constraints
    start: 9
    end: 17
```

### Automated Mode Configuration
```yaml
auto_delay:
  system_delay_seconds: 5.0   # Delay between iterations
```

## Runtime Model

Uses SeisSol runtime model from `src/main/scripts/speedup.py`:

| Mesh Size | 1 Node Runtime | 2 Node Runtime | TinyDA Overhead |
|-----------|----------------|----------------|-----------------|
| 500 (slow) | 469s | 246s | 26s |
| 750 | 149s | 87s | 8s |
| 1000 (fast) | 109s | 64s | 5s |

For >2 nodes, uses exponential speedup model.

### Wave-Based Runtime Calculation

For automated mode with fixed allocation:

```
if chains <= allocated_nodes:
    nodes_per_chain = allocated_nodes // chains
    runtime = (seissol_runtime(nodes_per_chain) + tinyda_overhead) × tinyda_iterations

if chains > allocated_nodes:
    waves = ceil(chains / allocated_nodes)
    runtime = waves × (seissol_runtime(1 node) + tinyda_overhead) × tinyda_iterations
```

## Output

Results are saved to `output/<timestamp>/`:

- `per_workflow_results.csv` - Per-workflow metrics
- `aggregate_results.csv` - Summary comparison
- `utilization_timeseries.csv` - Resource utilization over time
- `metric_comparison.png` - Bar charts comparing modes
- `utilization_timeseries.png` - Resource utilization plots
- `turnaround_distribution.png` - Turnaround time distributions

## Metrics

### Makespan
Total wall-clock time from first workflow submission to last completion.

### Per-Workflow Turnaround
Time from workflow submission to completion:
- Mean, median, P95 turnaround times
- Breakdown: compute time + delay time + queue wait time

### Resource Utilization
- Average utilization over simulation
- Utilization time series for visualization

## Statistical Analysis

When running multiple replications (`--replications N`):
- Paired t-test for significance
- 95% confidence intervals
- Cohen's d effect size

## Dependencies

- Python 3.8+
- numpy
- scipy
- matplotlib
- PyYAML

## Validation

The simulation ensures fair comparison:
- Same workflows used in both modes
- Same resource availability (148 nodes)
- Same random seeds for workflow generation
- Same runtime calculation (wave-based) in both modes

Key difference isolated: **human delay vs system delay**

## Thesis Usage

See `THESIS_MOTIVATION.md` for:
- Framing this experiment in a thesis introduction
- Suggested text for motivation and contributions sections
- Visual diagrams for presentation
- Discussion of limitations and conservative estimates

## Note on Implementation

This is a **standalone discrete-event simulation** that models the behavior of two execution approaches. It does not integrate with:
- Steep workflow engine
- Redis queues
- Production HPC scheduler

The purpose is to quantify the theoretical benefit of eliminating human delays, providing motivation for building a production feedback-driven workflow system.
