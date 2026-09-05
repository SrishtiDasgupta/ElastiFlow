"""
Self-check of the comparison helper. The cell tests are only meaningful if
`assert_records_equal` rejects the changes it is meant to catch, so these
cases pin its behaviour: exact for ints and strings, a 1e-12 relative
tolerance for floats, missing keys are failures, bookkeeping keys are
ignored, and nested dicts and lists are flattened.
"""
import pytest

from conftest import assert_records_equal

REF = {
    'total_cost_eur': 1234.5678,
    'n_done': 150,
    '_wall': 31.2,
    'per_solver': {'ansys': {'lic': 83.1, 'n': 50}},
    'per_run': [{'cost': 1.0}, {'cost': 2.0}],
}


def _with(**changes):
    got = {k: (dict(v) if isinstance(v, dict) else v) for k, v in REF.items()}
    got['per_solver'] = {'ansys': dict(REF['per_solver']['ansys'])}
    got['per_run'] = [dict(r) for r in REF['per_run']]
    got.update(changes)
    return got


def test_identical_passes():
    assert_records_equal(REF, _with())


def test_float_within_tolerance_passes():
    assert_records_equal(REF, _with(total_cost_eur=1234.5678 * (1 + 5e-13)))


def test_float_beyond_tolerance_fails():
    with pytest.raises(AssertionError, match='total_cost_eur'):
        assert_records_equal(REF, _with(total_cost_eur=1234.5678 * (1 + 1e-9)))


def test_int_change_fails():
    with pytest.raises(AssertionError, match='n_done'):
        assert_records_equal(REF, _with(n_done=149))


def test_missing_key_fails():
    got = _with()
    del got['n_done']
    with pytest.raises(AssertionError, match='MISSING n_done'):
        assert_records_equal(REF, got)


def test_bookkeeping_keys_ignored():
    assert_records_equal(REF, _with(_wall=999.0))


def test_nested_dict_change_fails():
    got = _with()
    got['per_solver']['ansys']['lic'] = 83.2
    with pytest.raises(AssertionError, match=r'per_solver\.ansys\.lic'):
        assert_records_equal(REF, got)


def test_nested_list_change_fails():
    got = _with()
    got['per_run'][1]['cost'] = 2.5
    with pytest.raises(AssertionError, match=r'per_run\.\[1\]\.cost'):
        assert_records_equal(REF, got)
