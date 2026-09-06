# Installing and running ElastiFlow

## Requirements

* Python 3.10 or newer (the dissertation results were produced with 3.13).
* A Redis server on `localhost:6379`. Both execution modes use it for the three
  work queues (this dependency of the *simulated* mode is removed in Phase B of
  `docs/REORGANISATION.md`).
* For live mode only: AWS credentials and the cluster setup under `deploy/aws/`
  and `deploy/nodes/`; SLURM on the on-premise tier.

### Redis

Debian/Ubuntu:

```
sudo apt-get install lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update && sudo apt-get install redis
sudo systemctl enable --now redis-server
```

macOS: `brew install redis && brew services start redis`.

## Python environment

```
python3 -m venv vortex_venv
vortex_venv/bin/python3 -m pip install -r requirements.txt -r requirements-dev.txt
vortex_venv/bin/python3 -m pip install --no-deps -e .
```

Use `python3 -m pip`, not the venv's `pip` launcher, if the venv is ever moved:
the launcher embeds an absolute path.

## Checking the installation

```
vortex_venv/bin/python3 -m pytest            # unit + regression: about half a minute
vortex_venv/bin/python3 -m pytest -m smoke   # every simulated policy once, compared exactly with the recorded baseline: about half a minute
```

The regression tests re-run one committed cell per campaign and compare every
metric with the dataset of record at tag `thesis-submitted-2026-09-04`; they must
pass before and after any change to the framework. The smoke suite re-runs all
16 simulated policies and compares every metric with
`tests/regression/baseline_all_policies.json`, recorded from a tree that passed
the regression tests. To move that reference deliberately, run
`tests/regression/record_baseline.py` and commit the new file with the reason.
The HPO schedulers, which have no simulated cell, are pinned the same way by
`tests/regression/baseline_hpo_allocation.json` (their allocation decisions,
recorded by `tests/regression/record_hpo_allocation.py`).

## Running a simulated cell by hand

From the package directory, with the venv active:

```
cd elastiflow
python simulate_sweep.py edf moldable --sort-key cost /tmp/out --seed 7 --N 100      # SeisSol--TinyDA
python simulate_main_LA.py --scheduler EDF-LAMF --N 150 --seed 7 --output-dir /tmp/la  # licence-constrained
```

or, from anywhere, through the one-switch entry point (`--mode` selects the
execution backend; everything after `--use-case` goes to the runner unchanged):

```
python -m elastiflow run --mode simulated --use-case seissol edf moldable --sort-key cost /tmp/out --seed 7 --N 100
python -m elastiflow run --mode simulated --use-case licence --scheduler EDF-LAMF --N 150 --seed 7 --output-dir /tmp/la
python -m elastiflow run --mode live --use-case seissol        # the live scheduler process (Redis, AWS); executor nodes run elastiflow/executor.py
```

`pip install -e .` also installs the same command as `elastiflow`.

Whole campaigns: `use_cases/seissol/results/sweep_PLAIN.py` and `use_cases/licence/results/canonical_sweep.py`
(both merge results into the dataset files in place; run them on a branch).
