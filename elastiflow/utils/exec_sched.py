import time
from typing import List, Tuple
import re

from elastiflow.config.constants import FREE_RESOURCES, MOLDABLE, RESOURCE_REQUEST_TIMEOUT, SIMULATE
from .request import ExecutorRequest, getConfig, sendRequest
from . import negotiation_log
from elastiflow.scripts.create_instance import createInstance, deleteInstanceFromIp

import yaml
import os
from elastiflow.execution.backend import backend_for

_PORTS_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'ports.yaml')

workflow_config = {}


def detectWorkflowType(wf_id):
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
    config = getWorkflowConfig(wf_id)
    constraints = config.get('constraints', {})
    mesh = config.get('mesh')

    # 0. Explicit adaptive opt-in via config.adaptive: true
    if config.get('adaptive') is True:
        return 'PLAIN_ADAPTIVE'

    # 1. Check for LA-specific fields (most specific)
    if 'license_pool' in constraints or 'software_id' in config:
        return 'LA'

    # 2. Check for HPO-specific fields (string mesh = model names like "vgg19")
    if isinstance(mesh, str):
        return 'HPO'

    # 3. Check for Plain-specific fields (workflowConfig array)
    if 'workflowConfig' in config:
        return 'PLAIN'

    # 4. Fallback: integer mesh likely means Plain or LA without explicit fields
    #    Default to PLAIN for backwards compatibility
    if isinstance(mesh, int):
        return 'PLAIN'

    # 5. Ultimate fallback
    raise ValueError(f"Cannot determine workflow type for {wf_id}: mesh={mesh}, config keys={config.keys()}")


def getWorkflowOnpremPort():
    with open(_PORTS_YAML, "r") as f:
        data = yaml.safe_load(f)

    ports = data.get("onprem_ports", [])
    if not ports:
        raise ValueError("No ports to pop")

    popped = ports.pop(0)
    data["onprem_ports"] = ports

    with open(_PORTS_YAML, "w") as f:
        yaml.safe_dump(data, f) # default = block style

    return popped

def setWorkflowConfig(id, workflow, sim, deadline):
    workflow_config[id] = workflow['config']
    workflow_config[id]['constraints'] = workflow.get('constraints', {})  # Store constraints (chains, tinydaIterations)
    workflow_config[id]['sim'] = sim
    workflow_config[id]['deadline'] = deadline
    workflow_config[id]['complete'] = False

    # Detect and cache workflow type for efficient routing
    workflow_config[id]['workflow_type'] = detectWorkflowType(id)

def getWorkflowConfig(id):
    return workflow_config[id]

def removeWorkflowConfig(id):
    return workflow_config.pop(id)

def setNewResources(id, data: Tuple):
    workflow_config[id]['new_resources'] = data

def setResourceRequestPending(id, pending: bool):
    workflow_config[id]['resource_request_pending'] = pending

def isResourceRequestPending(id):
    return workflow_config.get(id, {}).get('resource_request_pending', False)

def setWorkflowComplete(id, isComplete: bool):
    workflow_config[id]['complete'] = isComplete


def getClientInputs_Plain(wf_id, input: Tuple, ind):
    """
    Handler for Plain SeisSol workflows.

    Plain workflows use workflowConfig array for pre-planned iteration configs.
    Input format: (cohesion_value, hosts) where cohesion_value is numeric/string scalar.
    """
    mesh = getWorkflowConfig(wf_id)['mesh']
    sim = getWorkflowConfig(wf_id)['sim']
    workflow_config_array = getWorkflowConfig(wf_id)['workflowConfig']

    # Extract from pre-planned workflowConfig array
    chains = workflow_config_array[ind]['chains']
    tinyda_iterations = workflow_config_array[ind]['tinydaIterations']

    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, sim, MOLDABLE, chains)

    if not SIMULATE and len(hosts.get('on-prem', [])) != 0:
        port = getWorkflowOnpremPort()
    else:
        port = 4242

    return {
        'wf_id': wf_id,
        'cohesion': input[0],  # Simple scalar value
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port
    }, hosts, sim


def getClientInputs_LA(wf_id, input: Tuple, ind):
    """
    Handler for License-Aware SeisSol workflows.

    LA workflows can use either:
    - workflowConfig array for moldable scheduling (chains vary per iteration)
    - constraints for static allocation (chains constant across iterations)

    Input format: (cohesion_value, hosts) where cohesion_value is numeric scalar.
    """
    mesh = getWorkflowConfig(wf_id)['mesh']
    sim = getWorkflowConfig(wf_id)['sim']

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

    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, sim, MOLDABLE, chains)

    if not SIMULATE and len(hosts.get('on-prem', [])) != 0:
        port = getWorkflowOnpremPort()
    else:
        port = 4242

    return {
        'wf_id': wf_id,
        'cohesion': input[0],  # Simple scalar value
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port
    }, hosts, sim


