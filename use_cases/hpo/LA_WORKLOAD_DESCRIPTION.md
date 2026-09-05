# License-Aware (LA) SeisSol Workload Description

## 1. Overview

The License-Aware (LA) SeisSol workload extends the plain SeisSol scheduling problem with a **dual-resource constraint**: workflows require both **compute instances** and **commercial software license tokens** to execute. This models real-world HPC environments where expensive CAE (Computer-Aided Engineering) solvers — ANSYS, ABAQUS, LS-DYNA — have limited concurrent-use licenses that must be co-allocated alongside compute resources.

The LA workload is evaluated in **simulation mode** (`SIMULATE = True`): runtimes are computed from fitted models rather than executing actual SeisSol code. This enables testing at scale (700 workflows) within tractable time.

## 2. Scientific Application: SeisSol

**SeisSol** is a high-performance seismic wave propagation simulator that solves earthquake dynamics on unstructured tetrahedral meshes. It is a representative HPC workload: mesh-based, MPI-parallel, and computationally expensive.

### 2.1 Mesh Sizes

Three mesh resolutions control the problem scale:

| Mesh | Single-Node Runtime (on-prem) | 2-Node Runtime | TinyDA Overhead | Distribution |
|------|-------------------------------|----------------|-----------------|--------------|
| 1000 | 109s | 64s | 4.7s | 25% |
| 750 | 149s | 87s | 8.4s | 25% |
| 500 | 469s | 246s | 25.8s | 50% |

Larger mesh numbers correspond to coarser (faster) simulations; mesh 500 is the finest and most expensive.

### 2.2 Runtime Model

Runtimes are computed from fitted exponential models, not from actual execution:

```
runtime(nodes, mesh, instance_type) = a × exp(b × nodes/k + c × mesh/1000) + d
```

Where `a`, `b`, `c`, `d` are instance-specific fitted coefficients. For on-prem:
- `a = 1.452 × 10⁴`, `b = -2.520`, `c = -5.906`, `d = 56.73`
- Scaling factor `k = 8` (node normalisation)

The model supports multiple instance types: on-prem, hpc7a.24xlarge, hpc7a.12xlarge, c7i.24xlarge, c7i.12xlarge, c6i.32xlarge, c6i.16xlarge.

Each simulation's total runtime includes a fixed **TinyDA overhead** per mesh size (4.7–25.8s), added to the fitted compute time.

## 3. TinyDA Data Assimilation Framework

**TinyDA** is a data assimilation framework that wraps SeisSol simulations into an iterative ensemble-based workflow:

```
Workflow (complete execution)
  └─ Workflow Iteration (2-6 iterations)
       └─ TinyDA Iteration (1-9 per workflow iteration)
            └─ Chain (2-6 parallel simulation runs)
                 └─ SeisSol Simulation (single mesh solve on allocated nodes)
```

### 3.1 Chains (Parallel Ensemble Members)

Each **chain** is an independent SeisSol simulation run. Within a TinyDA iteration, all chains execute in **parallel**:

- **Load balancing**: Chains are distributed across available nodes using a max-heap scheduler. Each chain receives at least one node; extra nodes are assigned to the slowest chain.
- **Completion time**: Determined by the slowest chain (parallel bottleneck).
- **Two execution modes**:
  - `hosts >= chains`: Parallel — each chain gets ≥1 node, extra nodes distributed greedily.
  - `hosts < chains`: Sequential — excess chains are queued on existing nodes, extending runtime.

### 3.2 TinyDA Iterations (Sequential Assimilation Steps)

Within a workflow iteration, TinyDA iterations are **sequential** — each step improves the state estimate by assimilating observations from the previous step. The runtime for one workflow iteration is:

```
iteration_runtime = slowest_chain_runtime × (tinydaIterations + 2)
```

The `+2` accounts for setup and finalisation overhead in TinyDA.

### 3.3 Workflow Iterations (Outer Optimisation Loop)

Each workflow has 2–6 sequential **workflow iterations**. These are the reallocation boundaries — the moldable scheduler can adjust resources between them.

### 3.4 Cohesion Parameter

Cohesion is a fixed SeisSol solver parameter (value: `2.13` in simulation output). It is passed through the workflow chain as a loop variable (`input_coh` → `output_coh`) but does **not** affect scheduling or resource allocation.

## 4. What Makes License-Aware Different from Plain SeisSol

