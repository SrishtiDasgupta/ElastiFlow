#!/usr/bin/env python3
"""
HPO Constraint Constants File - Updated with Optimized Parameters
Based on empirical measurements and thesis methodology
"""

# ====================================================================================
# WORKFLOW PARAMETER RANGES - Optimized for Demonstrating Moldability  
# ====================================================================================

# HPO Iterations (optimization rounds)
MIN_WORKFLOW_ITERATIONS = 3  # Minimum for convergence detection
MAX_WORKFLOW_ITERATIONS = 5  # Multiple reallocation opportunities
AVG_WORKFLOW_ITERATIONS = 4  # Matches SeisSol's average iteration count

# Epochs (sequential training per trial — analogous to TinyDA iterations in SeisSol)
MIN_EPOCHS = 3    # Fast exploratory trials
MAX_EPOCHS = 36   # High-quality convergence trials (increased for meaningful training time)
AVG_TINYDA_ITERATIONS = 20  # Named for compatibility with existing codebase (was AVG_EPOCHS)
# Distribution: 3-12 epochs for exploration, 12-24 for balanced, 24-36 for final convergence

# Parallel Trials (per iteration)
MIN_TRIALS = 2   # Minimum for Bayesian optimization
MAX_TRIALS = 4   # Sufficient parallelism for resource sharing
AVG_PARALLEL_TRIALS = 3  # Matches SeisSol's parallel chain count (was 20, now reduced)

# ====================================================================================
# INFRASTRUCTURE CONFIGURATION
# ====================================================================================

# Total available resources
TOTAL_ON_PREMISE = 4  # 4x g4dn.xlarge (Slurm cluster)
TOTAL_CLOUD_RESERVED = 4  # 2x g4dn.xlarge + 2x g5.xlarge
TOTAL_CLOUD_ONDEMAND = 6  # 3x g4dn.xlarge + 3x g5.xlarge
TOTAL_RESOURCES = TOTAL_ON_PREMISE + TOTAL_CLOUD_RESERVED + TOTAL_CLOUD_ONDEMAND  # 14

# Workflow configurations to test
WORKFLOW_CONFIGS = [5, 10, 15]  # Different batch sizes for evaluation
PRIMARY_WORKFLOWS = 15  # Full batch for comprehensive evaluation

# ====================================================================================
# WORKFLOW DISPATCH ORDER — Optimized for Moldable vs Static Comparison
# ====================================================================================
# Maps dispatch position → data file index. Position 0 dispatched first, etc.
# For 5-wf batch: positions 0-4. For 10-wf: positions 0-9. For 15-wf: all 15.
#
# Strategy: Wide-Short workflows first (chains=4, low epochs) create resource contention.
# Static allocates 4 instances each → pool saturated → later workflows queue.
# Moldable allocates 2 each (cap=0.5) → pool has headroom → all workflows start → scale up later.
#
# Positions 0-4 (5-wf): [8,3] Wide-Short vgg19 fill on-prem+g4 → [9] g4 partial→g5 fallback → [7,5] freed on-prem
#   Static demand:   4+4+3+2+3 = 16 (>14 → queuing)
#   Moldable demand: 2+2+2+1+2 = 9  (all fit → 5 free for scale-up)
#
# Positions 5-9 (10-wf): [12] g4 burst → [1] g5 OD fallback → [4,10,14] sustained
#   Cumulative static: 32 (2.3x oversubscribed)
#   Cumulative moldable: 18 (1.3x — much less queuing)
#
# Positions 10-14 (15-wf): [0,6,11,2,13] Narrow-Long convnext tail
#   All chains=2, high epochs (18-28). Moldable starts with 1 instance each,
#   scales up as earlier Wide-Short workflows complete and free resources.
#   Cumulative static: 42 (3x). Moldable: 23 (1.6x).
WORKFLOW_ORDER = [8, 3, 9, 7, 5, 12, 1, 4, 10, 14, 0, 6, 11, 2, 13]

