# EDF_Optimized_LA Scheduler - Comprehensive Analysis

**Algorithm**: Deadline-Driven Moldable EDF with License Awareness (DDM-EDF)
**File**: `src/main/scheduler/edf_optimized_LA.py`
**Date**: November 2025

---

## 1. MOTIVATION & RESEARCH CONTEXT

The EDF_Optimized_LA scheduler addresses a critical challenge in **scientific computing environments**: scheduling workflows that require **both computational resources AND commercial software licenses** (like ANSYS, ABAQUS, LSDYNA), while meeting **deadline and budget constraints**.

### Key Problems Addressed

- **Dual-resource bottleneck**: Workflows need both compute (CPU cores) AND licenses (often scarce/expensive)
- **Deadline pressure**: Scientific simulations have hard deadlines (paper submissions, project milestones)
- **License contention**: Commercial licenses are often the limiting factor, not compute
- **Resource heterogeneity**: Mix of on-prem, reserved cloud, and on-demand instances with different costs/performance

### Innovation over Prior Work

- Combines **EDF (Earliest Deadline First)** ordering with **deadline urgency-based moldability**
- Adds **preemptive resource reallocation** - workflows with excess slack donate resources to critical workflows
- Integrates **sophisticated license management** with partial release strategies

---

## 2. WORKFLOW STRUCTURE

The system schedules **License-Aware (LA) SeisSol workflows** - seismic simulation workloads with specific structure.

### Workflow Anatomy

Example from `sample_workflows_LA/data0.yaml`:

```yaml
config:
  mesh: 1000                    # Problem size (mesh elements)
  software_id: 1                # ANSYS=1, ABAQUS=2, LSDYNA=3
  workflowConfig:               # 5 iterations, each with different resource needs
    - chains: 5, tinydaIterations: 1
    - chains: 6, tinydaIterations: 8
    - chains: 5, tinydaIterations: 5
    - chains: 5, tinydaIterations: 6
    - chains: 6, tinydaIterations: 4
  workflowIterations: 5
constraints:
  budget: $64.81                # Cost limit
  deadline: 14108.37s (~3.9h)   # Hard deadline
  chains: 5                     # Parallel chains
  tinydaIterations: 1           # TinyDA iterations per workflow iteration
  license_pool: ANSYS           # Required license type
```

### Key Workflow Characteristics

1. **Iterative structure**: Workflows execute 5 iterations sequentially (workflow iterations)
2. **Chains**: Each iteration runs multiple parallel "chains" (independent simulations)
3. **TinyDA iterations**: Within each workflow iteration, TinyDA (uncertainty quantification) runs multiple sub-iterations
4. **Dynamic resource needs**: Each iteration can have different `chains × tinydaIterations` requirements

### Execution Model

- **Per iteration**: Allocate `chains` nodes (1 node per chain)
- **Per node**: Can run multiple models in parallel (moldability via `nodes_per_chain`)
- **License consumption**: Calculated based on `cores × chains` using pool-specific strategies

---

## 3. MOLDABILITY - THE CORE INNOVATION

Moldability means **workflows can dynamically adjust their resource allocation** during execution to optimize performance/cost. This scheduler inherits sophisticated moldability from `Scheduler_LA.checkNewResources()`.

### 3.1 Moldable Resource Allocation (3-Tier Strategy)

The `checkNewResources()` method implements:

#### Tier 1: On-Premises Moldability

```python
nodes_per_chain = (chains - current_count + free_slots) // chains
# Example: 5 chains, currently 5 nodes (1 per chain), 10 free slots
#   → nodes_per_chain = (5 - 5 + 10) // 5 = 2
#   → Allocate 10 more nodes for 3 nodes per chain total
```

**Optimization Logic:**
- Maximize `nodes_per_chain` while respecting budget/deadline
- **Speedup requirement**: `runtime(N-1) / runtime(N) > 1.4` (diminishing returns check)
- **Deadline feasibility**: `speedup_runtime × iterations < available_runtime`

**Location**: `scheduler_LA.py:520-542`

#### Tier 2: Cloud Moldability with Instance Closeness

- Allocates heterogeneous cloud instances (reserved/on-demand)
- **Instance closeness**: Only add instances with runtime within 15% of existing (`checkCloseness()`)
- **Cold start accounting**: Adds `COLD_START_TIME` cost for on-demand provisioning
- **Capped nodes_per_chain**: Limited to 4 to prevent over-parallelization

**Location**: `scheduler_LA.py:544-597`

#### Tier 3: Fallback Allocation

- If moldability fails, allocate any available instances up to budget
- **Speedup guard**: Reject instances 20% slower than current unless adding 2+ nodes

**Location**: `scheduler_LA.py:599-650`

### 3.2 Why Moldability Matters

- **Performance heterogeneity**: Different instance types have different runtimes (GPU vs CPU)
- **Cost-performance tradeoff**: More nodes → faster but more expensive
- **License efficiency**: More nodes per chain → better license utilization per core

---

## 4. ELASTICITY & DYNAMIC BEHAVIOR

**Elasticity = Runtime resource adjustment** during workflow execution. This scheduler supports both scale-up and scale-down.

### 4.1 Dynamic Scale-Up

**Location**: `edf_optimized_LA.py:820-913`

Triggered when:
- Workflow requests more resources (`ExecutorRequest.REQUEST_RESOURCE`)
- Iteration completes and scheduler evaluates if more resources would help

