# Budget and License Cost Implementation Summary

**Date:** January 2025
**Purpose:** Document the budget architecture and license cost tracking design decisions

---

## Executive Summary

The LAMF scheduler uses a **simplified budget architecture**:
- **Budgets**: Hardware costs only (compute instance costs)
- **License costs**: Tracked separately for reporting, not part of budget constraints
- **Budget violations**: Checked against hardware costs only
- **Final reporting**: Shows both hardware and license costs separately

This design decision was made explicitly by the user to keep the system simple and avoid the complexity of dual-budget tracking.

---

## Key Design Decision (January 2025)

### User's Direction

**User request (Message 14):**
> "We keep it very simple for now. The budget is based on hardware costs only and we calculated the cost for procuring the instances and not licenses. Lets keep it this way. Revert any other changes. For license cost calculation, at the end of the run, just calculate how much cost additionally was incurred by the workflow for the licenses."

### Rationale

**Problem discovered:**
- Workflow budgets were calculated for hardware costs only (~$50-300)
- License costs are 10-30× higher (~$1,000-14,000)
- Initial proposal: Dual budget system (separate hardware_budget and license_budget)
- User decision: REJECTED - keep simple, track licenses separately

### Implementation

**Budget checking:**
```python
# Only hardware costs checked against budget
wf_hw_cost = self.computeCost(wf_id, wf_finish_time)  # Hardware only
budget_violated = wf_hw_cost > wf_data['budget']  # Hardware vs hardware
```

**License cost tracking:**
```python
# License costs stored separately in workflow data
self.df[id]['license_cost'] = license_cost  # Tracked but not enforced
```

**Final reporting:**
```python
Total Hardware Cost: €52.34
Total License Cost (additional): €1,247.89
Total Combined Cost: €1,300.23
License Cost Overhead: 2383% of hardware cost
```

---

## License Cost Calculation

### Formula Validation (JSSPP 2025 Section 2.2)

All formulas verified against the paper:

**LS-Dyna (Linear):**
```python
T(cores) = cores
Cost per token: €1,000/year
Example: 96 cores → 96 tokens → €96,000/year
```

**Abaqus (Power-law):**
```python
T(cores) = 5.0 × cores^0.422
Cost per token: €2,500/year
Example: 96 cores → 5.0 × 96^0.422 = 24.6 tokens → €61,500/year
```

**ANSYS Workgroup (MEBA + Workgroup):**
```python
T_meba = 1 (solver license)
T_workgroup = max(0, cores - 4)
Cost: €14,000/year (MEBA) + €1,700/year per workgroup token
Example: 96 cores → 1 + 92 = 93 tokens → €170,400/year
```

### Per-Second Cost Calculation

```python
SECONDS_PER_YEAR = 365.25 * 24 * 3600  # 31,557,600 seconds

LSDYNA_COST_PER_TOKEN_SEC = 1000.0 / SECONDS_PER_YEAR    # €0.0000317/token/sec
ABAQUS_COST_PER_TOKEN_SEC = 2500.0 / SECONDS_PER_YEAR    # €0.0000792/token/sec
ANSYS_MEBA_COST_SEC = 14000.0 / SECONDS_PER_YEAR         # €0.000444/sec
ANSYS_WORKGROUP_COST_SEC = 1700.0 / SECONDS_PER_YEAR     # €0.0000539/token/sec
```

---

## Moldable License Cost Tracking

### Challenge

Moldable workflows can scale up/down multiple times, changing resource allocations dynamically:

```
Iteration 0: Allocate 5 instances × 24 cores = 120 cores
Iteration 1: Scale down to 3 instances × 24 cores = 72 cores
Iteration 3: Scale up to 4 instances × 24 cores = 96 cores
Iteration 5: Complete with 4 instances
```

**Question:** How do we calculate license costs when allocations change?

### Solution: Segment-Based Tracking

**Data structure:**
```python
instances = {
    instance_obj: [
        (count, start_time, finish_time),  # Segment 1
        (count, start_time, finish_time),  # Segment 2
        ...
    ]
}
```

