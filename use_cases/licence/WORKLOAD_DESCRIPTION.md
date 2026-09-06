# Licence-constrained SeisSol–TinyDA workload

The second workload class of the dissertation (Chapter 8, Licence-Constrained
Scientific Workflows; the policies in Chapter 5, Elastic Scheduling with
Auxiliary Pool Constraints; the results in Chapter 9). The names below are the
dissertation's; where the code says something else, the identifier is given in
backticks. This description
was aligned with the submitted dissertation on 2026-09-06 and every number in
it was checked against the code or the dissertation's tables that day.

## 1. Overview

Each workflow is a SeisSol–TinyDA Bayesian inversion workflow (the first
workload class) annotated with a mandatory licence requirement drawn from one
of three commercial CAE solver pools, ANSYS, ABAQUS and LS-DYNA. Simulation
task, resource shape and infrastructure pool are those of the plain campaign.
What the class adds is a second, globally shared resource: a workflow must
secure tokens from its pool before it can execute, and the token consumption of
every concurrently executing workflow couples otherwise independent allocation
decisions. Chapter 5 formalises this as an auxiliary pool constraint evaluated
jointly with compute capacity in Stage 1 of the three-stage allocation
resolution, committed by a two-phase hold and commit.

The campaign runs in the simulated execution backend (`--mode simulated`, the
`LiveBackend` is never used): iteration runtimes come from the fitted runtime
model, not from SeisSol. Batch sizes N ∈ {150, 200, 300, 400, 500, 600, 700},
six submission seeds each, five policies, 210 runs
(`results/canonical_results.json`, merged by `results/canonical_sweep.py`).

## 2. The application: SeisSol behind TinyDA

SeisSol is the forward model (a seismic wave-propagation solver on unstructured
tetrahedral meshes, MPI-parallel); TinyDA is the delayed-acceptance MCMC
sampler that proposes parameters and drives the forward evaluations through
UM-Bridge. In the dissertation's model an evaluation is one SeisSol solve, an
execution stream is one MCMC chain (a sequence of evaluations), and an
iteration dispatches p concurrent chains and pools their samples at its end.

### 2.1 Mesh resolution, the problem-size descriptor

Three resolutions are used; 500 is the finest and most expensive. The
population is split 2:1:1 over 500:750:1000 (`MESH_DISTRIBUTION`). Runtimes
below are the fitted on-premise model of `elastiflow/scripts/speedup.py`
(`hpcOnpremRuntime`), which is what both the Scheduler's estimates and the
simulated backend use.

| mesh | 1 node | 2 nodes | TinyDA–UM-Bridge coupling added per evaluation (`tinyDaOverhead`) | share |
|---|---|---|---|---|
| 1000 (coarse) | 109 s | 64 s | 4.7 s | 25 % |
| 750 | 149 s | 87 s | 8.4 s | 25 % |
| 500 (fine) | 469 s | 246 s | 25.8 s | 50 % |

The dissertation's overhead table (Chapter 7) reports the measured coupling as
5.6, 10.4 and 27.2 s; the code injects the values above. The difference is
recorded in `docs/THESIS_CODE_DIFFERENCES.md`.

### 2.2 Runtime model

Per-evaluation runtime is the exponential fit of Chapter 8,

```
runtime(nodes, mesh, instance) = a · exp(b · nodes / N0 + c · mesh / 1000) + d
```

with instance-specific coefficients (`elastiflow/scripts/speedup.py`), the
reference node count N0 = 8 on-premise and 4 for the cloud families, and the
coupling overhead of §2.1 added. Instance types: on-premise, hpc7a.24xlarge,
hpc7a.12xlarge, c7i.24xlarge, c7i.12xlarge, c6i.32xlarge, c6i.16xlarge.

### 2.3 Iteration structure

