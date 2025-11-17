# LAMF Quick Start Guide

## Running LAMF Simulation in 3 Steps

### Prerequisites

```bash
# 1. Install dependencies
pip install simulus numpy pandas matplotlib seaborn pyyaml redis

# 2. Start Redis (required even for simulation mode)
sudo systemctl start redis-server

# 3. Navigate to project directory
cd /Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched
```

### Step 1: Configure Simulation (Optional)

Edit simulation parameters in `src/main/config/constants_LA.py`:

```python
TOTAL_WORKFLOWS = 100  # How many workflows to simulate (default: 100)

LICENSE_DISTRIBUTION = {
    'ANSYS': 0.33,     # 33% ANSYS workflows
    'ABAQUS': 0.33,    # 33% ABAQUS workflows
    'LSDYNA': 0.34     # 34% LSDYNA workflows
}
```

**Default settings work fine for testing** - you can skip this step.

### Step 2: Generate License-Aware Workflows

```bash
python src/main/scripts/workflow_generator_LA.py
```

**Expected output:**
```
Generating 100 license-aware workflows for LAMF...

MESH ------------- MIN ----------- MAX ---------------- AVG
deadline_1000: 14112.0, 14312.0, 14212.0
deadline_750: 31136.0, 31336.0, 31236.0
deadline_500: 127353.6, 127953.6, 127653.6
budget_1000: 57.57, 77.57, 67.57
budget_750: 96.27, 116.27, 106.27
budget_500: 307.14, 357.14, 332.14

  Generated 20/100 workflows...
  Generated 40/100 workflows...
  Generated 60/100 workflows...
  Generated 80/100 workflows...
  Generated 100/100 workflows...

============================================================
LICENSE DISTRIBUTION
============================================================
ANSYS   :   33 workflows ( 33.0%)
ABAQUS  :   33 workflows ( 33.0%)
LSDYNA  :   34 workflows ( 34.0%)
============================================================

✓ Generated 100 license-aware workflows
✓ Output directory: sample_workflows_LA/
✓ Budget plot saved: budget_plot_LA.png
```

**Output files:**
- `sample_workflows_LA/data0.yaml` through `data99.yaml` (100 workflow files)
- `budget_plot_LA.png` (visualization of budget distribution)

### Step 3: Run LAMF Simulation

```bash
python src/main/simulate_main_LA.py
```

**Expected output:**
```
======================================================================
LAMF SIMULATION MODE
======================================================================
Scheduler: License-Aware Moldable FCFS (LAMF)
Mode: SimPy Simulation
Workflows: 100
License Requirement: ALL workflows need licenses (ANSYS/ABAQUS/LSDYNA)

License Distribution:
  - ANSYS   :  33.0% (capacity: 150 tokens)
  - ABAQUS  :  33.0% (capacity: 100 tokens)
  - LSDYNA  :  34.0% (capacity: 120 tokens)

Settings: SIMULATE=True, MOLDABLE=True
======================================================================

Initializing Simulus simulators...
Creating simulation processes...
  [✓] Dispatcher process (loads license-aware workflows)
  [✓] LAMF scheduler process (dual-resource allocation)
  [✓] Completion processor (releases compute + licenses)

======================================================================
STARTING LAMF SIMULATION
======================================================================

Starting LAMF dispatcher for 100 workflows at 0.0...
All workflows require licenses (ANSYS/ABAQUS/LSDYNA)

[     0.0s] Dispatching workflow 0...
  [✓] Loaded lamf-test-abc123 (license: ANSYS)
✓ lamf-test-abc123 allocated at 0.0: {'on-prem': {'hpc-on-prem': (3, [...])}}
  with 1 license hold(s)
  ✓ Allocated 36 licenses from pool 'ANSYS' (hold: ...)

[   120.5s] Dispatching workflow 1...
  [✓] Loaded lamf-test-def456 (license: LSDYNA)
✓ lamf-test-def456 allocated at 120.5: {'reserved': {'c6i-32xl': (2, [...])}}
  with 1 license hold(s)
  ✓ Allocated 128 licenses from pool 'LSDYNA' (hold: ...)

⬇ Scaling down: freeing 1 instances
  ✓ Released ~64 licenses (hold: ...)

⬆ Scaling up: allocating 2 instances
  ✓ Allocated 48 licenses (available: 102)

...

[150000.0s] Sending END signal

======================================================================
SIMULATION COMPLETE
======================================================================
Check metrics output for:
  - License utilization per pool (ANSYS/ABAQUS/LSDYNA)
  - Dual-resource allocation efficiency
  - Moldable scale-up/scale-down events
  - Cost and deadline compliance
======================================================================

Simulation Runtime Report:
...
```