**Urgency-Based Boost Factors:**

```python
if urgency_level == 'CRITICAL':  # slack < 0.0
    urgency_boost = 1.5  # 50% more aggressive budget allocation
elif urgency_level == 'WARNING':  # slack < 0.2
    urgency_boost = 1.2
else:
    urgency_boost = 1.0  # Normal allocation
```

- Applies boost to `available_budget = (budget - used) × BFACTOR × urgency_boost`
- Allows critical workflows to "borrow" from future budget allocation

### 4.2 Dynamic Scale-Down

**Location**: `edf_optimized_LA.py:722-818`

#### Smart Scale-Down Guards

Prevent premature resource release:

**1. License Pool Guard** (lines 744-751):
```python
if pool_utilization > 0.70:  # Pool >70% utilized
    block_scale_down()  # Keep resources to avoid license contention
```

**2. Iteration Guard** (lines 754-757):
```python
if iteration > 3:  # Don't scale down in late iterations
    block_scale_down()
```

**3. Time Progress Guard** (lines 760-769):
```python
if elapsed_time / total_time > 0.70:  # >70% time elapsed
    block_scale_down()  # Too risky to scale down now
```

**4. Budget Progress Guard** (lines 772-779):
```python
if budget_progress > 0.50 or time_progress > 0.50:
    block_scale_down()  # Keep resources if ahead on budget
```

### 4.3 Partial License Release

**Location**: `edf_optimized_LA.py:579-653`

- Only releases **50% of freed licenses** immediately
- Retains 50% as buffer to avoid thrashing (release → re-acquire cycles)
- Uses **two-phase commit** (hold → commit) for atomicity

---

## 5. DEADLINE-DRIVEN MOLDABLE EDF (DDM-EDF) ALGORITHM

### 5.1 Core Algorithm Flow

```
LOOP every 30 seconds:
  ┌─ PHASE 1: Preemptive Reallocation ───────────────────┐
  │ • Compute deadline slack for all running workflows   │
  │ • Identify CRITICAL (slack < 0) and EXCESS (>0.5)    │
  │ • Scale down EXCESS workflows by 30%                 │
  │ • Freed resources become available for CRITICAL      │
  └───────────────────────────────────────────────────────┘
  ┌─ PHASE 2: Process Resource Requests ──────────────────┐
  │ • Sort by deadline (EDF ordering)                     │
  │ • For earliest-deadline request:                      │
  │   - Compute deadline urgency (slack ratio)            │
  │   - Apply urgency-based scale-up/scale-down logic     │
  │   - Allocate resources + licenses (with guards)       │
  └───────────────────────────────────────────────────────┘
  ┌─ PHASE 3: Schedule New Workflows ─────────────────────┐
  │ • Sort by deadline (EDF ordering)                     │
  │ • For earliest-deadline workflow:                     │
  │   - Allocate compute resources (moldable)             │
  │   - Check license availability                        │
  │   - Hold licenses (two-phase commit)                  │
  │   - Start workflow execution                          │
  └───────────────────────────────────────────────────────┘
```

**Location**: `edf_optimized_LA.py:78-202`

### 5.2 Deadline Urgency Calculation

**Location**: `edf_optimized_LA.py:252-313`

The innovation of DDM-EDF is **deadline slack** as the primary decision metric:

```python
slack_ratio = (time_remaining - time_needed) / time_needed

if slack < 0.0:     CRITICAL  # Will miss deadline
elif slack < 0.2:   WARNING   # Risky (< 20% margin)
elif slack < 0.5:   SAFE      # Comfortable
else:               EXCESS    # Plenty of time
```

**Urgency Thresholds** (from `constants_LA.py:142-146`):
```python
DEADLINE_URGENCY_CRITICAL = 0.0
DEADLINE_URGENCY_WARNING = 0.2
DEADLINE_URGENCY_SAFE = 0.5
EXCESS_SLACK_THRESHOLD = 0.5
```

**Urgency-Based Actions:**
- **CRITICAL**: Skip scale-down, force scale-up with 1.5× budget boost
- **WARNING**: Skip scale-down, attempt scale-up with 1.2× boost
- **SAFE**: Normal scaling behavior
- **EXCESS**: Eligible for preemptive scale-down (donate resources)

### 5.3 Slack Calculation Details

```python
def compute_deadline_slack(wf_id, sim):
    # Get workflow state
    instances, budget, deadline, start_time, mesh = get_workflow(wf_id)

    # Calculate remaining time
    time_remaining = deadline - current_time

    # Estimate time needed (conservative)
    runtime_per_iteration = getRuntime(1, mesh, current_instance.name)
    estimated_remaining_iterations = 2  # Conservative guess
    time_needed = runtime_per_iteration * estimated_remaining_iterations

    # Compute slack ratio
    slack_ratio = (time_remaining - time_needed) / time_needed

    # Cache for 10 seconds to reduce overhead
    cache[wf_id] = (slack_ratio, current_time)

    return slack_ratio
```

**Location**: `edf_optimized_LA.py:252-298`

---

## 6. PREEMPTIVE REALLOCATION

**Location**: `edf_optimized_LA.py:319-399`

This is the **unique contribution** of DDM-EDF over traditional EDF.

### Algorithm