def getClientInputs_HPO(wf_id, input: Tuple, ind):
    """
    Handler for HPO (Hyperparameter Optimization) workflows.

    HPO workflows use dynamic dict input for iteration config.
    Input format: (config_dict, hosts) where config_dict has epochs/next_trials keys.
    """
    mesh = getWorkflowConfig(wf_id)['mesh']
    sim = getWorkflowConfig(wf_id)['sim']

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
    from elastiflow.config.constants_HPO import MOLDABLE as HPO_MOLDABLE, SIMULATE as HPO_SIMULATE
    from elastiflow.config.constants_HPO import FREE_RESOURCES as HPO_FREE_RESOURCES
    from elastiflow.config.constants_HPO import RESOURCE_REQUEST_TIMEOUT as HPO_TIMEOUT
    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, sim, HPO_MOLDABLE, chains,
                                               HPO_FREE_RESOURCES, HPO_TIMEOUT)

    if not HPO_SIMULATE and len(hosts.get('on-prem', [])) != 0:
        port = getWorkflowOnpremPort()
    else:
        port = 4242

    return {
        'wf_id': wf_id,
        'cohesion': input[0],  # Full dict (HPO config)
        'hosts': alloc_hosts,
        'chains': chains,
        'tinyda_iterations': tinyda_iterations,
        'mesh': mesh,
        'port': port
    }, hosts, sim


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
    sim = cfg['sim']
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

    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, sim, MOLDABLE, chains)

    if not SIMULATE and len(hosts.get('on-prem', [])) != 0:
        port = getWorkflowOnpremPort()
    else:
        port = 4242

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
    }, hosts, sim


def getClientInputs(wf_id, input: Tuple, ind):
    """
    Dispatcher function that routes to type-specific input handlers.

    Routes to:
    - getClientInputs_Plain() for Plain SeisSol workflows
    - getClientInputs_LA() for License-Aware SeisSol workflows
    - getClientInputs_HPO() for HPO workflows

    Workflow type is detected once at initialization and cached in workflow_config.
    """
    workflow_type = getWorkflowConfig(wf_id).get('workflow_type', 'PLAIN')

    if workflow_type == 'PLAIN':
        return getClientInputs_Plain(wf_id, input, ind)
    elif workflow_type == 'LA':
        return getClientInputs_LA(wf_id, input, ind)
    elif workflow_type == 'HPO':
        return getClientInputs_HPO(wf_id, input, ind)
    elif workflow_type == 'PLAIN_ADAPTIVE':
        return getClientInputs_PlainAdaptive(wf_id, input, ind)
    else:
        raise ValueError(f"Unknown workflow type: {workflow_type} for workflow {wf_id}")


# hosts = {'on-prem': {}, 'reserved': {name: (n, [ips])}, 'on-demand': {}}
# cur_hosts = {name: [ips]}
def getHostsForIteration(wf_id, hosts: dict, ind, sim, moldable, chains=0,
                         free_resources=None, request_timeout=None):
    if free_resources is None:
        free_resources = FREE_RESOURCES
    if request_timeout is None:
        request_timeout = RESOURCE_REQUEST_TIMEOUT

    count = 0
    cur_hosts = {}
    for cluster in ['on-prem', 'reserved', 'on-demand']:
        for instance in hosts[cluster]:
            nodes = hosts[cluster][instance] # (count, [ips])
            match cluster:
                case 'on-prem':
                    cur_hosts[instance] = nodes[1][:1] * nodes[0]
                case 'reserved' | 'on-demand':
                    # Executor should ONLY use IPs provided by scheduler
                    # Scheduler has already allocated all instances based on budget/deadline
                    cur_hosts[instance] = cur_hosts.get(instance, []) + nodes[1]
            count += nodes[0]

    # chains = getWorkflowConfig(wf_id)['workflowConfig'][ind]['chains']
    chains = chains # for actula dynamic workflows we get the chains from the output instead of the config
    # Moldable allocation
    if moldable:
        # NOTE: dicts are mutable and are passed by reference
        # request more resources
        if count < chains:
            n = chains - count if chains - count < 4 else 3
            print(f'New resource request: {wf_id} requesting {n} resources for iteration {ind+1}')
            return sendAndFetchResponse(wf_id, ExecutorRequest.REQUEST_RESOURCE.value, hosts, ind, cur_hosts, n, chains, request_timeout)
        # Free resources
        elif free_resources and count > chains:
            n = count - chains
            print(f'Free resource request: {wf_id} requesting to free {n} resources for iteration {ind+1}')
            return sendAndFetchResponse(wf_id, ExecutorRequest.FREE_RESOURCE.value, hosts, ind, cur_hosts, n, chains, request_timeout)

    return cur_hosts, hosts

