# LAMF Algorithm Analysis

## Overview
**LAMF (License-Aware Moldable FCFS)** extends traditional moldable scheduling with dual-resource constraints: **compute resources** (CPU cores) AND **software licenses**. It dynamically scales workflows up/down between iterations while respecting both resource types.

---

## 1. Moldability Mechanism

### Core Moldable Logic (`fcfs_optimized_LA.py:194-287`)

The moldability is implemented in `processFreeRequestWithLicenses()` which runs between workflow iterations:

**Iteration-Weighted Constraints:**
```python
# Budget constraint (fraction of remaining budget available per iteration)
available_budget = max(0, budget - used_budget) * OPTIM_FCFS_BFACTOR[ind]

# Deadline constraint (fraction of remaining time available per iteration)
available_time = max(0, deadline - DEADLINE_BUFFER - current_time) * OPTIM_FCFS_DFACTOR[ind]
```

These factors (`BFACTOR` and `DFACTOR`) progressively tighten constraints in later iterations to avoid deadline violations.

### Scale-Down Decision (`fcfs_optimized_LA.py:226-245`)

**Algorithm:**
1. Try running with fewer chains per node (3 → 2 → 1)
2. Calculate runtime: `chains_per_node × runtime_per_model × tinyda_iterations`
3. If runtime < available_time, scale down is possible
4. Free excess instances **LIFO** (Last-In-First-Out)
5. Release corresponding licenses proportionally

**Dual-Resource Release (`fcfs_optimized_LA.py:422-472`):**
```python
# Free compute resources
for instance in instances (LIFO order):
    free min(request['count'], count) instances

# Free proportional licenses
licenses_to_free = calculate_tokens(pool, total_cores_freed, chains=1)
hold_id = license_holds.pop()  # LIFO release
license_manager.release(hold_id)
```

### Scale-Up Decision (`fcfs_optimized_LA.py:252-286`)

**Algorithm:**
1. Calculate additional instances needed: `min_needed_count - current_count`
2. Check available compute resources
3. **NEW:** Check available licenses for proposed allocation
4. If both constraints satisfied:
   - Allocate compute resources
   - Hold and commit licenses (two-phase)
   - Send new resources to executor
5. If licenses insufficient, attempt **partial allocation**

### Partial Allocation (`fcfs_optimized_LA.py:378-420`)

**Unique LAMF Feature:** When licenses are insufficient for desired allocation, find the largest subset of instances that fits:

```python
def findLicenseFeasibleAllocation(instances, available_licenses, license_pool):
    feasible = []
    for inst, count in instances:
        for i in range(count):
            test_cores = sum(cores) + inst.cores
            test_licenses = calculate_tokens(pool, test_cores, chains=1)

            if test_licenses <= available_licenses:
                feasible.append(inst)
            else:
                break  # Stop adding instances

    return feasible
```

This ensures workflows make progress even with license contention.

---

## 2. Token Calculation for Each License Type

Token calculation is **software-specific** and implemented in `license/policy.py`.

### Configuration (`licenses.yaml:29-44`)
```yaml
policy:
  mode: per_core
  rounding: ceil
  per_core:
    strategies:
      lsdyna:
        type: linear
      abaqus:
        type: powerlaw
        a: 5.0
        b: 0.422
        min_tokens: 5
      ansys:
        type: ansys_workgroup
```

### 1. **LS-Dyna (Linear Strategy)** (`policy.py:69-70`)

**Formula:**
```python
tokens = cores
```

**Example:**
- 96 cores → 96 tokens
- 48 cores → 48 tokens

**Use case:** Simple 1:1 core-to-token mapping.

---

### 2. **ABAQUS (Power-Law Strategy)** (`policy.py:72-79`)

**Formula:**
```python
tokens = ceil(max(min_tokens, a × cores^b))
```

**Default Parameters:**
- `a = 5.0`
- `b = 0.422` (sublinear scaling)
- `min_tokens = 5`

**Examples:**
- 4 cores → `max(5, 5.0 × 4^0.422)` = `max(5, 8.36)` = `9 tokens`
- 96 cores → `max(5, 5.0 × 96^0.422)` = `max(5, 25.57)` = `26 tokens`
- 256 cores → `max(5, 5.0 × 256^0.422)` = `max(5, 36.17)` = `37 tokens`