```python
def reallocate_for_deadline_critical(sim):
    critical_wfs = [wf for wf in running if slack(wf) < 0.0]
    excess_wfs = [wf for wf in running if slack(wf) > 0.5]

    if not critical_wfs or not excess_wfs:
        return  # Nothing to reallocate

    # Sort: most critical first, most excess first
    critical_wfs.sort(key=lambda wf: slack(wf))  # Ascending
    excess_wfs.sort(key=lambda wf: slack(wf), reverse=True)

    # Scale down top 2 excess workflows (limit to avoid thrashing)
    for wf in excess_wfs[:2]:
        to_free = max(1, int(current_count × 0.3))  # 30% scale-down
        scale_down_workflow(wf, to_free)
        # Freed resources immediately available for critical workflows
```

### Design Decisions

**Why 30% instead of 50%?**
- Less aggressive than normal scale-down to minimize disruption
- Balances helping critical workflows vs hurting donor workflows

**Thrashing Prevention:**
- Limited to **top 2 excess workflows** per cycle
- **10-second cache** on slack calculations (lines 264-268)
- Workflows won't repeatedly scale down/up

### Scale-Down Implementation

**Location**: `edf_optimized_LA.py:369-399`

```python
def scale_down_workflow(wf_id, count, sim):
    # Get workflow instances
    instances = get_workflow_instances(wf_id)

    # Free last n instances (LIFO)
    freed_instances = []
    freed_count = 0

    for i in range(len(instances) - 1, -1, -1):
        instance, inst_count, ips = instances[i]
        to_free = min(count - freed_count, inst_count)

        freed_instances.append((instance, to_free, ips[-to_free:]))
        instances[i] = (instance, inst_count - to_free, ips[:-to_free])

        freed_count += to_free
        if freed_count == count:
            break

    # Return resources to pool
    resource_manager.returnResources(wf_id, freed_instances)

    return freed_instances
```

---

## 7. LICENSE-AWARE RESOURCE MANAGEMENT

### 7.1 Two-Phase Commit Protocol

**Location**: `scheduler_LA.py:64-147`

When allocating resources:

```python
1. Calculate licenses_needed = f(cores, chains, license_pool)
   # Different strategies per pool:
   #   ANSYS: MEBA-style workgroup calculation
   #   ABAQUS: powerlaw (a × cores^b)
   #   LSDYNA: linear (1 token per core)

2. Hold licenses (TTL=300s):
   hold_id = license_manager.hold(pool, amount, owner, ttl)

3. Allocate compute resources:
   ips, alloc_resources = resource_manager.allocateResources(...)

4. If allocation succeeds:
   license_manager.commit(hold_id)
   Else:
   license_manager.release(hold_id)  # Rollback
```

**Why Two-Phase Commit?**
- **Atomicity**: Ensures compute + licenses are acquired together
- **Deadlock prevention**: Hold TTL expires if workflow fails to commit
- **Fairness**: Other workflows can acquire licenses from expired holds

### 7.2 License Calculation Strategies

**Location**: `resource_manager/license/manager.py`

```python
def calculate_tokens(pool, cores, chains):
    strategy = LICENSE_STRATEGIES[pool]

    if strategy == 'ansys_workgroup':
        # MEBA-style: Complex formula based on cores and chains
        return calculate_ansys_tokens(cores, chains)

    elif strategy == 'powerlaw':
        # ABAQUS: a × cores^b
        return calculate_powerlaw_tokens(cores)

    elif strategy == 'linear':
        # LSDYNA: 1 token per core
        return cores
```

### 7.3 License Pool Configuration

**Location**: `config/constants_LA.py:80-84`

```python
LICENSE_POOL_CAPACITY = {
    'ANSYS': 6700,    # Tuned for ~87% peak utilization
    'ABAQUS': 2200,   # High scarcity
    'LSDYNA': 7600
}

LICENSE_STRATEGIES = {
    'ANSYS': 'ansys_workgroup',  # MEBA-style calculation
    'ABAQUS': 'powerlaw',         # a × cores^b
    'LSDYNA': 'linear'            # 1 token per core
}
```

**Capacity Tuning:**
- Capacities tuned to create **meaningful scarcity** (87% peak usage)
- Previous values (16,800) caused only 3-13% utilization - licenses weren't a constraint
- Based on 400-workflow test: measured peaks were ANSYS=5,843, ABAQUS=1,894, LSDYNA=6,592
- Using 1.15× factor for 15% headroom

### 7.4 License-Aware Scale Operations

**checkNewResourcesWithLicenses** (edf_optimized_LA.py:415-505):
```python
def checkNewResourcesWithLicenses(resources, current_resources, budget,
                                   available_runtime, request, mesh, license_pool):
    # First, get compute allocation
    alloc_instances = checkNewResources(
        resources, current_resources, budget, available_runtime, request, mesh
    )

    if not alloc_instances:
        return ([], [])

    # Calculate licenses needed for proposed allocation
    total_cores = sum(inst.cores * count for inst, count in alloc_instances)

    licenses_needed = license_manager.calculate_tokens(
        pool=license_pool,
        cores=total_cores,
        chains=request.get('chains', 1)
    )

    # Check if licenses available
    available_tokens = license_manager.get_available_tokens(license_pool)

    if available_tokens >= licenses_needed:
        # Can allocate! Hold licenses
        hold_id = license_manager.hold(pool, licenses_needed, owner, ttl=300)
        license_manager.commit(hold_id)
        return (alloc_instances, [hold_id])
    else:
        # Insufficient licenses - try partial allocation
        feasible_instances = findLicenseFeasibleAllocation(
            alloc_instances, available_tokens, license_pool, request
        )

        if feasible_instances:
            # Partial allocation
            licenses_feasible = license_manager.calculate_tokens(...)
            hold_id = license_manager.hold(pool, licenses_feasible, owner, ttl=300)
            license_manager.commit(hold_id)
            return (feasible_instances, [hold_id])
        else:
            return ([], [])
```

