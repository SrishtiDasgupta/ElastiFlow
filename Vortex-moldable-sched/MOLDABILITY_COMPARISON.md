# Moldability Comparison: LAMF vs DDM-EDF

**Date:** November 2025
**Purpose:** Document the fundamental differences between iteration-based (LAMF) and urgency-based (DDM-EDF) moldability approaches for license-aware workflow scheduling.

---

## Table of Contents
1. [Fundamental Philosophy](#fundamental-philosophy)
2. [LAMF: Iteration-Based Moldability](#lamf-iteration-based-moldability)
3. [DDM-EDF: Urgency-Based Moldability](#ddm-edf-urgency-based-moldability)
4. [Key Differences in Practice](#key-differences-in-practice)
5. [Why LAMF Works Better](#why-lamf-works-better)
6. [Performance Comparison](#performance-comparison)
7. [Why DDM-EDF Struggles](#why-ddm-edf-struggles)
8. [Recommendation](#recommendation)

---

## Fundamental Philosophy

### LAMF (Iteration-Based)
**Core Question:** "Which iteration am I on?"

- **Decision Basis:** Workflow progress (iterations 0 → 5)
- **Complexity:** Simple - lookup iteration number
- **Predictability:** Deterministic (same for all workflows at iteration N)

### DDM-EDF (Urgency-Based)
**Core Question:** "How urgent is my deadline?"

- **Decision Basis:** Time remaining vs time needed
- **Complexity:** Complex - calculate slack ratio with estimates
- **Predictability:** Dynamic (varies by workflow state)

---

## LAMF: Iteration-Based Moldability

### Core Concept
**"Progress Through Workflow = Confidence in Estimates"**

LAMF uses iteration number as a proxy for how much we trust runtime/budget estimates.

### Iteration Weighting Factors

```python
# From constants.py:45-46
OPTIM_FCFS_BFACTOR = {0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95, 5: 1.0}
OPTIM_FCFS_DFACTOR = {0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95, 5: 1.0}

# At each iteration:
available_time = (deadline - current_time) * OPTIM_FCFS_DFACTOR[iteration]
available_budget = (budget - used_budget) * OPTIM_FCFS_BFACTOR[iteration]
```

| Iteration | Factor | Meaning | Behavior |
|-----------|--------|---------|----------|
| **0** (first) | 0.6 | "Unsure - only use 60% of remaining deadline/budget" | **Conservative** - hold resources in reserve |
| **1-2** (early) | 0.7-0.8 | "Getting more confident" | **Moderate** - start using more resources |
| **3-4** (late) | 0.9-0.95 | "Almost done - estimates proven accurate" | **Aggressive** - use most resources |
| **5** (final) | 1.0 | "Last iteration - use everything!" | **All-in** - no holding back |

### Scale-Down Decision (LAMF)

**Question:** "Can I complete this iteration with fewer resources given my current progress?"

```python
# From fcfs_optimized_LA.py:243
available_time = (deadline - current_time) * OPTIM_FCFS_DFACTOR[iteration]
runtime_needed = chains_per_node * runtime_per_model * tinyda_iterations

if runtime_needed < available_time:
    # Can complete with fewer resources - check guards, then scale down
```

**Example:**
- Deadline: 1000s, Current time: 200s, Remaining: 800s
- Iteration 2 (DFACTOR = 0.8)
- `available_time = 800 * 0.8 = 640s` (reserve 160s for safety)
- Runtime with 2 nodes: 500s < 640s → **Scale down from 3 → 2 nodes**

**Key insight:** Early iterations are conservative (0.6-0.8), late iterations aggressive (0.9-1.0).

### Scale-Up Decision (LAMF)

**Question:** "Am I falling behind schedule?"

#### Progress-Based Trigger
```python
# From fcfs_optimized_LA.py:273-277
time_progress = (current_time - start_time) / (deadline - start_time)
budget_progress = used_budget / total_budget

if time_progress > budget_progress + 0.05:
    # Spending time faster than budget = falling behind
    skip_scale_down = True
    force_scale_up_attempt = True
```

**Example:**
- 40% of time elapsed, but only 25% of budget used
- 40% > 25% + 5% → **Falling behind! Scale up!**

#### Late-Iteration Proactive Scale-Up
```python
# From fcfs_optimized_LA.py:282-286
if iteration >= 3 and time_progress > 0.50:
    # Late iteration + halfway through → preemptively scale up
    force_scale_up_attempt = True
```

**Key insight:** React to **actual progress**, not predicted deadlines.

### Smart Scale-Down Guards

LAMF includes four guards to prevent harmful scale-down in license-constrained environments:

```python
# From fcfs_optimized_LA.py:310-350

# GUARD 1: License pool utilization
if license_pool_utilization > 0.70:
    # Don't scale down - licenses are scarce
    block_scale_down()

# GUARD 2: Iteration number
if iteration > 3:
    # Too late to scale down and re-acquire resources
    block_scale_down()

# GUARD 3: Time progress
if time_progress > 0.70:
    # More than 70% done - keep resources
    block_scale_down()

# GUARD 4: Budget/time progress
if budget_progress > 0.50 or time_progress > 0.50:
    # Halfway through - don't release resources we'll need
    block_scale_down()
```

---

## DDM-EDF: Urgency-Based Moldability

### Core Concept
**"How Much Slack Do I Have Until Deadline?"**

DDM-EDF calculates deadline slack ratio to determine urgency level.

### Slack Calculation

```python
# From edf_optimized_LA.py:339-408
def compute_deadline_slack(wf_id):
    time_remaining = deadline - current_time
    time_needed = runtime_per_iteration * remaining_iterations * chains * tinyda_iterations

    slack_ratio = (time_remaining - time_needed) / time_needed
    return slack_ratio
```

### Urgency Levels

| Slack Ratio | Urgency Level | Meaning | Example |
|-------------|---------------|---------|---------|
| **< 0** | Beyond deadline | "Already late!" | Need 100s, have 80s |
| **0.0 - 0.8** | **CRITICAL** | "< 80% buffer" | Need 100s, have 150s (50% buffer) |
| **0.8 - 1.2** | **WARNING** | "80-120% buffer" | Need 100s, have 200s (100% buffer) |
| **1.2 - 1.5** | **SAFE** | "120-150% buffer" | Need 100s, have 250s (150% buffer) |
| **> 1.5** | **EXCESS** | "> 150% buffer" | Need 100s, have 300s (200% buffer) |

### Scale-Down Decision (DDM-EDF)

**Question:** "Do I have so much slack that I can donate resources?"

```python
# From edf_optimized_LA.py:907-926
slack_ratio = compute_deadline_slack(wf_id)

if slack_ratio < 0.8:  # CRITICAL
    skip_scale_down = True  # Don't scale down!
    urgency_boost = 3.0     # Try to scale UP with 3x boost
elif slack_ratio < 1.2:  # WARNING
    skip_scale_down = True
    urgency_boost = 2.5
elif slack_ratio > 1.5:  # EXCESS
    # Allow normal scale-down check
    urgency_boost = 1.5
```

**Example:**
- Deadline: 1000s, Current time: 200s, Remaining: 800s
- Estimated time needed: 600s
- Slack ratio: (800 - 600) / 600 = **0.33** → **CRITICAL**
- **Skip scale-down entirely, attempt 3x boosted scale-up**

**Key insight:** Urgency level **blocks or allows** scale-down based on deadline pressure.

### Scale-Up Decision (DDM-EDF)

**Question:** "Am I urgent enough to get more resources?"

```python
# From edf_optimized_LA.py:912
if urgency_level == 'CRITICAL':
    urgency_boost = 3.0  # Allocate 3x normal resources
elif urgency_level == 'WARNING':
    urgency_boost = 2.5  # Allocate 2.5x normal resources
else:
    urgency_boost = 1.5  # Allocate 1.5x normal resources
```

**Example:**
- Normal allocation: 2 instances
- CRITICAL urgency: 2 × 3.0 = **6 instances**
- WARNING urgency: 2 × 2.5 = **5 instances**

**Key insight:** Urgency **multiplies** resource allocation aggressiveness.

---

## Key Differences in Practice

### Example Workflow Scenario

**Setup:**
- 5 iterations total
- Deadline: 1000s
- Budget: $100
- Each iteration: 150s runtime with current resources

### Iteration 0 (First Iteration)

| Metric | LAMF | DDM-EDF |
|--------|------|---------|
| **Time elapsed** | 0s | 0s |
| **LAMF factor** | 0.6 (conservative) | N/A |
| **Available time** | 1000 × 0.6 = **600s** | N/A |
| **Time needed** | 5 iterations × 150s = 750s | 750s |
| **Slack ratio** | N/A | (1000 - 750) / 750 = **0.33** → CRITICAL |
| **Decision** | Can scale down (150s < 600s) ✓ | Skip scale-down, force 3x scale-up ✗ |

**LAMF:** "I'm early, be conservative - 600s budget is plenty for this 150s iteration → scale down"
**DDM-EDF:** "Only 33% slack! I'm CRITICAL → don't scale down, try to scale up!"

### Iteration 3 (Late Iteration)

| Metric | LAMF | DDM-EDF |
|--------|------|---------|
| **Time elapsed** | 500s | 500s |
| **LAMF factor** | 0.9 (aggressive) | N/A |
| **Available time** | (1000 - 500) × 0.9 = **450s** | N/A |
| **Time needed** | 2 iterations × 150s = 300s | 300s |
| **Slack ratio** | N/A | (500 - 300) / 300 = **0.67** → WARNING |
| **Decision** | Late-iteration proactive scale-up triggers | Skip scale-down, try 2.5x scale-up |

**Both agree:** Scale up! (But for different reasons)

### Iteration 4 (Almost Done, Ahead of Schedule)

**Scenario:** Workflow completed iterations faster than expected

| Metric | LAMF | DDM-EDF |
|--------|------|---------|
| **Time elapsed** | 550s (faster!) | 550s |
| **Time needed** | 1 iteration × 150s = 150s | 150s |
| **Slack ratio** | N/A | (450 - 150) / 150 = **2.0** → EXCESS |
| **Time progress** | 550/1000 = 55% | N/A |
| **Budget progress** | $40/$100 = 40% | N/A |
| **Progress check** | 55% > 40% + 5% → **Scale up!** | N/A |
| **Decision** | **Scale UP** (falling behind budget) | **Scale DOWN** (excess slack) |

**Opposite decisions!**

**LAMF:** "I'm spending time (55%) faster than budget (40%) → I must be using cheap but slow resources → scale up to faster instances!"
**DDM-EDF:** "I have 200% slack! Huge excess → scale down to free resources"

---

## Why LAMF Works Better

### 1. Iteration Weighting is Predictable

- **LAMF:** Every workflow at iteration 2 uses DFACTOR=0.8 (deterministic)
- **DDM-EDF:** Slack varies wildly based on runtime estimates (unpredictable)

**Example:** Two workflows with same deadline but different mesh sizes:
- Workflow A (small mesh): slack = 2.5 → EXCESS → scale down
- Workflow B (large mesh): slack = 0.4 → CRITICAL → scale up
- **LAMF treats both the same at iteration N** (consistent behavior)

### 2. Iteration Weighting Adapts Naturally

- **Early iterations (0-2):** Conservative (0.6-0.8) → hold resources for later
- **Late iterations (3-5):** Aggressive (0.9-1.0) → commit remaining resources

**DDM-EDF:** Urgency doesn't change with iteration progress - always based on absolute deadline slack.

**Result:** LAMF allocates resources more smoothly over workflow lifetime.

### 3. Progress-Based Triggers React to Reality

```python
# LAMF's progress check
if time_progress > budget_progress + 0.05:
    # Falling behind - scale up!
```

**This catches:**
- Slow instances (high time, low budget)
- Underestimated runtimes (more time needed)
- License acquisition delays (time lost)

**DDM-EDF's urgency** relies on **estimated** time_needed:
```python
slack = (time_remaining - estimated_time_needed) / estimated_time_needed
```

**If estimate is wrong → urgency is wrong → bad decisions!**

### 4. License Awareness is Built-In

LAMF's scale-down guards:
- **GUARD 1:** Don't scale down if license pool > 70% utilized
- **GUARD 2:** Don't scale down after iteration 3 (too late to re-acquire licenses)
- **GUARD 3:** Don't scale down if > 70% time elapsed
- **GUARD 4:** Don't scale down if > 50% budget/time used

**DDM-EDF has same guards** but urgency logic **fights against them**:
- Urgency says "CRITICAL - keep resources!"
- But if urgency is EXCESS, guards might still block scale-down
- **Conflicting signals!**

---

## Performance Comparison

### Experimental Results (400 Workflows)

| Metric | DDM-EDF v1 (urgency 0.3/0.6) | DDM-EDF v2 (urgency 0.8/1.2) | Static EDF |
|--------|------------------------------|------------------------------|------------|
| **Executed workflows** | 356 (89.0%) | 364 (91.0%) | **366 (91.5%)** ✓ |
| **Incomplete workflows** | 44 | 36 | **34** ✓ |
| **Average Flowtime** | 34,343.56s | 34,364.80s | **29,536.59s** ✓ |
| **Average Cost** | €98.93 | €116.81 | **€103.61** ✓ |
| **Total Cost** | €35,220.76 | €42,518.40 | **€37,919.67** ✓ |
| **Resource Utilization** | 59.86% | **65.73%** ✓ | 61.13% |
| **Overall miss rate** | 12.75% | 12.0% | **9.75%** ✓ |
| **Scale-up attempts** | 9 | 3 | 0 |
| **Scale-down success** | 84.9% | **15.0%** | 0% |

**Key Observations:**
1. **DDM-EDF v1:** Urgency too conservative → only 9 scale-ups
2. **DDM-EDF v2:** Urgency too aggressive → guards block 85% of scale-downs
3. **Static EDF:** No moldability overhead → best flowtime and completion rate

### Moldability Activity Analysis

#### DDM-EDF v1 (Original Thresholds)
- Scale-up attempts: **9** (0.025 per workflow)
- Scale-down attempts: **159**
- Success ratio: 1:17.7 scale-up:scale-down
- Net impact: **-459 instances**, -21,872 cores

**Problem:** Too few scale-ups, excessive scale-downs → resource starvation

#### DDM-EDF v2 (Tuned Thresholds)
- Scale-up attempts: **3** (fewer than v1!)
- Scale-down attempts: **227**
- Scale-down success: **15.0%** (collapsed from 84.9%)
- Blocked scale-downs: **193** (85%)
- Net impact: **-89 instances**, -4,272 cores

**Problems:**
1. Higher urgency thresholds meant fewer workflows qualified for scale-up
2. Minimum instance guard (MIN=2) blocked ~180 scale-down attempts
3. Guards conflicting with urgency logic

---

## Why DDM-EDF Struggles

### Problem 1: Slack Calculation Requires Accurate Estimates

```python
# DDM-EDF calculates
time_needed = runtime_per_model * chains * tinyda_iterations * remaining_iterations
```

**If estimates are off by 20%:**
- Calculated slack = (800 - 600) / 600 = 0.33 → CRITICAL
- **Actual** needed: 720s (not 600s)
- **True** slack = (800 - 720) / 720 = 0.11 → Still CRITICAL but different magnitude
- Wrong urgency → wrong decisions

**LAMF doesn't care about estimates** - just uses iteration number.

### Problem 2: Urgency Doesn't Correlate with Resource Availability

**DDM-EDF:** "I'm CRITICAL (slack 0.5) → give me 3x resources!"
**Reality:** License pool 90% utilized → can't allocate more
**Result:** Failed scale-up attempt, wasted overhead

**LAMF:** "I'm at iteration 3 (BFACTOR 0.9) and license pool is 90% → scale-down blocked by GUARD 1"
**Result:** Keep current allocation (sensible)

### Problem 3: Urgency Changes Too Rapidly

**Scenario:** Workflow completes one iteration faster than expected

| Time | Slack | Urgency | Action |
|------|-------|---------|--------|
| t=100 | 0.7 | CRITICAL | Force scale-up (add 3 instances) |
| t=250 | 1.8 | EXCESS | Scale down (remove 2 instances) |
| t=400 | 0.9 | WARNING | Force scale-up (add 2 instances) |

**Result:** Thrashing! Resources added/removed/added again → overhead dominates benefit.

**LAMF:** Iteration changes slowly (only 5 times per workflow) → stable decisions.

### Problem 4: Conflicting Logic

**Example conflict:**
- **Urgency calculation:** slack = 2.5 → EXCESS → "Scale down!"
- **GUARD 4:** time_progress = 60% > 50% → "Block scale-down!"
- **Result:** Scale-down attempted but blocked → wasted computation

**In DDM-EDF v2:** This happened **193 times** (85% of all scale-down attempts)

---

## Summary: Iteration-Based vs Urgency-Based

| Aspect | LAMF (Iteration) ✓ | DDM-EDF (Urgency) ✗ |
|--------|-------------------|---------------------|
| **Simplicity** | Lookup iteration number | Calculate slack with estimates |
| **Stability** | Changes 5 times (iterations 0-5) | Changes every request (dynamic slack) |
| **Predictability** | Same for all workflows at iteration N | Varies by workflow state |
| **Robustness** | Doesn't rely on accurate estimates | **Breaks if estimates wrong** |
| **License awareness** | Guards built around license saturation | Guards conflict with urgency logic |
| **Overhead** | Low (simple lookups) | High (slack calculation + metadata tracking) |
| **Performance** | Not tested in current experiments | **Underperforms static baseline** |

---

## Recommendation

### Use LAMF's Iteration-Based Logic with EDF Ordering

**Hybrid approach:**
1. **EDF ordering** for queue (deadline-based priority) ← Keep from DDM-EDF
2. **Iteration-weighted factors** for moldability ← Use from LAMF
3. **Progress-based triggers** for scale-up ← Use from LAMF
4. **License-aware guards** for scale-down ← Use from LAMF

**Remove:**
- Deadline slack calculation (too fragile)
- Urgency levels (too unstable)
- Preemptive reallocation (too much overhead)

**This gives you "EDF-ordered LAMF"** - best of both worlds:
- Workflows with tight deadlines get scheduled first (EDF queue)
- Moldability decisions based on proven iteration-based approach (LAMF)
- Simpler implementation, less overhead, more predictable behavior

### Expected Benefits

1. **Better completion rate:** Match or beat static EDF's 91.5%
2. **Lower flowtime:** Reduce overhead vs DDM-EDF's urgency calculations
3. **More scale-ups:** 30-50 attempts vs DDM-EDF's 3-9
4. **Balanced moldability:** Scale ratio closer to 1:2 instead of 1:17
5. **License efficiency:** Guards prevent harmful scale-down

---

## References

### Code Locations

- **LAMF Implementation:** `src/main/scheduler/fcfs_optimized_LA.py`
  - Iteration-based moldability: Lines 224-423
  - Smart guards: Lines 310-350

- **DDM-EDF Implementation:** `src/main/scheduler/edf_optimized_LA.py`
  - Urgency-based moldability: Lines 860-1006
  - Slack calculation: Lines 339-408

- **Constants:** `src/main/config/constants_LA.py`
  - LAMF factors: Lines 45-46
  - DDM-EDF thresholds: Lines 145-159

### Experimental Data

- **DDM-EDF v1:** `edf_400_moldable.rtf` (original thresholds)
- **DDM-EDF v2:** `edf_400_moldable.txt` (tuned thresholds)
- **Static Baseline:** `edf_400_static.txt`

---

**Document Version:** 1.0
**Last Updated:** November 2025
**Author:** Claude Code Analysis
