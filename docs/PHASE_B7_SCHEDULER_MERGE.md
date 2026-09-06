# Phase B7: merging the three scheduler forks

Status: plan, 2026-09-06, written after B6 (commit 9d8e705); the author's
decisions of the same day are recorded at the end. Nothing in this document is
applied yet. Every figure below was measured on that commit with
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

## Method

Method bodies were compared with `ast.unparse` after stripping docstrings,
first for identity, then with `difflib.SequenceMatcher(...).ratio()`; base
usage per concrete class was taken from the `self.<name>` attribute calls
that resolve to the base; constants were compared by importing the three
modules; the dissertation names were counted in the `.tex` sources of the
submitted build. The scripts are one-off and reproducible from this
description; they are not committed.
