# HSM: Hybrid Static-Moldable Scheduler

**Full Name**: Hybrid Static-Moldable EDF with License Awareness (EDF-HSM-LA)
**Date**: November 2025
**Implementation**: `/src/main/scheduler/edf_hsm_LA.py`

---

## Algorithm Overview

HSM combines the **deadline reliability of static allocation** with the **cost efficiency of moldable scheduling** by using different strategies for different workflow phases.

**Core Innovation**:
- **Iteration 0**: Static allocation (upfront resource commitment)
- **Iterations 1-5**: Moldable allocation (dynamic EDF-LAMF scaling)

---

## Two-Phase Design

### Phase 1: Static (Iteration 0 Only)

**Objective**: Provide "deadline insurance" with upfront resource commitment

**Behavior**:
- Resources allocated once at workflow start
- No dynamic scaling during iteration 0
- Uses OPTIM factor 0.6 (60% of remaining budget/deadline)
- No triggers fire, no guards evaluated
- Workflow executes iteration 0 on committed resources

**Code Location**: `edf_hsm_LA.py:660-665`
```python
if ind == 0:  # Iteration 0
    print(f"📌 [HSM ITERATION-0] Static phase - no scaling")
    return  # Skip all moldable logic
```

**Why Static for Iteration 0?**
- Iteration 0 sets the pace for the entire workflow
- Delays in iteration 0 cascade to remaining iterations
- Upfront commitment prevents early deadline misses
- Avoids cold start and resource acquisition delays

---

### Phase 2: Moldable (Iterations 1-5)

**Objective**: Optimize cost and handle runtime variations dynamically

**Behavior**: Full EDF-LAMF v5.1 moldability
- **4 Deadline-Driven Triggers**:
  - CRITICAL: deadline_urgency < 30% (less than 30% time remaining)
  - WARNING: deadline_urgency < 50% AND falling behind
  - EARLY: time_progress > budget_progress + 3%
  - MID-ITERATION: iteration ≥ 2 AND time_progress > 40%

- **Graduated Boost Factors**:
  - CRITICAL mode: 2.0× budget boost
  - WARNING mode: 1.5× budget boost
  - Regular scale-up: 1.2× budget boost
  - Normal allocation: 1.0× (no boost)

- **6 Scale-Down Guards**:
  1. License pool saturation (>80% utilization)
  2. Late iteration (iteration > 2)
  3. Deadline proximity (<50% time remaining)
  4. Time progress (>70% elapsed)
  5. Budget/time progress (>40% consumed)
  6. Minimum instance protection (≤2 instances)

- **Progressive OPTIM Factors**:
  - Iteration 1: 0.7 (70% of remaining budget/deadline)
  - Iteration 2: 0.8
  - Iteration 3: 0.9
  - Iteration 4: 0.95
  - Iteration 5: 1.0 (100% - final aggressive scaling)

**Code Location**: `edf_hsm_LA.py:667-960` (unchanged from EDF-LAMF v5.1)

---

## Implementation Simplicity

**Lines of Code**: 5 new lines (plus documentation updates)

**Modification**: Single conditional check in `processFreeRequestWithLicenses()`
```python
# Insert at line 660 (before existing moldable logic)
if ind == 0:
    return  # Static phase - skip moldable logic
# Existing EDF-LAMF logic continues for iterations 1-5
```

**Code Reuse**: 100%
- Static allocation logic: Already exists in `scheduler_LA.py:checkResources()`
- Moldable logic: Complete EDF-LAMF v5.1 implementation
- No new helper functions needed
- No configuration changes required

---

## Workflow Iteration Structure

**Workflow Iterations**: 2-6 (randomized at generation)
- Min: 2 iterations (18.7% of workflows)
- Max: 6 iterations (23.4% of workflows)
- Median: ~4 iterations

**Iteration Indices**: 0, 1, 2, 3, 4, 5 (zero-based)