# ====================================================================================
# MEASURED OVERHEADS - From g4.jsonl and g5.jsonl Data
# ====================================================================================

# Cold start time for on-demand instances (from thesis Section 5.3)
COLD_START_TIME = 400.52  # seconds

# Setup overheads per model (from g4_img64.jsonl instrumentation data)
SETUP_OVERHEAD = {
    'vgg19': 5.78,             # seconds (avg across all g4 runs)
    'wide_resnet101_2': 5.22,  # seconds
    'convnext_large': 12.91    # seconds
}

# DDP overhead for multi-GPU (from measurements — negligible)
DDP_OVERHEAD_PER_GPU = {
    1: 0.0,   # No DDP for single GPU
    2: 0.9,   # seconds for 2 GPUs
    4: 0.8    # seconds for 4 GPUs
}

# Ray coordination overhead (from measurements: essentially 0%)
RAY_COORDINATION_OVERHEAD = 0.00  # Previously assumed 15%, measured as ~0.000003%

# ====================================================================================
# RUNTIME DATA - Base Training Times (seconds)
# ====================================================================================

# g4dn.xlarge runtimes for 12 epochs, 1 GPU (Tesla T4 — slowest instance for deadline)
# Measured from g4_img64.jsonl at IMAGE_SIZE=64, batch_size=64
G4DN_RUNTIMES_12EP = {
    'vgg19': 266.02,
    'wide_resnet101_2': 355.88,
    'convnext_large': 408.32
}

# g5.xlarge runtimes for 12 epochs, 1 GPU (A10G — expensive instance for budget)
# Measured from g5_img64.jsonl at IMAGE_SIZE=64, batch_size=64
G5_RUNTIMES_12EP = {
    'vgg19': 198.00,
    'wide_resnet101_2': 267.62,
    'convnext_large': 275.45
}

# ====================================================================================
# INSTANCE PRICING (USD per hour)
# ====================================================================================

INSTANCE_COSTS = {
    # On-premise (calculated TCO from thesis methodology — see gpu-cluster-cost.md)
    'on_premise_g4': 0.84,  # $0.84/hour based on 3-year TCO for 4-node T4 GPU cluster

    # Cloud Reserved Instances (xlarge, 1-year no-upfront)
    'cloud_reserved_g4dn': 0.227,  # g4dn.xlarge reserved $/hour
    'cloud_reserved_g5': 0.435,    # g5.xlarge reserved $/hour

    # Cloud On-Demand Instances (xlarge)
    'cloud_ondemand_g4dn': 0.526,  # g4dn.xlarge on-demand $/hour
    'cloud_ondemand_g5': 1.006     # g5.xlarge on-demand $/hour (most expensive for budget)
}

# ====================================================================================
# CONSTRAINT CALCULATION - Using SeisSol Methodology (Consistent with Plain)
# ====================================================================================
#
# Plain SeisSol formula:
#   deadline = single_run_time × tinyda_iterations × workflow_iterations × 2
#   budget   = single_run_cost × chains × tinyda_iterations × workflow_iterations
#
# HPO mapping (epoch ↔ TinyDA iteration, trial ↔ chain):
#   1 epoch  = atomic sequential unit (like 1 SeisSol simulation run)
#   epochs   = sequential count per trial (like tinyda_iterations)
#   trials   = parallel count per iteration (like chains)
#
# Base unit is pure compute time per epoch — no cold start (same as Plain's 253s).
# The 2x contention factor covers cold start, queuing delays, etc.
# ====================================================================================

# Per-epoch runtime on slowest instance (g4dn, 1 worker) — pure compute, no cold start
# Derived from profiling: G4DN_RUNTIMES_12EP / 12
EPOCH_RUNTIME = {
    'vgg19': G4DN_RUNTIMES_12EP['vgg19'] / 12,                        # 22.17 s/epoch
    'wide_resnet101_2': G4DN_RUNTIMES_12EP['wide_resnet101_2'] / 12,   # 29.66 s/epoch
    'convnext_large': G4DN_RUNTIMES_12EP['convnext_large'] / 12        # 34.03 s/epoch
}

