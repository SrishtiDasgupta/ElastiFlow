# ElastiFlow

ElastiFlow is a runtime framework for elastic resource management of iterative,
dynamic scientific workflows on hybrid on-premise and cloud infrastructure. A
workflow negotiates its resource allocation with the scheduler at every
iteration boundary; the scheduler grants, modifies or denies the request against
cost, deadline and, where applicable, floating-licence constraints. The framework
runs in two modes behind one interface: **live**, against real clusters and
cloud instances, and **simulated**, as a discrete-event simulation that replaces
only the execution backend and keeps the orchestration code unchanged.

It is the software behind the dissertation *Elastic Resource Management for
Iterative and Dynamic Scientific Workflows* (Srishti Dasgupta, TUM, 2026). Three
use cases are evaluated there: seismic Bayesian inversion (SeisSol–TinyDA) and a
licence-constrained CAE variant of it, both in simulated mode, and
hyperparameter optimisation (HPO) on live AWS GPU instances.

## Layout

```
elastiflow/          the framework: gateway (server/, wf_queue/), scheduler/ (policies),
                     policies.py (the registry of the dissertation's policy names),
                     resource_manager/ (incl. licence/), workflow/ (Steep engine), utils/,
                     config/, scripts/ (dispatchers, workload generators, runtime models,
                     provisioning), the runners (simulate_*.py, main*.py)
use_cases/seissol/   results (datasets, sweeps, figure generators, plots), the Chapter 7
                     simulator validation, the TinyDA client/server driver
use_cases/licence/   results for the licence-constrained campaign
use_cases/hpo/       profiling data, results, the standalone N-sweep models, the CIFAR-10
                     application code
deploy/              runners/ (live execution on AWS and SLURM), aws/ (Terraform, cluster
                     configs), nodes/ (node-side scripts and the Seis-Bridge submodule)
motivation/          the manual-versus-automated workflow experiment of Chapter 1
tests/               regression (one tagged cell per campaign), unit (import composition),
                     smoke (every policy once), fixtures
docs/                INSTALL, ARCHITECTURE, PROVENANCE, REORGANISATION (the refactoring plan
                     and its status), history/ (working notes and context logs)
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
  committed output in `docs/PROVENANCE.md`.
* HPO ran live; its live driver is `elastiflow/simulate_main_HPO.py` with
  `deploy/runners/run_hpo.py` on the cluster, and the batch-size sweep used the
  analytical models under `use_cases/hpo/results/r7_n7_actual_vs_modeled/`.

## Status

Phase A of `docs/REORGANISATION.md` is complete: the repository is flattened,
packaged, cleaned and laid out by use case, with every step verified to
reproduce the tagged results. Phase B (one scheduler, one dispatcher, one
metrics class, the execution-backend interface of Fig. 7.1 made explicit) and
Phase C (one writer per figure, a provenance manifest) are next. The
`refactoring` branch is the work in progress; `main` holds the state the
dissertation was built from.

The licence under which this code is published has not yet been chosen.
