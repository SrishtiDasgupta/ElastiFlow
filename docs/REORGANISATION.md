# Reorganising the repository: ElastiFlow as a framework, three use cases

Status: PROPOSAL, nothing applied. Branch `refactoring`, base `main` at 968d306
(tag `thesis-submitted-2026-09-04` = the code and data behind the submitted PDF).

## 1. What the code is today (verified 2026-09-05)

The dissertation names the framework **ElastiFlow** (Ch1, Ch4, Ch6; "Vortex" never
appears). Its architecture (Ch4 §System Architecture, Ch6 §Orchestration Services)
is: Gateway, Scheduler, Resource Manager, Licence Manager, Load Balancer, Workflow
Engine, and per-application Drivers (seismic Bayesian inversion, HPO).

The repository does not reflect that. It holds **three parallel copies of the
system, one per experiment**, plus a fourth simulator for the HPO batch-size sweep:

| layer                  | SeisSol (base)            | Licence (`_LA`)              | HPO (`_HPO`)                |
|------------------------|---------------------------|------------------------------|-----------------------------|
| entry point            | `simulate_main.py` 44     | `simulate_main_LA.py` 146    | `simulate_main_HPO.py` 57   |
| live entry point       | `main.py` 40              | `main_LA.py` 117             | `main_HPO.py` 219           |
| scheduler ABC          | `Scheduler` 461           | `Scheduler_LA` 723           | `Scheduler_HPO` 330         |
| policies               | 4 live + 6 dead           | 4                            | 4                           |
| dispatcher (simulus)   | `dispatcher.py` 148       | `dispatcher_LA.py` 246       | `dispatcher_HPO.py` 240     |
| executor               | `executor.py` 63          | `executor_LA.py` 166         | `executor_HPO.py` 204       |
| constants              | `constants.py` 69         | `constants_LA.py` 196        | `constants_HPO.py` 310      |
| metrics                | `metrics.py` 512          | `metrics_LA.py` 1041         | `metrics_HPO.py` 583        |
| resource manager       | `resource_manager.py` 116 | `resource_manager_LA.py` 253 | (uses HPO instance model)   |
| workflow generator     | `workflow_generator.py`   | `..._LA.py`                  | `..._HPO.py`                |
| speedup model          | `speedup.py`              | (SeisSol's)                  | `speedup_HPO.py`            |
| sweep driver           | `plain_results/sweep_PLAIN.py` -> `simulate_sweep.py` | `license_results/canonical_sweep.py` -> `simulate_main_LA.py` | none: `HPO/results/plots/compute_total_cost.py` -> `sim_4corners_calibrated.py` (321 lines, imports nothing from `src/main`) |

35 forked files. The three ABCs are unrelated classes. `enable_smp` differs
between runners (False / False / True). Line counts: scheduler 8,942; utils 3,351;
scripts 4,479; entry points 1,819; service 2,133; fsx 5,901; experiments 4,612;
analysis 4,552; results-dir scripts 13,686.

Other facts the layout has to answer for:

* No package. No `setup.py`/`pyproject.toml`/`__init__.py`. `src/main` is implicitly
  on `sys.path`; 8 external scripts reach it via `sys.path.insert`.
* 48 tracked files carry absolute paths. Four are **default arguments in core
  runtime** (`resource_manager.py:10`, `resource_manager_LA.py:23`,
  `license/manager.py:39`, `constants_LA.py:119`); `verify_sweep.py:3` hardcodes its
  own directory; `speedup_plot.py:236` writes a cwd-relative path; both sweep
  drivers hardcode `../vortex_venv/bin/python3` (whose `pip` launcher is already
  broken because the venv was moved).
* `requirements.txt` contains shell commands and omits `simulus`, numpy, pandas,
  matplotlib, scipy, seaborn, boto3, paramiko.
* Duplicate and dead material tracked: `workflow/temp/` (500 of 502 files identical
  to `workflow/sample_workflows_LA/`, referenced by nothing), `old_bad/` trees,
  `plots_newHSM/` (June snapshot), `.OLD_OD4.json`, `fcfs_optimized_old.py`,
  `fcfs_optimized_kavitha.py`, `*_nisarg.*`, `speedup_plot_hpc24x.py`,
  `exec_sched__old.py`, `executor_nisarg_hpo_test.py`, `manual_simulation_HPO.py`,
  `functions.py`, `resources_HPO_hybrid.yaml`, 24 `.DS_Store`, a filename with a
  trailing space (`scripts/speedup_HPO.yaml `), a broken import
  (`cloud_runner_HPO_new.py:10` imports `scripts.speedup_runtime_HPO`, which lives
  in `service/scripts/`).
* Two unrelated trees called Seis-Bridge: `Seis-Bridge/` (ElastiFlow's own TinyDA
  client/server, 2,908 lines) and the submodule `fsx/Seis-Bridge` (sebwolf-de,
  105 MB, the TPV benchmark inputs the runners `cd` into).
* HPO application code (CIFAR-10 trainers, Ray Tune tests, `workflow_engine.py`)
  lives in `fsx/hyperparameter_test/`, under a SeisSol-named directory.

### Figure provenance (checked byte-for-byte against the thesis image tree)

| chapter | thesis images | generator output dir            | byte-identical | note |
|---------|---------------|---------------------------------|----------------|------|
| 7       | 8             | `analysis/sim_validation`       | 0 (renamed by hand: `thesis_fig3_mape_context.png` -> `mape.png`, ...) | raw input is an absolute path outside the repo (`parse_logs.py:28`); parsed CSVs are committed |
| 8       | 7             | `src/main/plots`, `plain_results/plots`, `license_results/plots` | 7 | |
| 9 SeisSol | 26          | `plain_results/plots`           | 26 | |
| 9 HPO   | 27            | `HPO/results/plots`             | 8  | 17 differ only in PDF metadata or a documented relabel (`02g`); **`04_paired_diff.pdf` in the repo is stale** |
| 9 Licence | 18          | `license_results/plots`         | 18 | |

`04_paired_diff.pdf` has **three writers**: `generate_thesis_plots.py:776`
(module-level, runs on every invocation), `regen_corrected_figs.py:490`
(unconditional in `main`), and `regen_paired_n7.py`. The thesis copy is the
`regen_paired_n7.py` output (reproduced from the committed
`total_cost_per_run.json`, 0 differing tokens, same twelve numbers). The other two
still write the superseded N=3/5/7 panel to the same name, and one of them ran last.
The thesis is right; the repository file is wrong. The same three-writer problem
exists for `02g_utilization_time`. No other figure in any campaign has more than one
writer.

## 2. Target layout

```
elastiflow/                         the framework (Ch4, Ch6). One package, one code path.
  __init__.py
  gateway/                          server.py (submission, completion), wf_queue/
  scheduler/
    base.py                         ONE Scheduler ABC (from scheduler.py; LA/HPO hooks become overridable methods)
    policies/                       fcfs.py, edf.py, heft.py, rank.py, lamf.py, hsm.py
    load_balancer/                  stream-to-resource (SeisSol) and phase-conditioned (HPO) assignment
  resource_manager/                 instance.py, manager.py, licence/ (manager, policy, models, persistence)
  workflow_engine/                  steep/ (parser, actions, variables), workflow.py, validate.py, dispatch.py (from exec_sched.py)
  runtime_model/                    speedup fitting and lookup (speedup.py, speedup_HPO_runtime.py)
  metrics/                          one Metrics class; use cases register extra columns
  execution/                        the Ch7 execution-backend interface and its two backends
    backend.py                      clock (now/sleep), provision(type, n), run_iteration(wf, args) -> result,
                                    notify_completion(event), queues. One interface, same API in both modes.
    live/                           boto3/paramiko provisioning, HTTP dispatch to executor nodes, real service
                                    scripts, Redis queues
    simulated/                      simulus clock and arrival process (from scripts/dispatcher.py), RuntimeModel
                                    (speedup.getRuntime over the use case's fitted tables), OverheadModel (the
                                    Ch7 overhead table as data: 7.7 s, 5.6 s, 10.4 s, cold start), modelled
                                    provisioning (COLD_START_TIME), in-memory queues (no Redis needed)
  config/                           ports.yaml; loader that resolves paths relative to the package
  cli.py                            python -m elastiflow run --mode simulated --use-case seissol --policy edf-elastic-c --N 100 --seed 7
                                    python -m elastiflow run --mode live      --use-case hpo ...
                                    --mode is the single composition-time switch that replaces the three SIMULATE constants

use_cases/                          what a use case contributes: workload, driver, runtime model, config, results
  _protocol.py                      the UseCase interface (name, constants, resources.yaml, licences.yaml?, workload generator,
                                    client-input handler = the Ch6 driver contract, metric extensions, and what the
                                    SIMULATED backend needs from it: runtime_model and overheads)
  seissol/
    driver.py                       getClientInputs_Plain + the TinyDA client/server (from Seis-Bridge/)
    workload.py                     workflow_generator.py
    config/                         constants.py, resources.yaml
    workflows/                      sample_workflows (400 YAML; see open decision 2)
    experiments/                    sweep drivers (sweep_PLAIN, sweep_K_PLAIN): the campaign, run in simulated mode
    results/                        plain_results datasets, figure generators, plots/
    validation/                     analysis/sim_validation (Ch7): fidelity of the simulator against live runs,
                                    validated for SeisSol--TinyDA only
  hpo/
    driver.py                       getClientInputs_HPO
    application/                    fsx/hyperparameter_test (CIFAR-10 trainers, workflow_engine.py)
    workload.py, config/, workflows/
    models/                         the standalone analytical models (sim_n5, sim_n7, sim_4corners_calibrated):
                                    NOT the framework simulator; they import nothing from it. HPO thesis numbers
                                    come from live AWS runs plus these models for the N sweep (Ch8:372).
                                    Confirmed by the author 2026-09-05: HPO runs live only; the simulated backend
                                    serves SeisSol and licence. simulate_main_HPO.py is the AWS live driver
                                    (SYNC_AND_REBUILD.md:240), not a simulator: as committed it crashes on
                                    enable_smp=True under macOS, and with SIMULATE forced it still executes the
                                    YAML's absolute /fsx/.../run_hpo.py service and sleeps in real time
                                    (steep_actions_HPO.py:76,178; dispatcher_HPO.py:187). It goes to deploy/runners.
    results/                        HPO/results datasets, generators, plots/
  licence/
    driver.py                       getClientInputs_LA
    workload.py, config/            constants_LA.py, licenses.yaml, resources.yaml (shared with seissol)
    workflows/                      sample_workflows_LA (800 YAML)
    experiments/                    canonical_sweep and the sensitivity sweeps: the campaign, run in simulated mode
    results/                        license_results datasets, snapshots, generators, plots/

deploy/                             how the framework is run for real, per target
  aws/                              IaC_scripts (terraform, cluster configs, slurm setup)
  runners/                          service/ (runner_base, seissol runners, hpo runners, setup_workers.sh)
  nodes/                            fsx/ node-side scripts (orchestrator.py, chain.py, misfit.py, setup*.sh)
  seis_bridge/                      the submodule (TPV inputs), path referenced by runners

motivation/                         experiments/manual_vs_auto (Ch1 motivation experiment)

thesis/
  figures.yaml                      chapter -> figure file -> generator -> dataset (the provenance manifest)
  sync_figures.py                   regenerate, then copy generator output INTO the thesis image tree, byte-compare

tests/
  regression/                       exists: one committed cell per campaign, 53 s, proven to fail
  unit/                             added during unification

docs/
  README.md, ARCHITECTURE.md (CLAUDE.md + contexts/HPO_ARCHITECTURE.md rewritten), PROVENANCE.md, history/ (contexts/*.log)

pyproject.toml, requirements.txt (from pip freeze), requirements-dev.txt, pytest.ini
```

What the layout says: **the simulator is not validation; it is one of the two
execution backends of the framework, and the one under which two of the three
thesis campaigns ran** (SeisSol--TinyDA and licence-constrained, Ch8:360; HPO ran
on live AWS, Ch8:372). Ch7's Fig. 7.1 already describes a single backend interface
with a mode flag. Today that interface does not exist in code: the mode is a
constant copied three times and 105 call sites in 33 files branch on `sim` or
`SIMULATE` individually; the runtime model is invoked from
`steep_actions.py:96-104` via the workflow's `service` script
(`scripts/simulate-tinyda-seissol.py`), the 7.7 s executor overhead is a literal
in two files, cold start is `(sim or time).sleep(COLD_START_TIME)` in
`create_instance.py:27`, and even simulated runs need a live Redis. The layout
makes the Ch7 sentence true.

**A use case is data plus a driver plus a runtime model, and it contributes no
scheduler code.** Licence awareness is a scheduler capability
(policies `lamf`, `hsm`; the Licence Manager) that the licence use case exercises,
not a third copy of the scheduler.

## 3. Migration, in three phases, each gated by the regression harness

### Status after A1 and A2 (2026-09-06)

A1 (flatten, paths) and A2 (dead and duplicate files) are done on `refactoring`;
harness 11/11 and all 16 simulated policies run after each. A "does it run" pass
on 2026-09-05 also ran all 25 figure generators (25/25) and compiled every file.
A2 removed 606 files: `workflow/temp/` (500 of 502 identical to the live LA set),
the `old_bad/` and `plots_newHSM/` snapshots with their generator, dead
`_old`/`_kavitha`/`_nisarg`/`hpc24x` modules and images, `old_helper_scripts/`,
the stale `test_hpo_system.py` (imports a class that no longer exists), root
scratch outputs, and two tracked `.pyc` files; renamed two shell scripts that had
a `.py` extension and the YAML with a trailing space; fixed `VENV_PY` in both sweep
drivers to `sys.executable` (pulled forward from A4 because the flatten had broken
it); added the never-committed `F0SENS` outputs of a tracked generator; ignored
`src/main/*.log`, which the HPO engine writes into the source tree.

A3 (2026-09-06): `src/main` is now the package `elastiflow/` with `pyproject.toml`
and an editable install (`pip install -e .`); 306 import lines in 56 files carry
the `elastiflow.` prefix, every `sys.path` hack that reached into the package is
gone, `config/paths.py` exposes `PACKAGE_DIR` and `REPO_ROOT`, and the drivers,
harness, Ch7 sweep and deploy scripts point at the new location. The in-package
`.gitignore` had been hiding `sample_workflows/`: the 400 SeisSol workflows the
runner reads were never committed, so a clone could not run that campaign. They
are tracked now, per the decision to keep generated workloads in the repository.
`src/test/` (workload fixtures and one ad-hoc script) moves in A5.

A4 (2026-09-06): `requirements.txt` is a pinned freeze of the environment that
produced the results (the old file held shell commands and omitted simulus);
`docs/INSTALL.md` carries the Redis and venv steps. Two test layers were added:
`tests/unit/test_imports.py` imports every framework module, including the
live-mode entry points (main, main_LA, main_HPO, the executors, AWS
provisioning), each in a fresh interpreter with a time limit, which is the
composition check live mode can get without infrastructure; and
`tests/smoke/` (`pytest -m smoke`, about seven minutes) runs all 11 SeisSol and 5
licence policies once plus the Ch7 runner. The import test found one defect of
A3 (`audit_workflows.py` referenced a name whose definition had been removed;
fixed) and two standalone analysis scripts that act on import
(`speedup_HPO.py` reads g4/g5.jsonl from the working directory,
`speedup_plot_HPO.py` writes plots/speedup_HPO.*); both are excluded with the
reason and get a `__main__` guard when they move in A5.

A5 (2026-09-06): the use-case and deployment layout is in place. `plain_results/`
is `use_cases/seissol/results/`, `analysis/sim_validation/` is
`use_cases/seissol/validation/` (with `urgency_weights.py` and the Ch5 figure it
draws), the TinyDA client/server from `Seis-Bridge/` is `use_cases/seissol/driver/`;
`license_results/` is `use_cases/licence/results/`; `HPO/` is `use_cases/hpo/`
(datasets, notes, `results/`), `fsx/hyperparameter_test/` is
`use_cases/hpo/application/`, `analysis/decompose.py` is `use_cases/hpo/analysis/`;
`service/`, `IaC_scripts/` and the node-side `fsx/` scripts are
`deploy/runners/`, `deploy/aws/` and `deploy/nodes/` (the Seis-Bridge submodule
moves with them); `experiments/manual_vs_auto/` is `motivation/manual_vs_auto/`;
`src/test/` is `tests/fixtures/`. Each results directory moved whole rather
than split into experiments/ and results/, so every intra-directory relative
path stayed valid; only the 19 repository-root derivations changed depth (each
verified to resolve to the root), plus the harness, smoke tests, `.gitignore`,
the cross-workload figure's two dataset paths, the HPO profiling-data paths in
the package, and the deploy scripts' checkout name (`/fsx/ElastiFlow`,
`~/ElastiFlow`). The historical `yamls_used/` records under
`use_cases/hpo/results/` keep the service paths they ran with. The dead
`elastiflow/sample_workflows_HPO/` example pair is removed. Still inside the
package for Phase B: `config/`, `scripts/` (dispatchers, generators, runtime
models, provisioning) and the workload YAMLs, because the framework imports them.