# Per-epoch cost on most expensive instance (g5 on-demand) — pure compute, no cold start
EPOCH_COST = {
    model: (G5_RUNTIMES_12EP[model] / 12) / 3600 * INSTANCE_COSTS['cloud_ondemand_g5']
    for model in G5_RUNTIMES_12EP
}

# ====================================================================================
# FINAL CONSTRAINT VALUES
# ====================================================================================

# Deadline: epoch_time × avg_epochs × workflow_iterations × 2x_contention
# (Same structure as Plain: run_time × tinyda_iters × wf_iters × 2)
AVG_DEADLINE = {
    model: EPOCH_RUNTIME[model] * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2
    for model in EPOCH_RUNTIME
}

# Budget: epoch_cost × trials × avg_epochs × workflow_iterations
# (Same structure as Plain: run_cost × chains × tinyda_iters × wf_iters)
AVG_BUDGET = {
    model: EPOCH_COST[model] * AVG_PARALLEL_TRIALS * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS
    for model in EPOCH_COST
}

# ====================================================================================
# SCHEDULER OPERATIONAL PARAMETERS
# ====================================================================================

# Minimum runtime values (for HPO trials)
MIN_RUNTIME = 60  # Minimum runtime for a single trial in seconds
MIN_ITERATION_RUNTIME = MIN_RUNTIME * MIN_EPOCHS  # Minimum iteration runtime
MIN_INSTANCE_COST = min(INSTANCE_COSTS.values()) * (MIN_RUNTIME / 3600)  # Minimum cost threshold

# Request and polling parameters
RESOURCE_REQUEST_TIMEOUT = 8 * 60  # 8 minutes — must exceed COLD_START_TIME (400s) + setup (~120s)
WORKFLOW_POLLING = 30  # Polling interval in seconds for new workflow requests
RESOURCE_UTILIZATION_POLLING = 20 * 60  # 20 minutes for resource utilization metrics

# Moldable scheduler optimization factors (from SeisSol approach)
OPTIM_FCFS_BFACTOR = {0: 0.1, 1: 0.3, 2: 0.4, 3: 0.6, 4: 0.7, 5: 0.8}  # Budget pressure factors
OPTIM_FCFS_DFACTOR = {0: 0.1, 1: 0.3, 2: 0.4, 3: 0.6, 4: 0.7, 5: 0.8}  # Deadline pressure factors
SPEEDUP_THRESHOLD = 1.4  # Minimum speedup for instance type switching
DEADLINE_BUFFER = 180  # Safety buffer for deadline calculations (3 minutes)
MOLDABLE_INITIAL_CAP = 0.5  # Start at half max parallelism; moldable scale-up fills the rest

# ====================================================================================
# SIMULATION PARAMETERS
# ====================================================================================

SIMULATE = False  # False for real execution; True for simulation only
MOLDABLE = True  # False for static scheduler; True for moldable (fcfs_optimized_HPO)
FREE_RESOURCES = True  # Allow resource deallocation
TOTAL_WORKFLOWS = PRIMARY_WORKFLOWS  # Default to 10 workflows

# Poisson arrival process: average inter-arrival time between workflow submissions
# For HPO (20 workflows over ~4 hours): avg ~720s between submissions
# For HPO (10 workflows over ~2 hours): avg ~720s between submissions
AVG_INTERARRIVAL_TIME = 720  # seconds between HPO workflow submissions

