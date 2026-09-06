"""
The one-switch entry point (B6) reproduces the datasets of record.

`python -m elastiflow run --mode simulated --use-case ...` must give exactly the
cell that the campaign drivers produce by running the runner scripts directly
(tests/regression/test_reproduce_tagged_cells.py). Same cells, same references.
"""
from conftest import (LICENCE_N, LICENCE_SEED, REPO, SEISSOL_N, SEISSOL_SEED,
                      assert_records_equal, la_env, load_json, load_module, run)


def test_seissol_cell_via_cli(repo, python, tmp_path):
    sweep = load_module(REPO / 'use_cases' / 'seissol' / 'results' / 'sweep_PLAIN.py')
    out_dir = tmp_path / 'seissol'
    out_dir.mkdir()
    run([python, '-m', 'elastiflow', 'run', '--mode', 'simulated', '--use-case', 'seissol',
         *sweep.VARIANT_ARGS['edf_moldable_c'], out_dir, '--seed', str(SEISSOL_SEED), '--N', str(SEISSOL_N)],
        cwd=REPO)
    outs = sorted(out_dir.glob('*.out'))
    assert len(outs) == 1, outs
    rec = sweep.parse_out(outs[0].read_text())
    ref = load_json(repo / 'use_cases' / 'seissol' / 'results' / 'plain_results_per_run.json')
    assert_records_equal(ref['edf_moldable_c__N100__seed7'], rec)


def test_licence_cell_via_cli(repo, python, tmp_path):
    parse_la_run = load_module(REPO / 'use_cases' / 'licence' / 'results' / 'parse_la_run.py')
    out_dir = tmp_path / 'la'
    out_dir.mkdir()
    cp = run([python, '-m', 'elastiflow', 'run', '--mode', 'simulated', '--use-case', 'licence',
              '--scheduler', 'EDF-LAMF', '--N', str(LICENCE_N), '--seed', str(LICENCE_SEED), '--output-dir', out_dir],
             cwd=REPO, env=la_env('EDF-LAMF'))
    rec = parse_la_run.parse(cp.stdout)
    ref = load_json(repo / 'use_cases' / 'licence' / 'results' / 'canonical_results.json')
    # the stdout-derived fields; the CSV-derived licence accounting is covered by licence_cell
    for k in ('policy', 'N', 'seed', 'submitted', 'completed', 'total_cost'):
        if k in ref['EDF-LAMF__N150__seed7'] and k in rec:
            assert rec[k] == ref['EDF-LAMF__N150__seed7'][k], k
    common = {k: v for k, v in rec.items() if k in ref['EDF-LAMF__N150__seed7'] and not k.startswith('_')}
    assert len(common) >= 5, f'too few comparable fields: {sorted(common)}'
    assert_records_equal({k: ref['EDF-LAMF__N150__seed7'][k] for k in common}, common)


def test_seissol_cell_via_policy_name(repo, python, tmp_path):
    """--policy (B7.5): the dissertation name reaches the same cell."""
    sweep = load_module(REPO / 'use_cases' / 'seissol' / 'results' / 'sweep_PLAIN.py')
    out_dir = tmp_path / 'seissol'
    out_dir.mkdir()
    run([python, '-m', 'elastiflow', 'run', '--mode', 'simulated', '--use-case', 'seissol', '--policy', 'Elastic-EDF_c',
         out_dir, '--seed', str(SEISSOL_SEED), '--N', str(SEISSOL_N)], cwd=REPO)
    outs = sorted(out_dir.glob('*.out'))
    assert len(outs) == 1, outs
    rec = sweep.parse_out(outs[0].read_text())
    ref = load_json(repo / 'use_cases' / 'seissol' / 'results' / 'plain_results_per_run.json')
    assert_records_equal(ref['edf_moldable_c__N100__seed7'], rec)


def test_licence_cell_via_policy_name(repo, python, tmp_path):
    parse_la_run = load_module(REPO / 'use_cases' / 'licence' / 'results' / 'parse_la_run.py')
    out_dir = tmp_path / 'la'
    out_dir.mkdir()
    cp = run([python, '-m', 'elastiflow', 'run', '--mode', 'simulated', '--use-case', 'licence', '--policy', 'EDF-LAMF',
              '--N', str(LICENCE_N), '--seed', str(LICENCE_SEED), '--output-dir', out_dir],
             cwd=REPO, env=la_env('EDF-LAMF'))
    rec = parse_la_run.parse(cp.stdout)
    ref = load_json(repo / 'use_cases' / 'licence' / 'results' / 'canonical_results.json')['EDF-LAMF__N150__seed7']
    common = {k: v for k, v in rec.items() if k in ref and not k.startswith('_')}
    assert len(common) >= 5
    assert_records_equal({k: ref[k] for k in common}, common)
