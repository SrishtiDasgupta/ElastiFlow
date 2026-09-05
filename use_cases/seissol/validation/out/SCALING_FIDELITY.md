# Scaling Fidelity: From Validated N=10 to Simulator-Only Larger N

**Goal**: justify trust in simulator results at workload scales (N) and pool
sizes larger than the N=10 / single-on-prem-node configuration validated
empirically in `DEVIATION_REPORT.md`.

**Approach**: a two-part argument — (A) a structural proof, from the
scheduler source code, that the per-decision logic is N-invariant; and
(B) explicit, honest limitations of the empirical sample, including the
fact that the BMW workflow definitions are no longer available to extend
the empirical sweep.

The original plan also included a sim-only empirical sweep at N ∈ {25, 50,
100, …} with confidence bands derived from the N=10 deviation analysis.
That sweep is **not** included here (see "Why no empirical sweep" below):
the BMW workload yamls were lost, so any sweep would necessarily run a
*different* workload than the one validated, breaking the chain of
evidence between the bands measured at N=10 and the curves at larger N.

---

## A. Structural N-invariance of the scheduler

The scheduler's per-decision behaviour is independent of the total
workload size N. This is a property of the source code, not an empirical
claim, and can be checked by reading
`src/main/scheduler/fcfs_optimized.py` and `src/main/scheduler/scheduler.py`.

### A.1 The main loop processes one workflow at a time

`fcfs_optimized.py:36-94` (the `run` loop):

```python
while True:
    # Check queue for resource requests
    resource_request = peekElement(resource_request_mb, self.resource_request_queue)
    ...
    # Check the queue for new jobs
    workflow_plan = peekElement(wf_mb, self.queue)
    ...
    if workflow_plan:
        wf_plan = eval(workflow_plan)
        ...
        ips, alloc_resources = self.allocateResources(constraints)
        ...
        if ips:
            removeElement(wf_mb, self.queue)
            ...
    (sim or time).sleep(WORKFLOW_POLLING)
```

The loop calls `peekElement` (Redis `LRANGE 0 0`, O(1)) and at most one
`removeElement` per iteration. It **never iterates over the queue** to
look at the second-or-later workflow, never counts pending jobs, and
never inspects how many workflows have already completed. The
per-iteration work is bounded by the size of the resource pool and the
constraints of the *single* workflow at the queue head.

### A.2 The allocation decision depends only on the workflow and the pool

`fcfs_optimized.py:99-216` (`checkNewResources`) implements the actual
decision rule. Its inputs are:

