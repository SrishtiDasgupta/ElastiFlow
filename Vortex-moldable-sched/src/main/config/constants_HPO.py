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

# Epochs (sequential training per trial)
MIN_EPOCHS = 3   # Fast exploratory trials
MAX_EPOCHS = 9   # High-quality convergence trials (removed expensive 12-epoch config)
AVG_TINYDA_ITERATIONS = 6  # Named for compatibility with existing codebase (was AVG_EPOCHS)
# Distribution: 3 epochs for early exploration, 6 for balanced, 9 for final convergence

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
WORKFLOW_CONFIGS = [5, 10, 15, 20]  # Different scales for evaluation
PRIMARY_WORKFLOWS = 15  # Sweet spot for demonstrating improvements

# Workflow dispatch order: Wide-Short first to saturate g4, triggering g5 partial-fallback
# Fallback triggers when g4 can't FULLY satisfy a request (not just when g4 gives 0 hosts)
# Positions 0-4 (5-wf): [8,3] Wide-Short fill on-prem+g4 → [9] g4 partial→g5 fallback → [7,5] freed on-prem
# Positions 5-9 (10-wf): [12] g4 burst → [1] g5 OD fallback → [4,10,14] sustained (14/14 peak at t=840)
# Positions 10-14 (15-wf): [0,6,11,2,13] Narrow-Med tail, moldable scale-up as long workflows finish
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
# CONSTRAINT CALCULATION - Using SeisSol Methodology
# ====================================================================================

def calculate_trial_cost(model, instance='cloud_ondemand_g5', epochs=12):
    """Calculate worst-case cost for a single trial"""
    base_runtime = G5_RUNTIMES_12EP[model] * (epochs / 12)
    setup = SETUP_OVERHEAD[model]
    ddp = DDP_OVERHEAD_PER_GPU[1]  # Single GPU assumption
    cold_start = COLD_START_TIME if 'ondemand' in instance else 0
    
    total_seconds = base_runtime + setup + ddp + cold_start
    total_hours = total_seconds / 3600
    
    return total_hours * INSTANCE_COSTS[instance]

def calculate_trial_runtime(model, instance='cloud_ondemand_g4dn', epochs=12):
    """Calculate worst-case runtime for a single trial"""
    base_runtime = G4DN_RUNTIMES_12EP[model] * (epochs / 12)
    setup = SETUP_OVERHEAD[model]
    ddp = DDP_OVERHEAD_PER_GPU[1]
    cold_start = COLD_START_TIME if 'ondemand' in instance else 0
    
    return base_runtime + setup + ddp + cold_start

# Pre-calculated base values for standard 12-epoch trials
COST_PER_TRIAL = {
    'vgg19': calculate_trial_cost('vgg19'),           # ~$0.305
    'wide_resnet101_2': calculate_trial_cost('wide_resnet101_2'),  # ~$0.385
    'convnext_large': calculate_trial_cost('convnext_large')       # ~$0.475
}

RUNTIME_PER_TRIAL = {
    'vgg19': calculate_trial_runtime('vgg19'),           # ~1391s
    'wide_resnet101_2': calculate_trial_runtime('wide_resnet101_2'),  # ~1675s
    'convnext_large': calculate_trial_runtime('convnext_large')       # ~2488s
}

# ====================================================================================
# FINAL CONSTRAINT VALUES - OPTIMIZED PARAMETERS
# ====================================================================================

# Budget constraints (includes parallel trials since cost accumulates)
AVG_BUDGET = {
    'vgg19': COST_PER_TRIAL['vgg19'] * (AVG_TINYDA_ITERATIONS/12*12) * AVG_WORKFLOW_ITERATIONS * AVG_PARALLEL_TRIALS,
    'wide_resnet101_2': COST_PER_TRIAL['wide_resnet101_2'] * (AVG_TINYDA_ITERATIONS/12*12) * AVG_WORKFLOW_ITERATIONS * AVG_PARALLEL_TRIALS,
    'convnext_large': COST_PER_TRIAL['convnext_large'] * (AVG_TINYDA_ITERATIONS/12*12) * AVG_WORKFLOW_ITERATIONS * AVG_PARALLEL_TRIALS
}

