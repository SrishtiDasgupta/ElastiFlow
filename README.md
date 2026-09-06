# ElastiFlow

ElastiFlow is a runtime framework for elastic resource management of dynamic,
iterative scientific workflows on hybrid on-premise and cloud infrastructure.
At every iteration boundary the Workflow Engine renegotiates the workflow's
allocation with the Scheduler, which answers APPROVE, MODIFY or DENY after a
three-stage resolution: feasibility under the remaining budget and deadline,
binding within the workflow's tier, atomic commit. Floating-licence pools enter
the feasibility test as a further dimension where they apply. The framework
runs in two modes behind one execution-backend interface: **live**, against
real clusters and cloud instances, and **simulated**, as a Simulus
discrete-event simulation that replaces only the execution plane and keeps the
orchestration code unchanged.

It is the software behind the dissertation *Elastic Resource Management and
Scheduling for Dynamic Iterative Workflows on Hybrid HPC-Cloud Infrastructure*
(Srishti Dasgupta, Technical University of Munich, submitted 2026-09-04). Three
use cases are evaluated there: seismic Bayesian inversion (SeisSol–TinyDA) and a
licence-constrained CAE variant of it, both in simulated mode, and
hyperparameter optimisation (HPO) on live AWS GPU instances. The documentation
uses the dissertation's vocabulary (dynamic workflows, elastic allocation,
execution streams, the Workflow Engine); `docs/THESIS_CODE_DIFFERENCES.md`
records where the dissertation and the code disagree.

## Layout

```
elastiflow/          the framework: gateway (server/, wf_queue/), scheduler/ (one base, the
                     licence and HPO layers, the policy classes), policies.py (the registry of
                     the dissertation's policy names), usecase.py (what each use case contributes:
                     plan recognition, iteration inputs, engine actions), execution/ (the backend
                     interface and its two backends), resource_manager/ (incl. licence/),
                     workflow/ (the Steep-based engine), utils/, config/, scripts/ (dispatchers,
                     workload generators, runtime models, provisioning), the runners
                     (simulate_*.py, main*.py), cli.py (python -m elastiflow run)
use_cases/seissol/   results (datasets, sweeps, figure generators, plots), the Chapter 7
                     simulator validation, the TinyDA client/server driver
use_cases/licence/   results for the licence-constrained campaign, the workload description
use_cases/hpo/       profiling data, results, the calibrated N-sweep model, the CIFAR-10
                     application code, the workload description
deploy/              runners/ (live execution on AWS and SLURM), aws/ (Terraform, cluster
                     configs), nodes/ (node-side scripts and the Seis-Bridge submodule)
motivation/          the manual-versus-automated workflow experiment (not reported in the
                     submitted dissertation)
tests/               regression (one tagged cell per campaign, the HPO allocation and loop
                     baselines), unit (import composition, hierarchy, CLI, manifest), smoke
                     (every policy once), fixtures
docs/                INSTALL, ARCHITECTURE, THESIS_CODE_DIFFERENCES (where the
                     dissertation and the code disagree), PROVENANCE, REORGANISATION (the
                     refactoring plan and its status), PHASE_B_BACKEND, PHASE_B7_SCHEDULER_MERGE,
                     history/ (working notes and context logs)
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

The regression tests re-run one committed cell of each campaign and compare
every metric with the dataset of record at tag `thesis-submitted-2026-09-04`.
They are the contract for any change to the framework.

## Reproducing results

* One simulated cell: `python -m elastiflow run --mode simulated --use-case seissol --policy Elastic-EDF_c ...`
  (`docs/INSTALL.md`, section "Running a simulated cell by hand"); `--mode live`
  starts the live scheduler process instead. `elastiflow/policies.py` maps the
  dissertation's policy names to the classes; `elastiflow/config/profiles.py`
  maps each use case to its constants module.
* A campaign: `use_cases/seissol/results/sweep_PLAIN.py` and
  `use_cases/licence/results/canonical_sweep.py` (they merge into the dataset
  files in place; run them on a branch).
* A figure: every dissertation figure is mapped to its dataset, generator and
  committed output in `thesis/figures.yaml` (rendered in `docs/PROVENANCE.md`);
  `python thesis/sync_figures.py check --thesis-images DIR` compares the committed
  files with the submitted images, `regenerate` re-runs a generator in a
  throw-away worktree and compares.
* HPO ran live; its driver on AWS is `elastiflow/simulate_main_HPO.py` with
  `deploy/runners/run_hpo.py` on the cluster, and the batch-size sweep used the
  calibrated model under `use_cases/hpo/results/r7_n7_actual_vs_modeled/`.

## Status

Phases A, B and C of `docs/REORGANISATION.md` are complete (2026-09-06): the
repository is flattened, packaged and laid out by use case; the execution
backend of Chapter 7 is one interface with a `--mode` switch; the three
scheduler forks are one hierarchy with the policies as sets of hooks
(`docs/PHASE_B7_SCHEDULER_MERGE.md`); every dissertation figure has exactly one
writer. Every step was verified to reproduce the tagged results. The
`refactoring` branch holds this work; `main` holds the state the dissertation
was built from. Before publication: the history scrub of credentials and
private addresses, the licence under which the code is published (not yet
chosen), and the open decisions listed in `docs/REORGANISATION.md`.
