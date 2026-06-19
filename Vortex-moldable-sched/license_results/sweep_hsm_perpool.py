"""Per-pool-rho HSM validation across the workload-size grid.

Validates the licence-aware per-pool gate (release abundant LSDYNA freely,
rho=0.95; hold expensive ANSYS/ABAQUS, rho=0.60) against EDF-LAMF across all N.
HSM-only runs; EDF-LAMF baseline from canonical_results.json (no re-run).

The decision metric is per-N directional consistency: how many of the 7 N HSM
beats EDF-LAMF on deadline / overall miss / effLU, and whether it does so at
near-neutral cost. Results -> license_results/hsm_perpool_sweep.json.
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/hsm_perpool_runs')
OUT_JSON = HERE / 'hsm_perpool_sweep.json'
CANON = HERE / 'canonical_results.json'
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

# Per-pool rho: release cheap/abundant LSDYNA, hold expensive ANSYS/ABAQUS.
PERPOOL = {'LA_HSM_POOL_RHO_LSDYNA': '0.95',
           'LA_HSM_POOL_RHO_ANSYS':  '0.60',
           'LA_HSM_POOL_RHO_ABAQUS': '0.60'}
NS = [150, 200, 300, 400, 500, 600, 700]
SEEDS = [7, 107, 207, 1007, 1107, 1207]
BASELINE = 'EDF-LAMF'


def key(N, sd):
    return f'HSMpp__N{N}__seed{sd}'


def run_cell(N, sd):
    out = RUN_ROOT / key(N, sd)
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90')
    env.update(PERPOOL)
    cmd = [str(PY), str(SIM), '--scheduler', 'EDF-HSM', '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out)]
    t0 = time.time()
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env, timeout=45 * 60)
    p = parse_la_run.parse(sp.read_text())
    rg = glob.glob(str(out / '*_results.csv'))
    if rg:
        r2 = LA.analyze_results(rg[0])
        p['eff_lic_util'] = 100 - r2['waste_frac']
    p.update(_policy='HSM-perpool', _N=N, _seed=sd, _exit=proc.returncode,
             _wall=round(time.time() - t0, 1))
    return p


def load():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def sweep():
    results = load()
    todo = [(N, s) for N in NS for s in SEEDS
            if key(N, s) not in results or results[key(N, s)].get('_parse_failed')]
    print(f'per-pool HSM sweep: {len(results)} cached, {len(todo)} to run')
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                rr = fut.result(); tag = 'ok' if rr['_exit'] == 0 else 'FAIL'
            except Exception as e:
                rr = {'_policy': 'HSM-perpool', '_N': c[0], '_seed': c[1],
                      '_parse_failed': True, '_error': str(e)}; tag = 'EXC'
            results[key(*c)] = rr
            OUT_JSON.write_text(json.dumps(results, indent=1))
            print(f'[{i}/{len(todo)}] {key(*c)}: {tag} dmiss={rr.get("deadline_miss_rate")} '
                  f'cost=€{rr.get("avg_cost_eur")} effLU={rr.get("eff_lic_util")} '
                  f'({rr.get("_wall")}s)', flush=True)
    report(results)


def _avg(rows, k):
    v = [x.get(k) for x in rows if x.get(k) is not None]
    return mean(v) if v else None


def report(results=None):
    results = results or load()
    canon = json.loads(CANON.read_text()) if CANON.exists() else {}
    base = {}
    for v in canon.values():
        if v.get('_policy') == BASELINE and not v.get('_parse_failed'):
            base.setdefault(v['_N'], []).append(v)
    hsm = {}
    for v in results.values():
        if not v.get('_parse_failed'):
            hsm.setdefault(v['_N'], []).append(v)

    print('\n' + '=' * 84)
    print('PER-POOL HSM vs EDF-LAMF across N (seed-avg)   LSDYNA rho=.95, ANSYS/ABAQUS rho=.60')
    print('=' * 84)
    print(f'{"N":>5}  {"dmiss H/L":>16} {"omiss H/L":>16} {"cost€ H/L":>16} {"effLU H/L":>16}')
    wins = {'dmiss': 0, 'omiss': 0, 'cost': 0, 'effLU': 0}
    tot = 0
    for N in NS:
        if N not in hsm or N not in base:
            continue
        tot += 1
        h, b = hsm[N], base[N]
        dh, dl = _avg(h, 'deadline_miss_rate'), _avg(b, 'deadline_miss_rate')
        oh, ol = _avg(h, 'overall_miss_rate'), _avg(b, 'overall_miss_rate')
        ch, cl = _avg(h, 'avg_cost_eur'), _avg(b, 'avg_cost_eur')
        eh, el = _avg(h, 'eff_lic_util'), _avg(b, 'eff_lic_util')
        if dh < dl: wins['dmiss'] += 1
        if oh < ol: wins['omiss'] += 1
        if ch < cl: wins['cost'] += 1
        if (eh or 0) > (el or 0): wins['effLU'] += 1
        print(f'{N:>5}  {dh:>7.3f}/{dl:<7.3f} {oh:>7.3f}/{ol:<7.3f} '
              f'{ch:>7.1f}/{cl:<7.1f} {(eh or 0):>7.1f}/{(el or 0):<7.1f}')
    print('-' * 84)
    print(f'HSM better than EDF-LAMF:  deadline {wins["dmiss"]}/{tot}   '
          f'overall {wins["omiss"]}/{tot}   cost {wins["cost"]}/{tot}   effLU {wins["effLU"]}/{tot}')


if __name__ == '__main__':
    report() if '--report' in sys.argv else sweep()
