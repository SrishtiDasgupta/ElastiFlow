"""The command line's option parsing and the --policy translation (B7.5):
every dissertation name maps to the entry point's own arguments, and
resolving those arguments gives the same policy back."""
import pytest

from elastiflow import cli, policies as P
from elastiflow.config import profiles


def test_parse_splits_options_from_runner_arguments():
    assert cli.parse(['run', '--mode', 'simulated', '--use-case', 'seissol', '--policy', 'Elastic-EDF_c', '/tmp/o', '--N', '100']) == \
        ('seissol', 'simulated', 'Elastic-EDF_c', ['/tmp/o', '--N', '100'])
    assert cli.parse(['run', '--mode=live', '--use-case=hpo', '--', '--count', '7']) == ('hpo', 'live', None, ['--count', '7'])
    for bad in ([], ['run'], ['run', '--mode', 'simulated'], ['run', '--mode', 'later', '--use-case', 'seissol']):
        with pytest.raises(SystemExit):
            cli.parse(bad)


def test_policy_translation_examples():
    assert cli.argv_for('seissol', 'simulated', 'Elastic-EDF_c', ['/tmp/o', '--seed', '7']) == ['edf', 'moldable', '/tmp/o', '--seed', '7', '--sort-key', 'cost']
    assert cli.argv_for('seissol', 'simulated', 'HEFT-ST', ['/tmp/o']) == ['heft', 'static', '/tmp/o']
    assert cli.argv_for('seissol', 'simulated', 'Elastic-Rank', ['/tmp/o', '--rank-budget', '5.0']) == ['rank', 'moldable', '/tmp/o', '--rank-budget', '5.0']
    assert cli.argv_for('seissol', 'simulated', 'priority_fcfs', ['/tmp/o']) == ['priority_fcfs', 'static', '/tmp/o']
    assert cli.argv_for('licence', 'simulated', 'HSM', ['--N', '150']) == ['--scheduler', 'HSM', '--N', '150']
    assert cli.argv_for('licence', 'simulated', 'LAMF', []) == ['--scheduler', 'FCFS-LAMF']
    assert cli.argv_for('hpo', 'live', 'Elastic-EDF', ['--count', '7']) == ['--algo', 'edf', '--mode', 'moldable', '--count', '7']
    assert cli.argv_for('hpo', 'simulated', None, ['--algo', 'fcfs']) == ['--algo', 'fcfs']


def test_every_policy_round_trips_through_its_entry_point():
    for p in P.POLICIES:
        args = P.runner_args(p, ['OUT'] if p.use_case == 'seissol' else [])
        if p.use_case == 'seissol':
            sort = args[args.index('--sort-key') + 1] if '--sort-key' in args else 'cost'
            back = P.resolve_seissol(args[0], args[1], sort)
        elif p.use_case == 'licence':
            back = P.get('licence', args[1])
        else:
            back = P.resolve_hpo(args[1], args[3])
        assert back is p, (p.name, args)


def test_live_seissol_and_licence_fix_their_policy():
    for uc in ('seissol', 'licence'):
        with pytest.raises(SystemExit, match='does not apply'):
            cli.argv_for(uc, 'live', P.names(uc)[0], [])


def test_unknown_policy_lists_the_known_ones():
    with pytest.raises(SystemExit, match='Elastic-Rank'):
        cli.argv_for('seissol', 'simulated', 'nope', [])


def test_usage_lists_active_policies_only():
    text = cli.usage()
    assert 'Elastic-EDF_c' in text and 'EDF-LAMF' in text and 'Elastic-EDF' in text
    assert 'priority_fcfs' not in text and 'heft_fcfs_req' not in text


def test_profiles_are_the_three_constants_modules():
    import elastiflow.config.constants as c, elastiflow.config.constants_LA as la, elastiflow.config.constants_HPO as h
    assert (profiles.load('seissol'), profiles.load('licence'), profiles.load('hpo')) == (c, la, h)
    assert P.get('licence', 'HSM').profile is la and P.get('seissol', 'HEFT-ST').profile is c