**Rationale:** Sublinear scaling (b < 1) means token requirements grow slower than core count, reflecting diminishing marginal license requirements for large parallel jobs.

---

### 3. **ANSYS Workgroup** (`policy.py:81-85`)

**Formula:**
```python
tokens = 1 + max(0, cores - 4)
```

**Examples:**
- 2 cores → `1 + 0` = `1 token` (MEBA baseline)
- 4 cores → `1 + 0` = `1 token`
- 8 cores → `1 + 4` = `5 tokens`
- 96 cores → `1 + 92` = `93 tokens`

**Rationale:** 1 MEBA (Mechanical Enterprise Bundle Advanced) license + 1 additional token per core beyond 4.

---

### 4. **ANSYS Packs** (`policy.py:87-92`)

**Formula:**
```python
tokens = 1 + (0 if cores ≤ 4 else ceil(log₂(cores / 4)))
```

**Examples:**
- 4 cores → `1 + 0` = `1 token`
- 8 cores → `1 + ceil(log₂(2))` = `1 + 1` = `2 tokens`
- 16 cores → `1 + ceil(log₂(4))` = `1 + 2` = `3 tokens`
- 64 cores → `1 + ceil(log₂(16))` = `1 + 4` = `5 tokens`
- 256 cores → `1 + ceil(log₂(64))` = `1 + 6` = `7 tokens`

**Rationale:** 1 MEBA + doubling packs (logarithmic scaling). Much more efficient than Workgroup for large core counts.

---

## 3. License Manager (`license/manager.py`)

### Two-Phase Commit Pattern

Prevents race conditions during allocation:

```python
# Phase 1: Reserve tokens (uncommitted)
hold_id = license_manager.hold(pool='ANSYS', amount=26, owner='wf-123', ttl=300)

# Phase 2: Convert to allocation
license_manager.commit(hold_id)

# Later: Release
license_manager.release(hold_id)
```

**State Tracking (`manager.py:51-78`):**
- `total_tokens`: Pool capacity (e.g., ANSYS: 150)
- `allocated`: Committed tokens
- `held`: Uncommitted reservation tokens
- `available = total - allocated - held - reserved_floor`

### LIFO Release Policy (`manager.py:227-228`)

```python
# Track allocation order per pool
self.allocation_order[pool].append(hold_id)  # On commit

# Release most recent first
self.allocation_order[pool].remove(hold_id)  # On release
```

**Rationale:** When workflows scale down, release the most recently allocated licenses first. This maintains temporal locality and can reduce fragmentation.

---

## 4. Key Integration Points

### Workflow Submission (`scheduler_LA.py`)
```python
(instances, budget, deadline, start_time, mesh, software_id, license_holds)
```

**Software ID Mapping (`manager.py:126-141`):**
- `0` → No licenses required
- `1` → ANSYS pool
- `2` → ABAQUS pool
- `3` → LSDYNA pool

### Dual-Resource Allocation (`fcfs_optimized_LA.py:120`)
```python
ips, alloc_resources, license_holds = allocateResourcesWithLicenses(constraints)

if ips and license_holds:
    # Success - send to executor with both resources
    sendWorkflowForExecution(wf_plan, ips, sim, deadline, license_holds)
else:
    # Check if impossible allocation (exceeds pool capacity)
    if licenses_needed > pool_status['total']:
        rejected_workflows.add(wf_plan['id'])
```

---

## 5. Configuration Example

**Pool Capacities (`licenses.yaml:6-18`):**
```yaml
pools:
  ANSYS:   total_tokens: 150
  ABAQUS:  total_tokens: 100
  LSDYNA:  total_tokens: 120
```

**Token Calculation Example:**
- ANSYS workflow with 96 cores:
  - Strategy: `ansys_workgroup`
  - Tokens: `1 + (96 - 4)` = `93 tokens`
  - Available: Check `150 - allocated - held ≥ 93`

