"""The policy registry resolves every name the entry points and drivers use to
the class they used before B7.0, and every registered class imports."""
import pytest

from elastiflow import policies as P


def test_every_policy_loads():
    for p in P.POLICIES:
        cls = p.load()
        assert cls.__name__ == p.cls, p


def test_names_are_unique_across_aliases():
    seen = {}
    for p in P.POLICIES:
        for n in (p.name, *p.aliases):
            assert (p.use_case, n) not in seen, f'{n} registered twice'
            seen[(p.use_case, n)] = p


@pytest.mark.parametrize('algo,mode,sort_key,cls,kwargs', [
    ('fcfs', 'static', 'runtime', 'FCFS_Optimized', {'sort_key': 'runtime_per_iteration'}),
    ('fcfs', 'static', 'cost', 'FCFS_Optimized', {'sort_key': 'cost_per_iteration'}),
    ('fcfs', 'moldable', 'runtime', 'FCFS_Optimized', {'sort_key': 'runtime_per_iteration'}),
    ('fcfs', 'moldable', 'cost', 'FCFS_Optimized', {'sort_key': 'cost_per_iteration'}),
    ('edf', 'static', 'runtime', 'EarliestDeadlineEDF', {'sort_key': 'runtime_per_iteration'}),
    ('edf', 'static', 'cost', 'EarliestDeadlineEDF', {'sort_key': 'cost_per_iteration'}),
    ('edf', 'moldable', 'runtime', 'EarliestDeadlineEDF', {'sort_key': 'runtime_per_iteration'}),
    ('edf', 'moldable', 'cost', 'EarliestDeadlineEDF', {'sort_key': 'cost_per_iteration'}),
    ('heft', 'static', 'runtime', 'HEFT_HEFT_REQ', {}),
    ('rank', 'moldable', 'runtime', 'PriorityPriority', {}),
    ('fcfs_scheduler', 'static', 'cost', 'FCFS_Scheduler', {'sort_key': 'cost_per_iteration'}),
    ('earliest_deadline_fcfs', 'static', 'cost', 'EarliestDeadlineFCFS', {'sort_key': 'cost_per_iteration'}),
    ('priority_fcfs', 'static', 'cost', 'PriorityFCFS', {}),
    ('heft_fcfs_req', 'static', 'cost', 'HEFT_FCFS_REQ', {}),
])
def test_sweep_arguments_resolve_as_before(algo, mode, sort_key, cls, kwargs):
    """The 11 cited variants keep the class and kwargs simulate_sweep.py's
    if-chain gave them; the four uncited policies resolve by module name."""
    p = P.resolve_seissol(algo, mode, sort_key)
    assert p.cls == cls
    assert p.kwargs('runtime_per_iteration' if sort_key == 'runtime' else 'cost_per_iteration') == kwargs


@pytest.mark.parametrize('name,cls,note', [
    ('LAMF', 'FCFS_Optimized_LA', 'License-Aware Moldable FCFS'),
    ('FCFS-LAMF', 'FCFS_Optimized_LA', 'License-Aware Moldable FCFS'),
    ('EDF-LAMF', 'EDF_Optimized_LA', 'License-Aware Moldable EDF'),
    ('FCFS-ST-LA', 'FCFS_Scheduler_LA', 'Static FCFS with license awareness'),
    ('EDF-ST-LA', 'EDF_Scheduler_LA', 'Static EDF with license awareness'),
    ('EDF-HSM', 'EDF_HSM_LA', 'Hybrid Static-Moldable EDF'),
    ('HSM', 'EDF_HSM_LA', 'Hybrid Static-Moldable EDF'),
])
def test_licence_runner_names_resolve_as_before(name, cls, note):
    p = P.get('licence', name)
    assert (p.cls, p.note, p.kwargs()) == (cls, note, {'sort_key': 'cost_per_iteration'})


@pytest.mark.parametrize('algo,mode,cls', [
    ('fcfs', 'static', 'FCFS_Scheduler_HPO'), ('fcfs', 'moldable', 'FCFS_Optimized_HPO'),
    ('edf', 'static', 'EDF_Scheduler_HPO'), ('edf', 'moldable', 'EDF_Optimized_HPO'),
])
def test_hpo_runner_arguments_resolve_as_before(algo, mode, cls):
    assert P.resolve_hpo(algo, mode).cls == cls


def test_inactive_set_is_exactly_the_uncited_three():
    assert {p.name for p in P.POLICIES if not p.active} == {'earliest_deadline_fcfs', 'priority_fcfs', 'heft_fcfs_req'}
    assert P.names('seissol') == ['FCFS-ST_r', 'FCFS-ST_c', 'Elastic-FCFS_r', 'Elastic-FCFS_c', 'EDF-ST_r', 'EDF-ST_c',
                                  'Elastic-EDF_r', 'Elastic-EDF_c', 'HEFT-ST', 'Elastic-Rank', 'fcfs_scheduler']
    assert P.names('licence', with_aliases=True) == ['FCFS-ST-LA', 'EDF-ST-LA', 'FCFS-LAMF', 'LAMF', 'EDF-LAMF', 'HSM', 'EDF-HSM']


def test_unknown_name_lists_the_known_ones():
    with pytest.raises(KeyError, match='Elastic-Rank'):
        P.get('seissol', 'nope')