**Phase A, move without changing behaviour** (git mv, import rewrites, path fixes,
deletions). The three runners and the three scheduler forks survive this phase
untouched inside the new tree; `elastiflow.cli` simply dispatches to them.
1. Package `src/main` as `elastiflow/`; `pyproject.toml`; imports become
   `from elastiflow.scheduler...`; external scripts drop their `sys.path` hacks.
2. Replace the absolute paths (4 core defaults, `verify_sweep.py`, `speedup_plot.py`,
   `parse_logs.py`) with package-relative resolution or CLI arguments;
   `sys.executable` in the sweep drivers.
3. Delete the dead and duplicate files listed above (each deletion justified by a
   zero-reference grep, already done). `workflow/temp/` goes.
4. Move results, deployment, validation and application code into `use_cases/`,
   `deploy/`, `validation/`. Dataset files keep their basenames; the harness and
   generators are updated in the same commit.
5. Rename the `Seis-Bridge/` tree to `use_cases/seissol/driver/` and leave the
   submodule the only thing called Seis-Bridge.
Exit criterion: 11/11 regression tests pass; every generator runs from a clean clone.

**Phase B, unify the forks** (the actual refactoring). First step: extract the
execution-backend interface of Fig. 7.1, so that `sim` leaves the scheduler
signatures and the 105 branch sites collapse into two backends selected once in
`cli.py`; the in-memory queue removes the Redis dependency of simulated runs.
Gate: the regression harness unchanged, and the harness passing with Redis
stopped. Live mode cannot be regression-tested without infrastructure; it gets a
composition smoke test (imports, config, backend wiring) and that limit is stated
in the docs. Then: one `Scheduler` base with
the LA and HPO extension points as methods; one dispatcher; one metrics class with
registered extensions; one `simulate` entry point parameterised by use case and
policy. Done policy by policy, each step re-verified against the tagged datasets.
Exit criterion: the harness extended to every policy at one (N, seed) per campaign,
all cell-for-cell identical; then a full re-sweep of one campaign compared to the
committed dataset.

