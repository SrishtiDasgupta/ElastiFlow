# LAMF: License-Aware Moldable FCFS Scheduler

## Overview

**LAMF (License-Aware Moldable FCFS)** extends Vortex's moldable scheduling framework with dual-resource constraints: **compute resources + software licenses**. It provides dynamic scale-up/scale-down scheduling for iterative scientific workflows (like SeisSol) that require licensed software (ANSYS, ABAQUS, LS-Dyna).

### Key Features

- **Dual-Resource Scheduling**: Coordinates allocation of both compute instances and software licenses
- **Moldable Execution**: Dynamically adjusts resources between workflow iterations
- **Iteration-Weighted Allocation**: Uses OPTIM_FCFS factors to allocate decreasing resource shares to later iterations
- **Two-Phase License Commit**: Hold → commit pattern prevents license allocation races
- **Partial Allocation**: When licenses are insufficient, allocates subset of requested resources
- **LIFO Resource Freeing**: Releases most recent allocations first (compute + licenses)
- **Hybrid Infrastructure**: Supports on-prem HPC + cloud reserved + cloud on-demand instances

## Architecture

### File Structure

```
src/main/
├── scheduler/
│   ├── scheduler_LA.py          # Base class with license management
│   ├── fcfs_scheduler_LA.py     # Static FCFS with licenses
│   └── fcfs_optimized_LA.py     # LAMF core implementation
├── resource_manager/
│   └── resource_manager_LA.py   # Extended resource manager with license tracking
├── workflow/
│   ├── workflow_LA.py           # License-aware workflow class
│   └── sample_workflows_LA/     # Sample workflow YAMLs
│       ├── seissol_ansys_small.yaml
│       ├── seissol_ansys_large.yaml
│       ├── seissol_abaqus_medium.yaml
│       └── seissol_lsdyna_medium.yaml
├── scripts/
│   ├── dispatcher_LA.py         # SimPy dispatcher for LA workflows
│   └── workflow_generator_LA.py # Generate license-aware workflows
├── utils/
│   └── resource_LA.py           # Extended constraint extraction
├── executor_LA.py               # License-aware executor
├── main_LA.py                   # Entry point for LAMF scheduler (real mode)
└── simulate_main_LA.py          # Entry point for LAMF simulation (SimPy)

config/
├── licenses.yaml                # License pool configuration (updated tokens)
└── constants_LA.py              # LAMF-specific constants (simulation settings)
```

### Components

#### 1. Scheduler Layer (`scheduler_LA.py`, `fcfs_optimized_LA.py`)

**Scheduler_LA (Base Class)**
- Provides license management methods for all LA schedulers
- `allocateResourcesWithLicenses()`: Dual-resource allocation
- `releaseLicensesForWorkflow()`: Release all licenses on completion
- `sendWorkflowForExecution()`: Extended with license hold IDs

**FCFS_Optimized_LA (LAMF Core)**
- `processFreeRequestWithLicenses()`: Main moldable logic with license awareness
- `checkNewResourcesWithLicenses()`: Scale-up with license availability check
- `findLicenseFeasibleAllocation()`: Partial allocation when licenses insufficient
- `freeResourcesWithLicenses()`: LIFO freeing of compute + licenses

#### 2. Resource Manager Layer (`resource_manager_LA.py`)

- Extended workflow tuple: `(instances, budget, deadline, start_time, mesh, software_id, license_holds)`
- Tracks license holds per workflow: `{wf_id: [hold_ids]}`
- `returnResources()`: Extended to release both compute and licenses
- `releaseLicenseHold()`: Partial license release for scale-down

#### 3. Executor Layer (`executor_LA.py`)

- Accepts `license-holds` field in workflow execution requests
- Logs license information for debugging
- License lifecycle managed by scheduler (executor only tracks)

### License Infrastructure

LAMF uses the existing `resource_manager/license/` infrastructure:

- **LicenseManager** (`license/manager.py`): Hold, commit, release operations
- **License Policies** (`license/policy.py`): Per-core calculation strategies
  - **ANSYS**: `ansys_workgroup` strategy (MEBA-style calculation)
  - **ABAQUS**: `powerlaw` strategy (a × cores^b)
  - **LS-Dyna**: `linear` strategy (1 token per core)
