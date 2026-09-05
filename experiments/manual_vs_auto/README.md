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

## Three Scheduling Modes

The experiment compares three scheduling modes:

### 1. Manual (Strict)
- Each iteration requests **exact** resources needed
- Job waits in queue until exact node count is available
- Human delay (hours) between iterations
- Represents traditional HPC batch scheduling

### 2. Manual (Flexible)
- Each iteration can **scale up or down** based on availability
- Uses queue-aware smart allocation strategy
- Human delay (hours) between iterations
- Represents moldable job scheduling with human intervention

### 3. Automated
- Resources allocated **once** for entire workflow
- All iterations run back-to-back with minimal system delay (~5s)
- Wave-based execution adapts to fixed allocation
- Represents feedback-driven automated workflows

## Key Differences Between Modes

| Aspect | Manual (Strict) | Manual (Flexible) | Automated |
|--------|-----------------|-------------------|-----------|
| Resource allocation | Exact per-iteration | Flexible per-iteration | Once for entire workflow |
| Delay between iterations | Human (hours) | Human (hours) | System (5 seconds) |
| Queue wait | Each iteration waits | Each iteration waits | Only initial wait |
| Resource sizing | Exact fit | Adapts to available | Max needed across iterations |
| Execution model | Fixed allocation | Wave-based (flexible) | Wave-based (fixed) |

## Queue-Aware Flexible Allocation Strategy

The Manual (Flexible) mode uses a smart allocation strategy that balances individual job speed with overall cluster throughput:

### Strategy

```
IF queue has waiting jobs:
    allocation = min(available, requested)
    # Take only what needed, leave resources for others

IF queue is empty AND extra resources available:
    allocation = min(available - reserve, requested × max_scale_factor)
    # Scale up to use idle resources, reserve some for new arrivals
```

### Example Scenarios

| Available | Requested | Queue | Allocation | Reason |
|-----------|-----------|-------|------------|--------|
| 100 | 10 | 5 jobs waiting | 10 | Queue not empty, don't hog resources |
| 100 | 10 | empty | 19 | Scale up (2×10=20, minus 1 reserve) |
| 5 | 10 | any | 5 | Scale down, run in waves |
| 3 | 10 | any | 3 | Scale down significantly |
| 0 | 10 | any | 0 | No resources, must wait |

### Configuration

```yaml
manual_flexible:
  min_nodes_per_job: 1      # Minimum nodes a job can run with
  max_scale_factor: 2.0     # Maximum scale-up (2.0 = up to 2× requested)
```

### Benefits

1. **No idle resources**: Jobs can run with fewer nodes using wave-based execution
2. **Efficient packing**: Multiple small jobs can run in parallel
3. **Opportunistic scaling**: Uses extra resources when no one else needs them
4. **Fair sharing**: Doesn't starve other jobs when queue is busy

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
│   ├── manual_fcfs.py            # Manual (Strict) scheduler
│   ├── manual_fcfs_flexible.py   # Manual (Flexible) scheduler with queue-aware allocation
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
    enabled: false        # Work hour constraints (can cause synchronized idle periods)
    start: 9
    end: 17
```

## Per-Engineer Variation

To create realistic human delay patterns, the simulation models each workflow as being managed by a different engineer with unique characteristics:

### Per-Engineer Profiles

Each engineer (workflow) has randomly assigned:

1. **Speed Multiplier** (lognormal, median=1.0, σ=0.3)
   - Range: 0.5× to 2.0×
   - Some engineers respond faster, others slower
   - Applied to base delay samples

2. **Work Schedule Jitter** (when work hours enabled)
   - Start time: ±1.5 hours around configured start (e.g., 7:30 AM - 10:30 AM)
   - End time: ±2.0 hours around configured end (e.g., 3:00 PM - 7:00 PM)
   - Different engineers have different schedules

3. **Time-of-Day Efficiency**
   - Morning (9-12): 0.9× (peak efficiency)
   - Post-lunch (12-14): 1.3× (slower - post-lunch slump)
   - Afternoon (14-17): 1.1× (slightly slower)
   - Evening (17-20): 1.4× (tired)
   - Outside hours: 1.5× (slowest)

### Why Per-Engineer Variation?

Without variation, all engineers would have synchronized delays, causing:
- Periodic bursts of activity followed by idle periods
- Unrealistic "wave" patterns in utilization graphs
- Artificial clustering of job submissions

With per-engineer variation:
- Delays are spread throughout the simulation
- More realistic continuous cluster utilization
- Different workflows progress at different rates

### Example

```
Engineer for wf-0001: speed=0.85× (fast), works 8:00-18:00
Engineer for wf-0002: speed=1.20× (slow), works 9:30-16:30
Engineer for wf-0003: speed=1.00× (average), works 7:45-17:15
```

This creates natural variation in when iterations are submitted, leading to more realistic resource utilization patterns.

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