**freeResourcesWithLicenses** (edf_optimized_LA.py:551-653):
```python
def freeResourcesWithLicenses(instances, request, sim, license_pool):
    # Free compute resources (LIFO)
    freed_instances = []
    for i in range(len(instances) - 1, -1, -1):
        instance, count, ips = instances[i]
        to_free = min(request['count'] - freed_count, count)
        freed_instances.append((instance, to_free, ips[-to_free:]))
        instances[i] = (instance, count - to_free, ips[:-to_free])
        freed_count += to_free
        if freed_count == request['count']:
            break

    resource_manager.returnResources(wf_id, freed_instances)

    # === PARTIAL LICENSE RELEASE ===
    if license_pool and freed_instances:
        total_cores_freed = sum(inst.cores * count
                                for inst, count, _ in freed_instances)

        licenses_to_free = license_manager.calculate_tokens(
            pool=license_pool,
            cores=total_cores_freed,
            chains=1
        )

        PARTIAL_RELEASE_FRACTION = 0.50  # Release 50%, keep 50% as buffer

        if wf_id in license_holds and license_holds[wf_id]:
            hold_id = license_holds[wf_id][-1]  # Most recent hold (LIFO)

            licenses_to_actually_release = int(licenses_to_free *
                                                PARTIAL_RELEASE_FRACTION)

            if licenses_to_actually_release >= alloc.amount:
                # Release entire hold
                license_holds[wf_id].pop()
                license_manager.release(hold_id)
            else:
                # Partial release: release old hold, create new smaller hold
                remaining_licenses = alloc.amount - licenses_to_actually_release

                license_holds[wf_id].pop()
                license_manager.release(hold_id)

                # Create new smaller hold for retained licenses
                new_hold_id = license_manager.hold(
                    pool=license_pool,
                    amount=remaining_licenses,
                    owner=wf_id,
                    ttl=300
                )
                license_manager.commit(new_hold_id)
                license_holds[wf_id].append(new_hold_id)

    return actual_licenses_released
```

---

## 8. EDF HEAP MANAGEMENT

**Location**: `edf_optimized_LA.py:207-246`

### Workflow Heap

```python
def processWorkflowsByDeadline(workflows):
    """Sort workflows by deadline (EDF ordering)"""
    for wf in workflows:
        wf_plan = eval(wf)
        if wf_plan['id'] == 'END':
            heapq.heappush(workflow_heap, (1000000, wf_plan['id'], wf_plan))
        else:
            deadline = wf_plan['submit_time'] + wf_plan['constraints']['deadline']
            heapq.heappush(workflow_heap, (deadline, wf_plan['id'], wf_plan))
```

### Resource Request Heap

```python
def processResourceRequestsByDeadline(requests):
    """Sort resource requests by deadline (EDF ordering)"""
    for req in requests:
        req_dict = eval(req)
        if req_dict['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
            # Min heap with deadlines
            wf = resource_manager.getWorkflow(req_dict['wf-id'])
            deadline = wf[2] if wf else float('inf')
        else:
            deadline = 0  # Free requests always on top

        heapq.heappush(resource_request_heap, (deadline, req_dict['wf-id'], req_dict))
```

### Heap Operations

```python
def peekWorkflow(heap):
    """Peek at top of heap without removing"""
    return heap and heap[0][2]

def popWorkflow(heap):
    """Remove top of heap"""
    heapq.heappop(heap)
```

**Design Notes:**
- Uses Python's `heapq` for O(log n) insertion/removal
- Min-heap ordered by absolute deadline (submit_time + deadline_constraint)
- END signal gets artificial deadline of 1000000 (always last)
- Free requests get deadline 0 (always first)

---

## 9. RESOURCE REQUEST PROCESSING

**Location**: `edf_optimized_LA.py:659-913`

### processFreeRequestWithLicenses

This is the main moldable scaling logic that combines:
1. DDM-EDF urgency-based triggers
2. LAMF iteration-weighted constraints
3. License-aware scale-down guards

