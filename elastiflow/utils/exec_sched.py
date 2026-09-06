import time
from typing import List, Tuple
import re

from elastiflow.config.constants import FREE_RESOURCES, RESOURCE_REQUEST_TIMEOUT
from .request import ExecutorRequest, getConfig, sendRequest
from . import negotiation_log

import yaml
import os

_PORTS_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'ports.yaml')

workflow_config = {}


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

def setWorkflowConfig(id, workflow, backend, deadline):
    workflow_config[id] = workflow['config']
    workflow_config[id]['constraints'] = workflow.get('constraints', {})  # Store constraints (chains, tinydaIterations)
    workflow_config[id]['backend'] = backend
    workflow_config[id]['deadline'] = deadline
    workflow_config[id]['complete'] = False

    # The use case (elastiflow/usecase.py) is recognised once from the plan and cached
    from elastiflow.usecase import for_plan
    use_case = for_plan(workflow_config[id])
    workflow_config[id]['use_case'] = use_case
    workflow_config[id]['workflow_type'] = use_case.workflow_type

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


def getClientInputs(wf_id, input: Tuple, ind):
    """The next iteration's inputs, read by the plan's use case (elastiflow/usecase.py)."""
    return getWorkflowConfig(wf_id)['use_case'].client_inputs(wf_id, input, ind)


# hosts = {'on-prem': {}, 'reserved': {name: (n, [ips])}, 'on-demand': {}}
# cur_hosts = {name: [ips]}
def getHostsForIteration(wf_id, hosts: dict, ind, backend, moldable, chains=0,
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

    backend = getWorkflowConfig(wf_id)['backend']

    # Mark request as pending so processNewResourcesHPO knows we're waiting.
    # Late responses (arriving after timeout) are discarded when pending=False.
    setNewResources(wf_id, None)  # Clear any stale data from previous iterations
    setResourceRequestPending(wf_id, True)

    rt_label = 'grow' if request_type == ExecutorRequest.REQUEST_RESOURCE.value else 'shrink'
    t_engine_request_sent = time.time()
    if backend.simulated:
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
        return mergeNewResources(resources, hosts, wf_id, ind, backend)

def mergeNewResources(new_resources, hosts, wf_id, ind, backend):
    # Merge new_resources with total hosts
    for cluster in ['on-prem', 'reserved', 'on-demand']:
        for instance in new_resources[cluster]:
            current =  hosts[cluster].get(instance, (0, []))
            hosts[cluster][instance] = (
                current[0] + new_resources[cluster][instance][0],
                current[1] + new_resources[cluster][instance][1]
            )
    return getHostsForIteration(wf_id, hosts, ind, backend, False)

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
                                                              
