# Novel License-Aware Scheduling Algorithms

**Date**: November 2025
**Context**: Proposed algorithms based on experience with EDF-LAMF, FCFS-LAMF, and DDM-EDF

---

## Executive Summary

After extensive work with EDF-LAMF (v4, v5, v5.1), FCFS-LAMF, and DDM-EDF, we've identified key unsolved problems in license-aware moldable scheduling:

1. **License Pool Saturation** (80% ANSYS peak at 500wf)
2. **Reactive Scaling Failures** (42% scale-up failures due to licenses at 700wf)
3. **Dual-Resource Complexity** (compute + licenses both bottleneck)
4. **Deadline vs Cost Tradeoff** (no single algorithm optimizes both)
5. **Capacity-Bound Moldability** (breaks at 600-700wf)

This document proposes **7 novel algorithms** to address these challenges.

---

## Problem Analysis from Current Implementations

### EDF-LAMF v5.1 Findings

**Successes:**
- ✅ Beats static at 200wf (6.5% vs 7.0% deadline miss)
- ✅ 94-95% scale-up success rate
- ✅ 2-5% cheaper than static at 600-700wf

**Limitations:**
- ❌ License pool saturation (ANSYS 80.30% peak at 500wf)
- ❌ Completion collapse at 700wf (69.4% vs 78.6% static)
- ❌ 30.7% deadline miss at 700wf (vs 21.4% static)
- ❌ 42% of scale-up failures due to insufficient licenses

**Root Cause**: System capacity saturation, NOT algorithm flaws.

### DDM-EDF Characteristics

**Strengths:**
- Preemptive reallocation (EXCESS workflows donate to CRITICAL)
- Urgency-based scaling (1.2× → 2.0× boost)
- Smart scale-down guards (protect deadline-critical workflows)

**Weaknesses:**
- Scales down both compute + licenses (but compute is saturated at 92%)
- Only 50% of freed licenses released (other 50% kept as buffer)
- No coordination across workflows sharing same license pool

### FCFS-LAMF Characteristics

**Strengths:**
- Simple, predictable behavior
- No priority inversion
- Low scheduling overhead

**Weaknesses:**
- No deadline awareness (FCFS ordering)
- Misses opportunities to help urgent workflows
- No preemptive reallocation

---

## Novel Algorithm Proposals

### 1. License Pool Balancing EDF (LPB-EDF)

**Problem Addressed**: Single license pool saturation while others underutilized.

**Core Innovation**: Route workflows to least-saturated compatible license pool.

**Algorithm**:
```python
def schedule_workflow(wf, available_pools):
    # 1. Get compatible license pools for this workflow
    compatible_pools = get_compatible_pools(wf.software_id)

    # 2. Select pool with lowest current utilization
    pool_utilizations = [(pool, get_utilization(pool)) for pool in compatible_pools]
    selected_pool = min(pool_utilizations, key=lambda x: x[1])[0]

    # 3. Allocate resources + licenses from selected pool
    if pool_utilizations[selected_pool] < 0.70:  # Not saturated
        allocate_with_priority(wf, selected_pool, priority='normal')
    else:  # All pools saturated
        # Fall back to deadline-urgency based allocation
        allocate_with_priority(wf, selected_pool, priority='urgent')
```

**Expected Impact**:
- ANSYS peak: 80.30% → 60-65% (balanced across pools)
- License failures: -10% reduction
- Works best when workflows are pool-compatible

**Implementation Complexity**: Medium
- Requires software compatibility matrix
- Minor workflow spec changes (allow multiple pool options)

