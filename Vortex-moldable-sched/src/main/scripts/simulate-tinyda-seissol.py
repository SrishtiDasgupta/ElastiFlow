import sys
from time import sleep
import json
import yaml
import heapq
import numpy as np

from speedup import getRuntime

def fetchWorkflow(i):
    file_name= "/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/sample_workflows/data"+ str(i) + ".yaml"
    with open(file_name, 'r') as stream:
        try:
            workflow = yaml.safe_load(stream)
            # print(f" why {workflow}")
        except yaml.YAMLError as exc:
            workflow = None
            print(f" error: {exc}")
        return workflow

def generateSampleRequest(workflow, workflow_iterator, number_of_hosts):
    chains = workflow['config']['workflowConfig'][workflow_iterator]['chains']
    cohesion = 3
    tinydaIterations = workflow['config']['workflowConfig'][workflow_iterator]['tinydaIterations']
    host_ips =  ["0.0.0.0" for x in range(number_of_hosts)]
    hosts = {"on-prem": host_ips}
    return {"chains": chains, "cohesion": cohesion, "hosts": hosts, "tinyda_iterations": tinydaIterations}

def simulationFunction(sleep_time):
    # print(f"Thread starting, will sleep for {sleep_time} seconds.")
    sleep(sleep_time)  # Put the thread to sleep
    # print(f'simulating finsihed')
    
def collectHostRuntimes(hosts, mesh): # returns a list of runtimes for all the hosts
    return {host: getRuntime(1, mesh, host) for host in hosts}

def parallelSimulation(hosts, chains, tinyda_iterations, host_count, mesh):
    host_runtimes = collectHostRuntimes(hosts, mesh)
    heap = []
    # Use a max heap - assign additional node to the slowest runtime
    # initial alloc - push 1 node for each chain
    to_be_pushed = chains
    for host in hosts:
        n = min(len(hosts[host]), to_be_pushed)
        for i in range(n):
            heapq.heappush(heap, (-host_runtimes[host], host, 1))
        to_be_pushed -= n
        hosts[host] = [] if n == len(hosts[host]) else hosts[host][n:]
        if to_be_pushed == 0: break
    
    # For each available node, add it to the highest runtime
    for host in hosts:
        for inst in hosts[host]:
            runtime, name, nodes = heapq.heappop(heap)
            if host_runtimes[host] > host_runtimes[name]: name = host
            new_runtime = getRuntime(nodes + 1, mesh, name)
            heapq.heappush(heap, (-new_runtime, name, nodes+1))
        
    return (-heap[0][0] * tinyda_iterations)

def sequentialSimulation(hosts, chains, tinydaIterations, mesh):
    runtimes = collectHostRuntimes(hosts, mesh) # {name: runtime}
    heap = []
    hostLength = 0
    for host in hosts:
        n = len(hosts[host])
        hostLength += n
        for i in range(n):
            heapq.heappush(heap, (runtimes[host], host)) # initial runtimes
    # print(runtimes)
    extraChains = chains - hostLength
    while extraChains:
        runtime, name = heapq.heappop(heap)
        heapq.heappush(heap, (runtime + runtimes[name], name))
        extraChains = extraChains - 1

    return max(heap)[0] * tinydaIterations

        
def simulate(t): # two cases for each iteration, one is when there are enough resources for each chain which means all run in parallel, and second is when number of hosts is less than chains
    chains = t['chains']
    tinydaIterations = t['tinyda_iterations'] + 2
    hosts = t['hosts']
    mesh = t['mesh']
    count = 0
    for name in hosts:
        count += len(hosts[name])

    if count >= t['chains']:
        sleep_time = parallelSimulation(hosts, chains, tinydaIterations, count, mesh)
    else:
        sleep_time = sequentialSimulation(hosts, chains, tinydaIterations, mesh)
    print(str({"cohesion":2.13, "runtime":sleep_time}))
    # return sleep_time # returning sleeptime to simulate the workflows manually
    # print(f'simulating a run for {sleep_time} secs')
    # simulationFunction(sleep_time)
    # print(f'simulating a run for {sleep_time} secs')

def validateRuntimes():
    for i in range(1, 21): # Comment out this loop when running it normally
        workflow = fetchWorkflow(i)
        workflow_runtime = 0.0
        for j in range(len(workflow['config']['workflowConfig'])):
            request = generateSampleRequest(workflow, j, workflow['config']['workflowConfig'][0]['chains'])
            # print(request)
            runtime = simulate(request)
            workflow_runtime += runtime
        print(f"{workflow['id']} has runtime: {workflow_runtime}")
    

if __name__ == "__main__":
    val = sys.argv[1:] 
    # val = ["{'cohesion': 3, 'hosts': {'on-prem': ['1', '2', '3']}, 'chains': 4, 'tinyda_iterations': 12, 'mesh': 1000}"]
    request = eval(val[0])
    simulate(request)
    # validateRuntimes() # validate the simulus results but directly running the sample workflows
    