def sendAndFetchResponse(wf_id, request_type, hosts, ind, cur_hosts, n, chains, request_timeout=None):
    if request_timeout is None:
        request_timeout = RESOURCE_REQUEST_TIMEOUT

    config = getWorkflowConfig(wf_id)

    # Get tinyda_iterations from workflowConfig or constraints (for all workflow types)
    if 'workflowConfig' in config:
        tinyda_iterations = config['workflowConfig'][ind]['tinydaIterations']
    else:
        tinyda_iterations = config.get('constraints', {}).get('tinydaIterations', 1)

    # OLD CODE (commented out, broke scheduler):
    # config = getWorkflowConfig(wf_id)['workflowConfig']
    # "tinyda-iterations": config[ind]['tinydaIterations'] + 1,

    request = {
        "request": request_type,
        "wf-id": wf_id,
        "count": n,
        "iteration": ind,
        "tinyda-iterations": tinyda_iterations,  # Fixed: now works for Plain/LA/HPO
        "chains": chains
    }

    sim = getWorkflowConfig(wf_id)['sim']
    backend = backend_for(sim)

    # Mark request as pending so processNewResourcesHPO knows we're waiting.
    # Late responses (arriving after timeout) are discarded when pending=False.
    setNewResources(wf_id, None)  # Clear any stale data from previous iterations
    setResourceRequestPending(wf_id, True)

    rt_label = 'grow' if request_type == ExecutorRequest.REQUEST_RESOURCE.value else 'shrink'
    t_engine_request_sent = time.time()
    if sim:
        request['request-time'] = backend.now()
    backend.resource_requests.send(request)

    # Wait for response until timeout
    resources = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
    req_type = ExecutorRequest.FREE_RESOURCE.value # Only because less computation than merge
    start_time, timeout = backend.now(), 30 + request_timeout # 30 sec network latency
    outcome = 'timed_out'
    t_engine_reply_received = ''
    while True:
        backend.sleep(5)
        if getWorkflowConfig(wf_id).get('new_resources', None):
            req_type, resources = getWorkflowConfig(wf_id).get('new_resources')
            t_engine_reply_received = time.time()
            outcome = 'granted'
            setNewResources(wf_id, None)
            setResourceRequestPending(wf_id, False)
            break
        if backend.now() - start_time > timeout:
            print(f'Timeout reached for {wf_id}. Continuing with available resources')
            setNewResources(wf_id, None)
            setResourceRequestPending(wf_id, False)  # Reject late responses
            break
    negotiation_log.log('executor',
                        wf_id=wf_id, iter_idx=ind, request_type=rt_label,
                        requested_count=n,
                        t_engine_request_sent=t_engine_request_sent,
                        t_engine_reply_received=t_engine_reply_received,
                        outcome=outcome)
    if req_type == ExecutorRequest.FREE_RESOURCE.value:
        return freeResources(resources, hosts, cur_hosts)
    else:
        return mergeNewResources(resources, hosts, wf_id, ind, sim)

def mergeNewResources(new_resources, hosts, wf_id, ind, sim):
    # Merge new_resources with total hosts
    for cluster in ['on-prem', 'reserved', 'on-demand']:
        for instance in new_resources[cluster]:
            current =  hosts[cluster].get(instance, (0, []))
            hosts[cluster][instance] = (
                current[0] + new_resources[cluster][instance][0],
                current[1] + new_resources[cluster][instance][1]
            )
    return getHostsForIteration(wf_id, hosts, ind, sim, False)

def freeResources(to_free_resources, hosts, cur_hosts):
    # Remove elements in new_resources from total and allocated hosts
    for cluster in ['on-prem', 'reserved', 'on-demand']:
        for instance in to_free_resources[cluster]:
            n, ips = to_free_resources[cluster][instance]
            match cluster:
                case 'on-prem':
                    hosts[cluster][instance] = (
                        hosts[cluster][instance][0] - n,
                        hosts[cluster][instance][1]
                    )
                    cur_hosts[instance] = cur_hosts[instance][:-n]
                case 'reserved':
                    hosts[cluster][instance] = (
                        hosts[cluster][instance][0] - n,
                        list(set(hosts[cluster][instance][1]) - set(ips))
                    )
                    cur_hosts[instance] = list(set(cur_hosts[instance]) - set(ips))
                case 'on-demand':
                    # Remove from tracking only — scheduler handles instance termination
                    # (termination moved to scheduler's freeResources to avoid double-terminate
                    # and ~5 min executor blocking)
                    ips_to_free = hosts[cluster][instance][1][-n:]
                    cur_hosts[instance] = list(set(cur_hosts[instance]) - set(ips_to_free))
                    hosts[cluster][instance] = (
                        hosts[cluster][instance][0] - n,
                        hosts[cluster][instance][1][:-n]
                    )
    return cur_hosts, hosts
                                                              
