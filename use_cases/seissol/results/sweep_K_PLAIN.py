"""k-sensitivity sweep for the Plain SeisSol-TinyDA moldable scale-down.

Validates the hard-coded packing ceiling `CHAINS_PER_NODE` (default 3) in the
moldable scale-down loop (Scheduler.processFreeRequest /
FCFS_Optimized.processFreeRequest). For each k in {1..5} it re-runs the moldable
Plain experiment and records the scheduler-level outcomes (makespan, on-demand
cost, deadline/budget misses, scale-down activity). The sweep answers two
questions for the dissertation:

  1. Is k = 3 a justified operating point, or would a different k do better?
  2. How sensitive are the outcomes to k (robustness around the chosen value)?

Static variants are excluded by construction: the scale-down loop never runs in
static mode, so k has no effect there.

This driver reuses the cell-runner machinery (parse_out, VARIANT_ARGS, venv,
sim-script path) from sweep_PLAIN.py and only adds the `--chains-per-node`
dimension. Results land in use_cases/seissol/results/k_sweep_per_run.json; per-cell run
dirs go under use_cases/seissol/results/k_sweep_runs/.

Usage:
    python sweep_K_PLAIN.py                       # full: 2 variants x k{1..5} x 6 seeds at N=400
    python sweep_K_PLAIN.py --k 1 2 3             # subset of k
    python sweep_K_PLAIN.py --variants edf_moldable_r
    python sweep_K_PLAIN.py --N 400 --seeds 7 107 # quick smoke
    python sweep_K_PLAIN.py --resume
    python sweep_K_PLAIN.py --dry-run
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from sweep_PLAIN import (
    VARIANT_ARGS, VENV_PY, SIM_SCRIPT, REPO_ROOT, parse_out,
)

HERE = Path(__file__).resolve().parent          # use_cases/seissol/results/
OUTPUT_JSON = HERE / 'k_sweep_per_run.json'
RUN_DIR = HERE / 'k_sweep_runs'

# Only moldable variants are meaningful: the scale-down loop (and thus k) is
# never reached in static mode. Default to the two headline runtime-sorted
# moldable variants (EDF + FCFS).
DEFAULT_VARIANTS = ['edf_moldable_r', 'fcfs_moldable_r']
ALLOWED_VARIANTS = [v for v in VARIANT_ARGS if 'moldable' in v]
DEFAULT_KS    = [1, 2, 3, 4, 5]
DEFAULT_NS    = [400]                            # Plain headline batch size
DEFAULT_SEEDS = [7, 107, 207, 1007, 1107, 1207]  # the 6 canonical seeds


def cell_key(variant: str, k: int, N: int, seed: int) -> str:
    return f'{variant}__k{k}__N{N}__seed{seed}'


def run_cell(variant: str, k: int, N: int, seed: int) -> dict:
    cell_id = cell_key(variant, k, N, seed)
    out_dir = RUN_DIR / cell_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / 'stdout.log'

    cmd = [
        str(VENV_PY), str(SIM_SCRIPT),
        *VARIANT_ARGS[variant],
        str(out_dir),
        '--seed', str(seed),
        '--N', str(N),
        '--chains-per-node', str(k),
    ]

    t0 = time.time()
    with open(stdout_path, 'w') as fh:
        proc = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.STDOUT,
            cwd=REPO_ROOT / 'src' / 'main', timeout=30 * 60,
        )
    wall_s = time.time() - t0

    out_files = list(out_dir.glob('*.out'))
    if not out_files:
        return {
            '_variant': variant, '_k': k, '_N': N, '_seed': seed,
            '_parse_failed': True, '_error': 'no .out file',
            '_wall_clock_s': round(wall_s, 1), '_exit_code': proc.returncode,
        }
    parsed = parse_out(out_files[0].read_text())
    parsed.update({
        '_variant': variant, '_k': k, '_N': N, '_seed': seed,
        '_wall_clock_s': round(wall_s, 1), '_exit_code': proc.returncode,
        '_out_file': out_files[0].name,
    })
    return parsed


def load_existing(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_results(path: Path, results: dict) -> None:
    path.write_text(json.dumps(results, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--variants', nargs='*', default=DEFAULT_VARIANTS,
                   choices=ALLOWED_VARIANTS)
    p.add_argument('--k',     nargs='*', type=int, default=DEFAULT_KS)
    p.add_argument('--N',     nargs='*', type=int, default=DEFAULT_NS)
    p.add_argument('--seeds', nargs='*', type=int, default=DEFAULT_SEEDS)
    p.add_argument('--resume', action='store_true',
                   help='Skip cells already present (and not parse-failed).')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    cells = [(v, k, N, sd)
             for v in args.variants
             for k in args.k
             for N in args.N
             for sd in args.seeds]
    print(f'k-sweep: {len(cells)} cells '
          f'({len(args.variants)} variants x {len(args.k)} k x '
          f'{len(args.N)} N x {len(args.seeds)} seeds)')
    if args.dry_run:
        for c in cells:
            print('  ', cell_key(*c))
        return

    results = load_existing(OUTPUT_JSON)
    print(f'existing JSON has {len(results)} cells; resume={args.resume}')

    for i, (v, k, N, sd) in enumerate(cells, 1):
        key = cell_key(v, k, N, sd)
        if args.resume and key in results and not results[key].get('_parse_failed'):
            print(f'[{i}/{len(cells)}] {key}: SKIP (cached)')
            continue
        print(f'[{i}/{len(cells)}] {key}: running... ', end='', flush=True)
        try:
            res = run_cell(v, k, N, sd)
            if res.get('_parse_failed') or res.get('_exit_code') != 0:
                print(f'FAILED (exit={res.get("_exit_code")}, '
                      f'parse_ok={not res.get("_parse_failed")})')
            else:
                print(f'ok in {res["_wall_clock_s"]}s  '
                      f'(makespan={res.get("batch_makespan_s")}, '
                      f'OD=€{res.get("total_cost_on_demand")}, '
                      f'dmiss={res.get("deadline_miss_rate")}, '
                      f'down={res.get("scale_down_nodes_freed")})')
        except subprocess.TimeoutExpired:
            res = {'_variant': v, '_k': k, '_N': N, '_seed': sd,
                   '_parse_failed': True, '_error': 'timeout'}
            print('TIMEOUT')
        results[key] = res
        save_results(OUTPUT_JSON, results)

    print(f'wrote {OUTPUT_JSON} ({len(results)} cells)')


if __name__ == '__main__':
    main()