**Risks**:
- Not all software is pool-compatible (ANSYS-specific can't use LSDYNA)
- May complicate license accounting

**Use Case**: Environments with flexible software licensing.

---

### 2. Cooperative License Sharing (CLS)

**Problem Addressed**: License bottleneck at saturation (root cause of deadline misses).

**Core Innovation**: Proactively release licenses from EXCESS workflows to help CRITICAL ones.

**Key Difference from DDM-EDF**: Only releases licenses, keeps compute resources.

**Algorithm**:
```python
def cooperative_license_sharing(sim):
    # 1. Identify workflows with excess slack
    excess_slack_wfs = [wf for wf in running_workflows
                        if get_slack_ratio(wf) > 0.50]

    # 2. Identify critical workflows needing licenses
    critical_wfs = [wf for wf in running_workflows
                    if get_slack_ratio(wf) < 0.30]

    if not critical_wfs:
        return  # No one needs help

    # 3. For each excess workflow, release licenses (keep compute!)
    for wf in excess_slack_wfs[:3]:  # Limit to top 3
        current_licenses = get_workflow_licenses(wf)

        # Release 75% of licenses (aggressive), keep 25% minimum
        to_release = int(current_licenses * 0.75)
        min_needed = calculate_min_licenses(wf.cores, wf.chains)
        actual_release = max(to_release, current_licenses - min_needed)

        if actual_release > 0:
            release_licenses(wf.id, actual_release)
            # Workflow continues with fewer licenses (slower, but still progressing)
            print(f"  🤝 COOPERATIVE: {wf.id} donating {actual_release} licenses")

    # 4. Critical workflows get first access to freed pool
    # (Normal allocation logic, but they're in CRITICAL mode with 2.0× boost)
```

**Why This Works**:
- Current v5.1: Compute at 92% (saturated), Licenses at 75-80% (bottleneck)
- DDM-EDF releases both compute + licenses (wastes scarce compute)
- CLS releases only licenses (keeps valuable compute, frees bottleneck resource)

**Expected Impact**:
- Deadline miss at 600-700wf: -15% improvement (30.7% → 26%)
- License failures: -20% reduction
- Completion rate: +5% improvement (69.4% → 73%)

**Implementation Complexity**: **Low** ⭐
- Builds on existing DDM-EDF preemptive reallocation
- Just modify to release licenses only (not compute)
- ~200 lines of code

**Risks**:
- Donor workflows may slow down (fewer licenses per core)
- If too aggressive (75% release), donors might miss deadlines
- Need careful tuning of release percentage

**Recommendation**: **IMPLEMENT THIS FIRST** (highest ROI, lowest complexity)

---

### 3. Predictive License Demand (PLD) Scheduler

**Problem Addressed**: Reactive scaling fails when saturation already occurred.

**Core Innovation**: Predict license demand spikes and prevent saturation before it happens.

**Algorithm**:
```python
def predictive_license_demand(sim, workflow_queue):
    # 1. Predict license demand for next 5 minutes
    upcoming_workflows = peek_next_n_workflows(workflow_queue, n=10)
    predicted_demand = {}

    for pool in ['ANSYS', 'ABAQUS', 'LSDYNA']:
        demand = sum([estimate_license_needs(wf, pool)
                      for wf in upcoming_workflows
                      if wf.license_pool == pool])
        predicted_demand[pool] = demand

    # 2. For each pool, check if predicted demand > available capacity
    for pool, demand in predicted_demand.items():
        current_allocated = get_pool_allocated(pool)
        pool_capacity = LICENSE_POOL_CAPACITY[pool]
        available = pool_capacity - current_allocated

        if demand > available * 1.5:  # Predicted spike 50% over capacity
            # 3. Proactively free licenses from workflows with slack
            deficit = demand - available
            print(f"  🔮 PREDICTIVE: {pool} spike predicted, need {deficit} more tokens")

            # Free licenses from workflows with >60% slack
            freed = proactively_free_licenses(pool, target=deficit,
                                              min_slack=0.60)
            print(f"  🔓 FREED: {freed} {pool} tokens from slack workflows")
```

**Machine Learning Component**:
```python
# Train model on historical data
features = [
    'time_of_day',           # License demand varies by time
    'queue_depth',           # More queued = higher upcoming demand
    'avg_workflow_size',     # Larger workflows = more licenses
    'current_utilization',   # Momentum effect
    'pool_type'              # ANSYS vs ABAQUS vs LSDYNA patterns
]

target = 'license_demand_next_5min'

# Model predicts upcoming demand spike
model = RandomForestRegressor()
model.fit(historical_features, historical_demand)

predicted_demand = model.predict(current_features)
```

**Expected Impact**:
- License failures: -15-20% reduction
- Proactive vs reactive (prevents vs fixes)
- Works with any base scheduler (EDF, FCFS, etc.)

**Implementation Complexity**: **High**
- Requires historical data collection
- ML model training and deployment
- Prediction accuracy critical

**Risks**:
- False positives: Free licenses unnecessarily (hurt donors)
- False negatives: Miss spikes (no benefit)
- Model drift: Demand patterns change, need retraining

**Recommendation**: Research project, not immediate production deployment.

---

### 4. Hybrid Static-Moldable (HSM) Scheduler

**Problem Addressed**: Deadline miss at scale due to reactive scaling.

**Core Innovation**: Combine static's upfront commitment with moldability's adaptation.

**Design**:
- **Iteration 0**: Allocate like static scheduler (upfront, conservative)
- **Iterations 1-4**: Moldable scaling (EDF-LAMF triggers and guards)

**Algorithm**:
```python
def hybrid_static_moldable(wf, iteration):
    if iteration == 0:
        # === STATIC PHASE ===
        # Allocate conservatively like baseline (ensure deadline feasibility)
        budget_for_iteration = wf.budget * 0.40  # 40% for first iteration
        time_for_iteration = (wf.deadline - wf.start_time) * 0.30  # 30% time

        # Calculate resources needed to complete in time
        min_instances = calculate_static_allocation(
            chains=wf.chains,
            runtime_target=time_for_iteration,
            budget=budget_for_iteration
        )

        # Commit resources upfront (like static)
        allocate_instances(wf.id, min_instances, mode='committed')

        print(f"  📌 STATIC PHASE: {wf.id} committed {min_instances} instances")

    else:
        # === MOLDABLE PHASE ===
        # Use EDF-LAMF logic: triggers, guards, urgency-based scaling

        # Calculate deadline urgency
        time_remaining = wf.deadline - getTime(sim)
        deadline_urgency = time_remaining / (wf.deadline - wf.start_time)

        # Apply EDF-LAMF triggers (CRITICAL, WARNING, EARLY, MID-ITERATION)
        if deadline_urgency < 0.30:
            force_scale_up_attempt = True
            urgency_mode = 'CRITICAL'
            boost = 2.0
        # ... (rest of EDF-LAMF logic)

        print(f"  🔄 MOLDABLE PHASE: {wf.id} iteration {iteration}, urgency {urgency_mode}")
```

**Why This Works**:
- Iteration 0 is highest risk (sets the pace)
- Static commitment provides "deadline insurance"
- Later iterations optimize cost when risk is clearer

**Expected Impact**:
- Deadline miss at 700wf: 30.7% → 21-25% (closer to static's 21.4%)
- Cost: Still cheaper than pure static (moldability in iterations 1-4)
- Completion rate: 69.4% → 75% (better than current EDF-LAMF)

**Implementation Complexity**: **Low** ⭐⭐
- Reuses existing static allocation logic (iteration 0)
- Reuses existing EDF-LAMF logic (iterations 1-4)
- Just adds conditional branching by iteration number
- ~300 lines of code

**Risks**:
- Iteration 0 might over-allocate (waste budget if finishes early)
- Two allocation modes may be confusing to reason about

**Recommendation**: **IMPLEMENT THIS SECOND** (proven approach, low complexity)

---

### 5. Multi-Objective Deadline-Cost Optimizer (MODCO)

**Problem Addressed**: Users care about both deadlines AND cost, current algorithms optimize only one.

**Core Innovation**: User-specified tradeoff between deadline priority and cost priority.

**Algorithm**:
```python
def multi_objective_scheduler(wf, alpha, beta):
    # alpha: deadline weight (0-1)
    # beta: cost weight (0-1), where alpha + beta = 1

    # 1. Calculate deadline urgency (EDF-LAMF style)
    deadline_urgency = calculate_urgency(wf)

    # 2. Calculate cost pressure (budget consumed / budget total)
    cost_pressure = wf.used_budget / wf.total_budget

    # 3. Compute composite score
    # Higher score = more urgent need for resources
    composite_score = alpha * (1 - deadline_urgency) + beta * (1 - cost_pressure)

    # 4. Determine allocation based on composite score
    if composite_score > 0.70:  # High urgency on both metrics
        boost_factor = 2.0
        allocation_mode = 'AGGRESSIVE'
    elif composite_score > 0.50:  # Moderate urgency
        boost_factor = 1.5
        allocation_mode = 'MODERATE'
    else:  # Low urgency
        boost_factor = 1.0
        allocation_mode = 'CONSERVATIVE'

    # 5. Allocate with computed boost
    available_budget = (wf.budget - wf.used_budget) * boost_factor
    allocate_resources(wf.id, available_budget, allocation_mode)
```

**User Interface**:
```yaml
# Workflow specification
workflow_id: wf_123
constraints:
  deadline: 14108.37s
  budget: €64.81
  optimization_preference:
    deadline_weight: 0.7  # Prioritize deadline
    cost_weight: 0.3      # But still consider cost
```

**Expected Impact**:
- User satisfaction: Higher (they get what they asked for)
- Flexibility: Same algorithm serves different use cases
  - α=1.0, β=0.0 → Pure EDF (deadline-only)
  - α=0.0, β=1.0 → Pure cost optimization
  - α=0.5, β=0.5 → Balanced

**Implementation Complexity**: **Medium**
- Extends EDF-LAMF with cost-awareness
- Composite score calculation straightforward
- Requires user preference specification

**Risks**:
- Users may not know how to set α and β
- Finding true Pareto frontier is NP-hard (this is heuristic)
- May perform worse than specialized algorithms

**Recommendation**: Good for research on user preferences, not immediate deployment.

---

### 6. License-First Earliest Deadline (LFED)

**Problem Addressed**: EDF is license-blind (sorts by deadline only, ignores pool saturation).

**Core Innovation**: Prioritize by composite metric: deadline urgency × license pool pressure.

**Algorithm**:
```python
def license_first_earliest_deadline(workflow_queue):
    # 1. Calculate priority for each workflow
    priorities = []
    for wf in workflow_queue:
        pool = wf.license_pool
        pool_util = get_pool_utilization(pool)  # 0.0 - 1.0
        deadline = wf.submit_time + wf.constraints['deadline']

        # Priority: workflows needing saturated pools + tight deadlines go first
        # Higher values = higher priority
        priority = (1 / deadline) * (1 + pool_util)

        priorities.append((priority, deadline, wf))

    # 2. Sort by composite priority (descending)
    priorities.sort(reverse=True, key=lambda x: x[0])

    # 3. Schedule in priority order
    for priority, deadline, wf in priorities:
        pool = wf.license_pool
        pool_util = get_pool_utilization(pool)

        print(f"  🎯 LFED: {wf.id} priority={priority:.2f}, "
              f"deadline={deadline}, pool={pool} ({pool_util*100:.1f}%)")

        # Try to allocate
        if allocate_with_licenses(wf):
            print(f"  ✅ ALLOCATED: {wf.id}")
        else:
            # If pool saturated, consider preempting low-priority workflows
            if pool_util > 0.75:
                preempted = preempt_low_priority_workflow(pool, wf)
                if preempted:
                    print(f"  🔄 PREEMPTED: {preempted.id} for {wf.id}")
                    allocate_with_licenses(wf)
```

**Why This Works**:
- Current EDF: Workflows with tight deadlines prioritized, even if pool has capacity
- LFED: Workflows needing saturated pools prioritized (prevents saturation)
- Example:
  - Workflow A: deadline=100s, pool=ANSYS (80% util) → priority = 0.01 × 1.8 = 0.018
  - Workflow B: deadline=50s, pool=LSDYNA (50% util) → priority = 0.02 × 1.5 = 0.030
  - Current EDF: B goes first (tighter deadline)
  - LFED: B still goes first (higher composite priority)
  - Workflow C: deadline=200s, pool=ANSYS (80% util) → priority = 0.005 × 1.8 = 0.009
  - Current EDF: C goes last (loosest deadline)
  - LFED: C might go before B (saturated pool gets boost)

**Expected Impact**:
- License failures: -20-25% reduction
- Better license utilization across pools
- Prevents saturation proactively

**Implementation Complexity**: **Medium**
- Composite priority calculation simple
- Preemption adds complexity (safe workflow pausing)
- Need aging mechanism (prevent starvation)

**Risks**:
- Preemption may hurt preempted workflows
- Starvation: Low-priority workflows may never run
- Need careful tuning of priority formula

**Recommendation**: Research project for fundamentally rethinking EDF priority.

---

### 7. Gang Scheduling for License Pools (GSLP)

**Problem Addressed**: Uncoordinated sharing leads to thrashing and inefficiency.

**Core Innovation**: Group workflows by license pool, coordinate resource allocation within gang.

**Algorithm**:
```python
def gang_scheduling_license_pools(sim):
    # 1. Group running workflows by license pool
    gangs = {
        'ANSYS': [wf for wf in running if wf.pool == 'ANSYS'],
        'ABAQUS': [wf for wf in running if wf.pool == 'ABAQUS'],
        'LSDYNA': [wf for wf in running if wf.pool == 'LSDYNA']
    }

    # 2. For each gang, coordinate resource allocation
    for pool, gang_workflows in gangs.items():
        pool_util = get_pool_utilization(pool)

        if pool_util > 0.75:  # Gang experiencing saturation
            print(f"  👥 GANG {pool}: Saturation {pool_util*100:.1f}%, "
                  f"coordinating {len(gang_workflows)} workflows")

            # 3. Identify workflows with slack in this gang
            slack_workflows = [wf for wf in gang_workflows
                               if get_slack_ratio(wf) > 0.50]

            # 4. Identify critical workflows in this gang
            critical_workflows = [wf for wf in gang_workflows
                                  if get_slack_ratio(wf) < 0.30]

            if critical_workflows and slack_workflows:
                # 5. Coordinate donation within gang
                target_to_free = calculate_licenses_needed(critical_workflows)

                # Sort slack workflows by most slack first
                slack_workflows.sort(key=lambda wf: get_slack_ratio(wf), reverse=True)

                freed_total = 0
                for donor_wf in slack_workflows:
                    if freed_total >= target_to_free:
                        break

                    # Scale down donor by 40%
                    to_free = int(get_workflow_licenses(donor_wf) * 0.40)
                    freed = scale_down_licenses(donor_wf, to_free)
                    freed_total += freed

                    print(f"  🤝 GANG DONATE: {donor_wf.id} → {freed} tokens to gang")

                print(f"  ✅ GANG FREED: {freed_total} {pool} tokens "
                      f"for {len(critical_workflows)} critical workflows")
```

**Why This Works**:
- Current DDM-EDF: System-wide preemptive reallocation (any pool to any pool)
- GSLP: Pool-specific coordination (ANSYS gang helps ANSYS critical workflows)
- More targeted, more efficient

**Expected Impact**:
- License utilization efficiency: -15-20% improvement
- Reduced contention within pools
- Better coordination (exact amount freed = exact amount needed)

**Implementation Complexity**: **Medium**
- Requires gang-level coordination logic
- Gang membership tracked dynamically
- Donation target calculation non-trivial

**Risks**:
- Donor workflows may suffer if too aggressive
- Gang coordination overhead (decision latency)
- Unfairness: Some gangs favored over others

**Recommendation**: Good for homogeneous workloads (many workflows per pool).

---

## Recommendation Matrix

| Algorithm | Problem | Expected Impact | Complexity | Priority |
|-----------|---------|-----------------|------------|----------|
| **CLS** | License bottleneck | -15% deadline miss at 600-700wf | **Low** | 🏆 **#1** |
| **HSM** | Deadline miss at scale | -10% deadline miss | **Low** | 🏆 **#2** |
| **LFED** | License-blind scheduling | -25% license failures | Medium | 🏆 **#3** |
| **LPB-EDF** | Single pool saturation | -10% license failures | Medium | #4 |
| **GSLP** | Uncoordinated sharing | -20% license waste | Medium | #5 |
| **MODCO** | User flexibility | Better satisfaction | Medium | #6 |
| **PLD** | Reactive lag | -20% license failures | **High** | #7 |

---

## Implementation Roadmap

### Phase 1: Low-Hanging Fruit (Weeks 1-4)

**Week 1-2: Cooperative License Sharing (CLS)**
- Modify DDM-EDF preemptive reallocation
- Change to release licenses only (not compute)
- Test at 600-700wf
- Expected: 15% deadline improvement

**Week 3-4: Hybrid Static-Moldable (HSM)**
- Add iteration-0 static phase to EDF-LAMF
- Test at all scales (200-700wf)
- Compare to EDF-LAMF and static baseline

### Phase 2: Novel Research (Weeks 5-10)

**Week 5-7: License-First Earliest Deadline (LFED)**
- Implement license-aware priority scheduling
- Add preemption mechanism
- Test and compare to CLS/HSM

**Week 8-10: Comparison Study**
- Run all algorithms at 200-700wf
- Collect metrics (deadline miss, cost, utilization)
- Identify best practices

### Phase 3: Advanced (Weeks 11-20)

**Week 11-16: Predictive License Demand (PLD)**
- Collect historical data
- Train ML model
- Deploy in simulation
- Validate predictions

**Week 17-20: Gang Scheduling (GSLP)**
- Implement gang coordination
- Test with homogeneous workloads
- Compare to individual scheduling

### Phase 4: Publication (Weeks 21-24)

**Research Paper**: "License-First Scheduling for Dual-Resource Constrained Scientific Workflows"
- Motivation: License saturation is the bottleneck
- Contributions: CLS, HSM, LFED
- Evaluation: 200-700wf testbed
- Results: 15-25% improvement over EDF-LAMF

---

## Quick Reference: When to Use Which Algorithm

| Scenario | Recommended Algorithm | Why |
|----------|----------------------|-----|
| **Current production** | EDF-LAMF v5.1 + capacity limits | Proven, stable, works well ≤500wf |
| **License saturation** | CLS or LFED | Directly addresses license bottleneck |
| **Deadline-critical** | HSM | Static phase protects deadlines |
| **Cost-critical** | EDF-LAMF v5.1 | Best cost efficiency at scale |
| **Multi-pool compatible** | LPB-EDF | Balances load across pools |
| **Homogeneous workloads** | GSLP | Gang coordination efficient |
| **Diverse user needs** | MODCO | Flexible tradeoff specification |
| **High variance workload** | PLD | Predictive prevents spikes |

---

## Key Insights from EDF-LAMF Experience

### What We Learned

1. **License pools are the real bottleneck** (not compute)
   - ANSYS peaks at 80% while compute at 92%
   - 42% of scale-up failures due to licenses at 700wf
   - Algorithm must be license-aware, not just deadline-aware

2. **Reactivity fails at saturation**
   - By the time triggers fire (<30% time), resources exhausted
   - Need proactive approaches (CLS, PLD) or upfront commitment (HSM)

3. **Partial release traps licenses**
   - 50% release in DDM-EDF keeps licenses in workflows that don't need them
   - CLS releases 75% (more aggressive)

4. **Compute and licenses are different**
   - DDM-EDF scales down both, but compute is saturated
   - CLS releases licenses only (keeps valuable compute)

5. **Moldability works brilliantly... until capacity limits**
   - 94-95% success rate at 200-500wf
   - Breaks at 600-700wf (not algorithm fault, capacity)
   - Algorithms can't create resources that don't exist

### Design Principles for Future Algorithms

1. **License-First Design**: Incorporate license pool state into scheduling decisions
2. **Proactive Over Reactive**: Prevent saturation before it happens
3. **Separate Compute and Licenses**: Different resources, different strategies
4. **Cooperative Sharing**: Workflows help each other (not just individual greed)
5. **Hybrid Approaches**: Combine strengths of multiple techniques
6. **User-Aware**: Different users have different priorities (deadline vs cost)

---

## References

- **EDF-LAMF v5.1 Analysis**: `/license_results/EDF-LAMF/EDF_LAMF_v5_1_Comprehensive_Analysis.md`
- **EDF-LAMF Implementation**: `/src/main/scheduler/edf_optimized_LA.py`
- **DDM-EDF Documentation**: `/license_results/EDF-LAMF/EDF_Optimized_LA_Analysis.md`
- **License Manager**: `/src/main/resource_manager/license/manager.py`

---

## Document History

- **v1.0** (November 2025): Initial algorithm proposals based on EDF-LAMF experience