**Phase C, figure provenance.** One writer per figure. `thesis/figures.yaml` lists
every thesis figure with its generator and dataset; `sync_figures.py` regenerates and
byte-compares against the submitted image tree. The stale `04_paired_diff.pdf` is
fixed here by deleting the two superseded writers, not by editing the thesis.

## 4. Rules that hold throughout

* The tag is the reference. Nothing in Phases A to C may change a number in any
  committed dataset or in any thesis figure; if it does, the change is a bug.
* Policy names, dataset schemas and figure basenames stay as they are (the thesis
  cites them).
* Thesis images are copied from generator output, never edited in place. The
  submitted image tree is frozen; `sync_figures.py` only reports differences.
* No SeisSol numerics or physics are touched (the TinyDA client/server moves as a
  unit).
* Every commit on this branch passes `python -m pytest`.

## 5. Open decisions (need the author)

1. **Repository root.** The git root is `Vortex-mid/`, one level above the code, with
   16 stray tracked files. Flatten so that `elastiflow/` is at the root (cleanest,
   needs a history rewrite or a fresh repository), or keep the two levels.
2. **Generated workload YAMLs.** 1,220 tracked files (400 SeisSol, 800 licence, 20
   HPO) are outputs of the generators plus a seed. Keep them tracked (reproducibility
   without running anything) or regenerate on demand and keep only a checksum.
3. **Repository name.** `Vortex-moldable-sched` uses the term the thesis retired and a
   name the thesis never uses. Rename to `elastiflow` on GitHub when it goes public.
4. **`fsx/Seis-Bridge` submodule.** Keep the submodule (public upstream, 105 MB, needed
   only by live runs) or vendor just `tpv5/` and `tpv13/` inputs.
