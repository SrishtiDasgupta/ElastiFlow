# Phase B7: merging the three scheduler forks

Status: plan, 2026-09-06, written after B6 (commit 9d8e705); the author's
decisions of the same day and the progress of the steps are recorded at the
end. B7.0 to B7.3 are done; B7.5 onwards are not applied yet. Every figure below was measured on that commit with
the scripts described in the "Method" section at the end.

## The constraint, restated

The tag `thesis-submitted-2026-09-04` is the reference. B7 may move code, it
may not move a number. Two consequences shape the plan:

* a method that exists in two forks may be merged only when the merged body
  is provably the same computation for every family that reaches it, or when
  the family that reaches it keeps its own override;
* a family that has no executable gate (HPO, and the four uncited SeisSol
  policies) gets one before its code is touched, or its code is not touched;
* no policy class is deleted. A policy the dissertation does not cite is kept
  *inactive*: importable, resolvable by name, covered by a baseline, but not
  offered by the entry points (author's decision, 2026-09-06).

The gates are the ones Phase B established: `python -m pytest` (regression,
11 tests), `python -m pytest -m smoke` (all 16 simulated cells against
`tests/regression/baseline_all_policies.json`, 635 fields, exact), plus what
B7.0 adds below.

## What the forks are (measured)

Three abstract bases with no common ancestor, 17 concrete policies, 8 244 lines
in `elastiflow/scheduler/`:

| family | base | concrete classes | lines |
|---|---|---|---|
| SeisSol | `scheduler.py` `Scheduler` (448) | `fcfs_optimized`, `earliest_deadline_edf`, `heft_heft_req`, `priority_priority`, and four uncited: `fcfs_scheduler` (the live `main.py` policy), `earliest_deadline_fcfs`, `priority_fcfs`, `heft_fcfs_req` | 1 321 |
| licence | `scheduler_LA.py` `Scheduler_LA` (711) | `fcfs_scheduler_LA`, `edf_scheduler_LA`, `fcfs_optimized_LA`, `edf_optimized_LA`, `edf_hsm_LA` | 4 141 |
| HPO | `scheduler_HPO.py` `Scheduler_HPO` (317) | `fcfs_scheduler_HPO`, `edf_scheduler_HPO`, `fcfs_optimized_HPO`, `edf_optimized_HPO` | 2 782 |

What the dissertation cites, and therefore what the smoke suite runs:

| dissertation name | class | selected by |
|---|---|---|
| FCFS-ST$_{r,c}$, Elastic-FCFS$_{r,c}$ | `FCFS_Optimized` (`MOLDABLE` toggles static/elastic) | `simulate_sweep.py fcfs static|moldable --sort-key` |
| EDF-ST$_{r,c}$, Elastic-EDF$_{r,c}$ | `EarliestDeadlineEDF` | `simulate_sweep.py edf ...` |
| HEFT-ST | `HEFT_HEFT_REQ` | `simulate_sweep.py heft static` |
| Elastic-Rank (two factor pairs) | `PriorityPriority` | `simulate_sweep.py rank moldable --rank-*` |
| FCFS-ST-LA, EDF-ST-LA, FCFS-LAMF, EDF-LAMF, HSM | `FCFS_Scheduler_LA`, `EDF_Scheduler_LA`, `FCFS_Optimized_LA`, `EDF_Optimized_LA`, `EDF_HSM_LA` | `simulate_main_LA.py --scheduler` |
| HPO static/elastic FCFS/EDF | the four `_HPO` classes | `main_HPO.py` (live), `simulate_main_HPO.py` (hybrid) |

Not cited and not run by any test: `FCFS_Scheduler` (still the policy of the
live SeisSol entry point `main.py`), `EarliestDeadlineFCFS`, `PriorityFCFS`,
`HEFT_FCFS_REQ`. They appear only as commented alternatives in
`simulate_main.py`.

### The bases, method by method

Comparison of normalised method bodies (docstrings stripped, `ast.unparse`
canonical form; "I" = identical to the SeisSol base, otherwise the difflib
ratio and length):

| method | `Scheduler` | `Scheduler_LA` | `Scheduler_HPO` |
|---|---|---|---|
| `__init__` | 5 | ~0.88 (10) | I |
| `run` (abstract) | 2 | I | I |
| `allocateResources` | 7 | I | I |
| `checkResources` | 25 | I | I |
| `purgeWorkflow` | 7 | I | ~0.99 (print prefix "HPO ") |
| `sendWorkflowForExecution` | 14 | ~0.93 (adds licence holds) | I |
| `sendNewResources` | 14 | ~0.80 (adds licence holds) | I |
| `sendFreedResources` | 13 | ~0.91 | ~1.00 (print prefix) |
| `allocateNewResources` | 11 | ~0.92 | ~0.99 (`mesh` renamed `model`) |
| `freeResources` | 24 | ~0.99 (7-tuple from the LA resource manager) | ~0.84 |
| `checkCloseness` | 10 | ~0.79 | absent |
| `checkNewResources` | 34 | ~0.11 (126, a different algorithm) | ~0.78 (45) |
| `processJobCompletion` | 11 | ~0.45 (59, releases licences) | ~0.48 (40, terminates on-demand) |
| SeisSol-only | `checkNewResourcesMoldable` 139, `processFreeRequest` 61, `freeResourcesMoldable` 22 | | |
| LA-only | | `allocateResourcesWithLicenses` 87, `_best_depth_total` 52, `allocateLicensesForWorkflow` 41, `releaseLicensesForWorkflow` 18, `calculateDualShadowTime` 10 | |
| HPO-only | | | `_terminate_ondemand_instances` 32, `getFamily` 6, `getInstanceKey` 2 |

So the HPO base is the SeisSol base with three cosmetic edits and three
additions; the licence base shares the skeleton and adds the licence layer,
with two methods (`checkNewResources`, `processJobCompletion`) that are its
own algorithms.

### Duplication inside each family

* **Licence.** `EDF_HSM_LA` is `EDF_Optimized_LA` plus `_hsm_in_static_phase`
  (67 lines): eight methods are byte-identical after normalisation
  (`checkNewResourcesWithLicenses` 113, `freeResourcesWithLicenses` 104,
  `isWorkflowImpossible` 58, `findLicenseFeasibleAllocation` 44,
  `processResourceRequestsByDeadline` 19, `processWorkflowsByDeadline` 10,
  `peekWorkflow`, `popWorkflow`; 358 lines), `processFreeRequestWithLicenses`
  is ~0.99 (354 vs 377) and `run` ~0.95. `FCFS_Optimized_LA` shares with
  `EDF_Optimized_LA` `freeResourcesWithLicenses` (identical),
  `checkNewResourcesWithLicenses` (~1.00) and `findLicenseFeasibleAllocation`
  (~0.98); its `processFreeRequestWithLicenses` is ~0.79 and its `run` ~0.07
  (FCFS order versus deadline order). The static pair shares `__init__`
  (~0.84) and `run` (~0.83).
* **HPO.** `FCFS_Optimized_HPO` and `EDF_Optimized_HPO` share five identical
  methods (117 lines) and six at ~0.99 or above (`allocateResourcesMoldableHPO`
  169, `selectOptimalInstanceType` 100, `checkNewResourcesHPO` 94,
  `sendWorkflowForExecutionHPO` 42, `sendNewResources` 39,
  `sendFreedResources` 18); `processMoldableRequestHPO` ~0.80, `run` ~0.62.
  The static pair: `createOnDemandWorkers` and `getInstanceTypeForHPO`
  identical, `allocateResourcesHPO` and `selectOptimalInstanceType` ~0.99,
  `sendWorkflowForExecutionHPO` ~1.00, `run` ~0.87.
* **SeisSol.** `FCFS_Optimized` carries its own copies of four base methods
  (`checkNewResources` vs the base's `checkNewResourcesMoldable` ~0.97,
  `processFreeRequest` ~1.00, `freeResources` vs `freeResourcesMoldable`
  ~1.00, `checkCloseness` ~0.84). Both copies are live: Elastic-FCFS runs the
  overrides, Elastic-EDF runs the base versions. The seven static-family `run`
  loops (59–65 lines each) are the policies themselves, similarity ~0.4.

### The support modules

| layer | files | relation |
|---|---|---|
| resource manager | `resource_manager.py`, `resource_manager_LA.py` (subclass, adds licence holds; `getWorkflow` returns a 7-tuple instead of 5), `heft_rm.py` (subclass) | already one hierarchy |
| metrics | `Metrics`, `MetricsLA`, `MetricsHPO` (unrelated classes; `computeMetrics` 192/302/258 lines, similarity < 0.1) | write the dataset files and the stdout that `sweep_PLAIN.parse_out` and `parse_la_run.parse` read: **schema, not code** |
| dispatcher | `dispatcher.py`, `dispatcher_LA.py` (own delay generation: compression 0.5, 2-minute jitter), `dispatcher_HPO.py` (Poisson option) | the arrival process is cited (Ch. 8); RNG call order is part of the reference |
| executor | `executor.py`, `executor_LA.py`, `executor_HPO.py` (`processQueueData` ~0.99; the execute function differs per use case) | |
| workflow engine | `steep_actions.py` vs `steep_actions_HPO.py` (`execute` ~0.08, HPO has its own retrying subprocess runner); parsers identical | |
| constants | `constants.py` 27 names; `constants_LA.py` = base + 28 names, one override (`TOTAL_WORKFLOWS`); `constants_HPO.py` 42 names, 19 shared, 8 with different values | per-use-case profiles in all but name |
| `utils/exec_sched.py` | three `getClientInputs_*` and `detectWorkflowType` | the use-case protocol lives here |

### Where the gates do not reach

* HPO: no simulated cell exists (the hybrid driver runs live services). But
  all four HPO schedulers construct offline against `resources_HPO.yaml` (5
  instance types), and their allocation logic is in pure methods:
  `selectOptimalInstanceType(budget, deadline, model, trials, epochs)`,
  `allocateResourcesHPO(constraints, backend)`,
  `allocateResourcesMoldableHPO(constraints, backend)`,
  `checkNewResourcesHPO(...)`. These can be recorded.
* The four uncited SeisSol policies: no runner selects them.
* The live paths: import composition only, as everywhere.

## Target shape

```
elastiflow/scheduler/
  base.py            Scheduler: the loop skeleton, the shared helpers
                     (allocateResources, checkResources, purgeWorkflow, send*,
                     allocateNewResources, freeResources), the extension points
  licence.py         LicenceScheduler(Scheduler): the licence layer that
                     Scheduler_LA adds today (allocateResourcesWithLicenses,
                     allocateLicensesForWorkflow, releaseLicensesForWorkflow,
                     its own checkNewResources and processJobCompletion)
  hpo.py             HPOScheduler(Scheduler): getFamily, getInstanceKey,
                     _terminate_ondemand_instances, the HPO processJobCompletion
  policies/          one file per policy, thin; the dissertation's names
elastiflow/policies.py   the registry: dissertation name -> class, kwargs, profile
elastiflow/config/profiles/{seissol,licence,hpo}.py   today's three constants modules
```

The CLI grows `--policy <dissertation name>` on top of B6's `--mode`, resolved
through the registry; the sweep drivers keep their own argument sets and can
resolve through the same registry.

What does **not** merge in B7: the three `computeMetrics` (they define the
dataset schemas and the stdout the parsers read; they get a common `Metrics`
protocol and nothing else), the licence and HPO algorithms themselves, and
the monolithic `run` loops until B7.4 says otherwise.

## Steps, each gated

**B7.0 Widen the reference.** (a) `tests/regression/record_hpo_allocation.py`
records, for each HPO class, the outputs of the pure allocation methods over a
fixed grid of constraints (models × budgets × deadlines × trial counts from
`constants_HPO`), into `tests/regression/baseline_hpo_allocation.json`; a test
compares exactly. (b) The policy registry `elastiflow/policies.py`: the
dissertation name → (class, kwargs, profile, `active`). `simulate_sweep.py`
and `simulate_main_LA.py` resolve their `algo`/`--scheduler` through it,
with their argument sets unchanged. `EarliestDeadlineFCFS`, `PriorityFCFS`
and `HEFT_FCFS_REQ` enter it as `active=False`: resolvable by their
registry name, absent from the choices the entry points print.
`FCFS_Scheduler` stays active because the live SeisSol entry point `main.py`
runs it. (c) The smoke baseline gains one cell for each of these four
(recorded with `record_baseline.py`, the reason stated in the commit), so an
inactive policy cannot rot unnoticed. Gate: the new baseline files exist and
pass; the existing 16 cells are unchanged.

**B7.1 One base.** `Scheduler_HPO` and `Scheduler_LA` become subclasses of
`Scheduler`. The seven identical methods are deleted from the forks; the
three HPO cosmetic variants are kept as overrides in `hpo.py` (print prefix,
the `model` name) so that log text does not change; the licence variants stay
as overrides in `licence.py`. Expected removal: about 120 lines, and the
three-way `isinstance`/import split disappears from the runners. Gate:
smoke, HPO allocation baseline, import composition.

**B7.2 Intra-family dedupe of identical methods.** `EDF_HSM_LA` subclasses
`EDF_Optimized_LA` and loses its 358 identical lines; `FCFS_Optimized_LA` and
`EDF_Optimized_LA` share `freeResourcesWithLicenses` through a licence
elastic mixin; the HPO elastic pair shares its 117 identical lines and the
static pair its 38. Only byte-identical (normalised) methods move in this
step. Expected removal: about 550 lines. Gate: smoke exact; the HPO baseline.

**B7.3 The near-identical methods, one diff at a time.** For every pair at
≥ 0.95 that is not identical (`processFreeRequestWithLicenses` HSM vs EDF-LAMF,
`checkNewResourcesWithLicenses` FCFS-LAMF vs EDF-LAMF, the six HPO elastic
methods, the four `FCFS_Optimized` copies of base methods, `freeResources`
base vs LA), the diff is read and classified: no-op (prints, names, comments)
→ merge; an extension → a hook on the base with the family's override; a
behavioural difference between two cited policies → **kept as is and reported
to the author**, because it is part of the results. Nothing at < 0.95 is
touched. Expected removal: 300–600 lines depending on the classification.
Gate: smoke exact after every single merge; a merge that changes a field is
reverted, not tuned.

**B7.4 The loop skeleton (author's decision: do it).** After B7.3 the
remaining duplication is the `run` methods: seventeen hand-written loops of
the same shape (drain resource requests → drain completions → peek the
workflow channel → END sentinel → admission → poll). The skeleton moves into
the base with hooks (`next_workflow`, `admit`, `on_resource_request`,
`on_completion`); each policy's loop is replaced by its hooks one policy at a
time, and the smoke suite is the arbiter after every single policy. This is
the only step that can change numbers through ordering (which channel is
drained first, where the 0.2 s scheduler overhead sleeps), so a policy whose
loop does not fit the skeleton without changing a field keeps its own `run`
and is listed in the step's report. Expected removal: up to about 1 500
lines. It runs last, after B7.7, so that everything else is already in
place when the loops are opened.

**B7.5 Profiles and CLI `--policy`.** `config/profiles/` holds the three
constants modules unchanged (the `from .constants import *` override chain in
`constants_LA.py` is kept until every reader goes through the profile); the
CLI gains `--policy <dissertation name>` resolved through the B7.0 registry,
active policies only in its help. Gate: the two CLI regression tests and the
smoke suite, unchanged.

**B7.6 One dispatcher, one executor.** `dispatcher(backend, arrivals)` where
`arrivals` is the use case's delay generator (the three current ones, moved
verbatim so the seeded RNG sequence is untouched); `executor.py` keeps one
`processQueueData` and takes the execute function from the use case. Gate:
smoke exact (the arrival sequence is in every field).

**B7.7 The use-case protocol.** `exec_sched.py`'s `detectWorkflowType` and the
three `getClientInputs_*`, plus `steep_actions_HPO.py`'s iteration runner,
become one `UseCase` object per use case (workflow constraints, client
inputs, iteration runner, metrics class, profile). This is the deferred item
from Phase A and closes the "three forks" section of `docs/ARCHITECTURE.md`.

Order: B7.0 → B7.1 → B7.2 → B7.3 → B7.5 → B7.6 → B7.7 → B7.4. The registry
sits in B7.0 because it is what makes the per-policy gating of B7.3 and B7.4
mechanical, and because the four inactive policies need it for their cells.

## Decisions

Taken by the author on 2026-09-06:

1. The four uncited SeisSol policies are kept, inactive (see the constraint
   above and B7.0b). Nothing is deleted.
2. B7.4, the loop skeleton, is wanted; it runs last.
3. `elastiflow/simulate_main_LA.py.tmp` was an untracked June draft of the
   licence runner (it still described a SimPy entry point and a DDM-EDF
   policy that no longer exists); nothing referenced it, it matched no
   committed version, and it was removed.

Still open:

4. The HPO hybrid driver keeps its name through B7; renaming it is a separate
   decision (see decision 3 in `docs/PHASE_B_BACKEND.md`).

## Progress

**B7.0 done (2026-09-06).** (a) `tests/regression/hpo_allocation.py` runs the
four HPO schedulers' pure allocation methods offline: `selectOptimalInstanceType`
over 648 grid points (3 models × 3 budget scales × 3 deadline scales × 3
trial counts × 2 epoch counts), the admission allocation for each of the 15
`data*.yaml` HPO workflows on a fresh scheduler and in sequence on one
scheduler without releases (which reaches the on-prem, reserved, on-demand
and exhausted paths), and a scale-up probe (`checkNewResourcesHPO`) after
each fresh elastic allocation (11 grow, 4 do not). 768 records in
`baseline_hpo_allocation.json`, recorded twice and compared before writing,
checked exactly by `test_hpo_allocation_baseline.py`. One finding on the way:
the HPO schedulers provision on-demand workers from a thread pool
(`createOnDemandWorkers`), and a simulus clock cannot be slept on from
another thread, so the harness uses a stub backend (simulated, clock 0,
immediate provisioning with IPs derived from the instance type); the hybrid
driver could never have reached this path on a simulated clock either, which
is consistent with HPO having run live. (b) `elastiflow/policies.py` is the
registry; `simulate_sweep.py`, `simulate_main_LA.py`, `simulate_main_HPO.py`
and `main_HPO.py` resolve through it with their argument sets and printed
lines unchanged (`tests/unit/test_policies.py` pins every name the drivers use
to the class it had). The three uncited SeisSol policies are `active=False`;
`fcfs_scheduler` is active as the live `main.py` policy. (c) The smoke
baseline was re-recorded with one cell for each of the four uncited policies
(`fcfs_scheduler`, `earliest_deadline_fcfs`, `priority_fcfs`, `heft_fcfs_req`,
each static, N = 100, seed 7); the 16 existing cells are byte-identical to the
previous file, so the reference did not move. All four run to completion;
`heft_fcfs_req` finishes 94 of 100 workflows at this cell, which is its
recorded behaviour, not a defect introduced here.

**B7.1 done (2026-09-06).** `Scheduler_LA` and `Scheduler_HPO` are subclasses
of `Scheduler`. Before deleting a duplicate, every free name of the method was
checked to bind to the same object in all three modules (the three that
differ across the bases are `Metrics`, `RESOURCE_REQUEST_TIMEOUT` and
`MIN_INSTANCE_COST`, plus HPO's own `deleteInstanceFromIp` and runtime
functions). Deleted from the licence layer: `run`, `allocateResources`,
`checkResources`, `purgeWorkflow` (its constructor now calls the base's and
adds the licence attributes). Deleted from the HPO layer: the constructor,
`run`, `allocateResources`, `sendWorkflowForExecution`, `sendNewResources`,
`sendFreedResources`, `checkResources`, `purgeWorkflow`. The two per-family
bindings the shared methods need are class attributes: `metrics_class`
(`Metrics`, `MetricsLA`, `MetricsHPO`) and `log_prefix` (`'HPO '` in the HPO
layer, so its two log lines read as before). Everything that binds a
differing name stays an override (`allocateNewResources` and `freeResources`
in HPO bind the 720 s timeout and HPO's termination; the licence layer's
messaging methods carry licence holds). 135 lines removed net;
`tests/unit/test_scheduler_hierarchy.py` pins which methods resolve to the
base and which stay overridden. Gates: default suite, smoke (20 cells), HPO
allocation baseline, all unchanged.

**B7.2 done (2026-09-06).** Only methods whose normalised bodies are
byte-identical moved, each after the same free-name binding check as B7.1
(one candidate failed it and stayed: `processResourceRequestsByDeadline`
differs between EDF-ST-LA and EDF-LAMF, so it stays per policy; HSM's is
EDF-LAMF's and is inherited). New homes: `EDFOrderingMixin` in
`scheduler.py` (`peekWorkflow`, `popWorkflow`, `processWorkflowsByDeadline`,
identical in all five EDF policies of the licence and HPO layers);
`Scheduler_LA_Elastic` (`freeResourcesWithLicenses`, identical in FCFS-LAMF
and EDF-LAMF); `Scheduler_HPO_Static` (the static `createOnDemandWorkers`)
and `Scheduler_HPO_Elastic` (`_syncOnDemandIPs`, the elastic
`createOnDemandWorkers`, `freeResources`, `getHPOInstanceCost`), with
`getInstanceTypeForHPO` on `Scheduler_HPO` itself. `EDF_HSM_LA` is now a
subclass of `EDF_Optimized_LA` and lost its eight identical copies; its
constructor, which was EDF-LAMF's statement for statement plus the phase
table (the normalised diff is one added line), now calls EDF-LAMF's and adds
that table, so the resource manager and metrics are still built once. Class
hierarchy after the step: FCFS-ST-LA → `Scheduler_LA`; EDF-ST-LA →
`EDFOrderingMixin`, `Scheduler_LA`; FCFS-LAMF → `Scheduler_LA_Elastic`;
EDF-LAMF → `EDFOrderingMixin`, `Scheduler_LA_Elastic`; HSM → `EDF_Optimized_LA`;
the HPO policies likewise over `Scheduler_HPO_Static` / `Scheduler_HPO_Elastic`.
`elastiflow/scheduler/` went from 8 244 lines before B7.1 to 7 402. Gates:
default suite (201), smoke (20 cells), HPO allocation baseline, all
unchanged; `test_scheduler_hierarchy.py` pins the new layers.

**B7.3 done (2026-09-06).** Every pair at ratio ≥ 0.95 that was not identical
was diffed after normalisation and classified. First the reference was widened
where the merge would otherwise have been unchecked: the HPO record now also
holds the messages each scheduler sends (the start request of every fresh
allocation; for the elastic classes one grow and one shrink notification on a
registered workflow) and the scale-up probe after every sequence allocation,
which reaches the cloud paths of `checkNewResourcesHPO` (the fresh
allocations all land on-prem, so those paths were unpinned before). That
record was produced from the pre-merge commit in a throw-away worktree and
the merged tree is compared with it exactly.

*No-ops, merged:* the HPO elastic pair's `allocateResourcesMoldableHPO`,
`selectOptimalInstanceType`, `sendNewResources`, `sendFreedResources` and the
static pair's `allocateResourcesHPO`, `selectOptimalInstanceType` (a dead
assignment and print texts, emoji versus none), `sendWorkflowForExecutionHPO`
across all four (the elastic request carries `moldable: True`, now set from
the layer's `moldable_request`); a `policy_label` (`'EDF '`) keeps the EDF
policies' log prefixes. The base's `allocateNewResources` serves HPO too
(`request_timeout` class attribute, 180 s versus HPO's 720 s), and the base's
`freeResources` serves the licence layer (it unpacks the resource manager's
5- or 7-tuple). Elastic-FCFS's copy of `processFreeRequest` and of the
moldable release were the base's with other callee names; both are gone and
its scale-up planner is now its `checkNewResourcesMoldable` override.

*Extensions, hooked:* HSM's `processFreeRequestWithLicenses` was EDF-LAMF's
plus the phase gate: the gate is now `_holdAllocation(...)` (False in
EDF-LAMF, HSM's `_hsm_in_static_phase` in HSM), called at the same point, with
the two log labels as class attributes; 202 lines gone from HSM.

*Behavioural differences between cited policies, kept behind a one-line hook
each and reported here:*

1. **Elastic-FCFS versus Elastic-EDF/Elastic-Rank (SeisSol), scale-up
   fallback.** `FCFS_Optimized.checkNewResourcesMoldable` skips a candidate
   instance that is slower than 1.2× the fleet's slowest and has a single free
   node (`else: continue`); the base's `Scheduler.checkNewResourcesMoldable`,
   which the port to the other elastic policies produced, takes it with one
   node. The two planners stay separate.
2. **FCFS-LAMF versus EDF-LAMF, licence feasibility.** The feasibility search
   sizes tokens with one chain in FCFS-LAMF and with the workflow's chain count
   in EDF-LAMF (and HSM): `_feasibilityChains(request)` returns 1 or
   `request['chains']`; the two 60-line methods are now one each in
   `Scheduler_LA_Elastic`.
3. **Elastic-FCFS versus Elastic-EDF (HPO), on-prem scale-up.** For an on-prem
   workflow (instance name `on-prem`), Elastic-FCFS models the added
   instances with the g5 runtime, Elastic-EDF with g4:
   `_runtimeFunctionFor(instance_type)`. The same condition appears in the two
   `processMoldableRequestHPO` loops, which are below the threshold and stay.
4. **HSM versus EDF-LAMF, the request loop.** Not merged (0.95, a `run`
   method, B7.4 territory), but the diff is worth knowing: when a workflow
   cannot be admitted, EDF-LAMF calls `setResourcesAvailable(False)` and waits
   for a release before trying again, HSM does not and retries every polling
   interval; HSM also writes its metrics files with the prefix `EDF_HSM_`
   rather than `EDF_`.

`elastiflow/scheduler/` is at 6 167 lines (8 244 before B7.1). Gates:
default suite (204), smoke (20 cells), the widened HPO record, all exact;
`test_scheduler_hierarchy.py` pins the hooks and labels.

## Method

Method bodies were compared with `ast.unparse` after stripping docstrings,
first for identity, then with `difflib.SequenceMatcher(...).ratio()`; base
usage per concrete class was taken from the `self.<name>` attribute calls
that resolve to the base; constants were compared by importing the three
modules; the dissertation names were counted in the `.tex` sources of the
submitted build. The scripts are one-off and reproducible from this
description; they are not committed.
