# ElastiFlow

ElastiFlow is a runtime framework that lets a scientific workflow renegotiate
its compute resources while it runs. It targets workflows whose shape is not
known when they are submitted, because each iteration's result decides what
the next iteration looks like, and it runs them on hybrid infrastructure: an
on-premise cluster plus reserved and on-demand cloud instances. It is the
software behind the dissertation *Elastic Resource Management and Scheduling
for Dynamic Iterative Workflows on Hybrid HPC-Cloud Infrastructure*
(Srishti Dasgupta, Technical University of Munich, 2026).

## The workflows: their shape is decided while they run

Seismic inversion with SeisSol and TinyDA, hyperparameter optimisation with
Ray Tune, and licensed engineering simulation all follow the same loop:
propose a set of candidates, evaluate them in parallel, look at the results,
propose the next set. Each round is an iteration. The number of parallel runs
in the next iteration, how many evaluations each run performs, and whether
there is a next iteration at all are decided by the results of this one.

```
                 parallel runs in the iteration           decided by
  iteration 1    ● ● ● ●                                  the submission
                       │ result
                       ▼
  iteration 2    ● ● ● ● ● ●                              iteration 1's result
                       │ result
                       ▼
  iteration 3    ● ●                                      iteration 2's result
                       │ result
                       ▼
  iteration 4    ● ● ● ● ●                                iteration 3's result
                       │
                       ▼
  iteration 5    ?   how many runs, how deep, and whether there is one at all:
                     known only when iteration 4 has finished
```

Each ● is one parallel run (an MCMC chain of SeisSol solves, or a training
trial of several epochs; the dissertation calls it an execution stream). Such
a workflow is not a directed acyclic graph: its tasks and edges do not exist
at submission time, so a workflow engine cannot plan it and a scheduler cannot
size it in advance. Conventional systems allocate once at submission and hold
that allocation for the workflow's lifetime, which is too much for the narrow
iterations and too little for the wide ones.

```
  nodes   allocate once (static)            nodes   renegotiated (elastic)
    6 │        ▓▓                              6 │        ██
    5 │        ▓▓          ▓▓                  5 │        ██          ██
    4 │  ██    ██    ░░    ██  ← fixed at 4    4 │  ██    ██          ██
    3 │  ██    ██    ░░    ██                  3 │  ██    ██          ██
    2 │  ██    ██    ██    ██                  2 │  ██    ██    ██    ██
    1 │  ██    ██    ██    ██                  1 │  ██    ██    ██    ██
      └──1─────2─────3─────4── iteration         └──1─────2─────3─────4── iteration
        ▓ shortfall: the iteration runs slower    ░ waste: paid for, idle
```

## What ElastiFlow does: negotiate at every iteration boundary

One Workflow Engine runs each workflow. When an iteration ends, the Engine
knows what the next one needs and asks the Scheduler for it. The Scheduler
answers against the workflow's own constraints, a deadline (hard) and a budget
(soft), and against the shared capacity of the site, including floating
licence pools where the application needs them. The application itself is not
changed: the Engine invokes it once per iteration through a small driver
contract (one structured input, one structured output).

```
        Workflow Engine                                   Scheduler
        (one per workflow)                                (one per deployment)
  ┌─────────────────────────┐                      ┌─────────────────────────┐
  │ Execute iteration i     │                      │ Resource Manager:       │
  │ Assess its result:      │   requestResources   │   capacity of cluster,  │
  │   next iteration needs  │ ───────────────────▶ │   reserved, on-demand   │
  │   p runs × e evaluations│   p, e, node type,   │ Licence Manager:        │
  │   remaining budget,     │   remaining budget,  │   token pools           │
  │   remaining time        │   remaining time     │                         │
  │                         │                      │ 1 feasible shapes       │
  │ Negotiate: wait for the │   assignAllocation   │ 2 bind within the tier  │
  │   answer                │ ◀─────────────────── │ 3 commit atomically,    │
  │ Execute iteration i+1   │   APPROVE | MODIFY   │   release the surplus   │
  │   on what was granted   │   | DENY, and nodes  │                         │
  └─────────────────────────┘                      └─────────────────────────┘
        ⟲ at every iteration boundary, until the workflow converges
```

The Scheduler resolves one request at a time in three stages: which resource
shapes fit the remaining budget, the remaining time, the free capacity and the
licence pool; which concrete nodes to bind, within the tier the workflow was
admitted to (a workflow never migrates between cluster and cloud); then an
atomic commit that returns any surplus to the pool for waiting workflows. A
request that has waited longer than the staleness threshold is denied and the
workflow continues under its current allocation.