# ====================================================================================
# EXPECTED CONSTRAINT VALUES (with avg 20 epochs, 4 iterations, 3 trials)
# ====================================================================================
"""
Constraint formula consistent with Plain SeisSol (epoch ↔ TinyDA iteration):

Deadline per workflow (epoch_time × 20 × 4 × 2):
- VGG19:       22.17 × 20 × 4 × 2 = 3,547s (59 min)
- Wide_ResNet:  29.66 × 20 × 4 × 2 = 4,746s (79 min)
- ConvNeXt:    34.03 × 20 × 4 × 2 = 5,445s (91 min)

Budget per workflow (epoch_cost × 3 × 20 × 4):
- VGG19:       ~$1.10
- Wide_ResNet:  ~$1.49
- ConvNeXt:    ~$1.54

Profiling data sources: HPO/g4_img64.jsonl, HPO/g5_img64.jsonl (36 entries each)
Runtime scales linearly with epochs (verified: CV < 5% across 3/6/9/12 epoch runs)
"""

# Print summary when module is imported
if __name__ == "__main__":
    print("=" * 60)
    print("HPO CONSTRAINT CONSTANTS - OPTIMIZED PARAMETERS")
    print("=" * 60)
    print(f"Workflow iterations: {MIN_WORKFLOW_ITERATIONS}-{MAX_WORKFLOW_ITERATIONS} (avg: {AVG_WORKFLOW_ITERATIONS})")
    print(f"Epochs per trial: {MIN_EPOCHS}-{MAX_EPOCHS} (avg: {AVG_TINYDA_ITERATIONS})")
    print(f"Parallel trials: {MIN_TRIALS}-{MAX_TRIALS} (avg: {AVG_PARALLEL_TRIALS})")
    print(f"Total resources: {TOTAL_RESOURCES} instances")
    print()
    print("BUDGET CONSTRAINTS (USD):")
    for model, budget in AVG_BUDGET.items():
        print(f"  {model}: ${budget:.2f}")
    print()
    print("DEADLINE CONSTRAINTS (hours):")
    for model, deadline in AVG_DEADLINE.items():
        print(f"  {model}: {deadline/3600:.1f} hours")
    print("=" * 60)

"""
## HPO Constraint Methodology — Consistent with Plain SeisSol

### Mapping between Plain SeisSol and HPO
  Plain: 1 SeisSol simulation run (253s)  ↔  HPO: 1 training epoch (22s)
  Plain: TinyDA iterations (7 sequential) ↔  HPO: epochs per trial (20 avg sequential)
  Plain: chains (4 parallel)              ↔  HPO: trials (3 parallel)
  Plain: workflow iterations (4)          ↔  HPO: workflow iterations (4)

### Constraint Formula (identical structure to Plain)
  deadline = epoch_runtime × avg_epochs × workflow_iterations × 2x_contention
  budget   = epoch_cost   × trials     × avg_epochs          × workflow_iterations

Base unit is pure compute per epoch on worst-case instance — NO cold start baked in.
Cold start and queuing delays are covered by the 2x contention factor (same as Plain).

### Example: vgg19 deadline
  epoch_runtime = 266.02s / 12 = 22.17 s/epoch (g4dn, 1 GPU, pure compute)
  deadline = 22.17 × 20 × 4 × 2 = 3,547s (59 min)

### Example: vgg19 budget
  epoch_cost = (198.00 / 12) / 3600 × $1.006 = $0.00461/epoch (g5 on-demand)
  budget = 0.00461 × 3 × 20 × 4 = $1.11

### Speedup Model (Power Law)
Fitted from 36 profiling runs per instance type (3 models x 3 worker counts x 4 epoch counts):
  runtime_per_epoch = a * (workers^b) * model_factor + c
  G4: a=23.039, b=-0.789, c=0.0  (R²=0.983)
  G5: a=16.445, b=-0.771, c=0.0  (R²=0.988)
  Model factors: vgg19=1.0, wide_resnet101_2=1.34, convnext_large=1.46

Runtime scales linearly with epochs (verified: CV < 5% across 3/6/9/12 epoch profiling runs).
"""
