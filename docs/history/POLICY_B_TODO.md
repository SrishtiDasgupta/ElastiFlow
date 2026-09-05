# Policy B — Phase-Aware Heterogeneity (Deferred)

This document tracks the structural changes needed to fully implement Policy B
(intra-family size mixing for HPO, gated by per-trial coupling). The
risk-free, additive substrate has been landed; this file enumerates the
work that touches the Driver↔Scheduler protocol and is therefore deferred
until after the experimental campaign on the single-size inventory.

## What has already been landed (additive, no behaviour change)

1. **Name-keyed runtime registry** —
   `src/main/scripts/speedup_HPO_runtime.py::RUNTIME_MODELS`.
   New entry-point `getRuntime(name, workers, model, epochs)` dispatches by
   full instance name. Legacy `getRuntime_g4` / `getRuntime_g5` are now thin
   wrappers around `getRuntime('g4dn.xlarge', …)` and
   `getRuntime('g5.xlarge', …)` respectively. Adding a new size = drop in a
   `(a, b, model_factors)` tuple.

2. **Family/size split** —
   `src/main/scheduler/scheduler_HPO.py` exposes module-level
   `getFamily(name)` and `getInstanceKey(name)`. Family captures GPU-arch
   compatibility (the predicate Policy B's bracket will use); the key is the
   full inventory name (the predicate the resize lock already uses).

3. **Per-(family, size) cost table** —
   `INSTANCE_COSTS` in `src/main/config/constants_HPO.py` has additive entries
   `cloud_{reserved,ondemand}_<full-name>` for `g4dn.{xl,2xl}` and
   `g5.{xl,2xl}`. The size-collapsed legacy keys
   (`cloud_reserved_g4dn`, …) are untouched and remain the only ones the
   current code reads.

These three changes are provably no-ops for the deployed inventory: each
family contains exactly one size, so name-keyed lookups, family-keyed
classification, and the existing legacy cost keys all resolve identically.

## What is deferred (structural, requires cluster validation)

### D1. Lift `calculate_optimal_batches` into the scheduler

The phase decomposition currently runs inside the Driver
(`fsx/hyperparameter_test/hpo_pipeline_verbose.py:82–325`) after the binding
has already happened. This is what forces the scheduler into worst-case
homogeneity: the same allocation may serve `parallel`, `balanced`, or
`distributed` phases, and the scheduler cannot tell which.

Lifting the function into the scheduler lets allocation and phase plan be
decided jointly. The scheduler computes `phase_plan = calculate_optimal_batches(num_trials, num_hosts, η)`
*before* binding; the bracket predicate (D2) then dispatches per phase.

### D2. Coupling-aware bracket predicate

Replace `inst.name == current_instance_type` (the strict-name lock at
`edf_optimized_HPO.py:676` and `fcfs_optimized_HPO.py:458`) with:

```python
def admissible(inst, anchor_runtime_set, phase):
    if getFamily(inst.name) != getFamily(anchor_name):
        return False                                  # cross-family always rejected
    if phase['workers_per_trial'] > 1:
        return inst.name == anchor_name              # tight coupling → strict size
    runtime = getRuntime(inst.name, 1, model, epochs)
    return any(math.isclose(runtime, r, rel_tol=0.15)
               for r in anchor_runtime_set)          # parallel mode → SeisSol-style bracket
```

The bracket reuses the SeisSol primitive in
`src/main/scheduler/scheduler_LA.py::checkCloseness` (`rel_tol=0.15`).

### D3. Driver↔Scheduler protocol change

The Driver currently receives only `--hosts <int>`. It would need:
- a phase plan (list of `(workers_per_trial, concurrent_trials, num_trials, mode)`),
- per-phase node lists, which are **homogeneous** for `balanced`/`distributed`
  phases and may be **heterogeneous within family** for `parallel` phases.

Concrete touch points:
- `service/plcl_runner_HPO.py::_generate_slurm_script` — emit the plan as a
  JSON file on `/fsx`, pass its path on the CLI.
- `fsx/hyperparameter_test/hpo_pipeline_verbose.py` — accept the plan instead
  of computing it. `calculate_optimal_batches` becomes dead code here (kept
  only as a fallback if the JSON file is absent).
- `service/cloud_runner_HPO_new.py` — symmetric change for the cloud path.

### D4. Heterogeneity-aware resize

`processMoldableRequestHPO` (`edf_optimized_HPO.py:469–642`) currently grows
strictly within `current_instance_type`. Generalise to:
- if next iteration's plan has any `workers_per_trial > 1` phase → strict
  same-name growth (today's behaviour),
- else → bracket within family.

`checkNewResourcesHPO` filter at line 676 changes from
`inst.name == instance_type_filter` to
`admissible(inst, anchor_runtime_set, phase)` from D2.

### D5. Heterogeneous costing

For heterogeneous parallel-mode bindings, predicted runtime is
`max_i runtime_i` (straggler-bound) and cost is
`Σ_i runtime_i × cost_per_second_i`. This is already what the on-demand
fallback does at `edf_optimized_HPO.py:258` — generalise the homogeneous path
to the same per-instance integration.

## Validation plan when D1–D5 land

1. Tag baseline (already done: `baseline-pre-adaptive-driver` at `4edf1d4`).
2. Smoke-test all four workflow types end-to-end on the rebuilt cluster:
   PLAIN, LA, HPO, PLAIN_ADAPTIVE. Each must produce identical output to the
   pre-Policy-B run on the same input YAML.
3. Run a single HPO workflow with `g4dn.xlarge + g4dn.2xlarge` mixed
   inventory to exercise the bracket. Verify:
   - `parallel` phases see heterogeneous bin-packing,
   - `balanced`/`distributed` phases see homogeneous groups,
   - resize across iterations preserves the per-phase admissibility rule.
4. Confirm cost-accounting matches `Σ runtime_i × rate_i` for the
   heterogeneous run.

## Why this is deferred

D1–D5 change the Driver↔Scheduler protocol. The deployed inventory does not
exercise the new behaviour (each family has one size), so the change cannot
be validated experimentally on the current cluster. Implementing D1–D5
without a multi-size cluster means landing untested protocol code on the
critical path of the HPO experimental campaign — bad cost/risk trade-off.

The thesis presents Policy B as the framework's general form and the
single-size deployment as its instantiated sub-case; the runs validate the
sub-case, the design validates the general form.

## D6. Restructure to match SeisSol's inline plan-and-bind pattern

D1–D5 as written treat planning and binding as two staged steps with an
explicit phase-plan artefact carried over the Driver↔Scheduler protocol.
SeisSol's moldable scheduler does the same joint decision *inline* inside
the binding loop (`fcfs_optimized.py:108-121`): an outer decrement on the
moldability parameter (`nodes_per_chain`), with admissibility, budget, and
deadline checked per candidate. There is no separate "plan" object — the
plan is the byproduct of the binding loop, and the driver receives a fully
decided binding plus an intra-binding placement step (the chain-distribution
heap in `tinyda_client.py::sequentialChainAllocation`).

HPO's joint problem is structurally isomorphic to SeisSol's; it should be
restructured to the same inline pattern.

### Refactor

Move `calculate_optimal_batches` out of
`fsx/hyperparameter_test/hpo_pipeline_verbose.py` and into the binding loops
inside `checkNewResourcesHPO` and `processMoldableRequestHPO`
(`fcfs_optimized_HPO.py`, `edf_optimized_HPO.py`). The loop body becomes:

```
for num_hosts in candidate_host_counts(descending):
    plan = calculate_optimal_batches(num_trials, num_hosts, η)
    nodes_per_phase = bind_per_phase(plan, inventory, admissible)
    if nodes_per_phase is None: continue          # admissibility failed
    cost = straggler_bound_cost(plan, nodes_per_phase)
    if cost > available_budget: continue          # budget failed
    if predicted_runtime(plan, nodes_per_phase) > available_time:
        return []                                  # deadline monotone — abandon
    return (plan, nodes_per_phase)
return []
```

The outer iteration order matters: larger `num_hosts` typically buys
shorter time but more cost, so iterating descending mirrors SeisSol's
"start expensive, decrement on budget failure". Deadline failure aborts
(monotone in the same direction as SeisSol).

### Protocol simplification

D3 collapses. The binding payload already encodes per-phase node lists as
a byproduct of `bind_per_phase`, so the driver receives one structured
object instead of `(binding, plan_path_on_/fsx)`. Both runners shed the
JSON-plan-pickup step:

- `service/plcl_runner_HPO.py::_generate_slurm_script` — emit only the
  per-phase node lists in the SLURM script header / env; no separate
  `/fsx/<wf-id>_plan.json` artefact.
- `service/cloud_runner_HPO_new.py` — symmetric; the per-phase node lists
  are passed to the pipeline directly.
- `fsx/hyperparameter_test/hpo_pipeline_verbose.py` —
  `calculate_optimal_batches` is removed entirely (no fallback needed; the
  driver never plans). The pipeline becomes a pure executor of the
  scheduler's binding, analogous to `tinyda_client.py`.

### Observable scheduling-decision differences vs the staged D1–D5 shape

Under the staged shape, planning fixes `num_hosts` first and binding tries
to satisfy it; an infeasible first pick requires an explicit replan loop.
Under the inline shape, `(plan, binding)` are co-iterated, so:

1. Tighter feasibility frontier — bindings that the staged shape would
   reject as infeasible at the first `num_hosts` pick are found by the
   decrement.
2. Better budget compliance under tight budgets — if a coupled plan is
   unaffordable on strict-name instances but a parallel plan on
   within-family bracket-mates is affordable, the inline loop finds it
   without an outer replan loop.
3. Resize across iterations becomes consistent with initial allocation —
   the same joint loop runs at every iteration boundary, the way SeisSol's
   already does.
4. No behavioural change in the *outputs* when both shapes are correctly
   implemented — same predicate, same cost model, same bracket tolerance.
   Only the order of evaluation and the code locality differ.

### Branching-factor caveat

SeisSol's inline loop walks a single integer (`nodes_per_chain ∈ {1..4}`).
HPO's inline loop calls `calculate_optimal_batches` per iteration, which
produces a list of phase tuples — denser per-iteration work than SeisSol's
one-line evaluation. The function is cheap, so the loop is still
inexpensive in absolute terms, but the iteration order over
`num_hosts` should be chosen so that the first feasible candidate is also
a good candidate (descending from `min(num_trials, total_inventory)` is
the natural choice, mirroring SeisSol's `nodes_per_chain` decrement from
4 downward).

### Status

D6 supersedes D3 and tightens D1, D4. D2 (the `admissible(inst, phase)`
predicate) and D5 (straggler-bound cost) are reused unchanged — they
become the inner-loop primitives of the inline structure rather than
upstream gates of a staged structure.

The validation plan is the same as for D1–D5: smoke-test all four workflow
types end-to-end on the rebuilt cluster, then run a mixed-size HPO
workflow and verify per-phase admissibility, heterogeneous bin-packing in
parallel phases, homogeneous groups in coupled phases, and
`Σ runtime_i × rate_i` cost accounting.