```python
def processFreeRequestWithLicenses(sim, wf_mb, request):
    # Get workflow state (7-value tuple for LA workflows)
    instances, budget, deadline, start_time, mesh, software_id, license_holds = \
        resource_manager.getWorkflow(request['wf-id'])

    license_pool = license_manager.get_pool_for_software(software_id)

    # Iteration-weighted constraints (from LAMF)
    ind = request['iteration']
    available_time = max(0, deadline - DEADLINE_BUFFER - getTime(sim)) \
                     * OPTIM_FCFS_DFACTOR[ind]

    # === DDM-EDF URGENCY-BASED TRIGGER ===
    slack_ratio = compute_deadline_slack(request['wf-id'], sim)
    urgency_level = get_urgency_level(slack_ratio)

    skip_scale_down = False
    force_scale_up_attempt = False
    urgency_boost = URGENCY_BOOST_SAFE

    if urgency_level == 'CRITICAL':
        skip_scale_down = True
        force_scale_up_attempt = True
        urgency_boost = URGENCY_BOOST_CRITICAL
    elif urgency_level == 'WARNING':
        skip_scale_down = True
        force_scale_up_attempt = True
        urgency_boost = URGENCY_BOOST_WARNING

    # === SCALE DOWN CHECK (with guards) ===
    if not skip_scale_down:
        # Try to find minimum needed instances
        chains_per_node = 3
        while chains_per_node > 0:
            runtime = chains_per_node * runtime_per_model * request['tinyda-iterations']

            if runtime < available_time:
                min_needed_count = request['chains'] // chains_per_node + \
                                   bool(request['chains'] % chains_per_node)

                if cur_count > min_needed_count:
                    # === SMART SCALE-DOWN GUARDS ===
                    should_scale_down = True

                    # GUARD 1: License pool utilization
                    if license_pool:
                        pool_status = license_manager.get_pool_status(license_pool)
                        pool_utilization = pool_status['allocated'] / pool_status['total']
                        if pool_utilization > 0.70:
                            should_scale_down = False
                            blocked_reason = 'license_pool_saturated'

                    # GUARD 2: Iteration number
                    if should_scale_down and ind > 3:
                        should_scale_down = False
                        blocked_reason = 'late_iteration'

                    # GUARD 3: Time progress
                    if should_scale_down:
                        time_progress = (getTime(sim) - start_time) / \
                                        (deadline - start_time)
                        if time_progress > 0.70:
                            should_scale_down = False
                            blocked_reason = 'time_progress'

                    # GUARD 4: Budget progress
                    if should_scale_down:
                        used_budget = metrics.computeCurrentCost(request['wf-id'],
                                                                 getTime(sim))
                        budget_progress = used_budget / budget
                        if budget_progress > 0.50 or time_progress > 0.50:
                            should_scale_down = False
                            blocked_reason = 'budget_or_time_progress'

                    # Execute or block scale-down
                    if should_scale_down:
                        request['count'] = cur_count - min_needed_count
                        metrics.updateResources(...)
                        actual_licenses_released = freeResourcesWithLicenses(...)
                        metrics.recordScaleDownAttempt(success=True, ...)
                        return
                    else:
                        metrics.recordScaleDownAttempt(success=False,
                                                        blocked_reason=blocked_reason)
                        break
            else:
                chains_per_node -= 1

    # === SCALE UP CHECK (with urgency boost) ===
    used_budget = metrics.computeCurrentCost(request['wf-id'], getTime(sim))

    if force_scale_up_attempt:
        available_budget = max(0, budget - used_budget) \
                           * OPTIM_FCFS_BFACTOR[ind] * urgency_boost
    else:
        available_budget = max(0, budget - used_budget) \
                           * OPTIM_FCFS_BFACTOR[ind]

    free_resources = resource_manager.getResources()

    # Check NEW resources with license constraints
    alloc_instances, license_holds_new = checkNewResourcesWithLicenses(
        free_resources, instances, available_budget, available_time,
        request, mesh, license_pool
    )

    if alloc_instances:
        # Allocate compute
        ips, alloc_resources = resource_manager.allocateResources(alloc_instances)

        # Track resource allocation
        metrics.updateResources(...)
        metrics.recordScaleUpAttempt(success=True, ...)

        # Send to executor with new licenses
        sendNewResources(request['wf-id'], ips, alloc_resources, sim,
                         request.get('client-ip', None), license_holds_new)

        # Update workflow's license holds
        if license_holds_new:
            license_holds[request['wf-id']] = \
                license_holds.get(request['wf-id'], []) + license_holds_new

            resource_manager.updateWorkflowLicenses(
                request['wf-id'],
                license_holds[request['wf-id']],
                mode='replace'
            )
    else:
        metrics.recordScaleUpAttempt(success=False, reason=..., ...)
```

### Iteration-Weighted Budget/Deadline Factors

**Location**: `config/constants.py`

```python
# Budget allocation weights per iteration
OPTIM_FCFS_BFACTOR = [0.6, 0.8, 0.9, 0.95, 1.0]

# Deadline allocation weights per iteration
OPTIM_FCFS_DFACTOR = [0.6, 0.75, 0.85, 0.92, 1.0]
```

**Rationale:**
- Early iterations (0-2): Conservative allocation (60-80% of remaining budget/time)
- Late iterations (3-4): Aggressive allocation (95-100% of remaining budget/time)
- Prevents early resource exhaustion while allowing late-stage acceleration

---

## 10. CONFIGURATION PARAMETERS

### DDM-EDF Specific Parameters

**Location**: `config/constants_LA.py:139-156`

```python
# Deadline urgency thresholds for scaling decisions
DEADLINE_URGENCY_CRITICAL = 0.0   # Negative or zero slack → CRITICAL
DEADLINE_URGENCY_WARNING = 0.2    # < 20% slack → WARNING
DEADLINE_URGENCY_SAFE = 0.5       # < 50% slack → SAFE

# Preemptive reallocation settings
PREEMPTIVE_REALLOC_ENABLED = True  # Enable preemptive scale-down for excess slack
EXCESS_SLACK_THRESHOLD = 0.5       # > 50% slack → can donate resources

# Urgency-based boost factors for scale-up
URGENCY_BOOST_CRITICAL = 1.5  # 50% more aggressive for critical workflows
URGENCY_BOOST_WARNING = 1.2   # 20% more aggressive for warning workflows
URGENCY_BOOST_SAFE = 1.0      # Normal allocation for safe workflows
```

### General Scheduler Parameters

