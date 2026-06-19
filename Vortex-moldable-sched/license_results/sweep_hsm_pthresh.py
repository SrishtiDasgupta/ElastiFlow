"""Per-pool P_thresh (rho) sensitivity sweep for HSM, at fixed N=400.

Sweeps EACH pool's threshold independently while holding the other two at their
deployed values (ANSYS=0.60, ABAQUS=0.60, LSDYNA=0.95) -- a ceteris-paribus
sensitivity around the operating point. Three curves (one per pool) x 7
thresholds x 6 seeds, deduped on the shared deployed config.

Captures overall AND per-solver deadline miss + licence cost so the figure can
use whichever isolates the effect. Results -> hsm_pthresh_sweep.json.
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN = Path('/tmp/hsm_pthresh_runs')
OUT = HERE / 'hsm_pthresh_sweep.json'
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

N = 400
SEEDS = [7, 107, 207, 1007, 1107, 1207]
POOLS = ['ANSYS', 'ABAQUS', 'LSDYNA']
THRESH = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
DEPLOYED = {'ANSYS': 0.60, 'ABAQUS': 0.60, 'LSDYNA': 0.95}


def configs():
    """Unique (ansys,abaqus,lsdyna) triples: sweep each pool, others deployed."""
    seen = {}
    for pool in POOLS:
        for t in THRESH:
            cfg = dict(DEPLOYED); cfg[pool] = t
            key = (cfg['ANSYS'], cfg['ABAQUS'], cfg['LSDYNA'])
            seen[key] = cfg
    return list(seen.values())


def ckey(cfg, sd):
    return f"a{cfg['ANSYS']}_b{cfg['ABAQUS']}_l{cfg['LSDYNA']}__seed{sd}"


def run_cell(cfg, sd):
    out = RUN / ckey(cfg, sd)
    out.mkdir(parents=True, exist_ok=True)
    sp = out / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90',
               LA_HSM_POOL_RHO_ANSYS=str(cfg['ANSYS']),
               LA_HSM_POOL_RHO_ABAQUS=str(cfg['ABAQUS']),
               LA_HSM_POOL_RHO_LSDYNA=str(cfg['LSDYNA']))
    cmd = [str(PY), str(SIM), '--scheduler', 'EDF-HSM', '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out)]
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env, timeout=45 * 60)
    p = parse_la_run.parse(sp.read_text())
    rg = glob.glob(str(out / '*_results.csv'))
    if rg:
        r = LA.analyze_results(rg[0])
        p['eff_lic_util'] = 100 - r['waste_frac']
        p['per_solver'] = {s: {'lic': round(v['lic'], 1), 'n': v['n'], 'done': v['done']}
                           for s, v in r['per_solver'].items()}
    p.update(_rho_ansys=cfg['ANSYS'], _rho_abaqus=cfg['ABAQUS'],
             _rho_lsdyna=cfg['LSDYNA'], _N=N, _seed=sd, _exit=proc.returncode)
    return p


def load():
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def main():
    res = load()
    todo = [(c, s) for c in configs() for s in SEEDS
            if ckey(c, s) not in res or res[ckey(c, s)].get('_parse_failed')]
    print(f'P_thresh sweep: {len(res)} cached, {len(todo)} to run (N={N})')
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                rr = fut.result(); tag = 'ok' if rr['_exit'] == 0 else 'FAIL'
            except Exception as e:
                rr = {'_rho_ansys': c[0]['ANSYS'], '_rho_abaqus': c[0]['ABAQUS'],
                      '_rho_lsdyna': c[0]['LSDYNA'], '_seed': c[1],
                      '_parse_failed': True, '_error': str(e)}; tag = 'EXC'
            res[ckey(*c)] = rr
            OUT.write_text(json.dumps(res, indent=1))
            print(f'[{i}/{len(todo)}] {ckey(*c)}: {tag} '
                  f'dmiss={rr.get("deadline_miss_rate")} '
                  f'liccost=€{rr.get("total_license_cost_eur")}', flush=True)
    print(f'done; {len(res)} cells in {OUT.name}')


if __name__ == '__main__':
    main()