| Aspect | Plain SeisSol | License-Aware (LA) |
|--------|---------------|-------------------|
| **Resource model** | Compute only | Compute + license tokens (dual-resource) |
| **Per-iteration config** | `workflowConfig` array (chains/tinyda vary) | Same `workflowConfig` array + license tracking |
| **License tracking** | None | Three pools: ANSYS, ABAQUS, LSDYNA |
| **Allocation check** | Compute availability only | Compute AND license tokens must both be available |
| **Cost model** | Hardware cost only | Hardware cost + license cost |
| **Scale-down guards** | Time/budget-based | Time/budget + license pool saturation |
| **Total workflows** | 400 | 700 |
| **Constraint constraints** | Single `constraints` block | Same, plus `license_pool` and `software_id` fields |

## 5. License Model

### 5.1 What "License" Means

These are **commercial software license tokens** — concurrent-use licences for CAE solvers. In enterprise HPC, a license pool has a fixed number of tokens; each running instance consumes tokens proportional to its core count. When the pool is exhausted, no additional instances can start that solver, regardless of compute availability.

### 5.2 Three License Pools

| Pool | Software ID | Token Formula | Pool Capacity | Peak Utilisation |
|------|-------------|--------------|---------------|-----------------|
| **ANSYS** | 1 | `T(n) = 1 + max(0, n - 4)` | 6,700 tokens | ~87% |
| **ABAQUS** | 2 | `T(n) = 5 × n^0.422` | 2,200 tokens | ~86% |
| **LSDYNA** | 3 | `T(n) = n` | 7,600 tokens | ~87% |

Where `n` = number of CPU cores allocated.

**Token calculation examples** (for 16-core allocation):
- ANSYS: `1 + max(0, 16-4) = 13` tokens
- ABAQUS: `5 × 16^0.422 ≈ 17.4` tokens
- LSDYNA: `16` tokens

Pool capacities are sized to create **meaningful scarcity** (~87% peak utilisation). Earlier experiments with 16,800 tokens per pool showed only 3–13% utilisation — licenses were never a constraint.

### 5.3 License Costs

Based on Henkel & Treiber 2015 and JSSPP 2025 Section 2.2:

| Software | Annual Cost | Per-Second Cost | Cost Model |
|----------|------------|-----------------|------------|
| **LSDYNA** | €1,000/token/year | 31.7 µ€/token/s | Linear: `cost = tokens × rate × duration` |
| **ABAQUS** | €2,500/token/year | 79.3 µ€/token/s | Power-law: `cost = tokens × rate × duration` |
| **ANSYS** | €14,000 MEBA + €1,700 workgroup/year | MEBA: 476 µ€/s + WG: 54 µ€/token/s | Fixed base + per-token: `cost = MEBA_rate × duration + WG_tokens × WG_rate × duration` |

The **total workflow cost** = hardware cost + license cost.

### 5.4 License Distribution

Workflows are randomly assigned to license pools:
- ANSYS: 33% of workflows
- ABAQUS: 33% of workflows
- LSDYNA: 34% of workflows

### 5.5 Two-Phase Commit for License Allocation

To prevent race conditions when multiple workflows compete for the same license pool:

1. **HOLD**: Reserve tokens with a 5-minute TTL
2. **Allocate compute**: If compute fails, release the hold
3. **COMMIT**: Make the token reservation permanent

## 6. Workflow Structure

### 6.1 Workflow YAML Format

```yaml
api: 4.7.0
id: lamf-test-ce4837ee

config:
  mesh: 1000                      # Mesh resolution
  software_id: 1                  # 1=ANSYS, 2=ABAQUS, 3=LSDYNA
  workflowIterations: 5
  workflowConfig:                 # Per-iteration configuration (varying)
    - chains: 5                   # Iteration 0
      tinydaIterations: 1
    - chains: 6                   # Iteration 1 (scale-up opportunity)
      tinydaIterations: 8
    - chains: 5                   # Iteration 2
      tinydaIterations: 5
    - chains: 5                   # Iteration 3
      tinydaIterations: 6
    - chains: 6                   # Iteration 4
      tinydaIterations: 4

constraints:
  budget: 64.81                   # Hardware budget in € (not including license cost)
  deadline: 14108.37              # Wall-clock deadline in seconds
  chains: 5                       # Baseline chains (iteration 0)
  tinydaIterations: 1             # Baseline TinyDA (iteration 0)
  license_pool: ANSYS             # Required license pool

vars:
- id: input_coh
  value: '3'                      # Cohesion parameter (fixed)

actions:
- type: for                       # Iterative execution
  input: input_coh
  enumerator: i
  yieldToInput: output_coh        # Feedback loop: output → next input
  actions:
  - type: execute
    service: scripts/simulate-tinyda-seissol.py
    inputs:
    - id: tinyda_input
      var: i
    outputs:
    - id: tinyda_output
      var: output_coh
```

