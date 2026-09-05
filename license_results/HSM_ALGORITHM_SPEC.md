# HSM (Hybrid Static–Moldable, license-aware) — algorithm spec

Self-contained handoff. Assumes no access to the repo. Enough to re-derive, fix,
or rewrite `scheduler/edf_hsm_LA.py` (class `EDF_HSM_LA`).

---

## 1. One-paragraph summary
HSM is a deadline-ordered (EDF), **license-aware elastic** scheduler for
license-constrained workflow execution. It is identical to the baseline elastic
policy **EDF-LAMF** (`EDF_Optimized_LA`) except for one addition: each workflow
runs in a **STATIC phase** (holds its current allocation; may scale **up** for
deadline safety, may **not** scale **down**) and makes a **one-way transition**
to a **MOLDABLE phase** (full EDF-LAMF cost-optimizing scale up/down) only once it
is *both* **on-track on its deadline** *and* **its license pool is uncontended**.
The transition is decided **per workflow** from its own projected slack and the
**global per-pool** token occupancy.

## 2. Where it lives
- Class `EDF_HSM_LA(Scheduler_LA)` in `src/main/scheduler/edf_hsm_LA.py`.
- Near-copy of `EDF_Optimized_LA` (EDF-LAMF). The **only** behavioral diffs are
  (a) the phase gate and (b) per-pool thresholds. Everything else — EDF heap
  ordering, initial allocation, urgency triggers, scale-down guards, partial
  license release — is shared/identical to EDF-LAMF.
- CLI scheduler id: `EDF-HSM`. Display name maps `EDF-HSM -> "HSM"`.
- LA-only (the system also has Plain and HPO use-cases; ignore those here).

## 3. Core idea: two per-workflow phases
- **STATIC**: hold the current allocation. Scale-**up** still allowed (driven by
  the existing deadline-urgency triggers). Scale-**down suppressed**.
- **MOLDABLE**: full EDF-LAMF logic (scale up and down, cost-optimizing).
- Transition is **STATIC → MOLDABLE only** (one-way; never re-enters STATIC).
- Per-workflow state: `self.hsm_phase: dict[wf_id -> 'STATIC'|'MOLDABLE']`,
  initialized empty in `__init__`; missing key means STATIC.

## 4. The phase gate (the heart of HSM)
`EDF_HSM_LA._hsm_in_static_phase(...)` returns `True` if the workflow should
**remain STATIC** this renegotiation (suppress scale-down), else `False`.

```python
def _hsm_in_static_phase(self, request, instances, deadline, start_time, mesh,
                         license_pool, software_id, ind, sim) -> bool:
    wf_id = request['wf-id']
    if self.hsm_phase.get(wf_id, 'STATIC') == 'MOLDABLE':
        return False                                  # already moldable -> stay

    # --- projected deadline slack at the CURRENT (held) allocation ---
    cur_instance = instances[-1][0]
    cur_count    = instances[-1][1]
    if not isinstance(instances[0][0], OnPremInstance):
        cur_count = sum(t[1] for t in instances)      # cloud: count across instances
    cur_count = max(1, cur_count)

    runtime_per_model   = getRuntime(1, mesh, cur_instance.name)   # 1 chain on this instance
    chains              = request['chains']
    tinyda              = request['tinyda-iterations']
    chains_per_node_eff = -(-chains // cur_count)      # ceil(chains/cur_count)
    runtime_per_iter    = chains_per_node_eff * runtime_per_model * tinyda

    last_iter        = max(OPTIM_FCFS_DFACTOR)         # highest iteration index (=5; iters 0..5)
    remaining_iters  = max(0, last_iter - ind)
    now              = getTime(sim)
    projected_finish = now + remaining_iters * runtime_per_iter
    slack            = (deadline - DEADLINE_BUFFER) - projected_finish
    window           = max(1e-9, deadline - start_time)
    safe             = slack >= HSM_SLACK_TAU * window

    # --- license-pool pressure vs THIS pool's own threshold ---
    pool_pressure = 0.0
    rho           = HSM_POOL_RHO_DEFAULT
    if license_pool:
        ps = self.license_manager.get_pool_status(license_pool)   # {'allocated','total',...}
        pool_pressure = ps['allocated'] / ps['total'] if ps['total'] else 0.0
        rho = HSM_POOL_RHO.get(license_pool, HSM_POOL_RHO_DEFAULT)
    cheap = pool_pressure < rho

    floor_ok = ind > STATIC_PHASE_ITERS               # hard minimum static window

    if floor_ok and safe and cheap:                   # RELEASE requires BOTH
        self.hsm_phase[wf_id] = 'MOLDABLE'
        return False
    return True                                       # HOLD (stay static)
```

