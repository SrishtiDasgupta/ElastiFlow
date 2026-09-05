"""Add the licence-POV fields (ELU, waste, per-solver, token-seconds) to the scarcity
sweep cells, using the SAME enrichment logic as canonical_sweep.run_cell so the two
datasets are directly comparable. Reads the per-run CSVs left in /tmp/scarcity_runs;
no re-simulation.
"""
import glob, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import license_analysis as LA          # noqa: E402

RUN_ROOT = Path('/tmp/scarcity_runs')
OUT = HERE / 'license_scarcity_sweep.json'

res = json.loads(OUT.read_text())
enriched = missing = failed = 0
for k, p in res.items():
    d = RUN_ROOT / k
    rg = glob.glob(str(d / '*_results.csv'))
    ug = glob.glob(str(d / '*_license_usage.csv'))
    if not rg:
        missing += 1
        continue
    try:
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
        enriched += 1
    except Exception as e:
        print(f'  FAIL {k}: {e}')
        failed += 1

OUT.write_text(json.dumps(res, indent=1))
print(f'  enriched {enriched} cells, {missing} without CSVs, {failed} failed')