```
workflow (2 to 6 iterations)
  └─ iteration i: p_i concurrent execution streams (chains, 2 to 6)
       └─ chain: e_i sequential evaluations (links, 1 to 9)
            └─ evaluation: one SeisSol solve on the chain's nodes
```

Chains run concurrently and are load-balanced over the allocated nodes with
a max-heap (each chain at least one node, surplus nodes to the slowest chain;
when chains outnumber nodes the surplus chains queue on the existing nodes).
The simulated iteration runtime is the slowest chain's evaluation time
multiplied by `tinyda_iterations + 2` (`elastiflow/scripts/tinyda_runtime.py`).
Iteration boundaries are where the elastic policies renegotiate. The cohesion
value carried through `input_coh`/`output_coh` is a fixed solver parameter and
does not affect scheduling.

## 3. What licence awareness adds

| aspect | SeisSol–TinyDA | licence-constrained |
|---|---|---|
| resources | compute only | compute and licence tokens, evaluated jointly |
| per-iteration configuration | `workflowConfig` array (chains and links per iteration) | the same, plus `license_pool` / `software_id` |
| feasibility at admission and at a boundary | capacity, remaining budget, remaining time | the same, and pool availability (Stage 1 condition 5 of Chapter 5) |
| cost reported | compute | compute, and licence cost as a separate total-cost-of-ownership figure |
| scale-down guards | time and budget progress | the same, plus the pool-saturation guard G1 |
| batch sizes | 100 to 400 | 150 to 700 |

## 4. The licence model

### 4.1 Pools

Tokens are concurrent-use licences held for the duration of an allocation and
deducted regardless of tier (bring-your-own-licence). Each pool is sized at
1.15 times the EDF-ST-LA peak demand at N = 400, the per-pool maximum over the
six seeds, so that the static baseline runs at 88 to 90 % peak occupancy
(`elastiflow/config/licenses.yaml`, `LICENSE_POOL_CAPACITY`):

| pool | `software_id` | token law f(c), c = cores held | capacity |
|---|---|---|---|
| ANSYS | 1 | 1 (MEBA base) + max(0, c − 2) HPC Workgroup tokens | 5 832 |
| ABAQUS | 2 | max(5, 5 · c^0.422), concave | 1 582 |
| LS-DYNA | 3 | c, linear | 6 219 |

The laws are those of Henkel and Treiber (`resource_manager/license/policy.py`:
`ansys_workgroup`, `powerlaw` with a = 5, b = 0.422, floor 5, `linear`).
Under the concave and the affine law a reduction in cores can raise the
tokens per core, which is why the guard G1 (§8.4) exists.

### 4.2 Licence cost

Vendor-published annual prices, converted at 1 EUR = 1.10 USD and annualised
over 31 557 600 s (Chapter 8):

| pool | annual price | USD per second |
|---|---|---|
| LS-DYNA | 1 000 EUR per token | 3.486 × 10⁻⁵ per token |
| ABAQUS | 2 500 EUR per token | 8.714 × 10⁻⁵ per token |
| ANSYS MEBA | 14 000 EUR per job | 4.880 × 10⁻⁴ per job |
| ANSYS HPC Workgroup | 1 700 EUR per token | 5.926 × 10⁻⁵ per token |

Licence cost is accumulated per workflow by the Licence Manager
(`license_cost_by_owner`, `license_cost_by_pool`) and reported beside compute
cost. It does not enter the budget gate: the budget bounds compute expenditure
only, and the binding constraint on licences is the pool capacity.

### 4.3 Assignment

Each workflow is assigned one pool at submission, fixed for its lifetime,
with probabilities 0.33 / 0.33 / 0.34 for ANSYS / ABAQUS / LS-DYNA under a
fixed seed (`LICENSE_DISTRIBUTION`).

### 4.4 Two-phase commit

