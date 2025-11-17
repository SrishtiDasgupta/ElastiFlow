import time
from typing import List, Tuple
import re

from config.constants import FREE_RESOURCES, MOLDABLE, RESOURCE_REQUEST_TIMEOUT, SIMULATE
from .sim import getTime
from .request import ExecutorRequest, getConfig, sendRequest
from scripts.create_instance import createInstance, deleteInstanceFromIp

import yaml

workflow_config = {}


def getWorkflowOnpremPort():
    with open("/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/ports.yaml", "r") as f:
        data = yaml.safe_load(f)

    ports = data.get("onprem_ports", [])
    if not ports:
        raise ValueError("No ports to pop")

    popped = ports.pop(0)
    data["onprem_ports"] = ports

    with open("/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/ports.yaml", "w") as f:
        yaml.safe_dump(data, f) # default = block style

    return popped

def setWorkflowConfig(id, workflow, sim, deadline):
    workflow_config[id] = workflow['config']
    workflow_config[id]['sim'] = sim
    workflow_config[id]['deadline'] = deadline
    workflow_config[id]['complete'] = False

def getWorkflowConfig(id):
    return workflow_config[id]

def removeWorkflowConfig(id):
    return workflow_config.pop(id)

def setNewResources(id, data: Tuple):
    workflow_config[id]['new_resources'] = data

def setWorkflowComplete(id, isComplete: bool):
    workflow_config[id]['complete'] = isComplete

def getClientInputs(wf_id, input: Tuple[float, List[str]], ind):
    config = getWorkflowConfig(wf_id)['workflowConfig']
    mesh = getWorkflowConfig(wf_id)['mesh']
    sim = getWorkflowConfig(wf_id)['sim']
    
    alloc_hosts, hosts = getHostsForIteration(wf_id, input[1], ind, sim, MOLDABLE)
    if not SIMULATE and len(hosts.get('on-prem', [])) != 0:
        port = getWorkflowOnpremPort()#
    else:
        port = 4242 # for cloud, this can be hardcoded since executor runs in isolated instances 
    return {
        'wf_id': wf_id,
        'cohesion': input[0],
        'hosts': alloc_hosts,
        'chains': config[ind]['chains'],
        'tinyda_iterations': config[ind]['tinydaIterations'],
        'mesh': mesh,
        'port': port
    }, hosts, sim


# hosts = {'on-prem': {}, 'reserved': {name: (n, [ips])}, 'on-demand': {}}
# cur_hosts = {name: [ips]}
def getHostsForIteration(wf_id, hosts: dict, ind, sim, moldable):
    count = 0
    cur_hosts = {}
    for cluster in ['on-prem', 'reserved', 'on-demand']:
        for instance in hosts[cluster]:
            nodes = hosts[cluster][instance] # (count, [ips])
            match cluster:
                case 'on-prem':
                    cur_hosts[instance] = nodes[1][:1] * nodes[0]
                case 'reserved':   
                    cur_hosts[instance] = cur_hosts.get(instance, []) + nodes[1]
                case 'on-demand':
                    onDemandIps = createInstance(instance, nodes[0] - len(nodes[1]), sim)
                    cur_hosts[instance] = cur_hosts.get(instance, []) + nodes[1] + onDemandIps
                    # If new instances are created, update available hosts
                    if onDemandIps:
                        hosts[cluster][instance] = (nodes[0], nodes[1] + onDemandIps)
            count += nodes[0]
    chains = getWorkflowConfig(wf_id)['workflowConfig'][ind]['chains']
   
    # Moldable allocation
    if moldable:
        # NOTE: dicts are mutable and are passed by reference
        # request more resources
        if count < chains:
            n = chains - count
            print(f'New resource request: {wf_id} requesting {n} resources for iteration {ind+1}')
            return sendAndFetchResponse(wf_id, ExecutorRequest.REQUEST_RESOURCE.value, hosts, ind, cur_hosts, n, chains)
        # Free resources
        elif FREE_RESOURCES and count > chains:
            n = count - chains
            print(f'Free resource request: {wf_id} requesting to free {n} resources for iteration {ind+1}')
            return sendAndFetchResponse(wf_id, ExecutorRequest.FREE_RESOURCE.value, hosts, ind, cur_hosts, n, chains)
    
    return cur_hosts, hosts
    
def sendAndFetchResponse(wf_id, request_type, hosts, ind, cur_hosts, n, chains):

    config = getWorkflowConfig(wf_id)['workflowConfig']
    request = {
        "request":request_type,
        "wf-id": wf_id,
        "count": n,
        "iteration": ind,
        "tinyda-iterations": config[ind]['tinydaIterations'] + 1,
        "chains": chains
    }
    
    sim = getWorkflowConfig(wf_id)['sim']
    if sim:
        request['request-time'] = sim.now
        sim.sync().send(sim, 'resource_request_mb', str(request))
    else:
        sendRequest(getConfig('scheduler'), getConfig('resource-request-port'), request)
    
    # Wait for response until timeout
    resources = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
    req_type = ExecutorRequest.FREE_RESOURCE.value # Only because less computation than merge
    start_time, timeout = getTime(sim), 30 + RESOURCE_REQUEST_TIMEOUT # 30 sec network latency
    while True:
        (sim or time).sleep(5)
        if getWorkflowConfig(wf_id).get('new_resources', None):
            req_type, resources = getWorkflowConfig(wf_id).get('new_resources')
            setNewResources(wf_id, None)
            break
        if getTime(sim) - start_time > timeout:
            print(f'Timeout reached for {wf_id}. Continuing with available resources')
            break
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
                    # Terminate last n instances
                    deleteInstanceFromIp(hosts[cluster][instance][1][-n:])
                    cur_hosts[instance] = list(set(cur_hosts[instance]) - set(hosts[cluster][instance][1][-n:]))
                    hosts[cluster][instance] = (
                        hosts[cluster][instance][0] - n,
                        hosts[cluster][instance][1][:-n]
                    )
    return cur_hosts, hosts
