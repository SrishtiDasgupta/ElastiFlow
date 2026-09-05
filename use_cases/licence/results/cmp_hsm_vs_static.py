"""Can HSM (per-pool gate + unconditional solver-aware cost guard) beat STATIC?

Runs HSM with LA_GUARD_SAT=0 (block any licence-cost-inflating scale-down at all
contention) at the diagnostic N where the elastic-vs-static gap lives, and
compares to EDF-ST-LA read from canonical_results.json (no re-run).
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]   # repository root
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN = Path('/tmp/hsm_vs_static_runs')
CANON = json.loads((HERE / 'canonical_results.json').read_text())
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

NS = [150, 300, 700]
SEEDS = [7, 107, 207, 1007, 1107, 1207]
GUARD = os.environ.get('GUARD_SAT', '0.0')   # unconditional cost guard by default


def run_cell(N, sd):
    out = RUN / f'g{GUARD}__N{N}__seed{sd}'
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90',
               LA_GUARD_SAT=GUARD)
    cmd = [str(PY), str(SIM), '--scheduler', 'EDF-HSM', '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out)]
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env, timeout=45 * 60)
    p = parse_la_run.parse(sp.read_text())
    rg = glob.glob(str(out / '*_results.csv'))
    if rg:
        r = LA.analyze_results(rg[0]); p['eff_lic_util'] = 100 - r['waste_frac']
    p.update(_N=N, _seed=sd, _exit=proc.returncode)
    return p


def st_avg(N, k):
    v = [c.get(k) for c in CANON.values() if c.get('_policy') == 'EDF-ST-LA'
         and c.get('_N') == N and c.get(k) is not None]
    return mean(v) if v else float('nan')


def main():
    todo = [(N, s) for N in NS for s in SEEDS]
    res = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                res.setdefault(c[0], []).append(fut.result())
            except Exception as e:
                print('EXC', c, e)
    def hsm(N, k):
        v = [x.get(k) for x in res.get(N, []) if x.get(k) is not None]
        return mean(v) if v else float('nan')
    print(f'\n=== HSM (LA_GUARD_SAT={GUARD}) vs EDF-ST-LA  ===')
    print(f'{"N":>5}  {"deadline H/S":>16} {"overall H/S":>16} {"cost€ H/S":>16} {"effLU H/S":>16}')
    for N in NS:
        print(f'{N:>5}  {hsm(N,"deadline_miss_rate"):>7.3f}/{st_avg(N,"deadline_miss_rate"):<7.3f} '
              f'{hsm(N,"overall_miss_rate"):>7.3f}/{st_avg(N,"overall_miss_rate"):<7.3f} '
              f'{hsm(N,"avg_cost_eur"):>7.1f}/{st_avg(N,"avg_cost_eur"):<7.1f} '
              f'{hsm(N,"eff_lic_util"):>7.1f}/{st_avg(N,"eff_lic_util"):<7.1f}')


if __name__ == '__main__':
    main()
