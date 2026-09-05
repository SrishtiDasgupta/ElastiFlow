"""Regenerate EDF-HSM cells with a UNIFORM pool-pressure threshold rho = 0.70.

The deployed code bakes per-pool defaults (ANSYS/ABAQUS 0.60, LSDYNA 0.95) into
HSM_POOL_RHO, so true uniform 0.70 requires overriding all three per-pool env
vars. This driver (1) runs EDF-HSM for every (N, seed) with uniform 0.70,
(2) enriches each run into a canonical-shaped cell (identical logic to
regen_newHSM.enrich_cell), (3) asserts the enriched schema matches the existing
canonical EDF-HSM cells, and (4) merges into canonical_results.json (after
backing the per-pool version up to canonical_results_PERPOOL.json).
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]   # repository root
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/hsm_uni_runs')
CANON = HERE / 'canonical_results.json'
BACKUP = HERE / 'canonical_results_PERPOOL.json'
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

NS = [150, 200, 300, 400, 500, 600, 700]
SEEDS = [7, 107, 207, 1007, 1107, 1207]


def run_cell(N, sd):
    out = RUN_ROOT / f'EDF-HSM__N{N}__seed{sd}'
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90',
               LA_HSM_POOL_RHO='0.70', LA_HSM_POOL_RHO_ANSYS='0.70',
               LA_HSM_POOL_RHO_ABAQUS='0.70', LA_HSM_POOL_RHO_LSDYNA='0.70')
    cmd = [str(PY), str(SIM), '--scheduler', 'EDF-HSM', '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out)]
    t0 = time.time()
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env, timeout=45 * 60)
    return (N, sd, proc.returncode, round(time.time() - t0, 1))


def enrich_cell(N, sd):
    d = RUN_ROOT / f'EDF-HSM__N{N}__seed{sd}'
    sp = d / 'stdout.log'
    p = parse_la_run.parse(sp.read_text())
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
    p.update(_policy='EDF-HSM', _N=N, _seed=sd, _exit=0)
    return p


def main():
    cells = [(N, sd) for N in NS for sd in SEEDS]
    print(f'Running {len(cells)} uniform-0.70 HSM cells...')
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_cell, N, sd): (N, sd) for N, sd in cells}
        for f in as_completed(futs):
            N, sd, rc, wall = f.result()
            print(f'  ran N{N} seed{sd}: exit={rc} wall={wall}s', flush=True)

    canon = json.loads(CANON.read_text())
    # reference schema: an existing EDF-HSM cell (canonical-complete)
    ref_key = next(k for k in canon if k.startswith('EDF-HSM__'))
    ref_fields = set(canon[ref_key])

    enriched = {}
    for N, sd in cells:
        cell = enrich_cell(N, sd)
        missing = ref_fields - set(cell)
        assert not missing, f'cell N{N} seed{sd} missing fields: {missing}'
        enriched[f'EDF-HSM__N{N}__seed{sd}'] = cell
    print(f'Enriched {len(enriched)} cells; schema OK (matches {ref_key}).')

    # back up per-pool canonical, then merge uniform HSM
    if not BACKUP.exists():
        BACKUP.write_text(CANON.read_text())
        print(f'Backed up per-pool canonical -> {BACKUP.name}')
    for k, v in enriched.items():
        canon[k] = v
    CANON.write_text(json.dumps(canon, indent=1))
    print(f'Merged uniform-0.70 HSM into {CANON.name}.')


if __name__ == '__main__':
    main()
