"""Sweep driver for the LA chapter.

Runs every (policy, N, seed) cell of the LA experiment as a subprocess,
parses each run's stdout, and persists the aggregated result set to
`la_results_per_run.json` next to this script.

Usage:
    python sweep_LA.py                # full sweep with the defaults below
    python sweep_LA.py --policies LAMF EDF-HSM
    python sweep_LA.py --N 200 400    # only run two workload sizes
    python sweep_LA.py --seeds 7 107  # only two seeds
    python sweep_LA.py --resume       # skip cells already in the JSON
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Resolve key paths from this file's location, not the CWD.
HERE = Path(__file__).resolve().parent          # license_results/
REPO_ROOT = HERE.parent                          # Vortex-moldable-sched/
SIM_SCRIPT = REPO_ROOT / 'src' / 'main' / 'simulate_main_LA.py'
VENV_PY = (REPO_ROOT.parent / 'vortex_venv' / 'bin' / 'python3')
OUTPUT_JSON = HERE / 'la_results_per_run.json'
RUN_DIR = HERE / 'sweep_runs'                    # per-cell run dirs go here
PARSE_MOD = HERE / 'parse_la_run.py'

DEFAULT_POLICIES = ['FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM']
DEFAULT_NS       = [200, 300, 400, 500, 600, 700]
DEFAULT_SEEDS    = [7, 107, 207, 1007, 1107, 1207]   # mirrors HPO


def cell_key(policy: str, N: int, seed: int) -> str:
    return f'{policy}__N{N}__seed{seed}'


def load_existing(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_results(path: Path, results: dict) -> None:
    path.write_text(json.dumps(results, indent=2))


def run_cell(policy: str, N: int, seed: int) -> dict:
    """Launch one simulator subprocess, parse its stdout, return the metrics."""
    cell_id = cell_key(policy, N, seed)
    out_dir = RUN_DIR / cell_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / 'stdout.log'

    # Run from REPO_ROOT so internal imports (`scheduler.…`, `config.…`,
    # `scripts.…`) resolve relative to src/main, which is on sys.path because
    # simulate_main_LA.py uses bare imports.
    cmd = [
        str(VENV_PY), str(SIM_SCRIPT),
        '--scheduler', policy,
        '--N', str(N),
        '--seed', str(seed),
        '--output-dir', str(out_dir),
    ]
    t0 = time.time()
    with open(stdout_path, 'w') as fh:
        proc = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.STDOUT,
            cwd=REPO_ROOT / 'src' / 'main',
            timeout=30 * 60,
        )
    wall_s = time.time() - t0

    # Parse — import the parser as a module so we don't fork another Python.
    sys.path.insert(0, str(HERE))
    import parse_la_run
    text = stdout_path.read_text()
    parsed = parse_la_run.parse(text)
    parsed['_policy'] = policy
    parsed['_N'] = N
    parsed['_seed'] = seed
    parsed['_wall_clock_s'] = round(wall_s, 1)
    parsed['_exit_code'] = proc.returncode
    return parsed


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--policies', nargs='*', default=DEFAULT_POLICIES)
    p.add_argument('--N',        nargs='*', type=int, default=DEFAULT_NS)
    p.add_argument('--seeds',    nargs='*', type=int, default=DEFAULT_SEEDS)
    p.add_argument('--resume',   action='store_true',
                   help='Skip cells whose key already exists in the JSON.')
    p.add_argument('--dry-run',  action='store_true',
                   help='Print the (policy,N,seed) list and exit.')
    args = p.parse_args()

    cells = [(pol, N, sd)
             for pol in args.policies
             for N in args.N
             for sd in args.seeds]
    print(f'sweep: {len(cells)} cells '
          f'({len(args.policies)} policies × {len(args.N)} Ns '
          f'× {len(args.seeds)} seeds)')
    if args.dry_run:
        for c in cells:
            print('  ', c)
        return

    results = load_existing(OUTPUT_JSON)
    print(f'existing JSON has {len(results)} cells; resume={args.resume}')

    for i, (pol, N, sd) in enumerate(cells, 1):
        key = cell_key(pol, N, sd)
        if args.resume and key in results and not results[key].get(
                '_parse_failed', False):
            print(f'[{i}/{len(cells)}] {key}: SKIP (cached)')
            continue
        print(f'[{i}/{len(cells)}] {key}: running... ', end='', flush=True)
        try:
            res = run_cell(pol, N, sd)
            if res.get('_parse_failed') or res.get('_exit_code') != 0:
                print(f'FAILED (exit={res.get("_exit_code")}, '
                      f'parse_ok={not res.get("_parse_failed")})')
            else:
                print(f'ok in {res["_wall_clock_s"]}s  '
                      f'(misses={res.get("overall_miss_rate")}, '
                      f'cost=€{res.get("avg_cost_eur")})')
        except subprocess.TimeoutExpired:
            res = {'_policy': pol, '_N': N, '_seed': sd,
                   '_parse_failed': True, '_error': 'timeout'}
            print('TIMEOUT')
        results[key] = res
        save_results(OUTPUT_JSON, results)

    print(f'wrote {OUTPUT_JSON} ({len(results)} cells)')


if __name__ == '__main__':
    main()
