"""The use-case protocol (B7.7): plans are recognised as before, each use case
reads its inputs and hands on its results as the engine's if-chains did, and
one Steep engine builds the use case's actions."""
import yaml
import pytest

from elastiflow import usecase
from elastiflow.config.paths import PACKAGE_DIR
from elastiflow.utils import exec_sched
from elastiflow.workflow.steep import steep_actions, steep_actions_HPO
from elastiflow.workflow.steep_workflow import Steep_Workflow

PLANS = {
    'seissol': f'{PACKAGE_DIR}/sample_workflows/data0.yaml',
    'licence': f'{PACKAGE_DIR}/workflow/sample_workflows_LA/data0.yaml',
    'hpo':     f'{PACKAGE_DIR}/workflow/sample_workflows_HPO/data0.yaml',
}


def plan(name):
    return yaml.safe_load(open(PLANS[name]))


def config_of(p):
    c = dict(p['config']); c['constraints'] = p.get('constraints', {}); return c


class Backend:
    simulated = True

    def now(self):
        return 0.0

    def lease_port(self):
        return 4242


def test_plans_are_recognised_as_before():
    assert usecase.for_plan(config_of(plan('seissol'))) is usecase.SEISSOL
    assert usecase.for_plan(config_of(plan('licence'))) is usecase.LICENCE
    assert usecase.for_plan(config_of(plan('hpo'))) is usecase.HPO
    assert usecase.for_plan({'adaptive': True, 'mesh': 750}) is usecase.SEISSOL_ADAPTIVE
    assert usecase.for_plan({'mesh': 750}) is usecase.SEISSOL                   # integer mesh, no workflowConfig
    assert usecase.for_plan({'software_id': 2, 'mesh': 'vgg19'}) is usecase.LICENCE   # licence fields win over the string mesh
    with pytest.raises(ValueError):
        usecase.for_plan({'mesh': None})
    assert [u.workflow_type for u in usecase.USE_CASES] == ['PLAIN', 'LA', 'HPO', 'PLAIN_ADAPTIVE']


def test_results_are_handed_on_as_the_engine_did():
    assert usecase.SEISSOL.next_input({'cohesion': '2.13', 'runtime': 5}) == 2.13
    assert usecase.LICENCE.next_input({'cohesion': 3}) == 3.0
    assert usecase.HPO.next_input({'config': {'next_trials': 2}, 'x': 1}) == {'next_trials': 2}
    assert usecase.HPO.next_input({'x': 1}) == {'x': 1}
    d = {'cohesion': 1, 'next_links': 0, 'terminate_reason': 'converged'}
    assert usecase.SEISSOL_ADAPTIVE.next_input(d) is d and usecase.SEISSOL_ADAPTIVE.terminates(d)
    assert not usecase.SEISSOL_ADAPTIVE.terminates({'terminate_reason': 'other'}) and not usecase.SEISSOL.terminates(d)


def test_engines():
    assert usecase.SEISSOL.engine_actions() == (steep_actions.ForEachAction, steep_actions.ExecuteAction)
    assert usecase.HPO.engine_actions() == (steep_actions.ForEachAction, steep_actions_HPO.ExecuteAction)
    assert issubclass(steep_actions_HPO.ExecuteAction, steep_actions.ExecuteAction)
    for name, execute_cls in (('seissol', steep_actions.ExecuteAction), ('hpo', steep_actions_HPO.ExecuteAction)):
        p = plan(name)
        wf = Steep_Workflow(p, Backend(), 1000.0)
        try:
            assert exec_sched.getWorkflowConfig(wf.id)['use_case'] is (usecase.SEISSOL if name == 'seissol' else usecase.HPO)
            fe = wf.actions[0]
            assert isinstance(fe, steep_actions.ForEachAction) and type(fe.actions[0]) is execute_cls
        finally:
            exec_sched.removeWorkflowConfig(wf.id)


def test_client_inputs_route_to_the_use_case():
    p = plan('seissol')
    exec_sched.setWorkflowConfig(p['id'], p, Backend(), 1000.0)
    try:
        chains = p['config']['workflowConfig'][0]['chains']
        hosts = {'on-prem': {}, 'reserved': {'c6i': (chains, [f'10.0.0.{i}' for i in range(chains)])}, 'on-demand': {}}
        args, hosts_out, backend = exec_sched.getClientInputs(p['id'], (3, hosts), 0)
        assert (args['wf_id'], args['cohesion'], args['chains'], args['mesh'], args['port']) == (p['id'], 3, chains, p['config']['mesh'], 4242)
        assert args['tinyda_iterations'] == p['config']['workflowConfig'][0]['tinydaIterations'] and hosts_out is hosts
    finally:
        exec_sched.removeWorkflowConfig(p['id'])