**Release rule = `safe AND cheap` (conjunction, NOT disjunction).** Preserve this
if rewriting. Rationale: the slack/`safe` estimate is **contention-blind** (the
runtime model is linear in co-located chains with **no co-location penalty**), so
a slack-only release is over-optimistic and triggers premature-scale-down ->
panic-scale-up thrash. The license-pool signal must be able to **veto** a release.
In practice `safe` is almost always true (deadlines are generous), so
**`cheap` (per-pool ρ) is the binding signal.**

## 5. Parameters and deployed defaults (all env-overridable)
Module-level in `edf_hsm_LA.py`:
```python
import os
STATIC_PHASE_ITERS   = int(os.environ.get('LA_HSM_STATIC_ITERS', '0'))
HSM_SLACK_TAU        = float(os.environ.get('LA_HSM_SLACK_TAU', '0.15'))
HSM_POOL_RHO_DEFAULT = float(os.environ.get('LA_HSM_POOL_RHO', '0.70'))   # fallback for unlisted pools
HSM_POOL_RHO = {
    'ANSYS':  float(os.environ.get('LA_HSM_POOL_RHO_ANSYS',  '0.60')),
    'ABAQUS': float(os.environ.get('LA_HSM_POOL_RHO_ABAQUS', '0.60')),
    'LSDYNA': float(os.environ.get('LA_HSM_POOL_RHO_LSDYNA', '0.95')),
}
```
- `STATIC_PHASE_ITERS` (0): hard floor; gate cannot fire while `ind <= STATIC_PHASE_ITERS`.
- `HSM_SLACK_TAU` (0.15): slack threshold as a fraction of the deadline window.
- Per-pool ρ semantics: high ρ → "cheap" more often → release that pool freely
  (recover cost); low ρ → hold under contention (protect deadlines). Deployed
  logic releases cheap/abundant **LSDYNA** (linear token law) and **holds**
  expensive **ANSYS/ABAQUS**.
- `pool_pressure = allocated / total` for the workflow's pool, **global** across
  all running workflows.

## 6. How the gate plugs into the renegotiation function
In `processFreeRequestWithLicenses(self, sim, wf_mb, request)`:
1. Unpack: `instances, budget, deadline, start_time, mesh, software_id, license_holds = self.resource_manager.getWorkflow(request['wf-id'])`;
   `license_pool = self.license_manager.get_pool_for_software(software_id) if software_id else None`;
   `ind = request['iteration']`.
2. `hsm_static = self._hsm_in_static_phase(request, instances, deadline, start_time, mesh, license_pool, software_id, ind, sim)`.
3. Run the existing EDF-LAMF body: compute `available_time`, evaluate urgency
   triggers (CRITICAL <30% time left; WARNING <50% + behind; EARLY behind by 3%;
   MID-ITERATION) which set `skip_scale_down` / `force_scale_up_attempt`.
4. **Inject exactly one line:** `if hsm_static: skip_scale_down = True`
   (STATIC suppresses scale-down; scale-up paths untouched).
5. Scale-down loop runs only `while chains_per_node > 0 and not skip_scale_down`,
   with the solver-aware cost guard (§7); then scale-up logic runs as usual.

## 7. Solver-aware scale-down guard (only if rewriting cost behavior)
Inside the scale-down branch, a guard blocks **cost-adverse** shrinks:
- `LA_GUARD_SAT = float(os.environ.get('LA_GUARD_SAT', '0.70'))` — saturation threshold.
- If `pool_utilization >= LA_GUARD_SAT` and `software_id`, compare license cost of
  keeping vs shrinking via `self.metrics.calculate_license_cost(software_id, cores, runtime)`
  and **block the shrink if it raises license cost** (`lic_shrink > lic_keep*1.001`).
- Token-cost laws (from `config/licenses.yaml`, fixed/cited — do NOT change):
  **ANSYS** = workgroup, **ABAQUS** = power-law `5.0·cores^0.422` (min 5), **LSDYNA** = linear.
- Tested null: `LA_GUARD_SAT=0.0` (unconditional guard) did NOT improve cost/deadline.

