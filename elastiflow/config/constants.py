MOLDABLE = True
FREE_RESOURCES = True
TOTAL_WORKFLOWS = 300
TOTAL_RESOURCES = 256 # onprem and cloud reserved resources - for resurce utilization calculation
AVG_WORKFLOW_ITERATIONS = 4
AVG_TINYDA_ITERATIONS = 7
# MIN_ALLOC_INSTANCES = 1 # What can be min allocated instances for a workflow
COLD_START_TIME = 384.136 + 16.4 # + 16 why ??
RESOURCE_CONFIG_PATH = ''
WORKFLOWS_DIR = ''
RESOURCE_REQUEST_TIMEOUT = 3*60 # NOTE: Justification for 3 mins
MIN_RUNTIME = 60 # 129.7587 # Runtime of 1 model run from the fastest instance 
MIN_ITERATION_RUNTIME = MIN_RUNTIME * 3 # min tinyda iteration  # MIN_RUNTIME * 2
MIN_INSTANCE_COST = 0 # 0.0002514 * 2 # 1 instance cost for min tinyda iterations
WORKFLOW_POLLING = 30 # polling interval in seconds for new user workflow requests # 20
SORT_COUNT = None # 0
RESOURCE_UTILIZATION_POLLING = 20 * 60 # 20 mins # 5*60

# slowest runtime
AVG_BUDGET = {
    1000: 0.5141 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS ,
    750: 0.8595 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS,
    500: 2.7422 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS
    } 
AVG_DEADLINE = {
    1000: 253 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2,
    750: 557 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2,
    500: 2281.28 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2
    } 
BUDGET_FACTOR = 2.5
DEADLINE_FACTOR = 7.5

# OPTIM_FCFS_BFACTOR = {0: 0.3, 1: 0.6, 2: 0.8}#{0: 0.1, 1: 0.3, 2: 0.4, 3: 0.6, 4: 0.7, 5: 0.8}
# OPTIM_FCFS_DFACTOR = {0: 0.3, 1: 0.6, 2: 0.8}

# OLD (too conservative for license-aware workloads):
# OPTIM_FCFS_BFACTOR = {0: 0.1, 1: 0.3, 2: 0.4, 3: 0.6, 4: 0.7, 5: 0.8}
# OPTIM_FCFS_DFACTOR = {0: 0.1, 1: 0.3, 2: 0.4, 3: 0.6, 4: 0.7, 5: 0.8}

# NEW (updated for better initial allocation with license constraints):
# Further increased after 400-workflow analysis showed chronic under-allocation
# Previous: {0: 0.3, 1: 0.5, 2: 0.6, 3: 0.7, 4: 0.8, 5: 0.9}
# Current:  Start aggressive (0.6), ramp to full allocation (1.0)
OPTIM_FCFS_BFACTOR = {0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95, 5: 1.0}
OPTIM_FCFS_DFACTOR = {0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95, 5: 1.0}
SPEEDUP_THRESHOLD = 1.4
# Maximum number of SeisSol-TinyDA chains the moldable scale-down loop will
# attempt to pack onto a single node. The loop probes k, k-1, ..., 1 chains
# per node and keeps the densest packing that still fits the per-iteration
# time budget (Scheduler.processFreeRequest / FCFS_Optimized.processFreeRequest).
# The runtime model assumes linear scaling (k chains cost k x the single-chain
# runtime), so k is the ceiling at which that linearity is assumed to hold.
# Default 3; overridable via simulate_sweep.py --chains-per-node for the
# k-sensitivity ablation.
CHAINS_PER_NODE = 3
# Closeness tolerance for the moldable cloud path (fraction). A cloud
# instance whose 1-node runtime is within ±CLOSENESS_TOLERANCE of any
# runtime in the workflow's current fleet is eligible for scale-up.
# Tighter values (e.g. 0.05) force fleet homogeneity; looser values (e.g.
# 1.0) let any candidate pass. Used by Scheduler.checkCloseness and
# FCFS_Optimized.checkCloseness. Ablation candidate.
CLOSENESS_TOLERANCE = 0.15
DEADLINE_BUFFER = 180
# Seed for np.random in the dispatcher's inter-arrival generation. Set
# via CLI (simulate_sweep.py --seed) for reproducible-but-varied draws
# in the main sweep. Default keeps the historical behaviour of
# delayGenerationFromSubmitTimes (which used to hardcode seed = 0).
SEED = 0