## What the Simulation Does

1. **Dispatcher** loads 100 license-aware workflows at realistic intervals
2. **LAMF Scheduler** allocates compute instances + software licenses
3. **Moldable decisions** scale up/down resources between workflow iterations
4. **License tracking** shows allocation, release, and partial allocation events
5. **Metrics** computed at end: cost, deadline compliance, license utilization

## Simulation Output

### Console Logs
- Workflow dispatch times
- Compute + license allocations
- Moldable scale-up/scale-down decisions
- License holds and releases
- Final metrics summary

### Generated Files
- `sample_workflows_LA/data*.yaml` - Generated workflows
- `budget_plot_LA.png` - Budget distribution visualization
- Metrics CSV (if configured in scheduler)

## Common Scenarios

### Quick Test (5 workflows, fast)

```bash
# Edit constants_LA.py
# Set: TOTAL_WORKFLOWS = 5

# Regenerate
python src/main/scripts/workflow_generator_LA.py

# Run
python src/main/simulate_main_LA.py
```

### Large Test (200 workflows)

```bash
# Edit constants_LA.py
# Set: TOTAL_WORKFLOWS = 200

# Regenerate
python src/main/scripts/workflow_generator_LA.py

# Run simulation
python src/main/simulate_main_LA.py
```

### License Stress Test (low capacity)

```bash
# Edit /Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml
# Reduce token counts:
pools:
  ANSYS:
    total_tokens: 50   # Was 150
  ABAQUS:
    total_tokens: 30   # Was 100
  LSDYNA:
    total_tokens: 40   # Was 120

# Run simulation (will show more license contention)
python src/main/simulate_main_LA.py
```

## Troubleshooting

### Error: "No such file or directory: sample_workflows_LA"

**Solution:**
```bash
mkdir -p src/main/sample_workflows_LA
python src/main/scripts/workflow_generator_LA.py
```

### Error: "Connection refused" (Redis)

**Solution:**
```bash
sudo systemctl start redis-server
# Or on macOS:
brew services start redis
```

### Error: "ModuleNotFoundError: No module named 'simulus'"

**Solution:**
```bash
pip install simulus
```

### Error: License pool mismatch

**Solution:** Ensure `constants_LA.py` license capacities match `licenses.yaml`:
```python
# constants_LA.py
LICENSE_POOL_CAPACITY = {
    'ANSYS': 150,    # Must match licenses.yaml
    'ABAQUS': 100,   # Must match licenses.yaml
    'LSDYNA': 120    # Must match licenses.yaml
}
```

## Understanding the Logs

### License Allocation
```
✓ Allocated 48 licenses from pool 'ANSYS' (hold: abc123)
```
- Workflow successfully obtained 48 ANSYS license tokens
- Hold ID `abc123` tracks this allocation

### Scale Down
```
⬇ Scaling down: freeing 2 instances
  ✓ Released ~24 licenses (hold: abc123)
```
- Moldable scheduler freed 2 compute instances
- Corresponding licenses released (LIFO - most recent first)

### Scale Up
```
⬆ Scaling up: allocating 3 instances
  ✓ Allocated 36 licenses (available: 150)
```
- Moldable scheduler added 3 compute instances
- Allocated 36 licenses (114 remaining available)

### Partial Allocation
```
⚠ Insufficient licenses: need 72, have 30
  ✓ Partial allocation: 2 instances, 24 licenses
```
- Requested 72 licenses but only 30 available
- LAMF allocated subset (2 instances instead of 4)
- Workflow runs with reduced parallelism but doesn't wait

### License Waiting
```
⏳ wf-123 waiting for resources or licenses...
```
- Workflow cannot start due to insufficient compute OR licenses
- Will retry when resources become available

## Next Steps

After running the simulation:

1. **Analyze metrics** - Check console output for cost/deadline compliance
2. **Visualize budgets** - Open `budget_plot_LA.png`
3. **Inspect workflows** - Check generated `sample_workflows_LA/data*.yaml`
4. **Experiment** - Modify `constants_LA.py` and rerun
5. **Compare** - Run baseline Vortex (`simulate_main.py`) for comparison

## Full Workflow (Copy-Paste)

```bash
# One-time setup
cd /Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched
pip install simulus numpy pandas matplotlib seaborn pyyaml redis
sudo systemctl start redis-server

# Generate workflows (do once, or after changing constants_LA.py)
python src/main/scripts/workflow_generator_LA.py

# Run simulation (can run multiple times with same workflows)
python src/main/simulate_main_LA.py
```

That's it! The simulation will show you LAMF's dual-resource scheduling in action.
