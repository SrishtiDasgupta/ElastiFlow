"""The HPO request loops match tests/regression/baseline_hpo_loop.json event
for event (see hpo_loop.py)."""
from conftest import REPO, load_json
from hpo_loop import compute


def test_hpo_loops_match_baseline():
    ref = load_json(REPO / 'tests' / 'regression' / 'baseline_hpo_loop.json')['records']
    got = compute()
    assert set(got) == set(ref)
    for name in ref:
        for i, (a, b) in enumerate(zip(ref[name], got[name])):
            assert a == b, f'{name}: event {i} differs: recorded {a!r}, now {b!r}'
        assert len(got[name]) == len(ref[name]), f'{name}: {len(got[name])} events, recorded {len(ref[name])}'
