"""Licence-token scarcity sweep: how does the system behave as token supply shrinks?

The canonical pools are sized at 1.15x the static peak demand at N=400 and are
therefore deliberately NON-BINDING (see licenses.yaml). Under that provisioning the
Stage 1 licence gate approves every request and the partial-allocation mechanism of
Sec. 5.6 never engages. This sweep holds the workload and the infrastructure fixed
and scales ONLY the token supply, via LA_LICENSE_SCALE, to locate the point at which
licences become the binding constraint and to compare static against elastic policies
in that regime.

  python sweep_license_scarcity.py            # run / resume
"""
import glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]   # repository root
SIM = REPO / 'src' / 'main' / 'simulate_main_LA.py'
PY_BIN = REPO.parent / 'vortex_venv' / 'bin' / 'python3'
RUN_ROOT = Path('/tmp/scarcity_runs')
OUT = HERE / 'license_scarcity_sweep.json'
sys.path.insert(0, str(HERE))
import parse_la_run                     # noqa: E402

POLICIES = ['FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM']
SCALES = [0.75, 0.60, 0.50, 0.35, 0.25]
N = 300
SEEDS = [7, 107, 207, 1007, 1107, 1207]


def key(pol, sc, sd):
    return f'{pol}__s{sc}__N{N}__seed{sd}'


def run_cell(pol, sc, sd):
    out_dir = RUN_ROOT / key(pol, sc, sd)
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = out_dir / 'stdout.log'
    env = dict(os.environ)
    env.update(LA_DEPTH_MODE='cost', LA_MAX_DEPTH='8', LA_PARTIAL_RELEASE='0.90',
               LA_LICENSE_SCALE=str(sc))
    if pol == 'EDF-HSM':
        env.update(LA_HSM_POOL_RHO='0.70', LA_HSM_POOL_RHO_ANSYS='0.70',
                   LA_HSM_POOL_RHO_ABAQUS='0.70', LA_HSM_POOL_RHO_LSDYNA='0.70')
    cmd = [str(PY_BIN), str(SIM), '--scheduler', pol, '--N', str(N),
           '--seed', str(sd), '--output-dir', str(out_dir)]
    t0 = time.time()
    with open(sp, 'w') as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              cwd=REPO / 'src' / 'main', env=env, timeout=45 * 60)
    r = parse_la_run.parse(sp.read_text())
    r.update(_policy=pol, _scale=sc, _N=N, _seed=sd,
             _exit=proc.returncode, _wall=round(time.time() - t0, 1))
    return r


def main():
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [(p, sc, sd) for p in POLICIES for sc in SCALES for sd in SEEDS
            if key(p, sc, sd) not in res or res[key(p, sc, sd)].get('_parse_failed')]
    print(f'scarcity sweep: {len(res)} cached, {len(todo)} to run', flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_cell, *c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                r = fut.result()
                tag = 'ok' if r['_exit'] == 0 and not r.get('_parse_failed') else 'FAIL'
            except Exception as e:
                r = {'_policy': c[0], '_scale': c[1], '_N': N, '_seed': c[2],
                     '_parse_failed': True, '_error': str(e)}
                tag = 'EXC'
            res[key(*c)] = r
            OUT.write_text(json.dumps(res, indent=1))
            done += 1
            n = r.get('negotiation') or {}
            print(f'[{done}/{len(todo)}] {key(*c)}: {tag} '
                  f'cost=€{r.get("avg_cost_eur")} miss={r.get("overall_miss_rate")} '
                  f'modify={n.get("modify")} denyLic={n.get("deny_licence")} '
                  f'({r.get("_wall")}s)', flush=True)
    print(f'\ndone; {len(res)} cells -> {OUT.name}')


if __name__ == '__main__':
    main()
