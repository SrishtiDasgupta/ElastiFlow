"""The HPO allocation decisions match tests/regression/baseline_hpo_allocation.json
exactly (see hpo_allocation.py). HPO's only executable gate."""
from conftest import REPO, assert_records_equal, load_json
from hpo_allocation import compute


def test_hpo_allocation_matches_baseline():
    ref = load_json(REPO / 'tests' / 'regression' / 'baseline_hpo_allocation.json')['records']
    got = compute()
    assert set(got) == set(ref), f'record keys differ: {sorted(set(got) ^ set(ref))[:10]}'
    assert_records_equal(ref, got)