- **License Pools** (`/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml`): Token pools with increased capacity

## Workflow Specification

### YAML Format

License-aware workflows extend standard Vortex workflows with two fields:

```yaml
api: 4.7.0
id: seissol-ansys-wf1

constraints:
  budget: 50.00
  deadline: 3600
  chains: 4
  tinydaIterations: 10
  license_pool: ANSYS          # NEW: License pool name (optional)

config:
  mesh: 1000                    # Mesh size (500, 750, 1000)
  workflowIterations: 3
  software_id: 1                # NEW: 0=unlicensed, 1=ANSYS, 2=ABAQUS, 3=LSDYNA

vars:
  - id: input_coh
    value: 3

actions:
  - type: for
    input: input_coh
    enumerator: i
    yieldToInput: output_coh
    actions:
      - type: execute
        service: ../../service/simulate-tinyda-seissol.py
        inputs:
          - id: tinyda_input
            var: i
        outputs:
          - id: tinyda_output
            var: output_coh
```

### Software ID Mapping

**Note: ALL workflows in LAMF require licenses. There are no unlicensed workflows (software_id=0).**

| software_id | License Pool | Calculation Strategy |
|-------------|--------------|---------------------|
| 1           | ANSYS        | ansys_workgroup     |
| 2           | ABAQUS       | powerlaw            |
| 3           | LSDYNA       | linear              |

## Usage

### Mode 1: Simulation Mode (Recommended for Testing)

Simulation mode uses the Simulus library (SimPy-based) for fast, deterministic testing.

#### Step 1: Generate License-Aware Workflows

```bash
cd /path/to/Vortex-moldable-sched
python src/main/scripts/workflow_generator_LA.py
```

This generates workflows with random license assignments:
- ~33% ANSYS workflows
- ~33% ABAQUS workflows
- ~34% LSDYNA workflows

Output:
- `sample_workflows_LA/data{0-N}.yaml` files
- `budget_plot_LA.png` - budget distribution visualization
- License distribution summary

#### Step 2: Run LAMF Simulation

```bash
python src/main/simulate_main_LA.py
```

Expected output:
```
======================================================================
LAMF SIMULATION MODE
======================================================================
Scheduler: License-Aware Moldable FCFS (LAMF)
Mode: SimPy Simulation
License Requirement: ALL workflows need licenses (ANSYS/ABAQUS/LSDYNA)
======================================================================

Initializing Simulus simulators...
Creating simulation processes...
  [✓] Dispatcher process (loads license-aware workflows)
  [✓] LAMF scheduler process (dual-resource allocation)
  [✓] Completion processor (releases compute + licenses)

======================================================================
STARTING LAMF SIMULATION
======================================================================

[     0.0s] Dispatching workflow 0...
  [✓] Loaded lamf-test-xxx (license: ANSYS)
[   120.5s] Dispatching workflow 1...
  [✓] Loaded lamf-test-yyy (license: ABAQUS)
...

✓ wf-1 allocated: {'on-prem': {'hpc24xl': (4, ['10.0.0.1', ...])}}
  with 2 license hold(s)
  ✓ Allocated 48 licenses from pool 'ANSYS' (hold: abc123)

⬇ Scaling down: freeing 2 instances
  ✓ Released ~24 licenses (hold: abc123)

⬆ Scaling up: allocating 3 instances
  ✓ Allocated 36 licenses (available: 150)

======================================================================
SIMULATION COMPLETE
======================================================================
Check metrics output for:
  - License utilization per pool (ANSYS/ABAQUS/LSDYNA)
  - Dual-resource allocation efficiency
  - Moldable scale-up/scale-down events
  - Cost and deadline compliance
======================================================================
```

#### Simulation Configuration

**All simulation settings are centralized in `src/main/config/constants_LA.py`**

Key configuration parameters:

