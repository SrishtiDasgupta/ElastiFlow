# Plan: FCFS-Moldable and EDF-Moldable Experiments with Cost/Runtime Sorted Resource Queues

## Goal
Run experiments for FCFS-moldable and EDF-moldable schedulers with two resource sorting strategies (cost-sorted and runtime-sorted) for 100, 200, 300, and 400 workflows.

## User Preferences
- **Cost-sorted**: Cheaper resources first (ascending order)
- **Runtime-sorted**: Faster resources first (ascending order - lower runtime first)
- **Output**: Separate CSV files per experiment (e.g., `results_fcfs_cost_100.csv`)

## Codebase Understanding Summary

### Entry Point
- **`simulate_main.py`**: Main simulator entry point using `simulus` for discrete event simulation
- Uses `FCFS_Optimized` for FCFS-moldable and `EarliestDeadlineEDF` for EDF-moldable
- Both schedulers accept a `sort_key` parameter for resource sorting

### Key Files (Non-LA, Non-HPO)
1. **`src/main/simulate_main.py`** - Entry point
2. **`src/main/scheduler/fcfs_optimized.py`** - FCFS-Moldable scheduler
3. **`src/main/scheduler/earliest_deadline_edf.py`** - EDF-Moldable scheduler
4. **`src/main/scheduler/scheduler.py`** - Base scheduler class
5. **`src/main/resource_manager/resource_manager.py`** - Resource management and sorting
6. **`src/main/resource_manager/heft_rm.py`** - HEFT resource manager (used by EDF)
7. **`src/main/resource_manager/instance.py`** - Instance classes with sorting attributes
8. **`src/main/scripts/dispatcher.py`** - Workflow dispatcher (controls `TOTAL_WORKFLOWS`)
9. **`src/main/scripts/workflow_generator.py`** - Generates sample workflows
10. **`src/main/config/constants.py`** - Configuration constants including `TOTAL_WORKFLOWS`
11. **`src/main/utils/metrics.py`** - Metrics collection and output

### Resource Sorting Mechanism
- Resources are sorted in `ResourceManager.__init__()` or via `sortResources(key)` / `sortResourcesByFunction(func)`
- Instance attributes available for sorting:
  - `cost_per_iteration` - Cost for one iteration
  - `runtime_per_iteration` - Runtime for one iteration
  - `cost_per_second` - Per-second cost
- FCFS_Optimized uses `sortResourcesByFunction()` with a lambda
- EDF uses `sortResources(sort_key)` directly

### Current Sort Keys Used
- `cost_per_iteration` - Sort by cost (cheaper first)
- `runtime_per_iteration` - Sort by runtime (faster first)

## Implementation Plan

### Step 1: Generate All Workflows (400 max)
Modify `workflow_generator.py`:
- Set `TOTAL_WORKFLOWS = 400`
- Run once to generate `data0.yaml` through `data399.yaml`
- These workflows will be reused for all experiments (100 uses first 100, 200 uses first 200, etc.)

### Step 2: Create Parameterized simulate_main.py
Modify `simulate_main.py` to accept command-line arguments:
```bash
python simulate_main.py --scheduler fcfs --sort cost --workflows 100
python simulate_main.py --scheduler edf --sort runtime --workflows 200
```

Parameters:
- `--scheduler`: `fcfs` (FCFS_Optimized) or `edf` (EarliestDeadlineEDF)
- `--sort`: `cost` (cost_per_iteration) or `runtime` (runtime_per_iteration)
- `--workflows`: 100, 200, 300, or 400

### Step 3: Update Metrics Output
Modify `utils/metrics.py`:
- Change output filename from `results.csv` to parameterized name
- Format: `results_<scheduler>_<sort>_<workflows>.csv`

### Step 4: Update Dispatcher
Modify `scripts/dispatcher.py`:
- Accept `TOTAL_WORKFLOWS` as parameter or read from constants
- Use the same value passed to simulate_main.py

### Step 5: Run All 16 Experiments
Execute all combinations:
```bash
for wf in 100 200 300 400; do
  for sched in fcfs edf; do
    for sort in cost runtime; do
      python simulate_main.py --scheduler $sched --sort $sort --workflows $wf
    done
  done
done
```

## Files to Modify

### 1. `src/main/scripts/workflow_generator.py`
- Change `TOTAL_WORKFLOWS = 400`
- Run once to generate all workflow files

### 2. `src/main/simulate_main.py`
Add command-line argument parsing:
```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--scheduler', choices=['fcfs', 'edf'], default='fcfs')
parser.add_argument('--sort', choices=['cost', 'runtime'], default='cost')
parser.add_argument('--workflows', type=int, default=100)
args = parser.parse_args()

# Map sort to attribute
sort_key = 'cost_per_iteration' if args.sort == 'cost' else 'runtime_per_iteration'

# Select scheduler
if args.scheduler == 'fcfs':
    sched = FCFS_Optimized(queue, finish_queue, resource_request_queue, sort_key=sort_key)
else:
    sched = EarliestDeadlineEDF(queue, finish_queue, resource_request_queue, sort_key=sort_key)
```

