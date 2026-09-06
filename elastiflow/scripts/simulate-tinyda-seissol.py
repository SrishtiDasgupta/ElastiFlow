import sys
from time import sleep
import json
import yaml
import heapq
import numpy as np

_SRC_MAIN = __import__('pathlib').Path(__file__).resolve().parents[1]

def fetchWorkflow(i):
    file_name= f"{_SRC_MAIN}/sample_workflows/data"+ str(i) + ".yaml"
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
    
from tinyda_runtime import iteration_runtime, collectHostRuntimes, parallelSimulation, sequentialSimulation  # noqa: E402,F401


def simulate(t):
    print(str(iteration_runtime(t)))


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
    