**HSM Phases by Iteration Count**:
```
2-iteration workflow:
  Iteration 0: STATIC
  Iteration 1: MOLDABLE

3-iteration workflow:
  Iteration 0: STATIC
  Iterations 1-2: MOLDABLE

6-iteration workflow (max):
  Iteration 0: STATIC
  Iterations 1-5: MOLDABLE
```

**Critical Design Note**: 23.4% of workflows execute iteration 5, so ALL iterations 1-5 must be moldable (not just 1-4).

---

## Expected Performance

### vs EDF-LAMF v5.1 (Moldable Baseline)

**Hypothesis**: HSM should have better deadline performance
- Static iteration 0 prevents early delays
- Expected improvement: 10-15% at 600-700wf

**Reasoning**:
- EDF-LAMF v5.1 deadline miss at 600-700wf: 19.8-30.7%
- Root cause: Reactive scaling fails when saturation occurs
- HSM solution: Proactive static commitment in iteration 0

### vs EDF-STATIC (Static Baseline)

**Hypothesis**: HSM should have better cost efficiency
- Moldability in iterations 1-5 optimizes resource usage
- Expected improvement: 3-5% cost reduction

**Reasoning**:
- Static allocates resources upfront for all iterations
- HSM only commits for iteration 0, adapts for iterations 1-5
- Moldable scaling uses fewer resources when deadlines comfortable

### Sweet Spot

**Expected Best Performance**: 400-600 workflows
- Below 400wf: All algorithms work well (low contention)
- 400-600wf: HSM combines static reliability + moldable efficiency
- Above 700wf: System capacity limits (licenses/compute exhausted)

---

## Key Advantages

1. **Deadline Protection**: Static iteration 0 provides baseline guarantee
2. **Cost Efficiency**: Moldable iterations 1-5 optimize resource usage
3. **Implementation Simplicity**: 5 lines of code, 100% reuse
4. **Low Risk**: Degrades to EDF-LAMF behavior if iteration 0 under-allocated
5. **License Awareness**: Full integration with license pool management
6. **Proven Components**: Combines two tested algorithms (static + EDF-LAMF)

---

## Key Design Decisions

### Why Iteration 0 Only (Not Iterations 0-1)?

**Considered**: Static for iterations 0-1, moldable for 2-5

**Rejected Because**:
- Iteration 1 already has OPTIM factor 0.7 (conservative allocation)
- EDF-LAMF triggers can handle iteration 1 effectively
- More iterations in moldable phase = more adaptation opportunities
- 2-iteration workflows would have NO moldable phase

**Decision**: Iteration 0 only provides sufficient deadline insurance

### Why Not Static for All Iterations?

**Answer**: That's just EDF-STATIC baseline (already exists)

**HSM Value**: Combines best of both worlds
- Static: Deadline reliability (iteration 0)
- Moldable: Cost efficiency (iterations 1-5)

### Why Reuse EDF-LAMF v5.1 (Not v4)?

**v5.1 Advantages**:
- 28% better deadline performance than v4 (16% → 11.5% at 400wf)
- Fixed trigger cancellation bug (31× more scale-ups)
- Proven stability at 200-500wf

**Design**: Build on best available moldable scheduler

---

## Testing Plan

### Validation Phase (200-300wf)
- 1 run per configuration
- Goal: Verify correctness, no crashes
- Expected: Similar performance to EDF-LAMF and EDF-STATIC

### Performance Phase (400-700wf)
- 3 runs per configuration (statistical significance)
- Goal: Demonstrate HSM advantage
- Expected: 10-15% deadline improvement vs EDF-LAMF

### Comparison Baselines
1. **EDF-LAMF v5.1** (moldable)
2. **EDF-STATIC** (static)

### Success Criteria
- ✅ No regressions at 200-500wf
- ✅ 10-15% deadline improvement at 600-700wf vs EDF-LAMF
- ✅ 3-5% cost reduction vs EDF-STATIC
- ✅ Handles all iteration counts (2-6) correctly

---

## Files

