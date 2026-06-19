"""Quick HSM-fix verification: EDF-LAMF vs new HSM (static=1) vs HSM control (static=0).

Confirms (a) the static=0 control reproduces EDF-LAMF (lever is inert when off),
(b) static=1 makes HSM genuinely diverge, and reports the DIRECTION (deadline /
cost / effLU) at the deployed r=0.90. Small 3-seed smoke at N=300.
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN = Path('/tmp/hsm_fix_runs')
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

SEEDS = [7, 107, 207, 1007, 1107, 1207]
N = 300
# (label, scheduler, env-overrides)
# Tests throttle-fix (now always on) + per-pool rho. Compare vs the pre-throttle
# HSM rho=0.7 (dmiss 0.101 / cost €123.7) and EDF-LAMF (0.115 / €119.4).
CONFIGS = [
    ('EDF-LAMF',           'EDF-LAMF', {}),
    ('HSM-u0.7(thr)',      'EDF-HSM',  {'LA_HSM_POOL_RHO': '0.70'}),   # uniform 0.7 + throttle
    ('HSM-perpool*',       'EDF-HSM',  {'LA_HSM_POOL_RHO_LSDYNA': '0.95',  # release cheap pool
                                        'LA_HSM_POOL_RHO_ANSYS':  '0.60',  # hold expensive
                                        'LA_HSM_POOL_RHO_ABAQUS': '0.60'}),
]


def run_cell(label, sched, overrides, sd):
    out = RUN / f'{label}__seed{sd}'.replace('*', '').replace('(', '').replace(')', '').replace('=', '')
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90')
    env.update(overrides)
    cmd = [str(PY), str(SIM), '--scheduler', sched, '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out)]
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env, timeout=45 * 60)
    p = parse_la_run.parse(sp.read_text())
    rg = glob.glob(str(out / '*_results.csv'))
    if rg:
        r2 = LA.analyze_results(rg[0])
        p['eff_lic_util'] = 100 - r2['waste_frac']
        p['n_done'] = r2['n_done']; p['n_miss'] = r2['n_miss']
    p.update(_label=label, _seed=sd, _exit=proc.returncode)
    return p


def main():
    todo = [(lbl, s, ov, sd) for (lbl, s, ov) in CONFIGS for sd in SEEDS]
    res = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                rr = fut.result(); tag = 'ok' if rr['_exit'] == 0 else 'FAIL'
            except Exception as e:
                rr = {'_label': c[0], '_seed': c[3], '_err': str(e)}; tag = 'EXC'
            res.setdefault(c[0], []).append(rr)
            print(f'  {c[0]:16s} seed{c[3]}: {tag} '
                  f'dmiss={rr.get("deadline_miss_rate")} '
                  f'omiss={rr.get("overall_miss_rate")} '
                  f'cost=€{rr.get("avg_cost_eur")} effLU={rr.get("eff_lic_util")}',
                  flush=True)

    def avg(rows, k):
        v = [r.get(k) for r in rows if r.get(k) is not None]
        return mean(v) if v else float('nan')

    print('\n' + '=' * 70)
    print(f'HSM-FIX COMPARISON (seed-avg, N={N}, r=0.90, {len(SEEDS)} seeds)')
    print('=' * 70)
    print(f'{"config":18s} {"dmiss":>8} {"omiss":>8} {"cost€":>8} {"effLU":>8}')
    for lbl, _, _ in CONFIGS:
        rows = res.get(lbl, [])
        print(f'{lbl:18s} {avg(rows,"deadline_miss_rate"):>8.3f} '
              f'{avg(rows,"overall_miss_rate"):>8.3f} '
              f'{avg(rows,"avg_cost_eur"):>8.1f} {avg(rows,"eff_lic_util"):>8.1f}')
    (HERE / 'hsm_fix_cmp.json').write_text(json.dumps(res, indent=1, default=str))


if __name__ == '__main__':
    main()