**Location**: `config/constants_LA.py:114-123`

```python
# Polling intervals (seconds)
WORKFLOW_POLLING = 30           # Check for new workflows every 30s
RESOURCE_UTILIZATION_POLLING = 20 * 60  # Resource metrics every 20 mins

# Timeouts
RESOURCE_REQUEST_TIMEOUT = 3 * 60  # 3 minutes for moldable requests
LICENSE_HOLD_TTL = 5 * 60          # 5 minutes for license holds

# Thresholds
SPEEDUP_THRESHOLD = 1.4         # Minimum speedup for heterogeneous instances
DEADLINE_BUFFER = 180           # 3 minute buffer before deadline
```

### Workload Generation Parameters

**Location**: `config/constants_LA.py:24-56`

```python
# Total workflows to simulate
TOTAL_WORKFLOWS = 700

# Mesh size distribution
MESH_DISTRIBUTION = {
    1000: 0.25,  # 25% large mesh
    750: 0.25,   # 25% medium mesh
    500: 0.50    # 50% small mesh
}

# Temporal compression factor
TEMPORAL_COMPRESSION_FACTOR = 0.5  # 2× arrival rate (peak demand)

# Submission jitter window (minutes)
SUBMISSION_JITTER_MINUTES = 2  # Tight clustering (burst scenario)

# License type distribution
LICENSE_DISTRIBUTION = {
    'ANSYS': 0.33,
    'ABAQUS': 0.33,
    'LSDYNA': 0.34
}
```

---

## 11. KEY INNOVATIONS SUMMARY

| Innovation | Description | Impact | Location |
|------------|-------------|--------|----------|
| **Deadline Urgency** | Uses `slack_ratio` instead of time/budget progress | More accurate deadline risk assessment | `edf_optimized_LA.py:252-298` |
| **Urgency Boost** | 1.5× budget multiplier for CRITICAL workflows | Saves workflows from missing deadlines | `edf_optimized_LA.py:702-720` |
| **Preemptive Reallocation** | Scale down EXCESS to help CRITICAL | Improves deadline success rate | `edf_optimized_LA.py:319-367` |
| **Smart Scale-Down Guards** | 4 guards prevent premature resource release | Avoids thrashing, license contention | `edf_optimized_LA.py:722-818` |
| **Partial License Release** | Release 50%, keep 50% buffer | Reduces license re-acquisition overhead | `edf_optimized_LA.py:591-642` |
| **Sophisticated Moldability** | 3-tier allocation with speedup/closeness checks | Optimizes cost/performance tradeoff | `scheduler_LA.py:505-650` |
| **Two-Phase License Commit** | Atomic compute + license allocation | Prevents resource/license mismatches | `scheduler_LA.py:64-147` |

---

## 12. PERFORMANCE CHARACTERISTICS

### Strengths

- **High deadline success rate**: Urgency-based scaling prioritizes at-risk workflows
- **License efficiency**: Partial release + preemptive reallocation reduce contention
- **Cost optimization**: Moldable allocation maximizes nodes_per_chain for better speedup
- **Heterogeneity handling**: Works across on-prem, reserved, on-demand instances
- **Fairness**: Two-phase commit prevents license hoarding
- **Adaptability**: Dynamic scaling responds to runtime variations

### Limitations

- **Slack estimation imperfect** (line 287): Uses conservative "2 iterations remaining" estimate
  - Could be improved with actual iteration tracking from executor
- **Preemptive reallocation overhead**: Scales down workflows that may need resources later
  - Risk of ping-pong effect if thresholds not well-tuned
- **License calculation coupling**: Requires accurate license token formulas per software
  - Errors in formula → over/under-allocation
- **No gang scheduling**: Workflows compete for resources, not coordinated co-scheduling
  - Could lead to fragmentation with heterogeneous workflows
- **Cache coherence**: 10-second slack cache may be stale in rapidly changing conditions
  - Trade-off between accuracy and overhead

### Scalability Considerations

- **Heap operations**: O(log n) for workflow/request insertion
- **Slack calculation**: Cached for 10s, amortizes cost
- **Preemptive reallocation**: Limited to top 2 workflows, bounded overhead
- **License checks**: O(1) pool status lookups
- **Polling interval**: 30s loop → max 700 workflows = 23 per second decision rate

---

## 13. RESEARCH CONTEXT

### Building Blocks

This algorithm builds on:

- **LAMF (License-Aware Moldable First)**: Base license management
  - Two-phase commit protocol
  - License pool management
  - Dual-resource allocation

- **FCFS_Optimized**: Sophisticated moldability (3-tier strategy)
  - Nodes_per_chain optimization
  - Instance closeness checking
  - Speedup threshold enforcement

- **EDF (Earliest Deadline First)**: Classical real-time scheduling
  - Optimal for single-resource systems under preemption
  - Deadline-based priority ordering

- **DDM (Deadline-Driven Moldability)**: Original contribution combining urgency + moldability

### Novel Contributions

1. **Deadline urgency as primary scaling metric** (not time/budget progress)
   - Slack ratio directly measures deadline risk
   - Enables proactive scaling before deadlines become critical

2. **Preemptive resource reallocation** between running workflows
   - Workflows donate resources to help others
   - Goes beyond traditional EDF (which only preempts new arrivals)

3. **Partial license release strategy** (50% retention)
   - Balances license availability with thrashing prevention
   - Novel approach to dual-resource elasticity