- `request` (this workflow's `chains`, `count`, `tinyda-iterations`)
- `current_resources` (what the workflow already holds, if reallocating)
- `budget`, `available_runtime` (this workflow's deadline/budget headroom)
- `mesh` (this workflow's compute-cost model)
- `resources` (the global free pool from `ResourceManager`)

The function iterates over `resources` (line 138, `for inst in instances`)
and applies budget/speedup/runtime checks per instance. Cost is
**O(|pool|)**, not O(N).

There is no global state of "how many workflows are in flight", "how
many have arrived", or "how many remain". The decision rule is a pure
function of `(this_workflow, current_pool_state)`.

### A.3 What *does* depend on N (and is correctly N-dependent)

The `current_pool_state` itself depends on N — at higher N, more
workflows are concurrently active, so the free pool is smaller more
often. This is the *contention* dimension of N-dependence, and it is
genuine system behaviour that the simulator must reproduce. The N=10
logs already exercise this: `infra_v2`/`v3` show wf9 (`4b52291f`)
tipping to cloud because three other workflows are still occupying
on-prem when it arrives. The simulator reproduces the same
contention-driven decision rule on the same input pool state (see
the `4b52291f` case study in `DEVIATION_REPORT.md`).

### A.4 Conclusion

> **The scheduler's per-decision behaviour is invariant in N.** Increasing
> N changes how often resources are contended at decision time, but does
> not change the rule by which contention is resolved. The validated
> N=10 evidence therefore extends to larger N for the *decision-logic*
> dimension of fidelity, by construction.

Resource-pool size scales the same way: the inner loop at
`fcfs_optimized.py:138` is O(|pool|), so larger pools just mean more
work per decision, with no behavioural change. The per-instance
budget/runtime checks are independent of how many other instances exist.

---

## B. Limitations and what cannot be claimed

### B.1 Why no empirical sweep

A sim-only scaling sweep at N > 10 was originally planned to provide an
illustrative figure. It is not included for one principled reason:

> **The BMW workflow definitions (the 10 specific `(mesh, chains,
> tinydaIterations, cohesion)` tuples on which the validation was
> performed) are no longer available.**

The yamls present in `src/main/sample_workflows/` are a different
workload set. A sweep on those would produce a real scaling curve, but
it would be a curve for **a different workload than the one validated**.
The MAPE confidence bands measured at N=10 (e.g. −30% bias on c6i,
−8% on hpc7a) were measured against the BMW workflows specifically.
Whether those bands transfer to other workflows depends on whether the
simulator's bias lives in:

- The `getRuntime` model coefficients themselves (workload-independent —
  bands transfer), or
- The model's response to particular `(mesh, chains, tinydaIterations)`
  ranges that BMW happened to occupy (workload-specific — bands do not
  transfer cleanly).

Without re-running BMW-equivalent inputs on infra, there is no way to
disambiguate these. Producing a sweep on a different workload and
overlaying the BMW-derived bands would be misleading. Producing the
sweep without bands would not advance the fidelity argument.

The honest move is to leave the empirical sweep out and rely on the
structural argument in section A, plus the empirical evidence at the
validated scale documented in `DEVIATION_REPORT.md`. A runnable sweep
harness (`run_scaling_sweep.py` in this directory) is left in place
in case future work re-creates an equivalent workload and wants to
extend the validation.

### B.2 Cloud burst contention

The N=10 sample produces, at peak, three concurrent on-demand
provisioning events. At larger N, the AWS API behaviour under burst
(tens or hundreds of simultaneous on-demand requests) is qualitatively
different from what either the simulator or the N=10 infra runs
observed. The simulator models cold-start as an i.i.d. parametrised
delay; reality has correlated delays, throttling, and capacity-pool
exhaustion. **State this explicitly in the thesis as a limitation of
the scaling claim**, scoped to the on-demand path.

### B.3 Co-tenancy effects on on-prem

The `wf2` outlier in the N=10 noise floor (CV 58% across infra
replicates: durations 750s vs 2983s vs 3043s for the *same* workflow
on the *same* on-prem node) is consistent with on-prem co-tenancy
interference. The simulator currently models per-workflow runtime
independently. At larger N with more concurrent on-prem workflows,
this could become a larger discrepancy. The on-prem MAPE band (±5%)
excludes wf2; including it widens to ±30%. Report both in the thesis.

### B.4 Pool composition assumptions

The N=10 validation used one specific mix of reserved + on-demand
instance types. Different pool compositions exercise the allocation
policy differently. The structural argument (A) holds across pool
compositions; the empirical bands documented in `DEVIATION_REPORT.md`
were measured on the BMW pool and are most reliable when extrapolated
within similar pool compositions.

### B.5 Cost-of-validation framing

Each infra run requires provisioning real reserved + on-demand
instances + FSx for one ~15-minute workload, at significant per-run
cost. A wider empirical sweep was infeasible at the time of the
validation campaign and is not feasible to re-run now. The cost
constraint is the justification for relying on the simulator for the
bulk of the thesis results, and the deviation results in
`DEVIATION_REPORT.md` plus the structural argument here are what makes
that reliance defensible.

---

## C. Headline claim for the thesis (assembled from layers 1 + 2 + 3)

> The Vortex simulator is a faithful surrogate for the deployed system
> at the validated scale (decision-policy agreement: 100% multiset, 80%+
> per-workflow assignment with disagreements traceable to a single
> calibratable runtime parameter; aggregate makespan, cost, and
> utilisation within 7% of infra; per-workflow timing error smaller
> than infra-vs-infra variance across operational sessions). The
> scheduler's per-decision logic is N-invariant by construction, so
> this fidelity extends to larger N along the decision-policy axis
> directly. Empirical scaling validation beyond N=10 was foreclosed by
> the loss of the original validation workload and the cost of
> re-running infra; this is acknowledged as a limitation of the
> empirical envelope, not a failure of the scheduler logic. Cloud
> burst contention and on-prem co-tenancy at scale are stated as
> further explicit limitations.
