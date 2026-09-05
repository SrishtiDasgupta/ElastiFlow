"""
Shared fixtures for the regression harness.

The harness re-runs one committed cell per campaign from the current working
tree and compares every parsed metric with the dataset of record. The
reference is the state tagged `thesis-submitted-2026-09-04`, from which the
dissertation PDF was built. All three simulators are seeded, so a cell
reproduces exactly; floats are compared to a 1e-12 relative tolerance only
because the HPO aggregation differs by one ulp in some standard deviations.

Nothing here writes into the datasets of record. The sweep drivers
(`plain_results/sweep_PLAIN.py`, `license_results/canonical_sweep.py`) merge
results in place into those files, so the tests call the underlying runners
directly and parse their output with the same functions the drivers use.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]          # Vortex-moldable-sched/
SRC_MAIN = REPO / 'src' / 'main'

# license_analysis.py and parse_la_run.py live in license_results/ and are
# imported by name in canonical_sweep.py; mirror its import environment.
for p in (str(REPO / 'license_results'), str(SRC_MAIN)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope='session')
def repo() -> Path:
    return REPO


@pytest.fixture(scope='session')
def python() -> str:
    """Interpreter used to spawn the simulators: the one running pytest."""
    return sys.executable


def load_module(path: Path):
    """Import a script by path without executing its __main__ block."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(cmd, cwd, env=None, timeout=900) -> subprocess.CompletedProcess:
    """Run a simulator cell, capturing stdout+stderr together as the sweep
    drivers do. Fails the test with the tail of the output on a non-zero exit."""
    cp = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=timeout)
    if cp.returncode != 0:
        tail = '\n'.join(cp.stdout.splitlines()[-40:])
        pytest.fail(f'{cmd[1]} exited {cp.returncode}\n--- output tail ---\n{tail}')
    return cp


def la_env(scheduler: str) -> dict:
    """Environment canonical_sweep.py uses for a licence cell. EDF-HSM must be
    pinned to the uniform rho = 0.70 gate or the per-pool variant runs instead."""
    env = os.environ.copy()
    if scheduler == 'EDF-HSM':
        env.update(LA_HSM_POOL_RHO='0.70', LA_HSM_POOL_RHO_ANSYS='0.70',
                   LA_HSM_POOL_RHO_ABAQUS='0.70', LA_HSM_POOL_RHO_LSDYNA='0.70')
    return env


def _flatten(obj, prefix=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f'{prefix}{k}.')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f'{prefix}[{i}].')
    else:
        yield prefix[:-1], obj


def assert_records_equal(ref: dict, got: dict, rel: float = 1e-12) -> None:
    """Every field of the committed record must be present in the re-run and
    equal: exactly for ints, strings, bools and None; within `rel` relative
    tolerance for floats. Bookkeeping keys (leading underscore) are skipped."""
    G = dict(_flatten(got))
    diffs, missing, compared = [], [], 0
    for key, a in _flatten(ref):
        if key.split('.')[-1].startswith('_'):
            continue
        if key not in G:
            missing.append(key)
            continue
        compared += 1
        b = G[key]
        if a == b:
            continue
        if isinstance(a, float) or isinstance(b, float):
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                scale = max(abs(a), abs(b), 1e-300)
                if abs(a - b) / scale <= rel:
                    continue
        diffs.append((key, a, b))
    msg = [f'{compared} fields compared, {len(diffs)} differ, {len(missing)} missing']
    msg += [f'  DIFF    {k}: committed={a!r} rerun={b!r}' for k, a, b in diffs[:25]]
    msg += [f'  MISSING {k}' for k in missing[:25]]
    assert not diffs and not missing, '\n'.join(msg)
    assert compared > 0, 'nothing was compared'


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())
