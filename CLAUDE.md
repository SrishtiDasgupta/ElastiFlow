# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

ElastiFlow, the framework behind Srishti Dasgupta's dissertation on elastic
resource management for iterative scientific workflows. `README.md` gives the
layout, `docs/ARCHITECTURE.md` the components and the three per-use-case forks,
`docs/PROVENANCE.md` the figure-to-dataset map, `docs/REORGANISATION.md` the
refactoring plan and its status. Read those before changing anything.

## Ground rules

* **The tag `thesis-submitted-2026-09-04` is the reference.** No change may
  alter a number in a committed dataset or in a dissertation figure. Run
  `python -m pytest` (regression + unit, about half a minute) before and after every
  change; run `python -m pytest -m smoke` (every policy once, compared exactly with
  `tests/regression/baseline_all_policies.json`, about half a minute) after
  anything that touches the framework. Re-record the baseline only when the
  reference is meant to move, and say so in the commit.
* **The user commits and pushes.** Stage the change set, show
  `git diff --cached --stat`, and hand over a commit message. Never run
  `git commit` or `git push`, and never add Claude/AI authorship or session
  trailers to a message.
* Verify before asserting: every claim about the code or the data is checked
  by running or reading it, and the check is shown.
* The sweep drivers merge results into the dataset files in place; do not run
  them as a test. The regression tests call the runners directly.
* Policy names, dataset schemas and figure basenames are cited by the
  dissertation and do not change.
* Live mode (AWS, SLURM, Ray) cannot be tested here; it gets the import
  composition test only, and that limit is stated wherever it matters.

## Commands

```
vortex_venv/bin/python3 -m pip install -r requirements.txt -r requirements-dev.txt
vortex_venv/bin/python3 -m pip install --no-deps -e .        # once; use python -m pip, not the venv's pip launcher
vortex_venv/bin/python3 -m pytest                            # default suite
vortex_venv/bin/python3 -m pytest -m smoke                   # all policies once
cd elastiflow && python simulate_sweep.py edf moldable --sort-key cost /tmp/out --seed 7 --N 100
cd elastiflow && python simulate_main_LA.py --scheduler EDF-LAMF --N 150 --seed 7 --output-dir /tmp/la
```

A Redis server must be running on localhost:6379, also for simulated runs.

## Things that have bitten before

* A relocated venv breaks its `pip` launcher (absolute shebang); `python -m pip` works.
* Paths built from components (`HERE.parents[n] / "dir"`) are invisible to a
  substring grep for `dir/`; grep for the quoted component too.
* Several analysis scripts under `elastiflow/scripts/` act on import
  (`speedup_HPO.py`, `speedup_plot_HPO.py`); the import test excludes them.
* Figure generators write into their own `plots/` directories, which are
  tracked; after running one, `git checkout --` the directory unless the
  regeneration is intended. Four HPO figures have more than one writer script
  (Phase C).
* `git mv` into a directory that already exists moves the source *into* it.
