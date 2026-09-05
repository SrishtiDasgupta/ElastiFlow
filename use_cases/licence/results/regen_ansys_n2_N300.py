"""Controlled comparison run: ANSYS token law n-4 -> n-2 (Henkel anshpc).

Re-runs the 5 LA policies at the headline N=300 (6 seeds) with the CURRENT code
(which now uses ANSYS T_hpc = n-2). Pools (licenses.yaml) are held fixed at the
n-4-calibrated sizes, so this isolates the token-law change on identical
infrastructure. Saves to ansys_n2_N300.json WITHOUT touching canonical_results
(the n-4 baseline) so the two can be diffed.

Each policy is run with the same env that produced its baseline canonical cell:
non-HSM via the plain canonical-sweep env; EDF-HSM with the uniform rho=0.70
partial-release env adopted for the thesis.
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]   # repository root
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/ansys_n2_runs')
OUT_JSON = HERE / 'ansys_n2_N300.json'
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

N = 300
SEEDS = [7, 107, 207, 1007, 1107, 1207]
POLICIES = ['FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM']


def env_for(pol):
    e = dict(os.environ)
    e.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8')
    if pol == 'EDF-HSM':
        e.update(LA_PARTIAL_RELEASE='0.90', LA_HSM_POOL_RHO='0.70',
                 LA_HSM_POOL_RHO_ANSYS='0.70', LA_HSM_POOL_RHO_ABAQUS='0.70',
                 LA_HSM_POOL_RHO_LSDYNA='0.70')
    return e


def run_cell(pol, sd):
    out = RUN_ROOT / f'{pol}__N{N}__seed{sd}'
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    cmd = [str(PY), str(SIM), '--scheduler', pol, '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out)]
    t0 = time.time()
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env_for(pol),
                              timeout=45 * 60)
    return (pol, sd, proc.returncode, round(time.time() - t0, 1))


def enrich(pol, sd):
    d = RUN_ROOT / f'{pol}__N{N}__seed{sd}'
    p = parse_la_run.parse((d / 'stdout.log').read_text())
    rg = glob.glob(str(d / '*_results.csv'))
    ug = glob.glob(str(d / '*_license_usage.csv'))
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
    p.update(_policy=pol, _N=N, _seed=sd, _exit=0)
    return p


def main():
    cells = [(p, s) for p in POLICIES for s in SEEDS]
    print(f'Running {len(cells)} n-2 cells at N={N}...')
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed({ex.submit(run_cell, p, s): (p, s) for p, s in cells}):
            pol, sd, rc, wall = f.result()
            print(f'  {pol} seed{sd}: exit={rc} wall={wall}s', flush=True)
    out = {f'{p}__N{N}__seed{s}': enrich(p, s) for p, s in cells}
    OUT_JSON.write_text(json.dumps(out, indent=1))
    print(f'Wrote {len(out)} cells -> {OUT_JSON.name}')


if __name__ == '__main__':
    main()