- ABAQUS workflow with 96 cores:
  - Strategy: `powerlaw (a=5.0, b=0.422)`
  - Tokens: `ceil(max(5, 5.0 × 96^0.422))` = `26 tokens`
  - Available: Check `100 - allocated - held ≥ 26`

---

## 6. Algorithm Flow Summary

### Initial Allocation
1. Workflow arrives in queue
2. Extract constraints (min_instances, budget, deadline, license_pool, software_id)
3. Calculate required licenses: `calculate_tokens(pool, total_cores, chains)`
4. Check dual availability:
   - Compute: `sum(instance.getFreeSlots()) >= min_instances`
   - Licenses: `get_available_tokens(pool) >= required_tokens`
5. If both satisfied:
   - Allocate compute instances
   - Hold + commit licenses (two-phase)
   - Send to executor with both resources
6. If insufficient licenses:
   - Attempt partial allocation
   - Or wait and retry

### Between-Iteration Moldability
1. Executor sends resource request at iteration boundary
2. Calculate iteration-weighted constraints:
   - `available_budget = remaining_budget × BFACTOR[iteration]`
   - `available_time = remaining_time × DFACTOR[iteration]`
3. **Scale-down check:**
   - Can we fit in available_time with fewer instances?
   - If yes: Free instances + release licenses (LIFO)
4. **Scale-up check:**
   - Do we need more instances to meet deadline?
   - Check compute availability
   - Check license availability for new allocation
   - If both satisfied: Allocate instances + hold/commit new licenses
   - If licenses insufficient: Attempt partial allocation
5. Update workflow state and continue execution

### Workflow Completion
1. Executor notifies completion
2. Free all compute instances
3. Release all license holds (via `releaseLicensesForWorkflow()`)
4. Update metrics and billing

---

## 7. Key Differences from Base Moldable Scheduling

| Aspect | Base Moldable (fcfs_optimized.py) | LAMF (fcfs_optimized_LA.py) |
|--------|-----------------------------------|------------------------------|
| **Resource Constraints** | Compute only | Compute + Licenses |
| **Allocation** | Single-resource check | Dual-resource check with two-phase commit |
| **Scale-up** | Allocate if compute available | Allocate if BOTH compute AND licenses available |
| **Scale-down** | Free compute instances | Free compute + proportionally release licenses |
| **Partial Allocation** | Not supported | Find largest license-feasible subset |
| **Rejection Logic** | Compute capacity only | Check both compute and license pool capacity |
| **State Tracking** | `(instances, budget, deadline, start_time, mesh)` | `(instances, budget, deadline, start_time, mesh, software_id, license_holds)` |

---

## 8. Performance Considerations

### Advantages
1. **Dynamic adaptation:** Workflows adjust to both compute and license availability
2. **Partial allocation:** Makes progress even under license contention
3. **LIFO release:** Maintains temporal locality, reduces fragmentation
4. **Two-phase commit:** Prevents race conditions in multi-workflow scenarios
5. **Iteration-weighted constraints:** Progressive tightening prevents deadline violations

### Challenges
1. **Complexity:** Dual-resource management increases scheduling overhead
2. **License contention:** High-demand workflows may experience starvation
3. **Partial allocation trade-offs:** May not achieve optimal speedup
4. **Token calculation overhead:** Per-allocation policy evaluation

### Optimization Opportunities
1. **License prediction:** Forecast future license demand to avoid contention
2. **Fairness policies:** Implement max_consecutive_starts_per_pool from config
3. **Preemption:** Allow high-priority workflows to reclaim licenses
4. **Dynamic pool rebalancing:** Shift tokens between pools based on demand

---

## Summary

**LAMF's novelty** lies in:

1. **Iteration-weighted moldability**: Dynamically adjusts resources between iterations using `BFACTOR`/`DFACTOR` weights
2. **Dual-resource constraints**: Simultaneously manages compute AND licenses
3. **Partial allocation**: When licenses are scarce, allocates largest feasible subset rather than failing
4. **Software-specific token policies**: Supports linear, power-law, and ANSYS-specific calculations
5. **Two-phase commit + LIFO**: Prevents races and maintains temporal locality

The algorithm ensures workflows can adapt to both compute and license availability while maintaining deadline/budget constraints through iteration-weighted resource decisions.