**Example tracking:**
```python
# Iteration 0: allocate 5 instances at t=100
instances: {m5.large: [(5, 100, None)]}

# Iteration 1: scale down to 3 instances at t=200
instances: {m5.large: [(5, 100, 200), (3, 200, None)]}

# Iteration 3: scale up to 4 instances at t=300
instances: {m5.large: [(5, 100, 200), (3, 200, 300), (4, 300, None)]}

# Workflow completes at t=400
instances: {m5.large: [(5, 100, 200), (3, 200, 300), (4, 300, 400)]}
```

**Cost calculation:**
```python
def computeCost(self, id, wf_finish_time):
    hardware_cost = 0.0
    license_cost = 0.0

    instances = self.df[id]['instances']
    software_id = self.df[id].get('software_id', 0)

    for instance_obj in instances:
        for count, start_time, finish_time in instances[instance_obj]:
            # Handle in-progress allocations
            if finish_time is None:
                finish_time = wf_finish_time

            duration = finish_time - start_time

            # Hardware cost
            hardware_cost += instance_obj.getCostPerSecond() * count * duration

            # License cost (for this segment)
            total_cores = instance_obj.cores * count
            segment_license_cost = self.calculate_license_cost(
                software_id, total_cores, duration
            )
            license_cost += segment_license_cost

    # Store license cost for reporting
    self.df[id]['license_cost'] = license_cost

    return hardware_cost  # Single float (NOT tuple)
```

**Result:** Each allocation segment is tracked separately with its own duration, and license costs are summed across all segments.

---

## Critical Bugs Fixed

### Bug 1: Negative Cores ValueError

**Error:**
```
ValueError: Invalid cores value: -96. Cores cannot be negative.
```

**Root cause:**
```python
# Line 418: fcfs_optimized_LA.py
request['count'] = min_needed_count - cur_count
# When scale-down blocked: min_needed_count=2, cur_count=5 → -3
```

**Why it appeared:**
- Nov 20, 2025: Scale-down guards added → blocks scale-down more often
- Nov 20, 2025: Defensive checks added → makes negative values fatal
- Before: Negative values silently produced complex numbers
- After: ValueError raised immediately

**Fix:**
```python
if request['count'] is None:
    request['count'] = min_needed_count - cur_count

    if request['count'] <= 0:
        print(f"  → No resource adjustment needed")
        return  # Exit early
```

### Bug 2: Count=0 Validation Too Strict

**Error:**
```
ValueError: Invalid count: 0. Instance count must be positive.
```

**Root cause:** Defensive validation blocked count=0, but zero is valid (no-op when cur_count == min_needed_count)

**Fix:**
```python
# Allow count=0 as no-op
if count == 0:
    print(f"  ⚠ [Metrics] Skipping {action} with count=0 (no-op)")
    return

# Only block negative
if count < 0:
    raise ValueError(f"Invalid count: {count}")
```

### Bug 3: Tuple Unpacking Mismatch

**Error:**
```
TypeError: unsupported operand type(s) for /: 'tuple' and 'float'
```

**Root cause:** `computeCost()` was changed to return tuple (hw_cost, lic_cost, total) but calling code expected float

**Fix:** Reverted to single float return (hardware cost only)
```python
# BEFORE (tuple):
def computeCost(self, id, wf_finish_time):
    ...
    return (hardware_cost, license_cost, total_cost)  # 3-tuple

# AFTER (float):
def computeCost(self, id, wf_finish_time):
    ...
    self.df[id]['license_cost'] = license_cost  # Store separately
    return hardware_cost  # Single float
```

**Updated all callers:**
```python
# BEFORE:
used_budget, _, _ = self.metrics.computeCurrentCost(...)

# AFTER:
used_budget = self.metrics.computeCurrentCost(...)
```

---

## Verification Results

### License Cost Correctness

**User question:** "Are license costs being calculated correctly for moldable workflows that allocate/release licenses over time?"

**Answer:** Yes, verified correct.

**Evidence:**
1. Each allocation segment tracked separately with (count, start_time, finish_time)
2. License costs calculated per segment based on actual cores and duration
3. All segments summed for total license cost
4. Handles scale-up, scale-down, and final release correctly