### 3. `src/main/scripts/dispatcher.py`
- Import `TOTAL_WORKFLOWS` from a shared location or accept as parameter
- Current line 11: `TOTAL_WORKFLOWS = 5` needs to be dynamic

### 4. `src/main/config/constants.py`
- Keep `TOTAL_WORKFLOWS` as default, but allow override

### 5. `src/main/utils/metrics.py`
- Modify `computeMetrics()` to accept output filename parameter
- Change from hardcoded `results.csv` to parameterized filename

## Detailed Code Changes

### simulate_main.py Changes
```python
import argparse
import simulus

# Parse arguments
parser = argparse.ArgumentParser(description='Run Vortex scheduler simulation')
parser.add_argument('--scheduler', choices=['fcfs', 'edf'], required=True)
parser.add_argument('--sort', choices=['cost', 'runtime'], required=True)
parser.add_argument('--workflows', type=int, required=True)
args = parser.parse_args()

# Set global workflow count (for dispatcher and metrics)
import config.constants as constants
constants.TOTAL_WORKFLOWS = args.workflows

# Import after setting constants
from scheduler.fcfs_optimized import FCFS_Optimized
from scheduler.earliest_deadline_edf import EarliestDeadlineEDF
from wf_queue.redis_queue import Redis_Queue
from scripts.dispatcher import dispatcher

# Setup
sort_key = 'cost_per_iteration' if args.sort == 'cost' else 'runtime_per_iteration'
output_file = f'results_{args.scheduler}_{args.sort}_{args.workflows}'

queue = Redis_Queue(queue_name='wf-queue')
finish_queue = Redis_Queue(queue_name='completed-jobs-queue')
resource_request_queue = Redis_Queue(queue_name='resource-request-queue')

if args.scheduler == 'fcfs':
    sched = FCFS_Optimized(queue, finish_queue, resource_request_queue, sort_key=sort_key)
else:
    sched = EarliestDeadlineEDF(queue, finish_queue, resource_request_queue, sort_key=sort_key)

# Pass output filename to metrics
sched.metrics.set_output_file(output_file)

# Run simulation
sim_dispatcher = simulus.simulator('dispatcher')
sim_sched = simulus.simulator('scheduler')
# ... rest of simulation setup
```

### dispatcher.py Changes
```python
from config.constants import TOTAL_WORKFLOWS  # Use from constants

def dispatcher(sim, wf_mb):
    delays = delayGenerationFromSubmitTimes(TOTAL_WORKFLOWS)
    # ... rest unchanged
```

### metrics.py Changes
```python
class Metrics():
    def __init__(self):
        self.df = {}
        self.free_resources = []
        self.collectFlag = True
        self.output_file = 'results'  # default

    def set_output_file(self, filename):
        self.output_file = filename

    def computeMetrics(self):
        # ... existing code ...
        df = pd.DataFrame(self.df).T
        df.to_csv(f'{self.output_file}.csv')
        resource_df.to_csv(f'{self.output_file}_resources.csv')
```

## Experiment Matrix (16 experiments)

| # | Scheduler | Sort | Workflows | Output Files |
|---|-----------|------|-----------|--------------|
| 1 | FCFS | cost | 100 | results_fcfs_cost_100.csv |
| 2 | FCFS | cost | 200 | results_fcfs_cost_200.csv |
| 3 | FCFS | cost | 300 | results_fcfs_cost_300.csv |
| 4 | FCFS | cost | 400 | results_fcfs_cost_400.csv |
| 5 | FCFS | runtime | 100 | results_fcfs_runtime_100.csv |
| 6 | FCFS | runtime | 200 | results_fcfs_runtime_200.csv |
| 7 | FCFS | runtime | 300 | results_fcfs_runtime_300.csv |
| 8 | FCFS | runtime | 400 | results_fcfs_runtime_400.csv |
| 9 | EDF | cost | 100 | results_edf_cost_100.csv |
| 10 | EDF | cost | 200 | results_edf_cost_200.csv |
| 11 | EDF | cost | 300 | results_edf_cost_300.csv |
| 12 | EDF | cost | 400 | results_edf_cost_400.csv |
| 13 | EDF | runtime | 100 | results_edf_runtime_100.csv |
| 14 | EDF | runtime | 200 | results_edf_runtime_200.csv |
| 15 | EDF | runtime | 300 | results_edf_runtime_300.csv |
| 16 | EDF | runtime | 400 | results_edf_runtime_400.csv |

## Execution Steps

1. **Generate workflows**:
   ```bash
   cd src/main/scripts
   # Edit workflow_generator.py: TOTAL_WORKFLOWS = 400
   python workflow_generator.py
   ```

2. **Run all experiments**:
   ```bash
   cd src/main
   for wf in 100 200 300 400; do
     for sched in fcfs edf; do
       for sort in cost runtime; do
         echo "Running: $sched $sort $wf"
         python simulate_main.py --scheduler $sched --sort $sort --workflows $wf
       done
     done
   done
   ```

3. **Collect results**: All CSV files will be in `src/main/` directory