**Implementation**: `/src/main/scheduler/edf_hsm_LA.py`
**Entry Point**: `/src/main/simulate_main_LA.py`
**Configuration**: `/src/main/config/constants_LA.py`
**Results**: Will be saved as `EDF_HSM_{N}_*.csv`

---

## Usage

### Running HSM Scheduler

```bash
# Already configured in simulate_main_LA.py
cd /Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main

# Run simulation (uses HSM by default now)
python simulate_main_LA.py

# Results saved to license_results/
```

### Switching Schedulers

Edit `simulate_main_LA.py`:
```python
# For HSM:
from scheduler.edf_hsm_LA import EDF_HSM_LA
sched = EDF_HSM_LA(...)

# For EDF-LAMF v5.1:
from scheduler.edf_optimized_LA import EDF_Optimized_LA
sched = EDF_Optimized_LA(...)

# For EDF-STATIC:
from scheduler.edf_scheduler_LA import EDF_Scheduler_LA
sched = EDF_Scheduler_LA(...)
```

---

## Algorithm Pseudocode

```
FUNCTION HSM_Schedule(workflows):
    # Phase 1: EDF Ordering
    workflow_heap = sort_by_deadline(workflows)

    WHILE workflows_remaining:
        workflow = pop_earliest_deadline(workflow_heap)

        # Initial allocation (iteration 0)
        resources = allocate_static(workflow, OPTIM_factor=0.6)
        start_execution(workflow, resources)

        # Iteration-based resource adjustment
        FOR iteration IN 1 to workflow.total_iterations-1:

            # === STATIC PHASE ===
            IF iteration == 0:
                # No scaling - resources already committed
                CONTINUE

            # === MOLDABLE PHASE ===
            ELSE:
                # Calculate deadline urgency
                time_remaining = workflow.deadline - current_time
                deadline_urgency = time_remaining / total_time

                # Trigger checks (CRITICAL, WARNING, EARLY, MID-ITERATION)
                IF deadline_urgency < 0.30:
                    force_scale_up(workflow, boost=2.0)  # CRITICAL
                ELIF deadline_urgency < 0.50 AND falling_behind:
                    force_scale_up(workflow, boost=1.5)  # WARNING
                ELIF time_progress > budget_progress + 0.03:
                    force_scale_up(workflow, boost=1.2)  # EARLY
                ELIF iteration >= 2 AND time_progress > 0.40:
                    force_scale_up(workflow, boost=1.2)  # MID-ITERATION

                # Scale-down check (with 6 guards)
                IF NOT urgent AND can_reduce_resources:
                    IF all_guards_pass:
                        scale_down(workflow)
                    ELSE:
                        block_scale_down(reason)

                # Scale-up check (license-aware)
                IF need_more_resources:
                    new_resources = allocate_with_licenses(
                        workflow,
                        budget * OPTIM_factor[iteration] * boost
                    )
                    IF new_resources.available:
                        scale_up(workflow, new_resources)

        # Workflow completion
        release_resources(workflow)
        release_licenses(workflow)
```

---

## Related Documents

- **Full Analysis**: `/license_results/EDF-LAMF/EDF_Optimized_LA_Analysis.md` (EDF-LAMF v5.1)
- **v5.1 Results**: `/license_results/EDF-LAMF/EDF_LAMF_v5_1_Comprehensive_Analysis.md`
- **Novel Algorithms**: `/license_results/Novel_License_Aware_Scheduling_Algorithms.md`
- **Implementation**: `/src/main/scheduler/edf_hsm_LA.py`

---

## References

- EDF-LAMF v5.1: Base moldable scheduler (iterations 0-5 moldable)
- EDF-STATIC: Base static scheduler (no moldability)
- OPTIM Factors: `OPTIM_FCFS_BFACTOR` and `OPTIM_FCFS_DFACTOR` from `constants_LA.py`
- License Pools: ANSYS (6700), ABAQUS (2200), LSDYNA (7600) tokens

---

**Status**: ✅ Implemented (November 2025)
**Testing**: Pending
**Expected Impact**: 10-15% deadline improvement at 600-700wf vs EDF-LAMF
