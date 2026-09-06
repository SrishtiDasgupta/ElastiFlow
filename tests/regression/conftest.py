"""
Shared fixtures for the regression harness.

The harness re-runs one committed cell per campaign from the current working
tree and compares every parsed metric with the dataset of record. The
reference is the state tagged `thesis-submitted-2026-09-04`, from which the
dissertation PDF was built. All three simulators are seeded, so a cell
reproduces exactly; floats are compared to a 1e-12 relative tolerance only
because the HPO aggregation differs by one ulp in some standard deviations.

Nothing here writes into the datasets of record. The sweep drivers
(`use_cases/seissol/results/sweep_PLAIN.py`, `use_cases/licence/results/canonical_sweep.py`) merge
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

REPO = Path(__file__).resolve().parents[2]          # repository root
PACKAGE = REPO / 'elastiflow'                       # installed editable: pip install -e .

# license_analysis.py and parse_la_run.py live in use_cases/licence/results/ and are
# imported by name in canonical_sweep.py; mirror its import environment.
if str(REPO / 'use_cases' / 'licence' / 'results') not in sys.path:
    sys.path.insert(0, str(REPO / 'use_cases' / 'licence' / 'results'))


# The `repo` and `python` fixtures live in tests/conftest.py, shared by all suites.


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


# --- cell runners shared by the regression tests, the smoke suite and the -------
# --- baseline recorder: one definition of "run this cell and parse it" ---------

SEISSOL_N, SEISSOL_SEED = 100, 7
LICENCE_N, LICENCE_SEED = 150, 7
LICENCE_POLICIES = ('FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM')


def seissol_variants() -> dict:
    """The 11 SeisSol--TinyDA variants as sweep_PLAIN.py defines them."""
    return load_module(REPO / 'use_cases' / 'seissol' / 'results' / 'sweep_PLAIN.py').VARIANT_ARGS


# The four SeisSol policies the dissertation does not cite, kept and resolvable
# by module name through elastiflow/policies.py (docs/PHASE_B7_SCHEDULER_MERGE.md,
# B7.0). One cell each in the smoke baseline, so they cannot rot unnoticed.
UNCITED_SEISSOL_VARIANTS = {
    'fcfs_scheduler_static':         ['fcfs_scheduler', 'static', '--sort-key', 'cost'],
    'earliest_deadline_fcfs_static': ['earliest_deadline_fcfs', 'static', '--sort-key', 'cost'],
    'priority_fcfs_static':          ['priority_fcfs', 'static'],
    'heft_fcfs_req_static':          ['heft_fcfs_req', 'static'],
}


def seissol_cell(python: str, variant: str, out_dir: Path, N: int = SEISSOL_N, seed: int = SEISSOL_SEED) -> dict:
    """Run one SeisSol cell exactly as sweep_PLAIN.py does and parse the .out
    file the simulator moves into out_dir with sweep_PLAIN.parse_out."""
    sweep = load_module(REPO / 'use_cases' / 'seissol' / 'results' / 'sweep_PLAIN.py')
    out_dir.mkdir(parents=True, exist_ok=True)
    args = sweep.VARIANT_ARGS.get(variant) or UNCITED_SEISSOL_VARIANTS[variant]
    run([python, 'simulate_sweep.py', *args, out_dir,
         '--seed', str(seed), '--N', str(N)], cwd=REPO / 'elastiflow')
    outs = sorted(out_dir.glob('*.out'))
    assert len(outs) == 1, f'expected one .out file in {out_dir}, found {outs}'
    rec = sweep.parse_out(outs[0].read_text())
    assert not rec.get('_parse_failed'), f'sweep_PLAIN.parse_out could not parse {variant}'
    return rec


def licence_cell(python: str, scheduler: str, out_dir: Path, N: int = LICENCE_N, seed: int = LICENCE_SEED) -> dict:
    """Run one licence cell exactly as canonical_sweep.py does: capture
    stdout+stderr, parse with parse_la_run.parse, then derive the licence
    accounting fields from the two CSVs with license_analysis."""
    import glob
    parse_la_run = load_module(REPO / 'use_cases' / 'licence' / 'results' / 'parse_la_run.py')
    LA = load_module(REPO / 'use_cases' / 'licence' / 'results' / 'license_analysis.py')
    out_dir.mkdir(parents=True, exist_ok=True)
    cp = run([python, 'simulate_main_LA.py', '--scheduler', scheduler, '--N', str(N),
              '--seed', str(seed), '--output-dir', out_dir],
             cwd=REPO / 'elastiflow', env=la_env(scheduler))
    rec = parse_la_run.parse(cp.stdout)
    results_csv = sorted(out_dir.glob('*_results.csv')); usage_csv = sorted(out_dir.glob('*_license_usage.csv'))
    assert len(results_csv) == 1 and len(usage_csv) == 1, \
        f'expected one results and one usage CSV in {out_dir}, found {sorted(out_dir.iterdir())}'
    r = LA.analyze_results(str(results_csv[0]))
    rec['tot_lic'] = r['tot_lic']; rec['tot_hw'] = r['tot_hw']
    rec['waste_frac'] = r['waste_frac']; rec['eff_lic_util'] = 100 - r['waste_frac']
    rec['lic_per_done'] = r['lic_per_done']; rec['overhead'] = r['overhead']
    rec['n_done'] = r['n_done']; rec['n_miss'] = r['n_miss']
    rec['per_solver'] = {s: {'lic': round(v['lic'], 1), 'n': v['n'], 'done': v['done']}
                         for s, v in r['per_solver'].items()}
    u = LA.analyze_usage(str(usage_csv[0]))
    rec['token_sec_total'] = sum(x['token_sec'] for x in u.values())
    rec['pool_tw_util'] = {pool: round(u[pool]['tw_util'], 1) for pool in u}
    return rec


def strip_bookkeeping(rec: dict) -> dict:
    """Drop the run-specific keys (wall clock, file names, exit codes)."""
    return {k: v for k, v in rec.items() if not k.startswith('_')}