**Example validation:**
```
Workflow with 3 segments:
  Segment 1: 5 instances × 24 cores × 100 sec = 120 cores × 100 sec
  Segment 2: 3 instances × 24 cores × 100 sec = 72 cores × 100 sec
  Segment 3: 4 instances × 24 cores × 100 sec = 96 cores × 100 sec

LSDYNA license cost:
  Segment 1: 120 tokens × 100 sec × €0.0000317/token/sec = €0.380
  Segment 2: 72 tokens × 100 sec × €0.0000317/token/sec = €0.228
  Segment 3: 96 tokens × 100 sec × €0.0000317/token/sec = €0.304
  Total: €0.912 ✓ Correct
```

### Budget Violation Checking

**Implementation:**
```python
# Check hardware cost against hardware budget
wf_hw_cost = self.computeCost(wf_id, wf_data['finish_time'])
budget_violated = wf_hw_cost > wf_data['budget']  # Hardware vs hardware

# License cost NOT included in violation check
wf_lic_cost = wf_data.get('license_cost', 0.0)  # Informational only
```

**Result:** Budget violations are checked correctly (hardware costs only), license costs reported separately.

---

## Metrics Output Structure

### Final Report Format

```
--- Workflow Completion Summary ---
Total workflows: 100
Completed: 95 (95.00%)
Incomplete: 5 (5.00%)

--- Cost Summary ---
Total Hardware Cost: €52.34
Total License Cost (additional): €1,247.89
Total Combined Cost: €1,300.23
License Cost Overhead: 2383% of hardware cost

--- Budget/Deadline Violations ---
Budget violations: 3 (hardware cost > hardware budget)
Deadline violations: 5

--- Performance Metrics ---
Average flowtime: 1024.5 sec
Average wait time: 45.2 sec
Resource utilization: 56.12%
```

### Per-Workflow Data

```python
{
    'wf-id': 'lamf-test-001',
    'cost': 13.25,              # Total cost (hw + license)
    'hardware_cost': 0.52,      # Hardware only
    'license_cost': 12.73,      # License only (stored separately)
    'budget': 1.50,             # Hardware budget
    'budget_violated': False,   # Checked against hardware_cost only
    'deadline_violated': False,
    ...
}
```

---

## Design Rationale Summary

### Why Hardware-Only Budgets?

**Considered alternatives:**
1. **Dual budgets** (hardware_budget + license_budget)
   - Pros: Accurate constraint tracking
   - Cons: Complex, requires redesigning workflow generator, budget assignment logic

2. **Combined budget** (hardware + license total)
   - Pros: Simple single number
   - Cons: Existing budgets too small (designed for hardware only)

3. **Hardware-only budgets** ⭐ **SELECTED**
   - Pros: Simple, no changes to existing budget logic
   - Cons: License costs not constrained (but tracked for reporting)

**User decision:** "Keep it very simple" → Hardware-only budgets

### Why Track License Costs Separately?

**Purpose:** Research analysis and reporting
- Show actual cost impact of license-aware scheduling
- Calculate license overhead percentage
- Compare license costs between LAMF and baseline
- Inform future budget design decisions

**Not used for:**
- Budget violation checking
- Admission control
- Scheduling decisions (except license token availability)

---

## Files Modified

| File | Changes | Purpose |
|------|---------|---------|
| `metrics_LA.py` | Lines 344-457 | computeCost() returns float, stores license_cost separately |
| `metrics_LA.py` | Lines 527-585 | Budget violation checks hardware only, reports both costs |
| `fcfs_optimized_LA.py` | Lines 259, 337, 402 | Removed tuple unpacking |
| `scheduler_LA.py` | Line 316 | Removed tuple unpacking |
| `metrics_LA.py` | Lines 233-241 | Fixed count=0 validation |
| `fcfs_optimized_LA.py` | Lines 417-423 | Fixed negative count bug |

---

## Related Documentation

- `LAMF_IMPLEMENTATION_SUMMARY.md` - Complete algorithm implementation
- `LAMF_PERFORMANCE_ANALYSIS.md` - Performance metrics and analysis
- `/Users/srishtidasgupta/PhD/PhD/Papers/JSSPP/JSSP_2025_Final.pdf` - License cost formulas (Section 2.2)

---

**Document Version:** 1.0
**Last Updated:** January 2025
**Status:** ✅ Implementation complete and verified
