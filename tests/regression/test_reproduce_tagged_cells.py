"""
One committed cell per campaign, re-run from the working tree and compared
field by field with the dataset of record.

Reference state: tag `thesis-submitted-2026-09-04`. The cells and the parsers
are the ones the sweep drivers use, so a passing run means the current tree
still produces the numbers printed in the dissertation for these cells.

Runtime on the reference machine: SeisSol ~20 s, licence ~30 s, HPO < 1 s.
"""
import shutil

from conftest import (assert_records_equal, la_env, load_json, load_module,
                      run)


def test_seissol_cell_reproduces(repo, python, tmp_path):
    """SeisSol--TinyDA: Elastic-EDF_c at N = 100, seed 7 (plain_results_per_run.json).

    sweep_PLAIN.py invokes `simulate_sweep.py <algo> <mode> <out_dir> ...` and
    parses the `.out` file the simulator moves into out_dir.
    """
    out = tmp_path / 'seissol'
    out.mkdir()
    run([python, 'simulate_sweep.py', 'edf', 'moldable', '--sort-key', 'cost',
         out, '--seed', '7', '--N', '100'], cwd=repo / 'elastiflow')

    sweep = load_module(repo / 'plain_results' / 'sweep_PLAIN.py')
    out_files = sorted(out.glob('*.out'))
    assert len(out_files) == 1, f'expected one .out file, found {out_files}'
    rec = sweep.parse_out(out_files[0].read_text())
    assert not rec.get('_parse_failed'), 'sweep_PLAIN.parse_out could not parse the run'

    ref = load_json(repo / 'plain_results' / 'plain_results_per_run.json')
    assert_records_equal(ref['edf_moldable_c__N100__seed7'], rec)


def test_licence_cell_reproduces(repo, python, tmp_path):
    """Licence-constrained: EDF-LAMF at N = 150, seed 7 (canonical_results.json).

    canonical_sweep.py captures stdout+stderr, parses it with parse_la_run.parse,
    then derives the licence-accounting fields from the two CSVs the cell writes
    with license_analysis.analyze_results / analyze_usage. Mirrored exactly.
    """
    scheduler = 'EDF-LAMF'
    out = tmp_path / 'la'
    out.mkdir()
    cp = run([python, 'simulate_main_LA.py', '--scheduler', scheduler,
              '--N', '150', '--seed', '7', '--output-dir', out],
             cwd=repo / 'elastiflow', env=la_env(scheduler))

    parse_la_run = load_module(repo / 'license_results' / 'parse_la_run.py')
    LA = load_module(repo / 'license_results' / 'license_analysis.py')
    rec = parse_la_run.parse(cp.stdout)

    results_csv = sorted(out.glob('*_results.csv'))
    usage_csv = sorted(out.glob('*_license_usage.csv'))
    assert len(results_csv) == 1 and len(usage_csv) == 1, \
        f'expected one results and one usage CSV in {out}, found {sorted(out.iterdir())}'

    r = LA.analyze_results(str(results_csv[0]))
    rec['tot_lic'] = r['tot_lic']; rec['tot_hw'] = r['tot_hw']
    rec['waste_frac'] = r['waste_frac']; rec['eff_lic_util'] = 100 - r['waste_frac']
    rec['lic_per_done'] = r['lic_per_done']; rec['overhead'] = r['overhead']
    rec['n_done'] = r['n_done']; rec['n_miss'] = r['n_miss']
    rec['per_solver'] = {s: {'lic': round(v['lic'], 1), 'n': v['n'], 'done': v['done']}
                         for s, v in r['per_solver'].items()}
    u = LA.analyze_usage(str(usage_csv[0]))
    rec['token_sec_total'] = sum(x['token_sec'] for x in u.values())
    rec['pool_tw_util'] = {pool: round(u[pool]['tw_util'], 1) for pool in u}

    ref = load_json(repo / 'license_results' / 'canonical_results.json')
    assert_records_equal(ref[f'{scheduler}__N150__seed7'], rec)


def test_hpo_cost_table_reproduces(repo, python, tmp_path):
    """HPO: the full total_cost_per_run.json regeneration (12 cells x 6 seeds).

    compute_total_cost.py locates the repository as parents[3] of its own path
    and writes its output next to itself, so it is copied into a temporary tree
    of the same depth together with the calibrated simulator it imports. The
    committed file is never touched.
    """
    plots = tmp_path / 'HPO' / 'results' / 'plots'
    sim = tmp_path / 'HPO' / 'results' / 'r7_n7_actual_vs_modeled'
    plots.mkdir(parents=True)
    sim.mkdir()
    shutil.copy(repo / 'HPO' / 'results' / 'plots' / 'compute_total_cost.py', plots)
    shutil.copy(repo / 'HPO' / 'results' / 'r7_n7_actual_vs_modeled'
                / 'sim_4corners_calibrated.py', sim)

    run([python, 'compute_total_cost.py'], cwd=plots)

    got = load_json(plots / 'total_cost_per_run.json')
    ref = load_json(repo / 'HPO' / 'results' / 'plots' / 'total_cost_per_run.json')
    assert set(got) == set(ref), f'cell keys differ: {set(got) ^ set(ref)}'
    assert_records_equal(ref, got)