### 6.2 Per-Iteration Variation (workflowConfig Array)

Unlike HPO workflows where iteration configs are dynamically determined by optimization output, LA workflows use a **pre-generated `workflowConfig` array** — each iteration's chains and tinydaIterations are fixed at workflow creation time:

```python
# Iteration 0: uses baseline values from constraints
workflowConfig[0] = {'chains': 5, 'tinydaIterations': 1}

# Iterations 1+: independently randomised
workflowConfig[1] = {'chains': random.randint(2, 6), 'tinydaIterations': random.randint(1, 9)}
workflowConfig[2] = {'chains': random.randint(2, 6), 'tinydaIterations': random.randint(1, 9)}
# ... etc
```

This variation creates natural moldability opportunities: an iteration requiring 6 chains followed by one needing 2 chains invites scale-down.

## 7. Constraint Formulation

### 7.1 Budget Formula

```
budget = single_run_cost × 4 (chains) × AVG_TINYDA_ITERATIONS × AVG_WORKFLOW_ITERATIONS
```

Where `single_run_cost` is the per-second cost of the slowest single-node runtime multiplied by on-demand instance rate:

| Mesh | Single-Run Cost (€·s) | Budget (€) | Normal Distribution |
|------|----------------------|------------|---------------------|
| 1000 | 0.5141 | 57.6 | N(μ=57.6, σ=10) |
| 750 | 0.8595 | 96.3 | N(μ=96.3, σ=10) |
| 500 | 2.7422 | 307.5 | N(μ=307.5, σ=15) |

Budget covers **hardware cost only**. License cost is tracked separately in metrics.

### 7.2 Deadline Formula

```
deadline = slowest_runtime × AVG_TINYDA_ITERATIONS × AVG_WORKFLOW_ITERATIONS × 2
```

| Mesh | Slowest Runtime (s) | Deadline (s) | Deadline (hours) | Normal Distribution |
|------|--------------------|-------------|-----------------|---------------------|
| 1000 | 253 | 14,168 | ~3.9h | N(μ=14168, σ=100) |
| 750 | 557 | 31,192 | ~8.7h | N(μ=31192, σ=100) |
| 500 | 2,281 | 127,751 | ~35.5h | N(μ=127751, σ=200) |

The 2× factor covers cold start, queuing delays, and contention — same methodology as Plain SeisSol.

### 7.3 Shared Constants

```python
AVG_TINYDA_ITERATIONS = 7    # Mean TinyDA iterations per workflow iteration
AVG_WORKFLOW_ITERATIONS = 4  # Mean workflow iterations
COLD_START_TIME = 400.5s     # On-demand instance cold start penalty
DEADLINE_BUFFER = 180s       # 3-minute safety margin before deadline
```

## 8. Workflow Generation

### 8.1 Scale

**700 workflows** generated with:
- Mesh distribution: 25% mesh-1000, 25% mesh-750, 50% mesh-500
- License distribution: 33% ANSYS, 33% ABAQUS, 34% LSDYNA
- Workflow iterations: random integer in [2, 6]
- Per-iteration chains: random integer in [2, 6]
- Per-iteration TinyDA iterations: random integer in [1, 9]
- Random seed: 0 (reproducible)

### 8.2 Temporal Arrival Pattern

Workflows are submitted over a compressed time window to create peak demand:

| Parameter | Value | Effect |
|-----------|-------|--------|
| `TEMPORAL_COMPRESSION_FACTOR` | 0.5 | 20-hour trace compressed to 10 hours (2× arrival rate) |
| `SUBMISSION_JITTER_MINUTES` | 2 | Workflows within a time slot arrive within 2-minute window (burst) |

This creates periods of high concurrent demand, stressing both compute and license pools.

## 9. Scheduler Variants

### 9.1 Four Schedulers

| Scheduler | Class | Ordering | Moldability | License-Aware |
|-----------|-------|----------|------------|---------------|
| **FCFS-LA Static** | `fcfs_scheduler_LA.py` | Arrival order | No | Yes |
| **LAMF** (FCFS Moldable) | `fcfs_optimized_LA.py` | Arrival order | Yes | Yes |
| **EDF-LA Static** | `edf_scheduler_LA.py` | Earliest deadline | No | Yes |
| **EDF-HSM** (Hybrid) | `edf_hsm_LA.py` | Earliest deadline | Hybrid (static iter 0, moldable iter 1+) | Yes |

