"""
Every simulated policy of every campaign, re-run and compared exactly with the
recorded baseline (tests/regression/baseline_all_policies.json).

This is the Phase B gate: the regression tests pin three cells to the datasets
of record; this suite pins all 16 policies to the behaviour recorded from a
tree that passed those tests. About seven minutes; marked `smoke`, so it is
excluded from a plain `pytest` run and selected with `pytest -m smoke`.

To move the reference deliberately, re-run tests/regression/record_baseline.py
and commit the new file with the reason.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'regression'))
from conftest import (LICENCE_POLICIES, REPO, assert_records_equal, licence_cell,  # noqa: E402
                      load_json, run, seissol_cell, seissol_variants, strip_bookkeeping)

pytestmark = pytest.mark.smoke


@pytest.fixture(scope='module')
def baseline() -> dict:
    """Loaded lazily so that a plain `pytest` run (which deselects this module)
    never touches the file."""
    return load_json(REPO / 'tests' / 'regression' / 'baseline_all_policies.json')['cells']


@pytest.mark.parametrize('variant', sorted(seissol_variants()))
def test_seissol_policy_matches_baseline(variant, python, tmp_path, baseline):
    rec = strip_bookkeeping(seissol_cell(python, variant, tmp_path / variant))
    assert_records_equal(baseline[f'seissol/{variant}'], rec)


@pytest.mark.parametrize('policy', LICENCE_POLICIES)
def test_licence_policy_matches_baseline(policy, python, tmp_path, baseline):
    rec = strip_bookkeeping(licence_cell(python, policy, tmp_path / policy))
    assert_records_equal(baseline[f'licence/{policy}'], rec)


def test_ch7_validation_runner_runs(tmp_path):
    """simulate_main.py is the entry the Chapter 7 scaling sweep drives; it
    writes its CSVs into the working directory, so it runs from a temporary one."""
    cp = run([sys.executable, str(REPO / 'elastiflow' / 'simulate_main.py')], cwd=tmp_path)
    assert 'Overall miss rate' in cp.stdout