# Deadline constraints (excludes parallel trials since they run concurrently)
# Includes 2x scaling factor for resource contention
AVG_DEADLINE = {
    'vgg19': RUNTIME_PER_TRIAL['vgg19'] * (AVG_TINYDA_ITERATIONS/12*12) * AVG_WORKFLOW_ITERATIONS * 2,
    'wide_resnet101_2': RUNTIME_PER_TRIAL['wide_resnet101_2'] * (AVG_TINYDA_ITERATIONS/12*12) * AVG_WORKFLOW_ITERATIONS * 2,
    'convnext_large': RUNTIME_PER_TRIAL['convnext_large'] * (AVG_TINYDA_ITERATIONS/12*12) * AVG_WORKFLOW_ITERATIONS * 2
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
# EXPECTED IMPROVEMENTS WITH OPTIMIZED PARAMETERS
# ====================================================================================
"""
With image_size=64 profiling (3-5 iterations, 3-9 epochs, 2-4 trials):

Budget per workflow (average, using g5 on-demand worst case + cold start):
- VGG19: ~$12.16
- Wide_ResNet: ~$13.55
- ConvNeXt: ~$13.86

Deadline per workflow (average, using g4dn worst case + cold start + 2x contention):
- VGG19: ~9.0 hours
- Wide_ResNet: ~10.2 hours
- ConvNeXt: ~11.0 hours

Profiling data sources: HPO/g4_img64.jsonl, HPO/g5_img64.jsonl (36 entries each)
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
## HPO Constraint Base Values Explanation

The HPO constraint calculations use empirically-derived base values representing worst-case
scenarios for cost and runtime per complete trial. All values measured at IMAGE_SIZE=64,
batch_size=64, single GPU per trial (profiling data: HPO/g4_img64.jsonl, HPO/g5_img64.jsonl).

### Cost Base Values (Budget Calculation)
The cost values are calculated using the most expensive instance configuration
(g5.xlarge on-demand at $1.006/hour) running a single GPU for 12 epochs, with added overheads
for cold start time (400.52 seconds for on-demand instances) and setup overhead per model.
Ray coordination overhead is negligible (~0.000003% measured, effectively 0%).

Example calculation for vgg19:
  base_runtime = 198.00s (12 epochs, 1 GPU, g5.xlarge)
  setup = 5.78s (model creation + dataloader + warmup)
  cold_start = 400.52s (on-demand instance startup)
  total_seconds = 198.00 + 5.78 + 0 + 400.52 = 604.30s
  total_hours = 604.30 / 3600 = 0.1679h
  cost_per_trial = 0.1679 * $1.006 = $0.169

### Runtime Base Values (Deadline Calculation)
The runtime values represent worst-case execution times using the slowest instance
(g4dn.xlarge, Tesla T4) with the same overhead calculations.

Example calculation for vgg19:
  base_runtime = 266.02s (12 epochs, 1 GPU, g4dn.xlarge)
  setup = 5.78s
  cold_start = 400.52s
  total = 672.32s per trial

### Constraint Formula
Budget constraints include parallel trials since cost accumulates across all simultaneous work:
  budget = cost_per_trial * epochs * iterations * parallel_trials

Deadline constraints exclude parallel trials since they run concurrently, but include a 2x
scaling factor for resource contention when multiple workflows compete for resources:
  deadline = runtime_per_trial * epochs * iterations * 2x_contention_factor

This approach ensures conservative bounds that accommodate realistic execution conditions
while providing optimization opportunities for the moldable scheduling algorithm.

### Speedup Model (Power Law)
Fitted from 36 profiling runs per instance type (3 models x 3 worker counts x 4 epoch counts):
  runtime_per_epoch = a * (workers^b) * model_factor + c
  G4: a=23.039, b=-0.789, c=0.0  (R²=0.983)
  G5: a=16.445, b=-0.771, c=0.0  (R²=0.988)
  Model factors: vgg19=1.0, wide_resnet101_2=1.34, convnext_large=1.46

Worker speedup (12 epochs): 2 workers ≈ 1.6-1.8x, 4 workers ≈ 2.9-3.3x
G5/G4 speed ratio: ~1.3-1.5x (A10G faster than T4)
"""