### 9.2 Static vs Moldable Behaviour

**Static** (FCFS-LA, EDF-LA):
- Resources and licenses allocated once at workflow submission
- Held for entire workflow duration regardless of per-iteration needs
- No scale-up or scale-down between iterations
- Allocation based on worst-case: `max(chains across iterations)` × `max(tinyda)`

**Moldable** (LAMF, EDF-HSM):
- Initial allocation at iteration 0 with conservative budget/deadline factors
- Between iterations, executor requests reallocation based on next iteration's `workflowConfig[i]`
- Scale-up: acquire additional instances + license tokens if available
- Scale-down: release excess instances + return license tokens to pool

### 9.3 Iteration-Weighted Budget/Deadline Allocation

The moldable scheduler allocates increasing fractions of the remaining budget and deadline as iterations progress:

```python
OPTIM_FCFS_BFACTOR = {0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95, 5: 1.0}
OPTIM_FCFS_DFACTOR = {0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95, 5: 1.0}
```

- **Iteration 0**: Use 60% of budget and time (conservative, protect remaining iterations)
- **Iteration 5**: Use 100% (final iteration, no need to reserve)

### 9.4 EDF-HSM Urgency Thresholds

The hybrid EDF scheduler uses time-based urgency to trigger interventions:

| Condition | Threshold | Action |
|-----------|----------|--------|
| **Critical** | `time_remaining < 40% × time_elapsed` | Force scale-up (urgency boost 2.5×) |
| **Warning** | `time_remaining < 80% × time_elapsed` | Attempt scale-up (urgency boost 1.8×) |
| **Safe** | `time_remaining > 200% × time_elapsed` | Allow scale-down |
| **Excess** | `time_remaining > 350% × time_elapsed` | Aggressive scale-down + relax guards |

### 9.5 Scale-Down Guards (License-Specific)

Moldable scale-down is blocked when:
- **License pool saturated**: Other workflows are waiting for tokens from this pool — releasing would help them, but the currently held allocation might be needed for subsequent iterations
- **Late iteration** (iteration > 3): Too close to completion, risky to change allocation
- **Time progress > 70%**: Most of the deadline consumed, preserve stability
- **Budget or time progress > 50%**: Conservative guard for early/mid execution

## 10. Dual-Resource Allocation Logic

### 10.1 Allocation Check (Both Resources Required)

For a workflow to start, the scheduler must satisfy **both**:

1. **Compute**: Sufficient instances available (on-prem or cloud)
2. **Licenses**: Sufficient tokens available in the workflow's license pool

If either is unavailable, the workflow is queued. The moldable scheduler can attempt **partial allocation** — reducing the instance count until both compute and licenses fit.

### 10.2 Scale-Up with License Constraint

```
Can we scale up?
  1. Check compute: more instances available?        → if no, FAIL (insufficient_compute)
  2. Check licenses: pool has tokens for new cores?  → if no, FAIL (insufficient_licenses)
  3. Check budget: cost of new allocation fits?       → if no, FAIL (budget_exhausted)
  4. Check time: enough time remaining?               → if no, FAIL (time_exhausted)
  All pass → SCALE UP (acquire instances + tokens via two-phase commit)
```

### 10.3 Scale-Down with License Release

```
Can we scale down?
  1. Check safe threshold: time_remaining > 200% of time_elapsed?
  2. Check not late iteration (iteration <= 3)?
  3. Check license pool: would releasing tokens help blocked workflows?
  All pass → SCALE DOWN (release instances + return tokens to pool)
```

## 11. Metrics Tracked

### 11.1 Per-Workflow Metrics

| Metric | Description |
|--------|-------------|
| `submit_time` | When workflow was submitted to scheduler |
| `exec_start_time` | When first instance was allocated |
| `finish_time` | When workflow completed |
| `wait_time` | Queue wait: `exec_start_time - submit_time` |
| `flowtime` | Total latency: `finish_time - submit_time` |
| `hardware_cost` | `Σ(instance_rate × count × duration)` |
| `license_cost` | `Σ(tokens × cost_per_token × duration)` |
| `total_cost` | `hardware_cost + license_cost` |
| `deadline_miss` | Boolean: `finish_time > deadline` |
| `budget_miss` | Boolean: `hardware_cost > budget` |

### 11.2 Moldability Metrics