## 8. Critical facts / gotchas (must hold in any rewrite)
- **Iteration 0 never reaches `processFreeRequestWithLicenses`.** Iteration 0 is
  the *initial* allocation, done in the scheduler `run()` loop (Phase 3, via
  `checkNewResourcesWithLicenses`). The renegotiation path only sees `ind ∈ {1..5}`.
  Any guard keyed on `ind==0` is dead code — that was the ORIGINAL HSM bug, which
  made HSM behaviorally identical to EDF-LAMF.
- OPTIM factor dicts `OPTIM_FCFS_BFACTOR == OPTIM_FCFS_DFACTOR ==
  {0:0.6, 1:0.7, 2:0.8, 3:0.9, 4:0.95, 5:1.0}`, applied as
  `available_time = (deadline − DEADLINE_BUFFER − now) × DFACTOR[ind]` (budget
  analogously). **`FACTOR[0]=0.6` is inert** (iteration 0 never renegotiates).
- Partial license release on scale-down: `PARTIAL_RELEASE_FRACTION =
  float(os.environ.get('LA_PARTIAL_RELEASE', '0.90'))` (release 90%, retain 10%).
  Shared with the other LA schedulers; the retained slice hedges re-acquisition.
- Transition is **one-way**; do NOT add STATIC re-entry unless deliberately
  implementing hysteresis (tested-and-rejected design direction).

## 9. What differs from EDF-LAMF (exhaustively)
1. `self.hsm_phase` state dict (init in `__init__`) + `_hsm_in_static_phase` helper
   + the per-pool ρ module constants + `STATIC_PHASE_ITERS`/`HSM_SLACK_TAU`.
2. The single injected line `if hsm_static: skip_scale_down = True` in
   `processFreeRequestWithLicenses`.
Nothing else. Same imports, same EDF heap, same initial allocation, same triggers.

## 10. Imports + `__init__` for a drop-in rewrite
Top of `edf_hsm_LA.py` (already present for EDF-LAMF; add `import os`):
```python
from config.constants_LA import (
    COLD_START_TIME, DEADLINE_BUFFER, MIN_INSTANCE_COST,
    OPTIM_FCFS_BFACTOR, OPTIM_FCFS_DFACTOR,
    RESOURCE_REQUEST_TIMEOUT, SPEEDUP_THRESHOLD, WORKFLOW_POLLING, TOTAL_WORKFLOWS,
)
from scripts.speedup import getRuntime
from resource_manager.instance import CloudOnDemandInstance, Instance, OnPremInstance
from resource_manager.resource_manager_LA import ResourceManager_LA
from resource_manager.license.exceptions import LicenseError, InsufficientTokens
from utils.sim import getTime, getAllElements, peekElement, removeElement
from utils.resource_LA import getConstraintsFromWorkflow, getEstimate
from scheduler.scheduler_LA import Scheduler_LA
import os
```
In `__init__`, alongside `self.license_holds = {}`:
```python
self.hsm_phase = {}   # {wf_id: 'STATIC' | 'MOLDABLE'}; one-way STATIC->MOLDABLE
```

## 11. Empirical bottom line (defines "correct")
- vs **EDF-LAMF** (other elastic): HSM is better — fewer deadline misses at 6/7
  workload sizes (N grid 150..700, 6 seeds), near-neutral cost. **This is the
  contribution.**
- vs **EDF-ST-LA** (static): HSM does **not** win overall. In this
  license-bottlenecked regime static has fewer deadline/overall misses and lower
  cost; HSM beats static only on resource utilization (mid-band N 200–600),
  budget-miss (mid-high N), and isolated cells (total cost at N=400, deadline at
  N=150). A correct rewrite reproduces **"best-elastic, not better-than-static."**

## 12. Validation harness (reproduce results)
- `license_results/sweep_hsm_perpool.py` — HSM per-pool across N grid, 6 seeds;
  EDF-LAMF/static baselines reused from `canonical_results.json` (unchanged code,
  byte-identical — never re-run baselines).
- `license_results/sweep_hsm_rho.py` + `fig_hsm_rho.py` — ρ sensitivity.
- `license_results/fig_hsm_vs_lamf_vsN.py [perpool]` — HSM-vs-EDF-LAMF across N.
- Run env for an HSM cell: `LA_DEPTH_MODE=cost LA_MAX_DEPTH=8 LA_PARTIAL_RELEASE=0.90`
  plus any `LA_HSM_*` overrides; `simulate_main_LA.py --scheduler EDF-HSM --N <N> --seed <s>`.