```python
# Workflow settings
TOTAL_WORKFLOWS = 100  # Number of workflows to generate and dispatch

# License distribution (must sum to ~1.0)
LICENSE_DISTRIBUTION = {
    'ANSYS': 0.33,     # 33% workflows use ANSYS
    'ABAQUS': 0.33,    # 33% workflows use ABAQUS
    'LSDYNA': 0.34     # 34% workflows use LSDYNA
}

# License pool capacities (must match /Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml)
LICENSE_POOL_CAPACITY = {
    'ANSYS': 150,
    'ABAQUS': 100,
    'LSDYNA': 120
}

# Mesh distribution (must sum to ~1.0)
MESH_DISTRIBUTION = {
    1000: 0.25,  # 25% large mesh
    750: 0.25,   # 25% medium mesh
    500: 0.50    # 50% small mesh
}

# Simulation mode flags
SIMULATE = True    # Run in simulation mode
MOLDABLE = True    # Enable moldable scheduling
```

**To adjust simulation parameters:**
1. Edit `src/main/config/constants_LA.py`
2. Regenerate workflows: `python src/main/scripts/workflow_generator_LA.py`
3. Run simulation: `python src/main/simulate_main_LA.py`

### Mode 2: Real Execution Mode

Real mode uses HTTP servers, Redis queues, and actual distributed execution.

#### 1. Prerequisites

```bash
# Install dependencies
pip install -r src/main/requirements.txt

# Start Redis (required)
sudo systemctl start redis-server

# Verify license configuration
cat src/main//Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml
```

#### 2. Running LAMF Scheduler

```bash
# Navigate to project root
cd /path/to/Vortex-moldable-sched

# Run LAMF scheduler
python src/main/main_LA.py
```

Expected output:
```
======================================================================
VORTEX - LICENSE-AWARE MOLDABLE FCFS (LAMF) SCHEDULER
======================================================================
Configuration:
  - Scheduler: LAMF (License-Aware Moldable FCFS)
  - Resource Management: Hybrid (On-Prem + Cloud)
  - License Pools: ANSYS, ABAQUS, LSDYNA
  - Moldable: Dynamic scale-up/scale-down between iterations
  - Iteration-weighted: OPTIM_FCFS_BFACTOR/DFACTOR allocation
======================================================================

Starting threads...
  [✓] Job submission server (port 8080)
  [✓] LAMF scheduler
  [✓] Completion server (port 8082)
  [✓] Completion processor
  [✓] Resource request server (port 8084)

======================================================================
LAMF SCHEDULER RUNNING
======================================================================
Submit workflows via: POST http://<scheduler-ip>:8080
Press Ctrl+C to stop
======================================================================
```

#### 3. Running License-Aware Executor

```bash
# In a separate terminal
python src/main/executor_LA.py
```

#### 4. Submitting Workflows

All workflows require licenses (ANSYS, ABAQUS, or LSDYNA):

```bash
# Submit small ANSYS workflow
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/yaml" \
  --data-binary @src/main/workflow/sample_workflows_LA/seissol_ansys_small.yaml

# Submit large ANSYS workflow
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/yaml" \
  --data-binary @src/main/workflow/sample_workflows_LA/seissol_ansys_large.yaml

# Submit medium ABAQUS workflow
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/yaml" \
  --data-binary @src/main/workflow/sample_workflows_LA/seissol_abaqus_medium.yaml

# Submit medium LSDYNA workflow
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/yaml" \
  --data-binary @src/main/workflow/sample_workflows_LA/seissol_lsdyna_medium.yaml
```

## LAMF Algorithm

### Moldable Decision Logic

For each workflow iteration, LAMF makes scale-up/scale-down decisions:

#### 1. Iteration-Weighted Constraints

```python
ind = request['iteration']
available_time = (deadline - current_time) * OPTIM_FCFS_DFACTOR[ind]
available_budget = (budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]
```

**OPTIM_FCFS factors** (from `config/constants.py`):
```python
OPTIM_FCFS_DFACTOR = [1.0, 0.8, 0.6, 0.4, 0.2]  # Time allocation
OPTIM_FCFS_BFACTOR = [1.0, 0.7, 0.5, 0.3, 0.1]  # Budget allocation
```

**Rationale**: Later iterations get smaller resource shares (uncertainty decreases as workflow progresses).

#### 2. Scale-Down Check

```python
# Can we free resources without missing deadline?
for chains_per_node in [3, 2, 1]:
    runtime = chains_per_node * runtime_per_model * tinyda_iterations
    if runtime < available_time:
        min_needed = chains // chains_per_node
        if current_count >= min_needed:
            # Scale down!
            free_count = current_count - min_needed
            freeResourcesWithLicenses(free_count)  # Frees compute + licenses
            return
```

