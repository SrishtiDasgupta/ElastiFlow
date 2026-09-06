"""The three arrival processes match tests/regression/baseline_dispatch.json
exactly (see dispatch_record.py)."""
from conftest import REPO, assert_records_equal, load_json
from dispatch_record import compute


def test_dispatch_matches_baseline():
    ref = load_json(REPO / 'tests' / 'regression' / 'baseline_dispatch.json')['records']
    got = compute()
    assert set(got) == set(ref)
    for k in ref:
        assert len(got[k]) == len(ref[k]), k
    assert_records_equal(ref, got)
