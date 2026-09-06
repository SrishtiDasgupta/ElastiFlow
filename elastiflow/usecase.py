"""The use-case protocol (B7.7): what a use case contributes to the framework.

A `UseCase` says how a workflow plan of that kind is recognised, how the
scheduler reads its constraints, how the workflow engine reads the inputs of
each iteration and what it hands to the next one, when an iteration's result
ends the workflow, which configuration profile applies, and which Steep
action classes run its iterations. The three use cases of the dissertation
(SeisSol--TinyDA, the licence-constrained campaign, HPO) and the adaptive
SeisSol variant are instances below; `for_plan` recognises a plan.

The readers of the iteration inputs are the former `getClientInputs_*`
functions of utils/exec_sched.py, moved here unchanged; the engine's
`getClientInputs` routes to the plan's use case.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Tuple

from elastiflow.config.constants import MOLDABLE
from elastiflow.utils import resource, resource_LA
from elastiflow.utils.exec_sched import getHostsForIteration, getWorkflowConfig


def getClientInputs_Plain(wf_id, input: Tuple, ind):
    """
    Handler for Plain SeisSol workflows.

    Plain workflows use workflowConfig array for pre-planned iteration configs.
    Input format: (cohesion_value, hosts) where cohesion_value is numeric/string scalar.
    """
    mesh = getWorkflowConfig(wf_id)['mesh']
    backend = getWorkflowConfig(wf_id)['backend']
    workflow_config_array = getWorkflowConfig(wf_id)['workflowConfig']

    # Extract from pre-planned workflowConfig array
    chains = workflow_config_array[ind]['chains']
    tinyda_iterations = workflow_config_array[ind]['tinydaIterations']

    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, backend, MOLDABLE, chains)

    port = backend.lease_port() if len(hosts.get('on-prem', [])) != 0 else 4242

    return {
        'wf_id': wf_id,
        'cohesion': input[0],  # Simple scalar value
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port
    }, hosts, backend


def getClientInputs_LA(wf_id, input: Tuple, ind):
    """
    Handler for License-Aware SeisSol workflows.

    LA workflows can use either:
    - workflowConfig array for moldable scheduling (chains vary per iteration)
    - constraints for static allocation (chains constant across iterations)

    Input format: (cohesion_value, hosts) where cohesion_value is numeric scalar.
    """
    mesh = getWorkflowConfig(wf_id)['mesh']
    backend = getWorkflowConfig(wf_id)['backend']

    # Read from workflowConfig if present (for moldable LAMF scheduling)
    # This allows chains to vary per iteration, triggering resource requests
    if 'workflowConfig' in getWorkflowConfig(wf_id):
        workflow_config_array = getWorkflowConfig(wf_id)['workflowConfig']
        chains = workflow_config_array[ind]['chains']
        tinyda_iterations = workflow_config_array[ind]['tinydaIterations']
    else:
        # Fallback to static constraints (for baseline static scheduling)
        constraints = getWorkflowConfig(wf_id).get('constraints', {})
        chains = constraints.get('chains', 1)
        tinyda_iterations = constraints.get('tinydaIterations', 1)

    # OLD CODE (always used static constraints):
    # constraints = getWorkflowConfig(wf_id).get('constraints', {})
    # chains = constraints.get('chains', 1)
    # tinyda_iterations = constraints.get('tinydaIterations', 1)

    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, backend, MOLDABLE, chains)

    port = backend.lease_port() if len(hosts.get('on-prem', [])) != 0 else 4242

    return {
        'wf_id': wf_id,
        'cohesion': input[0],  # Simple scalar value
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port
    }, hosts, backend


def getClientInputs_HPO(wf_id, input: Tuple, ind):
    """
    Handler for HPO (Hyperparameter Optimization) workflows.

    HPO workflows use dynamic dict input for iteration config.
    Input format: (config_dict, hosts) where config_dict has epochs/next_trials keys.
    """
    mesh = getWorkflowConfig(wf_id)['mesh']
    backend = getWorkflowConfig(wf_id)['backend']

    # Iteration 0: input[0] is initial config from workflow YAML (has 'epochs', 'next_trials')
    # Iteration 1+: input[0] is HPO pipeline output (has 'epoch', 'next_trials')
    if ind == 0:
        # First iteration: use 'epochs' (plural) and 'next_trials'
        constraints = getWorkflowConfig(wf_id).get('constraints', {})
        chains = input[0].get('next_trials', constraints.get('chains', 1))
        tinyda_iterations = input[0].get('epochs', constraints.get('tinydaIterations', 1))
    else:
        # Subsequent iterations: use 'epoch' (singular) from HPO output
        chains = input[0].get('next_trials', 0)
        tinyda_iterations = input[0].get('epoch', input[0].get('epochs', 1))

    # HPO uses its own constants from constants_HPO (not the shared constants.py)
    from elastiflow.config.constants_HPO import MOLDABLE as HPO_MOLDABLE
    from elastiflow.config.constants_HPO import FREE_RESOURCES as HPO_FREE_RESOURCES
    from elastiflow.config.constants_HPO import RESOURCE_REQUEST_TIMEOUT as HPO_TIMEOUT
    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, backend, HPO_MOLDABLE, chains,
                                               HPO_FREE_RESOURCES, HPO_TIMEOUT)

    port = backend.lease_port() if len(hosts.get('on-prem', [])) != 0 else 4242

    return {
        'wf_id': wf_id,
        'cohesion': input[0],  # Full dict (HPO config)
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port
    }, hosts, backend


def getClientInputs_PlainAdaptive(wf_id, input: Tuple, ind):
    """
    Handler for PLAIN_ADAPTIVE SeisSol workflows (convergence-driven).

    Input format: (config_dict, hosts) where config_dict carries the previous
    iteration's adaptive driver output: cohesion (scalar), next_links,
    next_chains, next_cohesion_mean, next_cohesion_var.

    On iteration 0 the dict comes from vars[0].value in the YAML and may only
    contain {cohesion, next_links, next_chains}.
    """
    cfg = getWorkflowConfig(wf_id)
    mesh = cfg['mesh']
    backend = cfg['backend']
    constraints = cfg.get('constraints', {})

    inner = input[0] if isinstance(input[0], dict) else {'cohesion': input[0]}

    if ind == 0:
        chains = int(inner.get('next_chains', constraints.get('chains', 2)))
        tinyda_iterations = int(inner.get('next_links',
                                          constraints.get('tinydaIterations', 2)))
    else:
        chains = int(inner.get('next_chains', constraints.get('chains', 2)))
        tinyda_iterations = int(inner.get('next_links',
                                          constraints.get('tinydaIterations', 2)))

    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, backend, MOLDABLE, chains)

    port = backend.lease_port() if len(hosts.get('on-prem', [])) != 0 else 4242

    cumulative_cap = constraints.get('tinydaIterations',
                                     cfg.get('workflowIterations', 10) * tinyda_iterations)

    return {
        'wf_id': wf_id,
        'cohesion': inner,             # full dict carries adaptive feedback fields
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port,
        'cumulative_links_cap': cumulative_cap,
    }, hosts, backend


@dataclass(frozen=True)
class UseCase:
    name: str
    workflow_type: str                  # the tag the workflow registry carries ('PLAIN', 'LA', 'HPO', 'PLAIN_ADAPTIVE')
    profile: str                        # elastiflow/config/profiles.py
    client_inputs: Callable             # (wf_id, input, ind) -> (args, hosts, backend): the next iteration's inputs
    constraints: Callable               # plan -> the scheduler's constraints dict
    next_input: Callable                # an iteration's result -> what the next iteration receives
    terminates: Callable                # an iteration's result -> True when it ends the workflow early
    hpo_engine: bool = False            # the HPO execute action runs its own iteration runner (steep_actions_HPO)

    def engine_actions(self):
        """The Steep action classes (ForEachAction, ExecuteAction) that run this use case."""
        from elastiflow.workflow.steep import steep_actions
        if self.hpo_engine:
            from elastiflow.workflow.steep import steep_actions_HPO
            return steep_actions.ForEachAction, steep_actions_HPO.ExecuteAction
        return steep_actions.ForEachAction, steep_actions.ExecuteAction


def _cohesion(result):
    # Plain and LA return numeric cohesion value
    return float(result['cohesion'])


def _never(result):
    return False


SEISSOL = UseCase('seissol', 'PLAIN', 'seissol', getClientInputs_Plain, resource.getConstraintsFromWorkflow, _cohesion, _never)
LICENCE = UseCase('licence', 'LA', 'licence', getClientInputs_LA, resource_LA.getConstraintsFromWorkflow, _cohesion, _never)
HPO = UseCase('hpo', 'HPO', 'hpo', getClientInputs_HPO, resource.getConstraintsFromWorkflow,
              lambda result: result.get('config', result),        # HPO returns full config dict for next iteration
              _never, hpo_engine=True)
# Adaptive SeisSol returns the full driver dict (cohesion + next_links/chains + posterior
# moments + diagnostic); the driver ends the workflow by its terminate_reason.
SEISSOL_ADAPTIVE = UseCase('seissol_adaptive', 'PLAIN_ADAPTIVE', 'seissol', getClientInputs_PlainAdaptive, resource.getConstraintsFromWorkflow,
                           lambda result: result,
                           lambda result: isinstance(result, dict) and result.get('terminate_reason') in ('converged', 'cap'))

USE_CASES = (SEISSOL, LICENCE, HPO, SEISSOL_ADAPTIVE)


def for_plan(config: dict) -> UseCase:
    """
    Detect workflow type based on distinguishing fields.

    Three workflow types:
    - PLAIN: Plain SeisSol (uses workflowConfig array, no licenses)
    - LA: License-Aware SeisSol (has license_pool/software_id, uses constraints)
    - HPO: Hyperparameter Optimization (string mesh, dict inputs)

    Detection hierarchy (order matters - check most specific first):
    1. License fields (license_pool/software_id) → LA
    2. String mesh → HPO
    3. workflowConfig array → PLAIN
    4. Integer mesh → PLAIN (fallback)
    """
    constraints = config.get('constraints', {})
    mesh = config.get('mesh')

    # 0. Explicit adaptive opt-in via config.adaptive: true
    if config.get('adaptive') is True:
        return SEISSOL_ADAPTIVE

    # 1. Check for LA-specific fields (most specific)
    if 'license_pool' in constraints or 'software_id' in config:
        return LICENCE

    # 2. Check for HPO-specific fields (string mesh = model names like "vgg19")
    if isinstance(mesh, str):
        return HPO

    # 3. Check for Plain-specific fields (workflowConfig array)
    if 'workflowConfig' in config:
        return SEISSOL

    # 4. Fallback: integer mesh likely means Plain or LA without explicit fields
    #    Default to PLAIN for backwards compatibility
    if isinstance(mesh, int):
        return SEISSOL

    # 5. Ultimate fallback
    raise ValueError(f"Cannot determine workflow type: mesh={mesh}, config keys={config.keys()}")