**LIFO Freeing**: Releases most recent allocations first (instances + licenses).

#### 3. Scale-Up Check

```python
# Check if new resources available (compute + licenses)
alloc_instances, license_holds = checkNewResourcesWithLicenses(
    free_resources, current_resources, available_budget,
    available_time, request, mesh, license_pool
)

if alloc_instances:
    # Allocate compute
    ips, alloc_resources = resource_manager.allocateResources(alloc_instances)

    # Send to executor with new licenses
    sendNewResources(wf_id, ips, alloc_resources, license_holds)
```

#### 4. License-Constrained Allocation

```python
def checkNewResourcesWithLicenses(resources, current_resources, budget,
                                   available_time, request, mesh, license_pool):
    # Step 1: Get compute allocation (from parent class)
    alloc_instances = checkNewResources(resources, current_resources, budget,
                                        available_time, request, mesh)

    if not alloc_instances:
        return ([], [])

    # Step 2: Calculate licenses needed
    total_cores = sum(inst.cores * count for inst, count in alloc_instances)
    licenses_needed = license_manager.calculate_tokens(license_pool, total_cores, chains)

    # Step 3: Check license availability
    available_tokens = license_manager.get_available_tokens(license_pool)

    if available_tokens >= licenses_needed:
        # Full allocation
        hold_id = license_manager.hold(license_pool, licenses_needed, wf_id, ttl=300)
        license_manager.commit(hold_id)
        return (alloc_instances, [hold_id])
    else:
        # Partial allocation (fit to available licenses)
        feasible_instances = findLicenseFeasibleAllocation(
            alloc_instances, available_tokens, license_pool
        )
        # ... allocate subset ...
```

#### 5. Partial Allocation (License-Constrained)

```python
def findLicenseFeasibleAllocation(instances, available_licenses, license_pool):
    feasible = []
    for inst, count in instances:
        for i in range(count):
            test_cores = sum(i.cores * c for i, c in feasible) + inst.cores
            test_licenses = license_manager.calculate_tokens(license_pool, test_cores, 1)

            if test_licenses <= available_licenses:
                # Can add this instance
                feasible.append((inst, 1))
            else:
                break  # Would exceed license limit

    return feasible if feasible else None
```

## Configuration

### LAMF Constants (`config/constants_LA.py`)

**All LAMF-specific constants are centralized in this file.**

Key configuration sections:

#### Simulation Settings
```python
SIMULATE = True      # Enable simulation mode
MOLDABLE = True      # Enable moldable scheduling
TOTAL_WORKFLOWS = 100  # Number of workflows to generate/dispatch
```

#### License Configuration
```python
# License distribution for workflow generation
LICENSE_DISTRIBUTION = {
    'ANSYS': 0.33,
    'ABAQUS': 0.33,
    'LSDYNA': 0.34
}

# Software ID mapping
LICENSE_SOFTWARE_ID = {
    'ANSYS': 1,
    'ABAQUS': 2,
    'LSDYNA': 3
}

# License pool capacities (must match licenses.yaml)
LICENSE_POOL_CAPACITY = {
    'ANSYS': 150,
    'ABAQUS': 100,
    'LSDYNA': 120
}
```

#### Workflow Distribution
```python
# Mesh size distribution
MESH_DISTRIBUTION = {
    1000: 0.25,  # 25% large mesh
    750: 0.25,   # 25% medium mesh
    500: 0.50    # 50% small mesh
}
```

#### Performance Tuning
```python
WORKFLOW_POLLING = 30              # Seconds between queue checks
RESOURCE_REQUEST_TIMEOUT = 180     # Timeout for moldable requests
LICENSE_HOLD_TTL = 300             # License hold timeout (two-phase commit)
DEADLINE_BUFFER = 180              # Safety buffer before deadline
```

**Important:** After modifying `constants_LA.py`, regenerate workflows before running simulation.

### License Pools (`/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml`)