4. **Integration of moldability, elasticity, and license awareness** in a single scheduler
   - Most prior work addresses these orthogonally
   - Unified framework for multi-constraint optimization

### Comparison to Related Work

| Scheduler | EDF Ordering | Moldability | License-Aware | Preemptive Realloc | Urgency-Based Scaling |
|-----------|--------------|-------------|---------------|--------------------|-----------------------|
| EDF (Classic) | ✓ | ✗ | ✗ | ✗ | ✗ |
| LAMF | ✗ (FCFS) | ✓ | ✓ | ✗ | ✗ |
| FCFS_Optimized | ✗ (FCFS) | ✓ | ✗ | ✗ | ✗ |
| **EDF_Optimized_LA** | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## 14. FUTURE IMPROVEMENTS

### Identified Limitations with Solutions

1. **Improve slack estimation** (line 287)
   ```python
   # Current: Conservative 2-iteration estimate
   estimated_remaining_iterations = 2

   # Better: Track actual iteration progress from executor
   completed_iterations = executor.get_completed_iterations(wf_id)
   total_iterations = workflowConfig length
   remaining_iterations = total_iterations - completed_iterations

   # Best: Use per-iteration timing to predict remaining time
   avg_iteration_time = sum(iteration_times) / len(iteration_times)
   time_needed = avg_iteration_time * remaining_iterations
   ```

2. **Adaptive urgency thresholds**
   ```python
   # Current: Static thresholds
   DEADLINE_URGENCY_CRITICAL = 0.0
   DEADLINE_URGENCY_WARNING = 0.2

   # Better: Adapt based on system load
   if system_utilization > 0.90:
       DEADLINE_URGENCY_WARNING = 0.3  # More conservative under load
   else:
       DEADLINE_URGENCY_WARNING = 0.15  # More aggressive when resources available
   ```

3. **Predictive preemptive reallocation**
   ```python
   # Current: React to current slack
   if slack < 0.0:
       critical_workflows.append(wf)

   # Better: Predict future slack based on resource allocation trends
   predicted_slack = predict_slack_at_time(wf, t + LOOKAHEAD_WINDOW)
   if predicted_slack < 0.0:
       preemptively_boost(wf)  # Earlier intervention
   ```

4. **Gang scheduling for co-located workflows**
   ```python
   # Current: Workflows compete independently
   # Better: Coordinate workflows sharing licenses

   workflows_using_ansys = [wf for wf in running
                            if wf.license_pool == 'ANSYS']

   # Coordinate scale-down to avoid license fragmentation
   coordinated_scale_down(workflows_using_ansys, target_free_licenses=1000)
   ```

5. **Machine learning for moldability decisions**
   ```python
   # Current: Rule-based moldability (3-tier strategy)
   # Better: Learn optimal nodes_per_chain from historical data

   features = [mesh_size, current_nodes, budget_remaining, time_remaining]
   optimal_nodes_per_chain = ml_model.predict(features)
   ```

### Research Directions

- **Multi-objective optimization**: Balance deadline, cost, AND license fairness
- **Stochastic modeling**: Handle uncertain runtime/resource availability
- **Distributed scheduling**: Scale to multi-cluster environments
- **Energy-aware scheduling**: Add power consumption as constraint
- **QoS differentiation**: Support priority classes (gold/silver/bronze SLAs)

---

## 15. IMPLEMENTATION DETAILS

### File Structure

```
Vortex-moldable-sched/
├── src/main/
│   ├── scheduler/
│   │   ├── edf_optimized_LA.py         # Main scheduler (this file)
│   │   ├── scheduler_LA.py             # Base license-aware scheduler
│   │   ├── fcfs_optimized_LA.py        # FCFS variant with moldability
│   │   └── ...
│   ├── resource_manager/
│   │   ├── resource_manager_LA.py      # Resource pool management
│   │   ├── license/
│   │   │   ├── manager.py              # License pool manager
│   │   │   ├── accounting.py           # Token calculation strategies
│   │   │   └── exceptions.py           # License-specific exceptions
│   │   └── instance.py                 # Instance abstractions
│   ├── config/
│   │   ├── constants_LA.py             # LA-specific configuration
│   │   ├── constants.py                # Base configuration
│   │   ├── resources.yaml              # Resource pool definitions
│   │   └── licenses.yaml               # License pool definitions
│   ├── utils/
│   │   ├── metrics_LA.py               # Extended metrics tracking
│   │   ├── exec_sched.py               # Workflow type detection
│   │   └── ...
│   └── workflow/
│       └── sample_workflows_LA/        # LA workflow definitions
│           ├── data0.yaml
│           ├── data1.yaml
│           └── ...
└── EDF_Optimized_LA_Analysis.md       # This document
```

### Key Classes

**EDF_Optimized_LA** (`edf_optimized_LA.py:48-913`)
- Inherits from `Scheduler_LA`
- Manages EDF heaps (workflow_heap, resource_request_heap)
- Implements preemptive reallocation
- Overrides resource request processing with urgency logic

**Scheduler_LA** (`scheduler_LA.py:28-650`)
- Base class for all license-aware schedulers
- Implements `checkNewResources()` with sophisticated moldability
- Provides `allocateResourcesWithLicenses()` with two-phase commit
- Defines `freeResourcesWithLicenses()` with partial release

