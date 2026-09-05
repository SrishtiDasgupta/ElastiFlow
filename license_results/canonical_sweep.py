"""Canonical LA sweep -> the single dataset behind every LA chapter number.

5 policies x N{150,200,300,400,500,600,700} x 6 seeds = 210 cells, baseline
33/33/34 deck, cost-depth mode. Each cell: standard metrics (parse_la_run) +
license-POV (effective utilisation, waste, lic/done, token-seconds, per-solver
completion, pool utilisation). Per-cell CSVs land in /tmp/canonical_runs;
the aggregated per-cell metrics are written incrementally to
license_results/canonical_results.json (committable, resumable).

Usage:
  python canonical_sweep.py            # run / resume
  python canonical_sweep.py --report   # just print tables from existing JSON
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
from statistics import mean, pstdev

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SIM_SCRIPT = REPO_ROOT / 'src' / 'main' / 'simulate_main_LA.py'
VENV_PY = Path(sys.executable)                  # the interpreter running this driver
RUN_ROOT = Path('/tmp/canonical_runs')
OUT_JSON = HERE / 'canonical_results.json'
sys.path.insert(0, str(HERE))
import parse_la_run            # noqa: E402
import license_analysis as LA  # noqa: E402

STATIC = ['FCFS-ST-LA', 'EDF-ST-LA']
MOLD = ['LAMF', 'EDF-LAMF', 'EDF-HSM']
POLICIES = STATIC + MOLD
NS = [150, 200, 300, 400, 500, 600, 700]
SEEDS = [7, 107, 207, 1007, 1107, 1207]


def key(pol, N, sd):
    return f'{pol}__N{N}__seed{sd}'


def run_cell(pol, N, sd):
    out_dir = RUN_ROOT / key(pol, N, sd)
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = out_dir / 'stdout.log'
    env = dict(os.environ); env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8',
                                       LA_PARTIAL_RELEASE='0.90')
    if pol == 'EDF-HSM':
        # The deployed code bakes PER-POOL pressure thresholds (ANSYS/ABAQUS 0.60,
        # LSDYNA 0.95). The canonical dataset is the UNIFORM rho = 0.70 variant, so
        # all three per-pool vars must be overridden as well as the default. Without
        # this the sweep silently produces the per-pool variant, which the thesis
        # rejected, and the EDF-HSM cells do not reproduce. See regen_hsm_uniform.py,
        # which is how these cells were originally made.
        env.update(LA_HSM_POOL_RHO='0.70', LA_HSM_POOL_RHO_ANSYS='0.70',
                   LA_HSM_POOL_RHO_ABAQUS='0.70', LA_HSM_POOL_RHO_LSDYNA='0.70')
    cmd = [str(VENV_PY), str(SIM_SCRIPT), '--scheduler', pol,
           '--N', str(N), '--seed', str(sd), '--output-dir', str(out_dir)]
    t0 = time.time()
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO_ROOT / 'src' / 'main', env=env, timeout=45 * 60)
    p = parse_la_run.parse(sp.read_text())
    rg = glob.glob(str(out_dir / '*_results.csv'))
    ug = glob.glob(str(out_dir / '*_license_usage.csv'))
    if rg:
        r = LA.analyze_results(rg[0])
        p['tot_lic'] = r['tot_lic']; p['tot_hw'] = r['tot_hw']
        p['waste_frac'] = r['waste_frac']; p['eff_lic_util'] = 100 - r['waste_frac']
        p['lic_per_done'] = r['lic_per_done']; p['overhead'] = r['overhead']
        p['n_done'] = r['n_done']; p['n_miss'] = r['n_miss']
        p['per_solver'] = {s: {'lic': round(v['lic'], 1), 'n': v['n'], 'done': v['done']}
                           for s, v in r['per_solver'].items()}
    if ug:
        u = LA.analyze_usage(ug[0])
        p['token_sec_total'] = sum(x['token_sec'] for x in u.values())
        p['pool_tw_util'] = {pool: round(u[pool]['tw_util'], 1)
                             for pool in u}
    p.update(_policy=pol, _N=N, _seed=sd, _exit=proc.returncode,
             _wall=round(time.time() - t0, 1))
    return p


def load():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def sweep():
    results = load()
    todo = [(p, N, s) for p in POLICIES for N in NS for s in SEEDS
            if key(p, N, s) not in results or results[key(p, N, s)].get('_parse_failed')]
    print(f'canonical sweep: {len(results)} cached, {len(todo)} to run '
          f'(of {len(POLICIES)*len(NS)*len(SEEDS)} total)')
    t0 = time.time(); done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                r = fut.result()
                tag = 'ok' if r['_exit'] == 0 and not r.get('_parse_failed') else 'FAIL'
            except Exception as e:
                r = {'_policy': c[0], '_N': c[1], '_seed': c[2], '_parse_failed': True,
                     '_error': str(e)}
                tag = 'EXC'
            results[key(*c)] = r
            OUT_JSON.write_text(json.dumps(results, indent=1))   # incremental save
            done += 1
            print(f'[{done}/{len(todo)}] {key(*c)}: {tag} '
                  f'cost=€{r.get("avg_cost_eur")} miss={r.get("overall_miss_rate")} '
                  f'effUtil={r.get("eff_lic_util")} ({r.get("_wall")}s)', flush=True)
    print(f'\nsweep done in {round(time.time()-t0,1)}s; {len(results)} cells in {OUT_JSON.name}\n')
    report(results)


def _avg(rows, k):
    v = [r.get(k) for r in rows if r.get(k) is not None]
    return mean(v) if v else None


def report(results=None):
    results = results or load()
    rows_by = {}
    for r in results.values():
        if r.get('_parse_failed'):
            continue
        rows_by.setdefault((r['_policy'], r['_N']), []).append(r)
    print('=' * 110)
    print('CANONICAL LA SWEEP (seed-avg)')
    print('=' * 110)
    print(f'{"policy":11} {"N":>4} | {"cost€":>7} {"miss":>6} {"util%":>6} '
          f'{"flow_s":>8} {"lic€":>6} {"effUtil":>7} {"waste%":>6} {"lic/done":>8} {"ovhd%":>5}')
    print('-' * 110)
    for N in NS:
        for pol in POLICIES:
            rows = rows_by.get((pol, N))
            if not rows:
                print(f'{pol:11} {N:>4} | (no data)'); continue
            print(f'{pol:11} {N:>4} | {_avg(rows,"avg_cost_eur"):>7.1f} '
                  f'{_avg(rows,"overall_miss_rate"):>6.3f} '
                  f'{_avg(rows,"avg_resource_util_pct"):>6.1f} '
                  f'{_avg(rows,"avg_flowtime_s"):>8.0f} '
                  f'{_avg(rows,"tot_lic")/1000:>5.1f}k '
                  f'{_avg(rows,"eff_lic_util"):>7.1f} '
                  f'{_avg(rows,"waste_frac"):>6.1f} '
                  f'{_avg(rows,"lic_per_done"):>8.1f} '
                  f'{_avg(rows,"overhead"):>5.0f}')
        print()
    # headline pairwise deltas vs strong static EDF-ST-LA
    print('=' * 110)
    print('Moldable EDF vs static EDF-ST-LA (seed-avg)   [neg cost/ pos effUtil = moldable better]')
    print('=' * 110)
    for N in NS:
        st = rows_by.get(('EDF-ST-LA', N))
        if not st:
            continue
        for pol in ('EDF-LAMF', 'EDF-HSM'):
            m = rows_by.get((pol, N))
            if not m:
                continue
            dc = (_avg(m, 'avg_cost_eur') / _avg(st, 'avg_cost_eur') - 1) * 100
            dmiss = _avg(m, 'overall_miss_rate') - _avg(st, 'overall_miss_rate')
            deff = _avg(m, 'eff_lic_util') - _avg(st, 'eff_lic_util')
            print(f'  N{N} {pol:9}: Δcost {dc:+5.1f}%  Δmiss {dmiss:+.3f}  ΔeffUtil {deff:+5.1f}pp')
        print()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    report() if a.report else sweep()