`hold` reserves the tokens with a time-to-live of 300 s (`LICENSE_HOLD_TTL`,
the dissertation's hold TTL); compute is then bound, and `commit` makes the
reservation permanent or `release` returns it. An expired hold returns its
tokens without a recovery log. The three calls are the holdUnits, commitHold
and releaseHold of Chapter 6.

## 5. Workflow specification

`elastiflow/workflow/sample_workflows_LA/data0.yaml`, as generated:

```yaml
api: 4.7.0
id: lamf-test-ce4837ee-...
config:
  mesh: 1000                      # problem-size descriptor
  software_id: 1                  # 1 = ANSYS, 2 = ABAQUS, 3 = LS-DYNA
  workflowIterations: 5
  workflowConfig:                 # per-iteration (chains, links); iteration 0 first
  - {chains: 5, tinydaIterations: 1}
  - {chains: 6, tinydaIterations: 8}
  - {chains: 5, tinydaIterations: 5}
  - {chains: 5, tinydaIterations: 6}
  - {chains: 6, tinydaIterations: 4}
constraints:
  budget: 64.81                   # compute budget (soft), dataset unit
  deadline: 14108.37              # seconds from submission (hard)
  chains: 5                       # first-iteration stream count p_1
  tinydaIterations: 1             # first-iteration evaluations per stream e_1
  license_pool: ANSYS
vars:
- id: input_coh
  value: '3'
actions:
- type: for                       # the iteration recurrence
  input: input_coh
  enumerator: i
  yieldToInput: output_coh        # one iteration's output is the next one's input
  actions:
  - type: execute
    service: scripts/simulate-tinyda-seissol.py   # the driver (simulated mode)
    inputs:  [{id: tinyda_input,  var: i}]
    outputs: [{id: tinyda_output, var: output_coh}]
```

The Scheduler sees only the first-iteration configuration and the constraints
at admission; the later entries of `workflowConfig` are revealed to it one
boundary at a time, which is how the simulation reproduces a dynamic workflow
with a reproducible trajectory (Chapter 8, Experimental Validity). Iteration 0
uses the `constraints` values; iterations 1 and later draw chains from [2, 6]
and links from [1, 9] independently (`workflow_generator_LA.py`).

## 6. Constraints

The unified derivation of Chapter 8 (Constraint Generation) with the SeisSol
population constants (p̄, ē, Ī) = (4, 7, 4) and the deadline contention factor
c_d = 2 (`AVG_TINYDA_ITERATIONS = 7`, `AVG_WORKFLOW_ITERATIONS = 4`,
`AVG_BUDGET`, `AVG_DEADLINE` in `config/constants.py`, shared by the two CPU
workloads). The worst-case single-evaluation runtime is the slowest instance at
one node (c7i.12xlarge), the worst-case cost the most expensive
(hpc7a.12xlarge).

| mesh | worst-case runtime | worst-case cost per evaluation | mean deadline = runtime · 7 · 4 · 2 | σ | mean budget = cost · 4 · 7 · 4 | σ |
|---|---|---|---|---|---|---|
| 1000 | 253 s | 0.5141 | 14 168 s (3.9 h) | 100 | 57.6 | 10 |
| 750 | 557 s | 0.8595 | 31 192 s (8.7 h) | 100 | 96.3 | 10 |
| 500 | 2 281 s | 2.7422 | 127 751 s (35.5 h) | 200 | 307.1 | 15 |

Individual constraints are Gaussian draws around these means. The budget is
a soft constraint (a workflow that overspends completes and is counted as a
budget miss), the deadline is hard in the reporting sense (a workflow runs to
completion and a late finish is a deadline miss). Cost units: the datasets and
YAMLs carry the unit the code calls EUR; the dissertation reports USD at
1 EUR = 1.10 USD (the figure generators apply the factor). A deadline buffer of
180 s (`DEADLINE_BUFFER`) is deducted before the urgency weight is applied.

## 7. Workload generation and arrivals

800 workflows are generated once with seed 0
(`elastiflow/workflow/sample_workflows_LA/`); a run of size N takes the first N
in dispatch order. Arrival times come from the BMW production trace of 370
submissions over 20 hours (`elastiflow/scripts/submitTimes.csv`), as for the
plain campaign, with every inter-arrival delay halved
(`TEMPORAL_COMPRESSION_FACTOR = 0.5`, floored at 1 s) and a jitter of
Uniform(0, 2 min) inside each slot (`SUBMISSION_JITTER_MINUTES = 2`,
`scripts/dispatcher_LA.py`). The doubling of the arrival rate follows from the
compression; it is not a separate parameter.

## 8. Policies

Five policies (Chapter 5, Instantiation for Licensed Engineering Simulation
Workflows), selected with `simulate_main_LA.py --scheduler` or
`python -m elastiflow run --use-case licence --policy`:

| dissertation name | class | `--scheduler` | admission order | allocation |
|---|---|---|---|---|
| FCFS-ST-LA | `fcfs_scheduler_LA.FCFS_Scheduler_LA` | `FCFS-ST-LA` | arrival | rigid: compute and tokens committed once at admission, held for the lifetime |
| EDF-ST-LA | `edf_scheduler_LA.EDF_Scheduler_LA` | `EDF-ST-LA` | earliest deadline | rigid |
| FCFS-LAMF (Licence-Aware Malleable-First) | `fcfs_optimized_LA.FCFS_Optimized_LA` | `LAMF` | arrival | elastic from the first boundary, scale-up and scale-down |
| EDF-LAMF | `edf_optimized_LA.EDF_Optimized_LA` | `EDF-LAMF` | earliest deadline | elastic from the first boundary |
| HSM (Hybrid Static-Malleable) | `edf_hsm_LA.EDF_HSM_LA` | `EDF-HSM` | earliest deadline | per-workflow phase: STATIC (scale-up only) until the transition to MALLEABLE (full EDF-LAMF) |

The runner's word for the elastic policies is `moldable`; the dissertation's is
elastic. Iteration 0 is allocated at admission against the full budget and
deadline; the urgency schedule applies from the first boundary.

### 8.1 Urgency weights

The same schedule as the plain SeisSol campaign (`OPTIM_FCFS_BFACTOR`,
`OPTIM_FCFS_DFACTOR` in `config/constants.py`; index 0 is admission):

| boundary i | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| α_i | 0.70 | 0.80 | 0.90 | 0.95 | 1.00 |

The available budget at a boundary is the remaining budget times α_i times
the trigger boost φ; the available time is (remaining time − 180 s) times
α_i.

### 8.2 Trigger modes

Evaluated in priority order at every boundary of the elastic policies
(`edf_optimized_LA.py`, `evaluateTrigger` region), with υ_i the remaining
deadline fraction, π_t the elapsed-time fraction and π_β the spent-budget
fraction:

| condition | boost φ | force scale-up | skip scale-down |
|---|---|---|---|
| υ_i < 0.30 | 2.0 | yes | yes |
| υ_i < 0.50 and π_t > π_β + 0.03 | 1.5 | yes | yes |
| π_t > π_β + 0.03 | 1.2 | yes | yes |
| i ≥ 2 and π_t > 0.40 | 1.2 | yes | yes |
| none | 1.0 | no | no |

### 8.3 HSM's phase gate

A workflow leaves the STATIC phase once, when two conditions hold at a
boundary: projected deadline slack at the current allocation exceeds a
threshold fraction of the deadline window (safe), and the pool's occupancy is
below P_thresh = 0.70 (cheap). The slack condition holds for nearly every
workflow, so occupancy is the binding condition. P_thresh is uniform over the
three pools in the campaign; the code's per-pool defaults differ
(`LA_HSM_POOL_RHO_*`), so `canonical_sweep.py` sets `LA_HSM_POOL_RHO*=0.70`.
Chapter 8 sweeps P_thresh over {0.5, …, 0.95} at N = 300.

### 8.4 Scale-down guards

Scale-down proceeds only when none of the guards holds (Chapter 5, guard table;
the code's blocked-reason labels in brackets):

| guard | condition |
|---|---|
| G1 | the declared pool is at least 70 % committed and the reduced shape would cost more tokens per core (ANSYS, ABAQUS) [`license_cost_adverse_saturated`; threshold `LA_GUARD_SAT` = 0.70] |
| G2 | late iteration, i > 2 [`late_iteration`] |
| G3 | less than half the deadline remains, υ_i < 0.50 |
| G4 | more than 70 % of the time has elapsed [`time_progress`] |
| G5 | more than 40 % of budget or time consumed [`budget_or_time_progress`] |
| G6 | at the instance floor, k ≤ 2 |

On a scale-down the workflow releases 90 % of the freed tokens and retains
10 % as a buffer against its own next scale-up (retention fraction ω_ret =
0.10, `LA_PARTIAL_RELEASE` default 0.90); Chapter 8 sweeps the release
fraction over {0.5, 0.7, 0.8, 0.9, 1.0}.

## 9. Joint feasibility

A request (admission or boundary) is feasible only if compute capacity, pool
availability, the available budget and the available time all admit it; the
Scheduler descends from the densest consolidation (3 chains per node,
`CHAINS_PER_NODE`) to one chain per node, and a request that fails on any
dimension is denied with the reason recorded (`insufficient_compute`,
`insufficient_licenses`, `budget_exhausted`, `time_exhausted`). A scale-up
acquires instances and tokens through the two-phase commit; a scale-down
releases instances and returns tokens as in §8.4.

## 10. Metrics

The per-workflow, resource-utilisation and licence-usage CSVs of
`elastiflow/utils/metrics_LA.py` carry the per-workflow timestamps
(submission, execution start, completion), compute and licence cost, the
deadline and budget flags, the scale-up and scale-down attempts with their
outcomes and reasons, resource utilisation sampled every 20 minutes, and
per-pool token occupancy. `results/canonical_sweep.py` merges them into
`canonical_results.json`; the figure generators compute the dissertation's
metrics (deadline, budget and overall miss rate, queue wait, turnaround,
makespan, cost per completed workflow, the cost-performance ratio, resource
utilisation) and the licence-specific ones of Chapter 9 (effective licence
utilisation, licence expenditure per completed workflow, per-solver
completion).

## 11. Infrastructure

The CPU pool of Chapter 8, shared with the plain campaign
(`elastiflow/config/resources.yaml`): 148 on-premise nodes of 48 cores (node
count of CoolMUC-3, per-node profile of SuperMUC-NG Phase 1, realised as
hpc7a.24xlarge under SLURM, 1.47 USD per node-hour from the three-year TCO at
90 % utilisation), and six cloud families with 6 reserved and 12 on-demand
slots each (hpc7a.24xlarge, hpc7a.12xlarge, c7i.24xlarge, c7i.12xlarge,
c6i.32xlarge, c6i.16xlarge; 36 reserved, 72 on-demand). On-demand
provisioning costs about 400 s of simulated time (`COLD_START_TIME`);
on-premise and reserved instances are warm.

## 12. Why the campaign is shaped this way

* Licence pools are a real constraint of industrial CAE sites and can bind
  before compute does; the three laws (linear, concave, affine with a fixed
  base) make the value of a scale-down solver-dependent, so no single
  heuristic is best for all pools.
* The per-iteration variation in `workflowConfig` is what gives an elastic
  policy something to do: a wide iteration followed by a narrow one is a
  scale-down opportunity and the reverse a scale-up opportunity.
* The compressed, jittered arrival stream and the range up to N = 700 create
  sustained pool pressure, so that the evaluation spans both constraint
  satisfaction and elastic scaling under contention (Chapter 8).
