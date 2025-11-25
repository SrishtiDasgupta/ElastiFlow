# LAMF Scheduling Architecture Analysis

**License-Aware Moldable First-Come-First-Served (LAMF) Scheduler**

**Date:** November 2025
**System:** Vortex-moldable-sched with License-Aware extensions

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Components](#architecture-components)
3. [Scheduler Variants](#scheduler-variants)
4. [Resource Allocation Mechanisms](#resource-allocation-mechanisms)
5. [Resource Deallocation Mechanisms](#resource-deallocation-mechanisms)
6. [Moldability: Scale-Up and Scale-Down](#moldability-scale-up-and-scale-down)
7. [License Management Integration](#license-management-integration)
8. [Key Algorithms](#key-algorithms)
9. [Summary](#summary)

---

## Overview

The License-Aware (LA) scheduling system extends Vortex's moldable scheduling with **dual-resource constraints**: both compute resources (instances) and software licenses must be allocated together. The system supports two scheduler variants:

1. **Static FCFS** (`fcfs_scheduler_LA.py`) - Resources allocated once at workflow start
2. **Moldable FCFS / LAMF** (`fcfs_optimized_LA.py`) - Dynamic scaling between iterations

Both variants coordinate compute and license allocation using a **two-phase commit protocol** to ensure atomicity.

---

## Architecture Components

### Core Classes

```
Scheduler_LA (Abstract Base)
├── FCFS_Scheduler_LA (Static)
└── FCFS_Optimized_LA (Moldable/LAMF)

ResourceManager_LA
├── Extends ResourceManager
└── Integrates LicenseManager

MetricsLA
└── Tracks dual-resource costs
```

### Key Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **Scheduler Base** | `scheduler_LA.py` | License allocation/deallocation logic, two-phase commit |
| **Static FCFS** | `fcfs_scheduler_LA.py` | Non-moldable FCFS with licenses |
| **LAMF** | `fcfs_optimized_LA.py` | Moldable FCFS with iteration-weighted scaling |
| **Resource Manager** | `resource_manager_LA.py` | Tracks workflow state + license holds |
| **Metrics** | `metrics_LA.py` | Cost calculation for dual resources |
| **License Manager** | `resource_manager/license/manager.py` | Token accounting, hold/commit/release |

---

## Scheduler Variants

### 1. Static FCFS (`fcfs_scheduler_LA.py`)

**Characteristics:**
- Resources allocated **once** at workflow submission
- No dynamic scaling during execution
- Both compute and licenses held for entire workflow duration
- Simpler logic, lower overhead

**Use Case:** Workflows with predictable resource needs, stable license requirements

### 2. Moldable FCFS / LAMF (`fcfs_optimized_LA.py`)

**Characteristics:**
- **Dynamic scaling** between workflow iterations
- Iteration-weighted budget/deadline allocation (OPTIM_FCFS factors)
- Scale-up when time/budget permits
- Scale-down when resources exceed need
- License allocations adjusted with compute scaling

**Use Case:** Long-running workflows with varying resource needs, license cost optimization

---

## Resource Allocation Mechanisms

### Initial Allocation Flow

Both schedulers use `allocateResourcesWithLicenses()` for initial allocation:

```
┌─────────────────────────────────────────────────────┐
│ 1. Extract Constraints                              │
│    - min_instances, budget, deadline                │
│    - license_pool (ANSYS/ABAQUS/LSDYNA)             │
│    - software_id, chains, mesh                      │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│ 2. Check Compute Resources                          │
│    - Query free instances                           │
│    - Match min_instances requirement                │
│    - Calculate total_cores                          │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│ 3. Calculate License Requirement                    │
│    - tokens = calculate_tokens(pool, cores, chains) │
│    - Uses pool-specific strategy:                   │
│      * ANSYS: MEBA formula                          │
│      * ABAQUS: Power law                            │
│      * LSDYNA: Linear (1 per core)                  │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│ 4. Two-Phase Commit                                 │
│    ┌──────────────────────────────────────┐         │
│    │ Phase 1: Hold                        │         │
│    │  - hold_id = license_manager.hold()  │         │
│    │  - Reserves tokens (5 min TTL)       │         │
│    └────────┬─────────────────────────────┘         │
│             │                                        │
│    ┌────────▼─────────────────────────────┐         │
│    │ Phase 2: Allocate Compute            │         │
│    │  - ips = allocateResources()         │         │
│    │  - Actual instance allocation        │         │
│    └────────┬─────────────────────────────┘         │
│             │                                        │
│             │ Success?                               │
│             ├─────────────┬──────────────┐           │
│             │ YES         │ NO           │           │
│    ┌────────▼────────┐ ┌──▼──────────┐  │           │
│    │ commit(hold_id) │ │release(hold)│  │           │
│    │ Make permanent  │ │Abort txn    │  │           │
│    └─────────────────┘ └─────────────┘  │           │
└─────────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│ 5. Track Workflow State                             │
│    - resource_manager.addWorkflow(                  │
│        instances, budget, deadline,                 │
│        software_id, license_holds                   │
│      )                                              │
│    - metrics.addToDataframe(...)                    │
└─────────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│ 6. Send to Executor                                 │
│    - sendWorkflowForExecution(ips, license_holds)   │
│    - Executor receives compute IPs + license info   │
└─────────────────────────────────────────────────────┘
```

### Allocation Decision Points

**Compute Availability:**
- On-prem: Prefer full allocation on single on-prem pool (148 instances × 48 cores)
- Cloud: Fallback to reserved/on-demand instances if on-prem unavailable

**License Availability:**
- Check `available_tokens >= licenses_needed`
- If insufficient: REJECT workflow (prevents starvation)
- Exception: Moldable variant attempts **partial allocation** (see below)

---

## Resource Deallocation Mechanisms

### Workflow Completion (Full Release)

```
┌─────────────────────────────────────────────────────┐
│ Executor sends completion signal                    │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│ processJobCompletion()                              │
│  ┌─────────────────────────────────────────┐        │
│  │ 1. resource_manager.returnResources(id) │        │
│  │    - Free compute instances             │        │
│  │    - Release IPs back to pool           │        │
│  └────────────┬────────────────────────────┘        │
│               │                                      │
│  ┌────────────▼────────────────────────────┐        │
│  │ 2. releaseLicensesForWorkflow(id)       │        │
│  │    - Get all hold_ids for workflow      │        │
│  │    - license_manager.release(hold_id)   │        │
│  │    - Tokens return to pool              │        │
│  └────────────┬────────────────────────────┘        │
│               │                                      │
│  ┌────────────▼────────────────────────────┐        │
│  │ 3. metrics.updateDataframe()            │        │
│  │    - Record finish_time, complete=True  │        │
│  │    - Calculate final cost               │        │
│  └─────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────┘
```

**Key Point:** Compute and licenses are **always released together** on workflow completion.

---

## Moldability: Scale-Up and Scale-Down

**Only in LAMF (`fcfs_optimized_LA.py`)** - Static FCFS does NOT scale.

### When Moldability Triggers

Between each workflow iteration, the executor sends a **resource request** to the scheduler. LAMF evaluates:

1. **Should we scale DOWN?** (free resources)
2. **Should we scale UP?** (allocate more resources)

### Decision Algorithm

```python
# Iteration-weighted constraints
available_time = (deadline - DEADLINE_BUFFER - current_time) × OPTIM_FCFS_DFACTOR[iteration]
available_budget = (budget - used_cost) × OPTIM_FCFS_BFACTOR[iteration]

# Current resources
current_instances = len(allocated_instances)
current_cores = sum(instance.cores × count)

# Minimum needed resources
min_needed_instances = calculate_min_for_deadline(available_time)

if current_instances > min_needed_instances:
    SCALE_DOWN()
elif current_instances < min_needed_instances AND budget_available:
    SCALE_UP()
else:
    NO_CHANGE()
```

### Scale-Down Flow

```
┌─────────────────────────────────────────────────────┐
│ freeResourcesWithLicenses()                         │
│                                                     │
│  ┌──────────────────────────────────────┐           │
│  │ 1. Free Compute (LIFO)               │           │
│  │    - Free last N instances           │           │
│  │    - Return IPs to pool              │           │
│  └────────┬─────────────────────────────┘           │
│           │                                          │
│  ┌────────▼─────────────────────────────┐           │
│  │ 2. Calculate Freed Licenses          │           │
│  │    cores_freed = sum(freed × cores)  │           │
│  │    tokens = calculate_tokens(cores)  │           │
│  └────────┬─────────────────────────────┘           │
│           │                                          │
│  ┌────────▼─────────────────────────────┐           │
│  │ 3. Release License Hold (LIFO)       │           │
│  │    hold_id = workflow_holds.pop()    │           │
│  │    license_manager.release(hold_id)  │           │
│  └──────────────────────────────────────┘           │
└─────────────────────────────────────────────────────┘
```

**Resource Freeing Strategy:**
- **LIFO (Last-In-First-Out)**: Most recently allocated instances freed first
- Rationale: Minimize disruption, free expensive resources first (cloud before on-prem)

### Scale-Up Flow

```
┌─────────────────────────────────────────────────────┐
│ checkNewResourcesWithLicenses()                     │
│                                                     │
│  ┌──────────────────────────────────────┐           │
│  │ 1. Check Available Compute           │           │
│  │    - Query free instances            │           │
│  │    - Respect budget constraint       │           │
│  │    - Prefer runtime-similar instances│           │
│  └────────┬─────────────────────────────┘           │
│           │                                          │
│  ┌────────▼─────────────────────────────┐           │
│  │ 2. Calculate License Need            │           │
│  │    new_cores = sum(new_inst × cores) │           │
│  │    tokens = calculate_tokens(...)    │           │
│  └────────┬─────────────────────────────┘           │
│           │                                          │
│           │ Licenses available?                     │
│           ├───────────┬─────────────────┐           │
│           YES         NO                │           │
│  ┌────────▼────────┐ ┌▼─────────────┐  │           │
│  │ Hold & Commit   │ │Try Partial   │  │           │
│  │ Full allocation │ │Allocation    │  │           │
│  └────────┬────────┘ └┬─────────────┘  │           │
│           │           │                │           │
│  ┌────────▼───────────▼─────────────┐  │           │
│  │ 3. Allocate Compute              │  │           │
│  │    ips = allocateResources()     │  │           │
│  └────────┬─────────────────────────┘  │           │
│           │                            │           │
│  ┌────────▼─────────────────────────┐  │           │
│  │ 4. Update Workflow State         │  │           │
│  │    - Add new instances           │  │           │
│  │    - Append new license_holds    │  │           │
│  └────────┬─────────────────────────┘  │           │
│           │                            │           │
│  ┌────────▼─────────────────────────┐  │           │
│  │ 5. Send to Executor              │  │           │
│  │    sendNewResources(ips, holds)  │  │           │
│  └──────────────────────────────────┘  │           │
└─────────────────────────────────────────────────────┘
```

### Partial Allocation (License-Constrained)

When full allocation exceeds available licenses, LAMF attempts **partial allocation**:

```python
def findLicenseFeasibleAllocation(instances, available_licenses):
    feasible = []
    for inst, count in instances:
        for i in range(count):
            test_cores = sum(feasible_cores) + inst.cores
            test_tokens = calculate_tokens(test_cores)

            if test_tokens <= available_licenses:
                feasible.append((inst, 1))
            else:
                break  # Stop when license limit reached

    return feasible
```

**Example:**
- Requested: 10 instances × 48 cores = 480 cores → 474 ANSYS tokens
- Available: 200 tokens
- Allocated: 4 instances × 48 cores = 192 cores → 189 tokens ✓

---

## License Management Integration

### License Pool Configuration

Three license pools (from `config/licenses.yaml`):

| Pool | Capacity | Strategy | Formula |
|------|----------|----------|---------|
| **ANSYS** | 33,600 tokens | MEBA (Workgroup) | `5×cores - 196` |
| **ABAQUS** | 33,600 tokens | Power Law | `a×cores^b` |
| **LSDYNA** | 33,600 tokens | Linear | `1×cores` |

### Two-Phase Commit Protocol

**Why needed?** Prevents deadlock and resource leaks.

```
Scenario without 2PC:
  1. Allocate compute ✓
  2. Try allocate licenses ✗ (fail)
  3. Compute resources leaked! (not freed)

With 2PC:
  1. Hold licenses (reversible) ✓
  2. Allocate compute
     - Success: commit licenses ✓
     - Failure: release hold ✓
```

### Hold Lifecycle

```
┌────────────┐     ┌───────────┐     ┌──────────┐
│  AVAILABLE │────→│  HELD     │────→│ALLOCATED │
│  (pool)    │hold │ (reserved)│commit│  (used)  │
└────────────┘     └─────┬─────┘     └────┬─────┘
                         │                 │
                         │ TTL expired     │release
                         │ or explicit     │
                         │ release         │
                         ▼                 ▼
                   ┌─────────────────────────┐
                   │     AVAILABLE (pool)    │
                   └─────────────────────────┘
```

**Hold TTL:** 5 minutes (prevents leaked holds if scheduler crashes)

### License Calculation Examples

**ANSYS (MEBA):**
```
240 cores → 5×240 - 196 = 1004 tokens
```

**Partial Allocation Logic:**
```
If available_tokens < needed_tokens:
    Try smaller allocation until feasible
```

---

## Key Algorithms

### 1. Iteration-Weighted Allocation (OPTIM_FCFS)

LAMF allocates budget/deadline **non-uniformly** across iterations:

```python
# From config/constants.py
OPTIM_FCFS_BFACTOR = [0.5, 0.3, 0.2]  # Budget weights per iteration
OPTIM_FCFS_DFACTOR = [0.5, 0.3, 0.2]  # Deadline weights per iteration

# Example: 3-iteration workflow, $100 budget, 1000s deadline
iteration_0: budget = $100 × 0.5 = $50, time = 1000 × 0.5 = 500s
iteration_1: budget = $100 × 0.3 = $30, time = 1000 × 0.3 = 300s
iteration_2: budget = $100 × 0.2 = $20, time = 1000 × 0.2 = 200s
```

**Rationale:** Allocate more resources early when workflow has flexibility.

### 2. Impossible Allocation Detection

Prevents infinite waiting:

```python
if licenses_needed > pool_capacity:
    rejected_workflows.add(wf_id)
    print(f"REJECTED - needs {licenses_needed}, pool has {pool_capacity}")
    remove_from_queue()
```

**Example:**
- Workflow needs 40,000 ANSYS tokens
- Pool capacity: 33,600 tokens
- **REJECTED** immediately (don't wait)

### 3. Resource Freeing Priority (LIFO)

```python
# Free most recently allocated instances first
for i in range(len(instances)-1, -1, -1):  # Reverse order
    instance, count, ips = instances[i]
    free(instance, count, ips[-count:])  # Free from end of list
```

**Rationale:**
- Cloud instances (more expensive) allocated last → freed first
- On-prem instances (cheaper) preserved

---

## Summary

### Static FCFS (`fcfs_scheduler_LA.py`)

| Aspect | Behavior |
|--------|----------|
| **Allocation** | Once at workflow start |
| **Deallocation** | Once at workflow completion |
| **Moldability** | ❌ None |
| **License Strategy** | Hold entire duration |
| **Complexity** | Low |
| **Use Case** | Predictable workloads |

### LAMF (`fcfs_optimized_LA.py`)

| Aspect | Behavior |
|--------|----------|
| **Allocation** | Initial + dynamic scale-ups |
| **Deallocation** | Dynamic scale-downs + completion |
| **Moldability** | ✅ Between iterations |
| **License Strategy** | Dynamic holds, LIFO release |
| **Complexity** | High |
| **Use Case** | Variable workloads, cost optimization |

### Resource Lifecycle

```
Workflow Submission
       │
       ▼
┌──────────────────┐
│ Initial Alloc    │  Compute + Licenses (2PC)
└────┬─────────────┘
     │
     ▼
┌──────────────────┐  ◄─── MOLDABLE ONLY
│  Iteration 0     │
└────┬─────────────┘
     │
     ▼
┌──────────────────┐
│ Scale Decision   │  Scale-up or Scale-down?
└────┬─────────────┘
     │
     ▼
┌──────────────────┐
│  Iteration 1     │
└────┬─────────────┘
     │
     ▼
    ...
     │
     ▼
┌──────────────────┐
│ Workflow Done    │  Release ALL (compute + licenses)
└──────────────────┘
```

### Key Design Principles

1. **Atomicity:** Two-phase commit ensures compute and licenses allocated together
2. **Fairness:** FCFS order prevents starvation
3. **Efficiency:** Moldability reduces waste through dynamic scaling
4. **Safety:** Impossible allocation detection prevents deadlocks
5. **License-Awareness:** All resource decisions account for license constraints

---

## Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `scheduler_LA.py` | 449 | Base class, 2PC logic, license allocation |
| `fcfs_scheduler_LA.py` | 154 | Static FCFS implementation |
| `fcfs_optimized_LA.py` | 494 | LAMF moldable implementation |
| `resource_manager_LA.py` | ~200 | Workflow state + license tracking |
| `metrics_LA.py` | 298 | Dual-resource cost calculation |
| `license/manager.py` | ~800 | License pool management, token calculation |

---

**End of Analysis**
