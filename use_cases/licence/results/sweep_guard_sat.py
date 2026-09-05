"""LA_GUARD_SAT sensitivity sweep (scale-down saturation guard, G1 of Table tab:guards).

G1 vetoes a COST-ADVERSE scale-down while the declared licence pool is at least
LA_GUARD_SAT committed. It applies to all three elastic policies (LAMF, EDF-LAMF,
EDF-HSM); the static policies never scale down so the guard cannot engage.

Deployed value is 0.70, which was a hardcoded literal in edf_optimized_LA.py and
fcfs_optimized_LA.py until 2026-09-03 and therefore had never been swept. This
sweep supplies the missing sensitivity data.

  GUARD 2.0 == guard disabled (pool utilisation can never reach 2.0).
  The 0.70 arm is NOT re-run; it is read from canonical_results.json.

Usage:
  python sweep_guard_sat.py --verify   # reproduce one canonical cell at 0.70
  python sweep_guard_sat.py            # run / resume the sweep
  python sweep_guard_sat.py --report   # tables from existing JSON
"""
import argparse, glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean, pstdev

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]   # repository root
SIM_SCRIPT = REPO_ROOT / 'src' / 'main' / 'simulate_main_LA.py'
VENV_PY = REPO_ROOT.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/guard_sat_runs')
OUT_JSON = HERE / 'guard_sat_sweep.json'
CANON = HERE / 'canonical_results.json'
sys.path.insert(0, str(HERE))
import parse_la_run            # noqa: E402
import license_analysis as LA  # noqa: E402

POLICIES = ['LAMF', 'EDF-LAMF', 'EDF-HSM']
GUARDS = [0.50, 0.60, 0.80, 0.90, 2.00]   # 0.70 comes from canonical; 2.00 == off
N_FIX = 300
SEEDS = [7, 107, 207, 1007, 1107, 1207]


def key(g, pol, sd):
    return f'{pol}__g{g}__N{N_FIX}__seed{sd}'


def run_cell(g, pol, sd, tag=None):
    out_dir = RUN_ROOT / (tag or key(g, pol, sd))
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = out_dir / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90',
               LA_GUARD_SAT=str(g))
    if pol == 'EDF-HSM':                       # uniform rho, as in canonical_sweep.py
        env.update(LA_HSM_POOL_RHO='0.70', LA_HSM_POOL_RHO_ANSYS='0.70',
                   LA_HSM_POOL_RHO_ABAQUS='0.70', LA_HSM_POOL_RHO_LSDYNA='0.70')
    cmd = [str(VENV_PY), str(SIM_SCRIPT), '--scheduler', pol,
           '--N', str(N_FIX), '--seed', str(sd), '--output-dir', str(out_dir)]
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
        p['pool_tw_util'] = {pool: round(u[pool]['tw_util'], 1) for pool in u}
    p.update(_policy=pol, _guard=g, _N=N_FIX, _seed=sd, _exit=proc.returncode,
             _wall=round(time.time() - t0, 1))
    return p


FIELDS = ['avg_cost_eur', 'deadline_miss_rate', 'overall_miss_rate', 'eff_lic_util',
          'lic_per_done', 'n_done', 'avg_wait_time_s', 'avg_flowtime_s']


def verify():
    """Run one cell at the deployed 0.70 and diff against its canonical twin."""
    canon = json.loads(CANON.read_text())
    pol, sd = 'EDF-LAMF', 7
    ref = canon[f'{pol}__N{N_FIX}__seed{sd}']
    print(f'verifying {pol} N={N_FIX} seed={sd} at LA_GUARD_SAT=0.70 ...')
    got = run_cell(0.70, pol, sd, tag='VERIFY')
    bad = 0
    for f in FIELDS + ['token_sec_total']:
        a, b = ref.get(f), got.get(f)
        ok = (a == b)
        if not ok: bad += 1
        print(f'  {f:22} canonical={a!r:>22}  rerun={b!r:>22}  {"OK" if ok else "*** DIFF ***"}')
    for f in ['per_solver', 'pool_tw_util', 'license_pools', 'moldability']:
        a, b = ref.get(f), got.get(f)
        ok = (a == b)
        if not ok: bad += 1
        print(f'  {f:22} {"OK" if ok else "*** DIFF ***"}')
    print('\nRESULT:', 'bit-for-bit identical, change is a no-op at the default'
          if bad == 0 else f'{bad} FIELDS DIFFER -- DO NOT PROCEED')
    return bad == 0


def load():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def sweep():
    results = load()
    todo = [(g, p, s) for g in GUARDS for p in POLICIES for s in SEEDS
            if key(g, p, s) not in results or results[key(g, p, s)].get('_parse_failed')]
    total = len(GUARDS) * len(POLICIES) * len(SEEDS)
    print(f'guard-sat sweep: {len(results)} cached, {len(todo)} to run (of {total})', flush=True)
    t0 = time.time(); done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                results[key(*c)] = fut.result()
            except Exception as e:
                results[key(*c)] = {'_policy': c[1], '_guard': c[0], '_N': N_FIX,
                                    '_seed': c[2], '_parse_failed': True, '_err': str(e)}
            done += 1
            OUT_JSON.write_text(json.dumps(results, indent=1))
            el = time.time() - t0
            print(f'  [{done}/{len(todo)}] {key(*c)}  {el/60:.1f} min elapsed, '
                  f'eta {(el/done)*(len(todo)-done)/60:.1f} min', flush=True)
    print(f'done in {(time.time()-t0)/60:.1f} min -> {OUT_JSON}')


def report():
    res = load()
    canon = json.loads(CANON.read_text()) if CANON.exists() else {}
    for k, v in canon.items():
        if v.get('_N') == N_FIX and v.get('_policy') in POLICIES and not v.get('_parse_failed'):
            res[key(0.70, v['_policy'], v['_seed'])] = dict(v, _guard=0.70)
    guards = sorted({c['_guard'] for c in res.values() if not c.get('_parse_failed')})
    print(f'\nLA_GUARD_SAT sensitivity at N={N_FIX}, 6 runs per cell '
          f'(2.00 = guard disabled)\n')
    for pol in POLICIES:
        print(f'--- {pol}')
        print(f"  {'guard':>6} {'cost USD':>9} {'dl miss':>9} {'all miss':>9} "
              f"{'ELU %':>8} {'lic/done':>9} {'done':>7}")
        for g in guards:
            cs = [c for c in res.values() if c.get('_policy') == pol and c.get('_guard') == g
                  and not c.get('_parse_failed')]
            if not cs: continue
            def m(f): 
                v = [c[f] for c in cs if c.get(f) is not None]
                return mean(v) if v else float('nan')
            tag = ' (off)' if g >= 2 else (' *' if abs(g - 0.70) < 1e-9 else '')
            print(f"  {g:>6.2f} {m('avg_cost_eur')*1.1:9.1f} {m('deadline_miss_rate'):9.3f} "
                  f"{m('overall_miss_rate'):9.3f} {m('eff_lic_util'):8.1f} "
                  f"{m('lic_per_done')*1.1:9.1f} {m('n_done'):7.1f}{tag}")
        print()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.verify: sys.exit(0 if verify() else 1)
    elif a.report: report()
    else: sweep(); report()
