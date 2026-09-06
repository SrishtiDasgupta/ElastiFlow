# Architecture

This document describes the code as it is on the `refactoring` branch
(2026-09-06), using the component names of the dissertation (Chapters 4, 6
and 7), and states plainly where the implementation still differs from that
description. Where the dissertation and the code disagree is recorded
in `docs/THESIS_CODE_DIFFERENCES.md`; the refactoring plan and its status are in
`docs/REORGANISATION.md`.

## Three planes

Chapter 6 places the components in three planes: the control plane (Gateway,
Scheduler, Resource Manager, instantiated once per deployment and serving every
workflow), the execution plane (one Workflow Engine instance per admitted
workflow, the application driver it spawns, and the Load Balancer inside that
driver), and the infrastructure plane (the on-premise cluster under SLURM and
the cloud tier of reserved and on-demand instances). The two design imperatives
of that chapter hold in the code: the Scheduler resolves every allocation-forming
request on one thread against one capacity snapshot, and submissions,
negotiation requests and completions arrive asynchronously into three durable
queues that the Scheduler drains in its polling cycle.

## Components and where they live

| dissertation component | package location | notes |
|---|---|---|
| Gateway (submitWorkflow ingress, completion relay, negotiation ingress) | `elastiflow/server/server.py`, `elastiflow/wf_queue/` | HTTP handlers; three Redis lists: `wf-queue` (the dissertation's Q_new), `resource-request-queue` (Q_neg), `completed-jobs-queue` (Q_completed). Ports from `config/resources.yaml`: user requests 8080, workflow completion 8082, resource requests 8084, executor 8089. |
| Scheduler | `elastiflow/scheduler/` | one base (`scheduler.py`: the request loop and its hooks, the SeisSol allocation methods), the licence layer (`scheduler_LA.py`), the HPO layer (`scheduler_HPO.py`), and the policy classes, each a set of hooks (`docs/PHASE_B7_SCHEDULER_MERGE.md`) |
| Resource Manager | `elastiflow/resource_manager/` | `instance.py` (instance model), `resource_manager.py` / `resource_manager_LA.py`; invoked only by the Scheduler (the single-writer arrangement of Chapter 6) |
| Licence Manager | `elastiflow/resource_manager/license/` | `manager.py` (`hold`, `commit`, `release`: the holdUnits, commitHold, releaseHold of Chapter 6, with the hold TTL), `policy.py` (the token laws per solver), `models.py`, `persistence.py` |
| Load Balancer | inside the schedulers and `utils/exec_sched.py` (SeisSol), inside the driver (HPO) | Chapter 6 places the Load Balancer inside each driver. The SeisSol stream-to-resource assignment (chains to nodes) is done by the policy classes and `utils/exec_sched.py`; the HPO phase-conditioned assignment is done by the driver (`use_cases/hpo/application/hpo_pipeline_verbose.py`). |
| Workflow Engine | `elastiflow/workflow/` | Steep parser and actions (`steep/`), `steep_workflow.py`, one engine for every use case; `utils/validate_workflow.py`; `utils/exec_sched.py` holds the workflow registry and the negotiation with the Scheduler (requestResources, the wait for assignAllocation); `elastiflow/usecase.py` is the use-case protocol (plan recognition, iteration inputs, hand-over between iterations, the engine's action classes) |
| Application drivers | `use_cases/seissol/driver/` (TinyDA client/server), `deploy/runners/run_seissol.py`, `deploy/runners/run_hpo.py` over `use_cases/hpo/application/` (CIFAR-10 trainers, Ray Tune) | the per-iteration `service` a workflow's `execute` action invokes; `scripts/simulate-tinyda-seissol.py` stands in for the SeisSol driver in simulated mode. Chapter 4 names them `drivers/seissol_tinyda.py`, `drivers/raytune_hpo.py`, `drivers/seissol_tinyda_licensed.py` (the last is the SeisSol driver again, licence semantics being confined to the Scheduler). |
| Runtime model | `elastiflow/scripts/speedup.py`, `speedup_HPO_runtime.py` | the fitted speedup curves per mesh and instance type (Chapter 8); the Scheduler's estimates and the simulated backend's iteration times come from the same functions |
| Provisioning | `elastiflow/scripts/create_instance.py`, `create_instance_HPO.py` | `backend.provision` / `backend.release`: boto3 in live mode, a delay of `COLD_START_TIME` and a synthetic address in simulated mode |
| Dispatcher (arrival process) | `elastiflow/scripts/dispatcher.py`, `dispatcher_LA.py`, `dispatcher_HPO.py` | one arrival loop; the SeisSol profile replays the BMW submission trace (`scripts/submitTimes.csv`, 370 submissions over 20 hours) with jitter, the licence profile halves the delays (floored at 1 s) and jitters over 2 minutes, the HPO profile draws exponential delays with mean 720 s |

The lifecycle a user sees is Submitted, Queued, Waiting, Running, Completed;
Scaling is a sub-state of Running internal to the Engine. Admission answers
ADMIT; a boundary request answers APPROVE, MODIFY or DENY. A request older
than the staleness threshold (`RESOURCE_REQUEST_TIMEOUT`, 180 s; 720 s for HPO)
is denied and the workflow continues under its allocation.

## Execution modes

The dissertation (Chapter 7, Fig. 7.1) describes one execution-backend interface
with a mode flag; the code has it (its introduction is recorded in
`docs/PHASE_B_BACKEND.md`). The
interface is `elastiflow/execution/backend.py`: clock (`now`, `sleep`), the
three message channels (`workflows`, `completions`, `resource_requests`), the
scheduler-to-executor messages (`start_workflow`, `notify_resources`), process
spawning, provisioning (`provision`, `release`), iteration execution
(`run_iteration`) and on-premise port leases. Two implementations:

* `SimulatedBackend`: a Simulus clock and mailboxes; provisioning is a delay of
  `COLD_START_TIME` (384 s setup plus 16 s boot, about 400 s, for SeisSol and
  licence; 530 s for HPO) and a synthetic address; an iteration takes the
  modelled runtime from `scripts/tinyda_runtime.py` (in process, the same
  functions the stub `scripts/simulate-tinyda-seissol.py` wraps) plus the
  measured 7.7 s engine and executor coordination overhead (Chapter 7);
* `LiveBackend`: wall clock, Redis queues filled by the Gateway's HTTP servers,
  HTTP to the executor nodes, boto3 provisioning, the service subprocess.

The entry point constructs one backend and passes it down (`--mode` of
`python -m elastiflow run`, or the runner scripts directly); every component
receives it as `backend`, and `backend.simulated` is the only mode test left.
Both modes construct the three Redis queues at start-up: the author kept this
(recorded in `docs/PHASE_B_BACKEND.md`), and it is what Chapter 7 describes (the Gateway
"enqueues them to the Redis-backed admission queue", and the orchestration code
"executes unchanged").

HPO has no working simulated backend: `simulate_main_HPO.py` is the driver that
ran on AWS (it wraps the live engine in Simulus); the batch-size sweep in the
dissertation uses the calibrated model under
`use_cases/hpo/results/r7_n7_actual_vs_modeled/`, which imports nothing from
the framework. The author confirmed on 2026-09-05 that HPO runs live only and
the simulated backend serves SeisSol and licence.

## What is shared and what remains per use case

The framework is one code path (the merge of the former three scheduler forks
is recorded in `docs/PHASE_B7_SCHEDULER_MERGE.md`): one `Scheduler` base with the
request loop of Chapter 6 as a skeleton of hooks (`Scheduler_LA(Scheduler)` and
`Scheduler_HPO(Scheduler)` override what differs for their family, and every
policy class is a set of hooks with no `run` of its own), one dispatcher loop,
one executor node loop, one Workflow Engine, one execution backend, one
registry of policies and one use-case protocol. What remains per use case is
data and the use case's own functions:

| layer | SeisSol–TinyDA | licence-constrained | HPO |
|---|---|---|---|
| runner | `simulate_sweep.py` (campaign), `simulate_main.py` (Chapter 7), `main.py` (live) | `simulate_main_LA.py`, `main_LA.py` (live) | `simulate_main_HPO.py` (the AWS driver), `main_HPO.py` |
| scheduler layer | `scheduler.py` `Scheduler` (+ `EDFOrderingMixin`, `Scheduler_Ordered`) | `scheduler_LA.py` `Scheduler_LA(Scheduler)`, `Scheduler_LA_Elastic` | `scheduler_HPO.py` `Scheduler_HPO(Scheduler)`, `Scheduler_HPO_Static`, `Scheduler_HPO_Elastic` |
| arrival profile | `dispatcher.SEISSOL` | `dispatcher_LA.LICENCE` | `dispatcher_HPO.arrivals(...)` |
| executor functions | SeisSol's execute function (`executor.py`) | `executor_LA.py` (execute and resource update) | `executor_HPO.py` (execute and resource update) |
| use case (engine actions, iteration inputs) | `usecase.SEISSOL`, `SEISSOL_ADAPTIVE` | `usecase.LICENCE` | `usecase.HPO` with `steep_actions_HPO.ExecuteAction` |
| constants (profile) | `config/constants.py` | `constants_LA.py` | `constants_HPO.py` |
| metrics | `utils/metrics.py` | `metrics_LA.py` | `metrics_HPO.py` |
| resource manager | `resource_manager.py` | `resource_manager_LA.py` | HPO instance model |
| workload generator | `workflow_generator.py` | `workflow_generator_LA.py` | `workflow_generator_HPO.py` |
| workloads | `sample_workflows/` (400) | `workflow/sample_workflows_LA/` (800) | `workflow/sample_workflows_HPO/` (20) |

Four behavioural differences between cited policies are kept behind named hooks
and listed in `docs/PHASE_B7_SCHEDULER_MERGE.md` (the SeisSol elastic fallback,
the FCFS-LAMF and EDF-LAMF feasibility chains, the HPO on-premise runtime model,
HSM's hold on the availability flag).

### Policies as the dissertation names them

The mapping below is code: `elastiflow/policies.py` is the registry the
entry points resolve through (name → class, constructor arguments, whether
the name is offered), `python -m elastiflow run ... --policy NAME` translates a
name into the runner's own arguments, and `elastiflow/config/profiles.py`
names the constants module each use case runs with (the three modules stay
where they are: `constants.py`, `constants_LA.py`, `constants_HPO.py`). The
runners' word `moldable` and the class attribute `MOLDABLE` are the code's
names for what the dissertation calls elastic allocation (Q4: workflow-level
elasticity, L3 malleable plus L2 moldable in the taxonomy of Chapter 2); `static`
is the rigid allocation of the `-ST` baselines (Q3). The four SeisSol policies
the dissertation does not cite (`fcfs_scheduler`, the policy of the live
`main.py`; `earliest_deadline_fcfs`; `priority_fcfs`; `heft_fcfs_req`) are
registered by module name, the last three as inactive, and each has one cell
in the smoke baseline.

SeisSol–TinyDA, via `simulate_sweep.py <algo> <mode> [--sort-key runtime|cost]`:

| dissertation name | algo/mode | class |
|---|---|---|
| FCFS-ST_r, FCFS-ST_c | `fcfs static`, sort key runtime / cost | `fcfs_optimized.FCFS_Optimized` |
| Elastic-FCFS_r, Elastic-FCFS_c | `fcfs moldable` | same class, `MOLDABLE = True` |
| EDF-ST_r, EDF-ST_c, Elastic-EDF_r, Elastic-EDF_c | `edf static` / `edf moldable` | `earliest_deadline_edf.EarliestDeadlineEDF` |
| HEFT-ST | `heft static` | `heft_heft_req.HEFT_HEFT_REQ` (with `resource_manager/heft_rm.py`) |
| Elastic-Rank (0.50, 0.50), Elastic-Rank (0.25, 0.75) | `rank moldable --rank-budget/--rank-deadline` | `priority_priority.PriorityPriority` |

Licence-constrained, via `simulate_main_LA.py --scheduler`:

| dissertation name | `--scheduler` | class |
|---|---|---|
| FCFS-ST-LA | `FCFS-ST-LA` | `fcfs_scheduler_LA.FCFS_Scheduler_LA` |
| EDF-ST-LA | `EDF-ST-LA` | `edf_scheduler_LA.EDF_Scheduler_LA` |
| FCFS-LAMF | `LAMF` | `fcfs_optimized_LA.FCFS_Optimized_LA` |
| EDF-LAMF | `EDF-LAMF` | `edf_optimized_LA.EDF_Optimized_LA` |
| HSM | `EDF-HSM` (with `LA_HSM_POOL_RHO*=0.70`) | `edf_hsm_LA.EDF_HSM_LA` |

HPO, via `simulate_main_HPO.py --algo {fcfs,edf} --mode {static,moldable}`:

| dissertation name | algo/mode | class |
|---|---|---|
| FCFS-ST, EDF-ST | `fcfs static`, `edf static` | `FCFS_Scheduler_HPO`, `EDF_Scheduler_HPO` |
| Elastic-FCFS, Elastic-EDF | `fcfs moldable`, `edf moldable` | `FCFS_Optimized_HPO`, `EDF_Optimized_HPO` |

## Data of record

`use_cases/seissol/results/plain_results_per_run.json` (264 runs: 11 variants ×
4 batch sizes {100, 200, 300, 400} × 6 seeds), `use_cases/licence/results/canonical_results.json`
(210 runs: 5 policies × 7 batch sizes {150, 200, 300, 400, 500, 600, 700} × 6 seeds),
`use_cases/hpo/results/plots/total_cost_per_run.json` (12 cells: 4 policies × N
in {3, 5, 7}, 6 seeds each, from the calibrated model, calibrated on the live R7
run). Superseded generations are kept beside them as dated snapshots
(`*.ansys_n4_2026-09-02.json`, `*.pre_negotiation_2026-09-02.json`) so that any
artefact can be scored against the generation that produced it. The regression
tests reproduce one cell of each from the current tree. Cost fields are in the
unit the code calls EUR; the dissertation reports USD at 1 EUR = 1.10 USD, and
the figure generators apply the factor.

## Site-specific values

`config/resources.yaml` carries the scheduler's LRZ address and the instance
runtimes and prices of the evaluated pool (148 on-premise nodes of 48 cores, 36
reserved and 72 on-demand cloud slots across six instance types); `deploy/`
scripts assume the checkout at `/fsx/ElastiFlow` on the cluster's FSx mount.
