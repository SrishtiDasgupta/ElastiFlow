"""
One committed cell per campaign, re-run from the working tree and compared
field by field with the dataset of record.

Reference state: tag `thesis-submitted-2026-09-04`. The cells and the parsers
are the ones the sweep drivers use (see the cell runners in conftest.py), so a
passing run means the current tree still produces the numbers printed in the
dissertation for these cells.

Runtime on the reference machine: SeisSol ~2 s, licence ~3 s, HPO < 1 s (before
the in-process runtime model of B4b these were ~20 s and ~30 s).
"""
import shutil

from conftest import (assert_records_equal, licence_cell, load_json, run,
                      seissol_cell)


def test_seissol_cell_reproduces(repo, python, tmp_path):
    """SeisSol--TinyDA: Elastic-EDF_c at N = 100, seed 7
    (use_cases/seissol/results/plain_results_per_run.json)."""
    rec = seissol_cell(python, 'edf_moldable_c', tmp_path / 'seissol')
    ref = load_json(repo / 'use_cases' / 'seissol' / 'results' / 'plain_results_per_run.json')
    assert_records_equal(ref['edf_moldable_c__N100__seed7'], rec)


def test_licence_cell_reproduces(repo, python, tmp_path):
    """Licence-constrained: EDF-LAMF at N = 150, seed 7
    (use_cases/licence/results/canonical_results.json)."""
    rec = licence_cell(python, 'EDF-LAMF', tmp_path / 'la')
    ref = load_json(repo / 'use_cases' / 'licence' / 'results' / 'canonical_results.json')
    assert_records_equal(ref['EDF-LAMF__N150__seed7'], rec)


def test_hpo_cost_table_reproduces(repo, python, tmp_path):
    """HPO: the full total_cost_per_run.json regeneration (12 cells x 6 seeds).

    compute_total_cost.py locates the repository as parents[4] of its own path
    and writes its output next to itself, so it is copied into a temporary tree
    of the same depth together with the calibrated simulator it imports. The
    committed file is never touched.
    """
    plots = tmp_path / 'use_cases' / 'hpo' / 'results' / 'plots'
    sim = tmp_path / 'use_cases' / 'hpo' / 'results' / 'r7_n7_actual_vs_modeled'
    plots.mkdir(parents=True)
    sim.mkdir()
    shutil.copy(repo / 'use_cases' / 'hpo' / 'results' / 'plots' / 'compute_total_cost.py', plots)
    shutil.copy(repo / 'use_cases' / 'hpo' / 'results' / 'r7_n7_actual_vs_modeled'
                / 'sim_4corners_calibrated.py', sim)

    run([python, 'compute_total_cost.py'], cwd=plots)

    got = load_json(plots / 'total_cost_per_run.json')
    ref = load_json(repo / 'use_cases' / 'hpo' / 'results' / 'plots' / 'total_cost_per_run.json')
    assert set(got) == set(ref), f'cell keys differ: {set(got) ^ set(ref)}'
    assert_records_equal(ref, got)