```yaml
pools:
  ANSYS:
    total_tokens: 150        # Increased for LAMF testing (was 12)
  ABAQUS:
    total_tokens: 100        # Increased for LAMF testing (was 8)
  LSDYNA:
    total_tokens: 120        # Increased for LAMF testing (was 10)

policy:
  mode: per_core
  two_phase_commit: true     # Hold → commit to avoid races
  lifo_release: true         # Release newest allocations first
```

### Compute Resources (`config/resources.yaml`)

Same as base Vortex (no changes needed).

## Debugging

### Scheduler Logs

LAMF provides detailed logging:

```
✓ wf-123 allocated: {'on-prem': {'hpc24xl': (4, ['10.0.0.1', ...])}}
  with 2 license hold(s)
  ✓ Allocated 48 licenses from pool 'ANSYS' (hold: abc123)

⬇ Scaling down: freeing 2 instances
  ✓ Released ~24 licenses (hold: abc123)

⬆ Scaling up: allocating 3 instances
  ✓ Allocated 36 licenses (available: 150)

⚠ Insufficient licenses: need 72, have 30
  ✓ Partial allocation: 2 instances, 24 licenses
```

### Common Issues

**Issue**: Workflow waiting for resources/licenses
```
⏳ wf-123 waiting for resources or licenses...
```
**Solution**: Check license pool capacity in `licenses.yaml` or free up compute resources.

**Issue**: License error
```
✗ License allocation failed: InsufficientTokens
```
**Solution**: Increase `total_tokens` in `licenses.yaml` or wait for workflows to complete.

**Issue**: No scale-up despite free resources
```
⏸ No scaling: insufficient resources or licenses
```
**Solution**: Either compute resources or licenses are insufficient. Check both.

## Performance Expectations

Based on the proposal analysis, LAMF is expected to achieve:

- **Cost Reduction**: 30-40% compared to static license-unaware scheduling
- **Deadline Compliance**: 95-98% (vs. 80-85% for static)
- **License Utilization**: 75-85% (vs. 60% for greedy backfilling)
- **Moldability Benefit**: 20-30% cost savings from dynamic reallocation

## Comparison with Other Approaches

| Scheduler               | Moldable | License-Aware | Cost Efficiency | Deadline Compliance |
|------------------------|----------|---------------|-----------------|---------------------|
| **LAMF**               | ✓        | ✓             | High (30-40%)   | High (95-98%)       |
| Vortex FCFS_Optimized  | ✓        | ✗             | Medium          | High (90-95%)       |
| Vortex FCFS_Scheduler  | ✗        | ✗             | Low             | Medium (85-90%)     |
| Schedulus Greedy       | ✗        | ✓             | Medium          | Medium (80-85%)     |

## Extending LAMF

### Adding New License Pools

1. Update `/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml`:
```yaml
pools:
  COMSOL:
    total_tokens: 100
    reserved_floor: 0
    burst_cap: null

policy:
  per_core:
    strategies:
      comsol:
        type: linear  # or powerlaw, ansys_workgroup
```

2. Add software_id mapping in `resource_manager_LA.py`:
```python
pool_map = {
    1: 'ANSYS',
    2: 'ABAQUS',
    3: 'LSDYNA',
    4: 'COMSOL'  # NEW
}
```

3. Update workflow YAML:
```yaml
constraints:
  license_pool: COMSOL
config:
  software_id: 4
```

### Implementing Other Algorithms

LAMF is one of 5 proposed algorithms. To implement others:

- **BMW (Bi-level Moldable Weighted Fair Queuing)**: See `license_aware_moldable_scheduling_proposals.md` Section 2
- **LAPMS (License-Aware Predictive Moldable Scheduler)**: Section 3
- **HLAM (Hybrid License-Aware Moldable)**: Section 4
- **LADDM (License-Aware Dual-Deadline Moldable)**: Section 5

## References

- **Proposal Document**: `license_aware_moldable_scheduling_proposals.md`
- **Vortex Analysis**: `vortex_seissol_analysis.md`
- **Schedulus Analysis**: `license_aware_scheduling_analysis.md`
- **Context Logs**: `contexts/context_1.log`, `contexts/context_2_ondemand_implementation.log`

## Contributors

Developed as part of PhD research on license-aware moldable scheduling for scientific workflows.

## License

Same as parent Vortex project.