| Metric | Description |
|--------|-------------|
| `scale_up_attempts` | Total scale-up requests across all workflows |
| `scale_up_successes` | Successful scale-ups |
| `scale_up_failures_by_reason` | Breakdown: insufficient_compute, insufficient_licenses, budget_exhausted, time_exhausted |
| `scale_down_attempts` | Total scale-down requests |
| `scale_down_successes` | Successful scale-downs |
| `scale_down_blocked_by_reason` | Breakdown: license_pool_saturated, late_iteration, time_progress, budget_or_time_progress |
| `total_instances_added/removed` | Net instances allocated/freed by moldable decisions |
| `total_licenses_acquired/released` | Net license tokens acquired/released |

### 11.3 System Metrics

| Metric | Description |
|--------|-------------|
| `resource_utilization` | % of compute slots in use, sampled every 20 minutes |
| `license_utilization` | Per-pool: (timestamp, pool, total_tokens, allocated, utilisation%) |
| `makespan` | Wall-clock from first submission to last completion |

### 11.4 Output Files

```
{PREFIX}_{TOTAL_WORKFLOWS}_results.csv       # Per-workflow detailed metrics
{PREFIX}_{TOTAL_WORKFLOWS}_resources.csv      # Resource utilisation time series
{PREFIX}_{TOTAL_WORKFLOWS}_license_usage.csv  # Per-pool license utilisation
```

Where PREFIX is e.g., `LAMF`, `FCFS_Static_LA`, `EDF_Static_LA`, `EDF_HSM`.

## 12. Infrastructure (Simulation Mode)

In simulation mode, no actual instances are launched. The resource configuration defines the **simulated** infrastructure:

| Resource | Type | Slots | Cores/Slot | Cost/hr |
|----------|------|-------|-----------|---------|
| On-prem | on-prem | 4 | 4 | $0.526 |
| Cloud reserved (g4dn) | g4dn.xlarge | 0 | 4 | $0.227 |
| Cloud reserved (g5) | g5.xlarge | 0 | 4 | $0.435 |
| Cloud on-demand (g4dn) | g4dn.xlarge | 0 | 4 | $0.526 |
| Cloud on-demand (g5) | g5.xlarge | 0 | 4 | $1.006 |

**Note**: The slot counts in `resources.yaml` are configurable per experiment. The simulation models instance-specific runtimes using the fitted functions in `speedup.py`, enabling heterogeneous instance type evaluation.

## 13. Key Design Rationale

### 13.1 Why License-Aware Matters

1. **Real-world constraint**: Enterprise HPC sites run ANSYS, ABAQUS, LS-DYNA with expensive per-core licenses. License pools are shared across teams and projects.
2. **Scheduling bottleneck**: License availability can be more constraining than compute — a cluster with 1,000 cores but 200 license tokens can only run 200-core workloads.
3. **Cost optimisation**: Moldable scheduling can trade compute parallelism for reduced license duration, lowering total cost.
4. **Dual-resource contention**: Static allocation wastes both compute AND licenses when a workflow iteration needs fewer resources than allocated.

### 13.2 Why Three Different License Formulas

Different token formulas force the scheduler to make diverse allocation decisions:
- **LSDYNA (linear)**: Token cost scales directly with parallelism — most expensive to scale up
- **ABAQUS (power-law)**: Sublinear scaling — moderate cost to add cores
- **ANSYS (fixed base + linear)**: First 4 cores cost only 1 token; above 4, each core adds 1 token

This diversity prevents a single scheduling heuristic from being universally optimal.

### 13.3 Why `workflowConfig` Array

The per-iteration configuration variation is the **key enabler of moldability**:
- Iteration 0: 5 chains, 1 TinyDA → light compute, low license usage
- Iteration 1: 6 chains, 8 TinyDA → heavy compute, high license usage → scale-up opportunity
- Iteration 2: 3 chains, 2 TinyDA → light again → scale-down opportunity

Without this variation, there would be no benefit to moldable scheduling — static allocation would be optimal.

### 13.4 Why 700 Workflows

700 workflows with temporal compression create realistic multi-tenant contention:
- Multiple workflows competing for the same license pool simultaneously
- Peak periods where all three pools approach 87% utilisation
- Sufficient statistical mass for meaningful makespan and cost comparisons

### 13.5 Why Temporal Compression

The 2× compression (`TEMPORAL_COMPRESSION_FACTOR = 0.5`) with 2-minute jitter creates bursty arrival patterns that stress the scheduler:
- Periods of high concurrent demand → license pools saturate
- Moldable scheduling can react by scaling down low-priority workflows to free tokens
- Static scheduling holds all tokens → longer queues for later arrivals
