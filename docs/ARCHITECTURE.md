# Architecture

This document describes the code as it is on the `refactoring` branch after
Phase A, using the component names of the dissertation (Ch. 4 and 6), and
states plainly where the implementation still differs from that description.
The target layout and the plan are in `docs/REORGANISATION.md`.

## Components and where they live

| dissertation component | package location | notes |
|---|---|---|
| Gateway (submission, completion, resource-request endpoints) | `elastiflow/server/server.py`, `elastiflow/wf_queue/` | HTTP handlers; three Redis queues (`wf-queue`, `completed-jobs-queue`, `resource-request-queue`). Ports from `config/resources.yaml`: user requests 8080, workflow completion 8082, resource requests 8084, executor 8089. |
| Scheduler | `elastiflow/scheduler/` | one abstract base per use case today (see below) and the policy classes |
| Resource Manager | `elastiflow/resource_manager/` | `instance.py` (instance model), `resource_manager.py` / `resource_manager_LA.py` |
| Licence Manager | `elastiflow/resource_manager/license/` | `manager.py`, `policy.py` (token laws per solver), `models.py`, `persistence.py` |
| Load Balancer | inside the schedulers and `utils/exec_sched.py` | stream-to-resource assignment (SeisSol) and phase-conditioned assignment (HPO) are methods of the policy classes, not a separate module yet |
| Workflow Engine | `elastiflow/workflow/` | Steep parser and actions (`steep/`), `steep_workflow.py`, one engine for every use case; `utils/validate_workflow.py`; `utils/exec_sched.py` holds the workflow registry and the resource negotiation with the scheduler; `elastiflow/usecase.py` is the use-case protocol (plan recognition, iteration inputs, hand-over between iterations, the engine's action classes) |
| Application drivers | `use_cases/seissol/driver/` (TinyDA client/server), `use_cases/hpo/application/` (CIFAR-10 trainers, Ray) | the per-iteration `service` a workflow invokes |
| Runtime model | `elastiflow/scripts/speedup.py`, `speedup_HPO_runtime.py` | fitted speedup curves per mesh and instance type; the simulated backend's source of iteration times |
| Provisioning | `elastiflow/scripts/create_instance.py`, `create_instance_HPO.py` | boto3 in live mode; `(sim or time).sleep(COLD_START_TIME)` in simulated mode |
| Dispatcher (arrival process) | `elastiflow/scripts/dispatcher.py`, `dispatcher_LA.py`, `dispatcher_HPO.py` | replays the BMW submission trace (`scripts/submitTimes.csv`) with jitter; the licence variant compresses it by 0.5 and jitters over 2 min |

## Execution modes

The dissertation (Fig. 7.1) describes one execution-backend interface with a
mode flag; since Phase B (`docs/PHASE_B_BACKEND.md`) the code has it. The
interface is `elastiflow/execution/backend.py`: clock (`now`, `sleep`), the
three message channels (`workflows`, `completions`, `resource_requests`), the
scheduler-to-executor messages (`start_workflow`, `notify_resources`), process
spawning, provisioning (`provision`, `release`), iteration execution
(`run_iteration`) and on-premises port leases. Two implementations:

* `SimulatedBackend`: simulus clock and mailboxes; provisioning is a delay of
  `COLD_START_TIME` (400.5 s SeisSol, 530 s HPO) and a synthetic IP; an
  iteration takes the modelled runtime from `scripts/tinyda_runtime.py`
  (in process, the same functions the stub `scripts/simulate-tinyda-seissol.py`
  wraps) plus the measured 7.7 s executor overhead (Ch. 7);
* `LiveBackend`: wall clock, Redis queues filled by the Gateway's HTTP servers,
  HTTP to the executor nodes, boto3 provisioning, the service subprocess.

The entry point constructs one backend and passes it down (`--mode` of
`python -m elastiflow run`, or the runner scripts directly); every component
receives it as `backend`, and `backend.simulated` is the only mode test left.
Even simulated runs construct the three Redis queues at start-up, as the
submitted code did.

HPO has no working simulated backend: `simulate_main_HPO.py` is the live driver
that was run on AWS (it wraps the live engine in simulus); the batch-size sweep
in the dissertation uses the standalone analytical models under
`use_cases/hpo/results/r7_n7_actual_vs_modeled/`, which import nothing from the
framework. The author confirmed on 2026-09-05 that HPO runs live only and the
simulated backend serves SeisSol and licence.

## The three per-use-case forks

The framework exists in three parallel copies, one per experiment. Since
B7.1 the scheduler bases form one hierarchy (`Scheduler`, with
`Scheduler_LA` and `Scheduler_HPO` as subclasses that override what differs
for their family); the remaining layers are still copies. Phase B7
(`docs/PHASE_B7_SCHEDULER_MERGE.md`) merges them step by step.

| layer | SeisSol | licence | HPO |
|---|---|---|---|
| runner | `simulate_sweep.py` (campaign), `simulate_main.py` (Ch. 7) | `simulate_main_LA.py` | `simulate_main_HPO.py` (live) |
| scheduler base | `scheduler/scheduler.py` `Scheduler` (+ `EDFOrderingMixin`) | `scheduler_LA.py` `Scheduler_LA(Scheduler)`, `Scheduler_LA_Elastic` | `scheduler_HPO.py` `Scheduler_HPO(Scheduler)`, `Scheduler_HPO_Static`, `Scheduler_HPO_Elastic` |
| dispatcher | one loop, `dispatcher.dispatcher(backend, arrivals)`; the profile `SEISSOL` | the profile `LICENCE` in `dispatcher_LA.py` | `dispatcher_HPO.arrivals(...)` |
| executor | one node loop, `executor.processQueueData` / `serve`; SeisSol's execute function | `executor_LA.py` (execute and resource update) | `executor_HPO.py` (execute and resource update) |
| workflow engine | one, `steep_workflow.Steep_Workflow`; use cases `usecase.SEISSOL`, `SEISSOL_ADAPTIVE` | `usecase.LICENCE` | `usecase.HPO` with `steep_actions_HPO.ExecuteAction`, its own iteration runner |
| constants | `config/constants.py` | `constants_LA.py` | `constants_HPO.py` |
| metrics | `utils/metrics.py` | `metrics_LA.py` | `metrics_HPO.py` |
| resource manager | `resource_manager.py` | `resource_manager_LA.py` | HPO instance model |
| workload generator | `workflow_generator.py` | `workflow_generator_LA.py` | `workflow_generator_HPO.py` |
| workloads | `sample_workflows/` (400) | `workflow/sample_workflows_LA/` (800) | `workflow/sample_workflows_HPO/` (20) |

### Policies as the dissertation names them

The mapping below is code: `elastiflow/policies.py` is the registry the
entry points resolve through (name → class, constructor arguments, whether
the name is offered), `python -m elastiflow run ... --policy NAME` translates a
name into the runner's own arguments, and `elastiflow/config/profiles.py`
names the constants module each use case runs with (the three modules stay
where they are: `constants.py`, `constants_LA.py`, `constants_HPO.py`). The four SeisSol policies the dissertation does not
cite (`fcfs_scheduler`, the policy of the live `main.py`; `earliest_deadline_fcfs`;
`priority_fcfs`; `heft_fcfs_req`) are registered by module name, the last
three as inactive, and each has one cell in the smoke baseline.

SeisSol–TinyDA, via `simulate_sweep.py <algo> <mode> [--sort-key runtime|cost]`:

| thesis name | algo/mode | class |
|---|---|---|
| FCFS-ST_r, FCFS-ST_c | `fcfs static`, sort key runtime / cost | `fcfs_optimized.FCFS_Optimized` |
| Elastic-FCFS_r, Elastic-FCFS_c | `fcfs moldable` | same class, `MOLDABLE = True` |
| EDF-ST_r, EDF-ST_c, Elastic-EDF_r, Elastic-EDF_c | `edf static` / `edf moldable` | `earliest_deadline_edf.EarliestDeadlineEDF` |
| HEFT-ST | `heft static` | `heft_heft_req.HEFT_HEFT_REQ` (with `resource_manager/heft_rm.py`) |
| Elastic-Rank[50,50], Elastic-Rank[25,75] | `rank moldable --rank-budget/--rank-deadline` | `priority_priority.PriorityPriority` |

Licence-constrained, via `simulate_main_LA.py --scheduler`:

| thesis name | `--scheduler` | class |
|---|---|---|
| FCFS-ST-LA | `FCFS-ST-LA` | `fcfs_scheduler_LA.FCFS_Scheduler_LA` |
| EDF-ST-LA | `EDF-ST-LA` | `edf_scheduler_LA.EDF_Scheduler_LA` |
| FCFS-LAMF | `LAMF` | `fcfs_optimized_LA.FCFS_Optimized_LA` |
| EDF-LAMF | `EDF-LAMF` | `edf_optimized_LA.EDF_Optimized_LA` |
| HSM | `EDF-HSM` (with `LA_HSM_POOL_RHO*=0.70`) | `edf_hsm_LA.EDF_HSM_LA` |

HPO, via `simulate_main_HPO.py --algo {fcfs,edf} --mode {static,moldable}`:
`FCFS_Scheduler_HPO`, `FCFS_Optimized_HPO`, `EDF_Scheduler_HPO`, `EDF_Optimized_HPO`.

## Data of record

`use_cases/seissol/results/plain_results_per_run.json` (264 runs: 11 variants ×
4 batch sizes × 6 seeds), `use_cases/licence/results/canonical_results.json`
(210 runs: 5 policies × 7 batch sizes × 6 seeds),
`use_cases/hpo/results/plots/total_cost_per_run.json` (12 cells × 6 seeds from
the analytical model, calibrated on the live R7 run). Superseded generations are
kept beside them as dated snapshots (`*.ansys_n4_2026-09-02.json`,
`*.pre_negotiation_2026-09-02.json`) so that any artefact can be scored against
the generation that produced it. The regression tests reproduce one cell of each
from the current tree.

## Site-specific values

`config/resources.yaml` carries the scheduler's LRZ address and the instance
runtimes and prices of the evaluated pool (148 on-premise slots, 36 reserved and
72 on-demand cloud slots across six instance types); `deploy/` scripts assume the
checkout at `/fsx/ElastiFlow` on the cluster's FSx mount.