Policies differ in admission order and in whether they renegotiate at all.
The dissertation's names are the ones the code uses: the static baselines
FCFS-ST, EDF-ST and HEFT-ST allocate once; Elastic-FCFS, Elastic-EDF and
Elastic-Rank renegotiate; FCFS-ST-LA, EDF-ST-LA, FCFS-LAMF, EDF-LAMF and HSM
are their licence-aware counterparts. `elastiflow/policies.py` is the registry.

## Architecture

```
                   ┌──────────────────────────────────────────────────────────┐
  submitWorkflow   │ CONTROL PLANE, one per deployment                        │
  ────────────────▶│ Gateway ─▶ Q_new ─▶ Scheduler ─▶ Resource Manager        │
    (YAML)         │            Q_neg       │     └──▶ Licence Manager        │
                   │            Q_completed │  one request at a time,         │
                   │   (Redis lists)        │  one capacity account           │
                   └────────────▲────────────┬────────────────────────────────┘
         requestResources,        assignAllocation
         notifyCompletion       │            │
                   ┌────────────┴────────────▼────────────────────────────────┐
                   │ EXECUTION PLANE, one Workflow Engine per workflow        │
                   │ Workflow Engine ─▶ driver ─▶ load balancer               │
                   │   YAML: vars, for + execute actions, config, constraints │
                   │   drivers: SeisSol–TinyDA, Ray Tune HPO                  │
                   └───────────────────────────┬──────────────────────────────┘
                                               │ execution backend, one interface
                   ┌───────────────────────────▼──────────────────────────────┐
                   │ INFRASTRUCTURE PLANE                                     │
                   │ on-premise cluster (SLURM) · reserved cloud · on-demand  │
                   │ cloud; a workflow is pinned to one tier for its life     │
                   └──────────────────────────────────────────────────────────┘

  --mode live       the real thing: SLURM, SSH, boto3, HTTP and Redis between hosts
  --mode simulated  a Simulus discrete-event clock and mailboxes and the fitted
                    runtime model stand in for execution; the control plane and
                    the Workflow Engine run unchanged (dissertation, Chapter 7)
```

`docs/ARCHITECTURE.md` maps every component to its package and states where
the code differs from the dissertation's description;
`docs/THESIS_CODE_DIFFERENCES.md` lists the differences found between the two.

## The three use cases

| use case | workload | mode | policies | data of record |
|---|---|---|---|---|
| `seissol` | seismic Bayesian inversion, SeisSol behind TinyDA via UM-Bridge; 148-node cluster plus six cloud instance families | simulated | 11 variants, N = 100 to 400, six seeds | `use_cases/seissol/results/plain_results_per_run.json` |
| `licence` | the same workflows, each drawing tokens from an ANSYS, ABAQUS or LS-DYNA pool | simulated | 5, N = 150 to 700, six seeds | `use_cases/licence/results/canonical_results.json` |
| `hpo` | hyperparameter optimisation of CIFAR-10 classifiers with Ray Tune and ASHA on a 14-node GPU pool | live on AWS | 4, N = 3 to 7 | `use_cases/hpo/results/plots/total_cost_per_run.json` |

## Layout

```
elastiflow/          the framework: gateway (server/, wf_queue/), scheduler/ (one base, the
                     licence and HPO layers, the policy classes), policies.py (the registry),
                     usecase.py (what each use case contributes), execution/ (the backend
                     interface and its two backends), resource_manager/ (incl. licence/),
                     workflow/ (the Steep-based engine), utils/, config/, scripts/ (dispatchers,
                     workload generators, runtime models, provisioning), the runners
                     (simulate_*.py, main*.py), cli.py (python -m elastiflow run)
use_cases/seissol/   results (datasets, sweeps, figure generators, plots), the simulator
                     validation against live runs, the TinyDA client/server driver
use_cases/licence/   results for the licence-constrained campaign, the workload description
use_cases/hpo/       profiling data, results, the calibrated N-sweep model, the CIFAR-10
                     application code, the workload description
deploy/              runners/ (live execution on AWS and SLURM), aws/ (Terraform, cluster
                     configs), nodes/ (node-side scripts and the Seis-Bridge submodule)
motivation/          a manual-versus-automated workflow experiment (not in the dissertation)
tests/               regression (one tagged cell per campaign, the HPO allocation and loop
                     baselines), unit (import composition, hierarchy, CLI, manifest), smoke
                     (every policy once), fixtures
docs/                INSTALL, ARCHITECTURE, THESIS_CODE_DIFFERENCES, PROVENANCE, the
                     refactoring records, history/ (working notes and context logs)
thesis/              figures.yaml (every data figure of the dissertation: submitted image,
                     committed file, the one writer, state) and sync_figures.py
```

## Install and check

See `docs/INSTALL.md`. In short: a venv, `pip install -r requirements.txt`,
`pip install -e .`, a Redis server on localhost, then

```
python -m pytest            # unit + regression, about half a minute
python -m pytest -m smoke   # every simulated policy once against the recorded baseline, about 40 s
```


