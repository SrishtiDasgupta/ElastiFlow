"""Regenerate the LA figure suite with the NEW licence-aware per-pool HSM.

The committed canonical_results.json holds the OLD (dead-code) EDF-HSM cells.
This script (1) re-parses the already-completed new-HSM per-pool runs in
/tmp/hsm_perpool_runs/ with the exact canonical enrichment, (2) replaces the
EDF-HSM cells in a COPY of canonical_results.json -> canonical_results_newHSM.json,
and (3) regenerates the canonical figure modules into a NEW folder plots_newHSM/
(old plots/ untouched) by monkeypatching each module's CANON + OUT_DIR globals.

Does NOT touch: fig_la_scarcity (separate s-sweep snapshot, old HSM) or
fig_xworkload (hardcoded GRID) -- both reported as needing their own update.
"""
import importlib, glob, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_la_run, license_analysis as LA

CANON = HERE / 'canonical_results.json'
MERGED = HERE / 'canonical_results_newHSM.json'
RUN_ROOT = Path('/tmp/hsm_perpool_runs')
NEW_PLOTS = HERE / 'plots_newHSM'
NEW_PLOTS.mkdir(exist_ok=True)

NS = [150, 200, 300, 400, 500, 600, 700]
SEEDS = [7, 107, 207, 1007, 1107, 1207]

CORE_MODULES = [
    'fig_la_cost_structure',   # LA_01
    'fig_la_vs_n',             # LA_02, LA_03
    'fig_la_efficiency',       # LA_04, LA_05, LA_06
    'fig_la_solver_pool',      # LA_07, LA_08, LA_08b
    'fig_la_boundary',         # LA_boundary (no HSM; regen for a complete set)
]


def enrich_cell(N, sd):
    """Re-parse a completed per-pool HSM run into a canonical-shaped cell."""
    d = RUN_ROOT / f'HSMpp__N{N}__seed{sd}'
    sp = d / 'stdout.log'
    if not sp.exists():
        return None
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


def build_merged():
    merged = json.loads(CANON.read_text())
    n_repl, n_miss = 0, 0
    for N in NS:
        for sd in SEEDS:
            cell = enrich_cell(N, sd)
            k = f'EDF-HSM__N{N}__seed{sd}'
            if cell is None:
                n_miss += 1
                print(f'  ! missing run for {k} (kept OLD cell)')
                continue
            merged[k] = cell
            n_repl += 1
    MERGED.write_text(json.dumps(merged, indent=1))
    print(f'merged: replaced {n_repl} EDF-HSM cells, {n_miss} missing -> {MERGED.name}')
    return merged


def main():
    merged = build_merged()
    for name in CORE_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, 'CANON'):
            mod.CANON = merged          # swap dataset (modules that read canonical)
        mod.OUT_DIR = NEW_PLOTS         # swap output folder
        print(f'[{name}] -> {NEW_PLOTS.name}/')
        mod.main()
    print(f'\nDone. New-HSM suite in {NEW_PLOTS}')
    print('NOT regenerated (need their own update):')
    print('  - fig_la_scarcity   (reads data_la_scarcity.json: separate s-sweep, old HSM)')
    print('  - fig_xworkload     (hardcoded GRID synthesis numbers)')


if __name__ == '__main__':
    main()
