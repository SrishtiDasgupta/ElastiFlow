"""Iteration-0 OPTIM-factor sensitivity sweep for the LAMF policies.

Justifies the hand-set iteration-0 budget/deadline residual factor f0 = 0.60
(OPTIM_FCFS_BFACTOR[0] == OPTIM_FCFS_DFACTOR[0]) applied by EDF-LAMF and
FCFS-LAMF. f0 hedges allocation-under-zero-observation at iteration 0: too high
over-commits the unverified estimate (budget/deadline blow-ups), too low
under-provisions early (deadline debt). The expected signature is a U-shaped
overall-miss curve with monotone cost, so 0.60 is justified iff it is the
miss-rate minimiser or the cost/deadline knee.

Each cell runs simulate_main_LA.py with LA_ITER0_FACTOR=<f0>; only iteration 0
is affected (HSM skips iter-0, statics don't scale). Reuses the canonical cell
runner pattern + parse_la_run. Results -> use_cases/licence/results/iter0_sweep.json.

Usage:
    python sweep_LAMF_iter0.py                  # EDF-LAMF+LAMF x f0{.4..8} x N300 x 6 seeds
    python sweep_LAMF_iter0.py --f0 0.5 0.6 0.7
    python sweep_LAMF_iter0.py --N 300 400
    python sweep_LAMF_iter0.py --seeds 7 107    # quick smoke
    python sweep_LAMF_iter0.py --report
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]   # repository root
SIM_SCRIPT = REPO_ROOT / 'src' / 'main' / 'simulate_main_LA.py'
VENV_PY = REPO_ROOT.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/iter0_runs')
OUT_JSON = HERE / 'iter0_sweep.json'
sys.path.insert(0, str(HERE))
import parse_la_run            # noqa: E402
import license_analysis as LA  # noqa: E402

# Only LAMF policies apply OPTIM_FCFS_*FACTOR[0] (no iter-0 skip, dynamic scaling).
POLICIES = ['EDF-LAMF', 'LAMF']          # LAMF == FCFS-LAMF in display naming
DEFAULT_F0 = [0.4, 0.5, 0.6, 0.7, 0.8]
DEFAULT_NS = [300]
DEFAULT_SEEDS = [7, 107, 207, 1007, 1107, 1207]


def key(pol, f0, N, sd):
    return f'{pol}__f{f0}__N{N}__seed{sd}'


def run_cell(pol, f0, N, sd):
    out_dir = RUN_ROOT / key(pol, f0, N, sd)
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = out_dir / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_ITER0_FACTOR=str(f0))
    cmd = [str(VENV_PY), str(SIM_SCRIPT), '--scheduler', pol,
           '--N', str(N), '--seed', str(sd), '--output-dir', str(out_dir)]
    t0 = time.time()
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO_ROOT / 'src' / 'main', env=env,
                              timeout=45 * 60)
    p = parse_la_run.parse(sp.read_text())
    rg = glob.glob(str(out_dir / '*_results.csv'))
    ug = glob.glob(str(out_dir / '*_license_usage.csv'))
    if rg:
        r = LA.analyze_results(rg[0])
        p['tot_lic'] = r['tot_lic']; p['tot_hw'] = r['tot_hw']
        p['waste_frac'] = r['waste_frac']; p['eff_lic_util'] = 100 - r['waste_frac']
        p['lic_per_done'] = r['lic_per_done']; p['overhead'] = r['overhead']
        p['n_done'] = r['n_done']; p['n_miss'] = r['n_miss']
    if ug:
        u = LA.analyze_usage(ug[0])
        p['token_sec_total'] = sum(x['token_sec'] for x in u.values())
    p.update(_policy=pol, _f0=f0, _N=N, _seed=sd, _exit=proc.returncode,
             _wall=round(time.time() - t0, 1))
    return p


def load():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def sweep(pols, f0s, ns, seeds):
    results = load()
    todo = [(p, f0, N, s) for p in pols for f0 in f0s for N in ns for s in seeds
            if key(p, f0, N, s) not in results
            or results[key(p, f0, N, s)].get('_parse_failed')]
    total = len(pols) * len(f0s) * len(ns) * len(seeds)
    print(f'iter0 sweep: {len(results)} cached, {len(todo)} to run (of {total})')
    t0 = time.time(); done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                r = fut.result()
                tag = 'ok' if r['_exit'] == 0 and not r.get('_parse_failed') else 'FAIL'
            except Exception as e:
                r = {'_policy': c[0], '_f0': c[1], '_N': c[2], '_seed': c[3],
                     '_parse_failed': True, '_error': str(e)}
                tag = 'EXC'
            results[key(*c)] = r
            OUT_JSON.write_text(json.dumps(results, indent=1))
            done += 1
            print(f'[{done}/{len(todo)}] {key(*c)}: {tag} '
                  f'miss={r.get("overall_miss_rate")} dmiss={r.get("deadline_miss_rate")} '
                  f'cost=€{r.get("avg_cost_eur")} ({r.get("_wall")}s)', flush=True)
    print(f'\nsweep done in {round(time.time()-t0,1)}s; {len(results)} cells\n')
    report(results)


def _avg(rows, k):
    v = [r.get(k) for r in rows if r.get(k) is not None]
    return mean(v) if v else None


def report(results=None):
    results = results or load()
    by = {}
    for r in results.values():
        if r.get('_parse_failed'):
            continue
        by.setdefault((r['_policy'], r['_N'], r['_f0']), []).append(r)
    f0s = sorted({k[2] for k in by})
    ns = sorted({k[1] for k in by})
    pols = sorted({k[0] for k in by})
    print('=' * 80)
    print('ITERATION-0 OPTIM-FACTOR SWEEP (seed-avg)   [* = deployed f0=0.6]')
    print('=' * 80)
    for N in ns:
        for pol in pols:
            print(f'\n{pol}  (N={N})')
            print(f'  {"f0":>5} {"dmiss":>8} {"bmiss":>8} {"overall":>8} {"cost€":>8} {"effUtil":>8}')
            for f0 in f0s:
                rows = by.get((pol, N, f0))
                if not rows:
                    continue
                star = '*' if abs(f0 - 0.6) < 1e-9 else ' '
                print(f' {star}{f0:>5} {_avg(rows,"deadline_miss_rate"):>8.3f} '
                      f'{_avg(rows,"budget_miss_rate"):>8.3f} '
                      f'{_avg(rows,"overall_miss_rate"):>8.3f} '
                      f'{_avg(rows,"avg_cost_eur"):>8.1f} '
                      f'{(_avg(rows,"eff_lic_util") or float("nan")):>8.1f}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--f0', nargs='*', type=float, default=DEFAULT_F0)
    ap.add_argument('--N', nargs='*', type=int, default=DEFAULT_NS)
    ap.add_argument('--seeds', nargs='*', type=int, default=DEFAULT_SEEDS)
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    report() if a.report else sweep(POLICIES, a.f0, a.N, a.seeds)
