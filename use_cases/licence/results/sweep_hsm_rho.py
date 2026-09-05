"""HSM licence-pool-pressure gate (rho) sensitivity sweep.

Sweeps the contention threshold rho = LA_HSM_POOL_RHO for the licence-aware HSM
gate: a workflow releases its held allocation to the MOLDABLE phase only when it
is BOTH on-track (slack) AND the pool is uncontended (pressure < rho). Higher rho
=> holds only under severe contention => cheaper (toward EDF-LAMF); lower rho =>
holds earlier => more deadline/effLU protection at a cost premium.

ONLY HSM is run (its code changed). The EDF-LAMF baseline is read from the
existing canonical_results.json (unchanged code => byte-identical, verified), so
no baseline re-run. Results -> use_cases/licence/results/hsm_rho_sweep.json.

Usage:
    python sweep_hsm_rho.py                 # rho{.7 .8 .9 .95} x N300 x 6 seeds
    python sweep_hsm_rho.py --rho 0.6 0.7
    python sweep_hsm_rho.py --report
"""
import argparse, glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]   # repository root
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/hsm_rho_runs')
OUT_JSON = HERE / 'hsm_rho_sweep.json'
CANON = HERE / 'canonical_results.json'
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

DEFAULT_RHO = [0.7, 0.8, 0.9, 0.95]   # cost-saving (high-rho) region
DEFAULT_NS = [300]
DEFAULT_SEEDS = [7, 107, 207, 1007, 1107, 1207]
BASELINE_POLICY = 'EDF-LAMF'


def key(rho, N, sd):
    return f'HSM__rho{rho}__N{N}__seed{sd}'


def run_cell(rho, N, sd):
    out = RUN_ROOT / key(rho, N, sd)
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90',
               LA_HSM_POOL_RHO=str(rho))
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
        p['n_done'] = r2['n_done']; p['n_miss'] = r2['n_miss']
    # gate engagement
    txt = sp.read_text()
    p['_gate_holds'] = txt.count('holding allocation')
    p['_gate_trans'] = txt.count('STATIC->MOLDABLE')
    p.update(_policy='HSM', _rho=rho, _N=N, _seed=sd, _exit=proc.returncode,
             _wall=round(time.time() - t0, 1))
    return p


def load():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def baseline_cells(ns, seeds):
    """Pull EDF-LAMF baseline cells from canonical_results.json (no re-run)."""
    if not CANON.exists():
        print('  ! canonical_results.json not found -- no baseline'); return {}
    d = json.loads(CANON.read_text())
    out = {}
    for v in d.values():
        if v.get('_policy') == BASELINE_POLICY and v.get('_N') in ns and v.get('_seed') in seeds:
            out[f'BASE__N{v["_N"]}__seed{v["_seed"]}'] = v
    print(f'  reusing {len(out)} EDF-LAMF baseline cells from canonical_results.json')
    return out


def sweep(rhos, ns, seeds):
    results = load()
    todo = [(r, N, s) for r in rhos for N in ns for s in seeds
            if key(r, N, s) not in results or results[key(r, N, s)].get('_parse_failed')]
    print(f'HSM rho sweep: {len(results)} cached, {len(todo)} to run')
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                rr = fut.result(); tag = 'ok' if rr['_exit'] == 0 else 'FAIL'
            except Exception as e:
                rr = {'_policy': 'HSM', '_rho': c[0], '_N': c[1], '_seed': c[2],
                      '_parse_failed': True, '_error': str(e)}; tag = 'EXC'
            results[key(*c)] = rr
            OUT_JSON.write_text(json.dumps(results, indent=1))
            print(f'[{i}/{len(todo)}] {key(*c)}: {tag} '
                  f'dmiss={rr.get("deadline_miss_rate")} omiss={rr.get("overall_miss_rate")} '
                  f'cost=€{rr.get("avg_cost_eur")} effLU={rr.get("eff_lic_util")} '
                  f'holds={rr.get("_gate_holds")}/{rr.get("_gate_trans")} ({rr.get("_wall")}s)',
                  flush=True)
    report(results, ns, seeds)


def _avg(rows, k):
    v = [x.get(k) for x in rows if x.get(k) is not None]
    return mean(v) if v else float('nan')


def report(results=None, ns=None, seeds=None):
    results = results or load()
    ns = ns or DEFAULT_NS; seeds = seeds or DEFAULT_SEEDS
    base = list(baseline_cells(ns, seeds).values())
    print('\n' + '=' * 78)
    print('HSM rho-GATE SWEEP (seed-avg, N=300, r=0.90)   baseline EDF-LAMF from canonical')
    print('=' * 78)
    print(f'{"config":16s} {"dmiss":>8} {"omiss":>8} {"cost€":>8} {"effLU":>8} {"holds/trans":>12}')
    if base:
        print(f'{"EDF-LAMF":16s} {_avg(base,"deadline_miss_rate"):>8.3f} '
              f'{_avg(base,"overall_miss_rate"):>8.3f} {_avg(base,"avg_cost_eur"):>8.1f} '
              f'{_avg(base,"eff_lic_util"):>8.1f} {"(baseline)":>12}')
    rhos = sorted({x['_rho'] for x in results.values() if not x.get('_parse_failed')})
    for r in rhos:
        rows = [x for x in results.values()
                if x.get('_rho') == r and not x.get('_parse_failed')]
        h = sum(x.get('_gate_holds', 0) for x in rows)
        t = sum(x.get('_gate_trans', 0) for x in rows)
        print(f'{"HSM rho="+str(r):16s} {_avg(rows,"deadline_miss_rate"):>8.3f} '
              f'{_avg(rows,"overall_miss_rate"):>8.3f} {_avg(rows,"avg_cost_eur"):>8.1f} '
              f'{_avg(rows,"eff_lic_util"):>8.1f} {str(h)+"/"+str(t):>12}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--rho', nargs='*', type=float, default=DEFAULT_RHO)
    ap.add_argument('--N', nargs='*', type=int, default=DEFAULT_NS)
    ap.add_argument('--seeds', nargs='*', type=int, default=DEFAULT_SEEDS)
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    report() if a.report else sweep(a.rho, a.N, a.seeds)
