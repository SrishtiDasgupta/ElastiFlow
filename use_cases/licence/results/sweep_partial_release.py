"""Partial-release-fraction sensitivity sweep for the moldable LA policies.

Justifies the deployed scale-down partial-release fraction r = 0.90
(PARTIAL_RELEASE_FRACTION; release 90% of freed licenses, retain 10% as a
self-buffer). r trades pool-sharing against self-buffering:
  r -> 1.0  : dump ~all freed tokens back to the shared pool (others acquire
              sooner) but keep no buffer for the workflow's own next scale-up.
  r -> 0.0  : hoard tokens for future scale-ups (local deadline safety) but
              starve the shared pool (more misses elsewhere, worse effLU).
Expect an interior optimum / knee in overall-miss; r = 0.90 is justified iff it
minimises misses or sits at the knee.

Each cell runs simulate_main_LA.py with LA_PARTIAL_RELEASE=<r> (already an
env-configurable knob in the schedulers -- no code change). Reuses the canonical
cell-runner pattern + parse_la_run. Results -> use_cases/licence/results/partial_release_sweep.json.

Usage:
    python sweep_partial_release.py                 # 3 moldable pols x r{.5..1} x N300 x 6 seeds
    python sweep_partial_release.py --r 0.8 0.9 1.0
    python sweep_partial_release.py --seeds 7 107   # quick smoke
    python sweep_partial_release.py --report
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
RUN_ROOT = Path('/tmp/partial_release_runs')
OUT_JSON = HERE / 'partial_release_sweep.json'
sys.path.insert(0, str(HERE))
import parse_la_run            # noqa: E402
import license_analysis as LA  # noqa: E402

# All three moldable LA policies apply PARTIAL_RELEASE_FRACTION on scale-down.
POLICIES = ['EDF-LAMF', 'LAMF', 'EDF-HSM']     # LAMF == FCFS-LAMF in display naming
DEFAULT_R = [0.5, 0.7, 0.8, 0.9, 1.0]
DEFAULT_NS = [300]
DEFAULT_SEEDS = [7, 107, 207, 1007, 1107, 1207]


def key(pol, r, N, sd):
    return f'{pol}__r{r}__N{N}__seed{sd}'


def run_cell(pol, r, N, sd):
    out_dir = RUN_ROOT / key(pol, r, N, sd)
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = out_dir / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE=str(r))
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
        r2 = LA.analyze_results(rg[0])
        p['tot_lic'] = r2['tot_lic']; p['tot_hw'] = r2['tot_hw']
        p['waste_frac'] = r2['waste_frac']; p['eff_lic_util'] = 100 - r2['waste_frac']
        p['lic_per_done'] = r2['lic_per_done']; p['overhead'] = r2['overhead']
        p['n_done'] = r2['n_done']; p['n_miss'] = r2['n_miss']
    if ug:
        u = LA.analyze_usage(ug[0])
        p['token_sec_total'] = sum(x['token_sec'] for x in u.values())
        p['pool_tw_util'] = {pool: round(u[pool]['tw_util'], 1) for pool in u}
    p.update(_policy=pol, _r=r, _N=N, _seed=sd, _exit=proc.returncode,
             _wall=round(time.time() - t0, 1))
    return p


def load():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def sweep(pols, rs, ns, seeds):
    results = load()
    todo = [(p, r, N, s) for p in pols for r in rs for N in ns for s in seeds
            if key(p, r, N, s) not in results
            or results[key(p, r, N, s)].get('_parse_failed')]
    total = len(pols) * len(rs) * len(ns) * len(seeds)
    print(f'partial-release sweep: {len(results)} cached, {len(todo)} to run (of {total})')
    t0 = time.time(); done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                rr = fut.result()
                tag = 'ok' if rr['_exit'] == 0 and not rr.get('_parse_failed') else 'FAIL'
            except Exception as e:
                rr = {'_policy': c[0], '_r': c[1], '_N': c[2], '_seed': c[3],
                      '_parse_failed': True, '_error': str(e)}
                tag = 'EXC'
            results[key(*c)] = rr
            OUT_JSON.write_text(json.dumps(results, indent=1))
            done += 1
            print(f'[{done}/{len(todo)}] {key(*c)}: {tag} '
                  f'miss={rr.get("overall_miss_rate")} dmiss={rr.get("deadline_miss_rate")} '
                  f'effUtil={rr.get("eff_lic_util")} cost=€{rr.get("avg_cost_eur")} '
                  f'({rr.get("_wall")}s)', flush=True)
    print(f'\nsweep done in {round(time.time()-t0,1)}s; {len(results)} cells\n')
    report(results)


def _avg(rows, k):
    v = [x.get(k) for x in rows if x.get(k) is not None]
    return mean(v) if v else None


def report(results=None):
    results = results or load()
    by = {}
    for x in results.values():
        if x.get('_parse_failed'):
            continue
        by.setdefault((x['_policy'], x['_N'], x['_r']), []).append(x)
    rs = sorted({k[2] for k in by})
    ns = sorted({k[1] for k in by})
    pols = sorted({k[0] for k in by})
    print('=' * 84)
    print('PARTIAL-RELEASE-FRACTION SWEEP (seed-avg)   [* = deployed r=0.9, retain 10%]')
    print('=' * 84)
    for N in ns:
        for pol in pols:
            print(f'\n{pol}  (N={N})')
            print(f'  {"r":>5} {"dmiss":>8} {"bmiss":>8} {"overall":>8} {"effUtil":>8} {"cost€":>8}')
            for r in rs:
                rows = by.get((pol, N, r))
                if not rows:
                    continue
                star = '*' if abs(r - 0.9) < 1e-9 else ' '
                print(f' {star}{r:>5} {_avg(rows,"deadline_miss_rate"):>8.3f} '
                      f'{_avg(rows,"budget_miss_rate"):>8.3f} '
                      f'{_avg(rows,"overall_miss_rate"):>8.3f} '
                      f'{(_avg(rows,"eff_lic_util") or float("nan")):>8.1f} '
                      f'{_avg(rows,"avg_cost_eur"):>8.1f}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--r', nargs='*', type=float, default=DEFAULT_R)
    ap.add_argument('--N', nargs='*', type=int, default=DEFAULT_NS)
    ap.add_argument('--seeds', nargs='*', type=int, default=DEFAULT_SEEDS)
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    report() if a.report else sweep(POLICIES, a.r, a.N, a.seeds)