**ResourceManager_LA** (`resource_manager/resource_manager_LA.py`)
- Extends base ResourceManager with license tracking
- Maintains workflow state as 7-tuples: (instances, budget, deadline, start_time, mesh, software_id, license_holds)
- Integrates LicenseManager for dual-resource management

**LicenseManager** (`resource_manager/license/manager.py`)
- Manages multiple license pools (ANSYS, ABAQUS, LSDYNA)
- Implements two-phase commit (hold → commit/release)
- Supports multiple token calculation strategies
- Tracks allocations and provides pool status

### Workflow Execution Flow

```
1. User submits workflow → Job Server (port 8080)
2. Workflow added to queue (Redis)
3. EDF_Optimized_LA.run() loop:
   a. PHASE 1: Preemptive reallocation
      - Compute slack for all running workflows
      - Scale down EXCESS, free resources

   b. PHASE 2: Process resource requests
      - Pop earliest-deadline request from heap
      - Compute urgency, apply boost if CRITICAL/WARNING
      - Attempt scale-up/down with license constraints

   c. PHASE 3: Schedule new workflows
      - Pop earliest-deadline workflow from heap
      - Allocate compute resources (moldable)
      - Hold licenses (two-phase commit)
      - Send to executor (port 8089)

4. Executor runs workflow:
   - Executes iterations sequentially
   - Sends resource requests between iterations
   - Reports completion to Completion Server (port 8082)

5. Scheduler processes completion:
   - Releases compute resources
   - Releases licenses
   - Updates metrics
```

### Communication Patterns

**Scheduler → Executor** (via sendWorkflowForExecution):
```python
request = {
    "initial-alloc": True,
    "wf-plan": wf_plan,
    "hosts": ips,  # {on-prem: {...}, reserved: {...}, on-demand: {...}}
    "deadline": deadline,
    "license-holds": license_holds  # [hold_id1, hold_id2, ...]
}
```

**Executor → Scheduler** (via resource_request_queue):
```python
request = {
    "request": ExecutorRequest.REQUEST_RESOURCE.value,  # or FREE_RESOURCE
    "wf-id": wf_id,
    "iteration": iteration_index,
    "chains": num_chains,
    "tinyda-iterations": num_tinyda_iterations,
    "count": instances_requested,  # >0 for scale-up, <0 for scale-down
    "client-ip": executor_ip,
    "request-time": timestamp
}
```

**Executor → Scheduler** (completion notification):
```python
completion = {
    "wf-id": wf_id,
    "status": "SUCCESS" | "FAILED",
    "completion-time": timestamp
}
```

---

## 16. TESTING & VALIDATION

### Test Workflows

**Location**: `src/main/workflow/sample_workflows_LA/`

- **700 workflows** generated with varying:
  - Mesh sizes: 500 (50%), 750 (25%), 1000 (25%)
  - License pools: ANSYS (33%), ABAQUS (33%), LSDYNA (34%)
  - Budgets: Derived from mesh size and software
  - Deadlines: Derived from mesh size and temporal compression

**Temporal Compression**:
```python
TEMPORAL_COMPRESSION_FACTOR = 0.5  # 2× arrival rate (peak demand)
SUBMISSION_JITTER_MINUTES = 2      # Tight clustering (burst scenario)
```

### Metrics Tracked

**Location**: `utils/metrics_LA.py`

1. **Workflow metrics**:
   - Completion time
   - Deadline success rate
   - Cost (actual vs budget)
   - Makespan

2. **Resource metrics**:
   - CPU utilization
   - Instance allocation timeline
   - Scale-up/down events

3. **License metrics**:
   - Pool utilization over time
   - Token allocation/release events
   - Hold duration statistics

4. **Moldability metrics**:
   - Scale-up attempts (success/failure reasons)
   - Scale-down attempts (success/blocked reasons)
   - Nodes_per_chain distribution
   - Urgency level distribution

### Running Simulations

```bash
# Run EDF_Optimized_LA scheduler
cd /Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched
python src/main/simulate_main_LA.py

# Configuration in config/constants_LA.py:
# - Set TOTAL_WORKFLOWS = 700
# - Set TEMPORAL_COMPRESSION_FACTOR = 0.5
# - Set SUBMISSION_JITTER_MINUTES = 2
# - Ensure PREEMPTIVE_REALLOC_ENABLED = True

# Output:
# - Metrics CSV: results/EDF_700_metrics.csv
# - Resource utilization: results/EDF_700_utilization.csv
# - License pool stats: results/EDF_700_license_pools.csv
```

---

## 17. CONCLUSIONS

The **EDF_Optimized_LA scheduler** represents a sophisticated research-grade scheduler for **multi-resource constrained scientific workflows**. It successfully integrates:

1. **Classical scheduling theory** (EDF)
2. **Practical systems concerns** (moldability, elasticity)
3. **Dual-resource management** (compute + licenses)
4. **Novel algorithmic contributions** (deadline urgency, preemptive reallocation)

The scheduler demonstrates strong systems research combining:
- **Real-time scheduling theory**
- **Resource management**
- **Practical HPC/cloud systems engineering**

Key takeaways:
- **Deadline slack** is a more direct urgency metric than time/budget progress
- **Preemptive reallocation** between running workflows improves deadline success
- **Partial license release** balances availability with thrashing prevention
- **Smart guards** are essential for stable elastic scaling in production systems

This implementation provides a solid foundation for future research in:
- Multi-objective workflow scheduling
- License-aware resource management
- Deadline-driven moldable scheduling
- Hybrid cloud-HPC environments
