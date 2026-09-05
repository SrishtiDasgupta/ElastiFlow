"""Sweep driver for the plain SeisSol-TinyDA chapter.

Runs every (algorithm-variant, N, seed) cell of the plain experiment as
a subprocess invocation of `simulate_sweep.py`, parses each .out file
into a structured dict, and persists the aggregated result set to
`plain_results_per_run.json` next to this script.

The 11 canonical algorithm-variants reproduce the appendix's table:

    fcfs_static_r / fcfs_static_c / fcfs_moldable_r / fcfs_moldable_c
    edf_static_r  / edf_static_c  / edf_moldable_r  / edf_moldable_c
    heft_static
    rank_moldable_5050   (BUDGET=5.0, DEADLINE=5.0)
    rank_moldable_2575   (BUDGET=2.5, DEADLINE=7.5)

Usage:
    python sweep_PLAIN.py                   # full sweep with the defaults
    python sweep_PLAIN.py --variants edf_moldable_r fcfs_static_r
    python sweep_PLAIN.py --N 200 400
    python sweep_PLAIN.py --seeds 7 107
    python sweep_PLAIN.py --resume
    python sweep_PLAIN.py --dry-run
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # plain_results/
REPO_ROOT = HERE.parent                          # Vortex-moldable-sched/
SIM_SCRIPT = REPO_ROOT / 'elastiflow' / 'simulate_sweep.py'
VENV_PY = Path(sys.executable)                  # the interpreter running this driver
OUTPUT_JSON = HERE / 'plain_results_per_run.json'
RUN_DIR = HERE / 'sweep_runs'                    # per-cell run dirs go here

# --- The 11 algorithm-variants ---------------------------------------------
# Each maps to a list of CLI args appended to `simulate_sweep.py <algo> <mode> <out>`
VARIANT_ARGS = {
    'fcfs_static_r':       ['fcfs',  'static',   '--sort-key', 'runtime'],
    'fcfs_static_c':       ['fcfs',  'static',   '--sort-key', 'cost'],
    'fcfs_moldable_r':     ['fcfs',  'moldable', '--sort-key', 'runtime'],
    'fcfs_moldable_c':     ['fcfs',  'moldable', '--sort-key', 'cost'],
    'edf_static_r':        ['edf',   'static',   '--sort-key', 'runtime'],
    'edf_static_c':        ['edf',   'static',   '--sort-key', 'cost'],
    'edf_moldable_r':      ['edf',   'moldable', '--sort-key', 'runtime'],
    'edf_moldable_c':      ['edf',   'moldable', '--sort-key', 'cost'],
    'heft_static':         ['heft',  'static'],
    'rank_moldable_5050':  ['rank',  'moldable', '--rank-budget', '5.0', '--rank-deadline', '5.0'],
    'rank_moldable_2575':  ['rank',  'moldable', '--rank-budget', '2.5', '--rank-deadline', '7.5'],
}

DEFAULT_VARIANTS = list(VARIANT_ARGS.keys())
DEFAULT_NS       = [100, 200, 300, 400, 500, 600, 700]
DEFAULT_SEEDS    = [7, 107, 207, 1007, 1107, 1207]


def cell_key(variant: str, N: int, seed: int) -> str:
    return f'{variant}__N{N}__seed{seed}'


# --- Parsing ---------------------------------------------------------------
_NUM = r'([+-]?\d+(?:\.\d+)?)'


def _grab(text: str, pattern: str, conv=float):
    m = re.search(pattern, text)
    return conv(m.group(1)) if m else None


def parse_out(text: str) -> dict:
    """Parse the .out file produced by metrics.computeMetrics() into a
    structured dict. All money is in EUR (the simulator's native unit);
    conversion to USD happens at plot/render time."""
    out = {}
    out['total_workflows']     = _grab(text, rf'Total workflows = {_NUM}', int)
    out['executed_workflows']  = _grab(text, rf'Executed workflows = {_NUM}', int)
    out['avg_flowtime_s']      = _grab(text, rf'Average Flowtime = {_NUM}')
    out['batch_makespan_s']    = _grab(text, rf'Batch Makespan = {_NUM}')
    out['avg_cost_eur']        = _grab(text, rf'^Average Cost = {_NUM}')
    out['avg_cost_on_prem']    = _grab(text, rf'Average Cost on-prem = {_NUM}')
    out['avg_cost_reserved']   = _grab(text, rf'Average Cost reserved-cloud = {_NUM}')
    out['avg_cost_on_demand']  = _grab(text, rf'Average Cost on-demand-cloud = {_NUM}')
    out['cost_pct_on_prem']    = _grab(text, rf'Cost % on-prem = {_NUM}')
    out['cost_pct_reserved']   = _grab(text, rf'Cost % reserved-cloud = {_NUM}')
    out['cost_pct_on_demand']  = _grab(text, rf'Cost % on-demand-cloud = {_NUM}')
    out['total_cost_eur']      = _grab(text, rf'Total Cost = {_NUM}')
    out['total_cost_on_prem']  = _grab(text, rf'Total Cost on-prem = {_NUM}')
    out['total_cost_reserved'] = _grab(text, rf'Total Cost reserved-cloud = {_NUM}')
    out['total_cost_on_demand']= _grab(text, rf'Total Cost on-demand-cloud = {_NUM}')
    out['avg_wait_time_s']     = _grab(text, rf'Average Wait Time = {_NUM}')
    out['util_overall_pct']    = _grab(text, rf'Average Resource Utilization \(overall\) = {_NUM}')
    out['util_on_prem_pct']    = _grab(text, rf'Util on-prem = {_NUM}')
    out['util_reserved_pct']   = _grab(text, rf'Util reserved-cloud = {_NUM}')
    out['util_on_demand_pct']  = _grab(text, rf'Util on-demand-cloud = {_NUM}')
    out['deadline_miss_rate']  = _grab(text, rf'Deadline miss rate = {_NUM}')
    out['budget_miss_rate']    = _grab(text, rf'Budget miss rate = {_NUM}')
    out['overall_miss_rate']   = _grab(text, rf'Overall miss rate = {_NUM}')
    out['wasted_time_hours']   = _grab(text, rf'Time spent on incomplete workflows = {_NUM}')
    out['wasted_cost_eur']     = _grab(text, rf'Wasted cost on incomplete workflows = {_NUM}')

    # Scaling decisions
    out['scale_up_attempts']         = _grab(text, rf'Scale-up attempts = {_NUM}', int)
    out['scale_up_full']             = _grab(text, rf'Granted in full  = {_NUM}', int)
    out['scale_up_partial']          = _grab(text, rf'Granted partial  = {_NUM}', int)
    out['scale_up_denied']           = _grab(text, rf'Denied \(no grant\) = {_NUM}', int)
    out['scale_up_nodes_requested']  = _grab(text, rf'Scale-up nodes requested \(total\) = {_NUM}', int)
    out['scale_up_nodes_granted']    = _grab(text, rf'Scale-up nodes granted \(total\)   = {_NUM}', int)
    out['scale_up_on_prem_attempts'] = _grab(text, rf'On-prem path:  attempts {_NUM}, granted', int)
    out['scale_up_on_prem_granted']  = _grab(text, rf'On-prem path:  attempts \d+, granted {_NUM}', int)
    out['scale_up_cloud_attempts']   = _grab(text, rf'Cloud path:    attempts {_NUM}, granted', int)
    out['scale_up_cloud_granted']    = _grab(text, rf'Cloud path:    attempts \d+, granted {_NUM}', int)
    out['scale_down_attempts']       = _grab(text, rf'Scale-down attempts = {_NUM}', int)
    out['scale_down_executed']       = _grab(text, rf'Executed \(>=1 node freed\) = {_NUM}', int)
    out['scale_down_nodes_freed']    = _grab(text, rf'Total nodes freed         = {_NUM}', int)
    out['executor_request_up']       = _grab(text, rf'REQUEST_RESOURCE events \(wanted scale-up\)   = {_NUM}', int)
    out['executor_request_down']     = _grab(text, rf'FREE_RESOURCE events    \(wanted scale-down\) = {_NUM}', int)
    out['scheduler_override_rate']   = _grab(text, rf'Scheduler-override rate \(REQUEST_RESOURCE → scheduler scaled down or noop\) = \d+/\d+ \({_NUM}%')

    # Per-tier scale-up grants
    out['scale_up_grants_on_prem']   = _grab(text, rf'on-prem        = {_NUM} nodes', int)
    out['scale_up_grants_reserved']  = _grab(text, rf'reserved-cloud = {_NUM} nodes', int)
    out['scale_up_grants_on_demand'] = _grab(text, rf'on-demand      = {_NUM} nodes', int)

    if out['total_workflows'] is None or out['total_cost_eur'] is None:
        out['_parse_failed'] = True
    return out


# --- Subprocess driver -----------------------------------------------------
def run_cell(variant: str, N: int, seed: int) -> dict:
    cell_id = cell_key(variant, N, seed)
    out_dir = RUN_DIR / cell_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / 'stdout.log'

    cmd = [
        str(VENV_PY), str(SIM_SCRIPT),
        *VARIANT_ARGS[variant],
        str(out_dir),
        '--seed', str(seed),
        '--N', str(N),
    ]

    t0 = time.time()
    with open(stdout_path, 'w') as fh:
        proc = subprocess.run(
            cmd,
            stdout=fh, stderr=subprocess.STDOUT,
            cwd=REPO_ROOT / 'elastiflow',
            timeout=30 * 60,
        )
    wall_s = time.time() - t0

    # Find the .out file the simulator wrote
    out_files = list(out_dir.glob('*.out'))
    if not out_files:
        return {
            '_variant': variant, '_N': N, '_seed': seed,
            '_parse_failed': True, '_error': 'no .out file',
            '_wall_clock_s': round(wall_s, 1), '_exit_code': proc.returncode,
        }
    text = out_files[0].read_text()
    parsed = parse_out(text)
    parsed['_variant'] = variant
    parsed['_N'] = N
    parsed['_seed'] = seed
    parsed['_wall_clock_s'] = round(wall_s, 1)
    parsed['_exit_code'] = proc.returncode
    parsed['_out_file'] = str(out_files[0].name)
    return parsed


# --- Main loop -------------------------------------------------------------
def load_existing(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_results(path: Path, results: dict) -> None:
    path.write_text(json.dumps(results, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--variants', nargs='*', default=DEFAULT_VARIANTS,
                   choices=DEFAULT_VARIANTS)
    p.add_argument('--N',        nargs='*', type=int, default=DEFAULT_NS)
    p.add_argument('--seeds',    nargs='*', type=int, default=DEFAULT_SEEDS)
    p.add_argument('--resume',   action='store_true',
                   help='Skip cells whose key already exists in the JSON.')
    p.add_argument('--dry-run',  action='store_true',
                   help='Print the (variant,N,seed) list and exit.')
    args = p.parse_args()

    cells = [(v, N, sd)
             for v in args.variants
             for N in args.N
             for sd in args.seeds]
    print(f'sweep: {len(cells)} cells '
          f'({len(args.variants)} variants × {len(args.N)} Ns × {len(args.seeds)} seeds)')
    if args.dry_run:
        for c in cells:
            print('  ', c)
        return

    results = load_existing(OUTPUT_JSON)
    print(f'existing JSON has {len(results)} cells; resume={args.resume}')

    for i, (v, N, sd) in enumerate(cells, 1):
        key = cell_key(v, N, sd)
        if args.resume and key in results and not results[key].get(
                '_parse_failed', False):
            print(f'[{i}/{len(cells)}] {key}: SKIP (cached)')
            continue
        print(f'[{i}/{len(cells)}] {key}: running... ', end='', flush=True)
        try:
            res = run_cell(v, N, sd)
            if res.get('_parse_failed') or res.get('_exit_code') != 0:
                print(f'FAILED (exit={res.get("_exit_code")}, '
                      f'parse_ok={not res.get("_parse_failed")})')
            else:
                print(f'ok in {res["_wall_clock_s"]}s  '
                      f'(misses={res.get("overall_miss_rate")}, '
                      f'cost=€{res.get("total_cost_eur")})')
        except subprocess.TimeoutExpired:
            res = {'_variant': v, '_N': N, '_seed': sd,
                   '_parse_failed': True, '_error': 'timeout'}
            print('TIMEOUT')
        results[key] = res
        save_results(OUTPUT_JSON, results)

    print(f'wrote {OUTPUT_JSON} ({len(results)} cells)')


if __name__ == '__main__':
    main()
