from enum import Enum, auto
from abc import ABC
import sys
import time
from typing import List
import subprocess
import yaml 
import numpy as np

from config.constants import SIMULATE
from utils.sim import getTime
from utils.exec_sched import getClientInputs, getWorkflowConfig, setWorkflowComplete

from .steep_variables import Variable

class ActionType(Enum):
    ForEach = auto()
    Execute = auto()
    Include = auto()

class Action(ABC):
    def execute():
        pass

class ForEachAction(Action):

    # we need to have an init in order to subscribe to the important variables
    def __init__(self, wf_id, input_parameter: Variable, enumerator: Variable, output_parameter: Variable=None, yieldToInput: Variable=None, actions: List[Action] = []):
        self.type = ActionType.ForEach

        # Initialize parameters
        self.input_parameter = input_parameter # List of all inputs
        self.enumerator = enumerator # Current input value
        self.yieldToInput = yieldToInput # The output that must be passed back as input - the same as output from execute action
        self.output_parameter = output_parameter # Output of all iterations
        self.actions = actions
        self.wf_id = wf_id

        # Subscribe to variables - prepare for next iteration when there is a change in yieldToInput
        self.yieldToInput and self.yieldToInput.subscribe(self.prepareOutput)
            
    def add_to_input(self, value):
        self.input_parameter.append(value)
        self.execute(self.hosts)
    
    def prepareOutput(self):
        outputValue = self.yieldToInput.getValue()
        self.add_to_input(outputValue)
        self.output_parameter and self.output_parameter.append(outputValue)
    
    def execute(self, hosts):
        # Add value from input to enumerator
        self.hosts = hosts
        new_input = self.input_parameter.getValue()
        if isinstance(new_input, tuple):
            self.hosts = hosts
            self.enumerator.append(new_input)
        else:
            self.enumerator.append((new_input, hosts))
        return self.hosts

class ExecuteAction(Action):

    def __init__(self, wf_id, service, input_parameters : List[Variable] = [], output_parameters: List[Variable] = []):
        self.type = ActionType.Execute
        self.service = "/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/scripts/simulate-tinyda-seissol.py" 
        self.output_parameters = output_parameters
        self.input_parameters = input_parameters # Technically the enumerator of the for action
        self.wf_id = wf_id
        
        # Init iterator for dynamic iterations
        self.workflow_iterations = getWorkflowConfig(self.wf_id)["workflowIterations"]
        self.workflow_iterator = 0
        
        for parameter in self.input_parameters:
            parameter.subscribe(self.check_readiness)
    
    def check_readiness(self):
        # value is discarded for execute actions
        ready = True
        for parameter in self.input_parameters:
            ready = ready and parameter.value_list
        if ready:
            self.execute()
    
    def execute(self):
        # We assume there is only 1 input in the list - (cohesion, hosts) for next wf iteration
        input = self.input_parameters[0].getValue()
        # We assume the output will always be 1 element - cohesion value for next iteration
        result = None
        ind = self.workflow_iterator # workflow iterations
        try:
            args, hosts, sim = getClientInputs(self.wf_id, input, ind)
            # print(f'Executing Action with input {args}')
            start = time.time()
            #args["prior_output"] = self.output_parameters[-1]
            print(f"{self.wf_id} Workflow iteration {self.workflow_iterator} started at {start}")
            result = subprocess.run([sys.executable, self.service, str(args)], check=True, capture_output=True, text=True)           
            if sim or SIMULATE:
                result = eval(result.stdout)
                runtime = float(result['runtime'])
                deadline = getWorkflowConfig(self.wf_id)['deadline']
                sleep_time = min(runtime, max(deadline - getTime(sim), 0))
                (sim or time).sleep(sleep_time + 7.7) # NOTE: Executor overhead
                if sleep_time < runtime:
                    print(f'Killing workflow {self.wf_id}')
                    return
            else:
                print(f"{self.wf_id} Workflow iteration {self.workflow_iterator} started at {time.time()}")
                output_lines = result.stdout.splitlines()  # Split the output into lines
                input_value = output_lines[0]
                print(input_value)
                result = eval(input_value)
            # if on-prem, add back the port that was assigned for the next iteration or another workflow to use
            if not SIMULATE and len(args['hosts'].get('on-prem', [])) != 0:
                port = args['port']
                with open("/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/ports.yaml", "r") as f:
                    data = yaml.safe_load(f)
                ports = data.get("onprem_ports", [])
                ports.append(port)
                data["onprem_ports"] = ports
                with open("/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/ports.yaml", "w") as f:
                    yaml.safe_dump(data, f) 
            self.workflow_iterator = self.workflow_iterator + 1 # increment the iterator for the workflow
            print(f"[DEBUG] {self.wf_id}: iteration {self.workflow_iterator}/{self.workflow_iterations}")
            if self.workflow_iterator < self.workflow_iterations:
                # Handle output based on workflow type
                print(f"[DEBUG] {self.wf_id}: Continuing to next iteration")
                workflow_type = getWorkflowConfig(self.wf_id).get('workflow_type', 'PLAIN')
                if workflow_type == 'HPO':
                    # HPO returns full config dict for next iteration
                    output_value = result.get('config', result)
                else:
                    # Plain and LA return numeric cohesion value
                    output_value = float(result['cohesion'])

                self.output_parameters[0].append((output_value, hosts))
            else:
                # Else condition terminates the execution since a new input value is not appended
                print(f"[DEBUG] {self.wf_id}: Final iteration, calling setWorkflowComplete")
                setWorkflowComplete(self.wf_id, True)
                print(f"[DEBUG] {self.wf_id}: setWorkflowComplete called, complete={getWorkflowConfig(self.wf_id)['complete']}")
        except subprocess.CalledProcessError as e:
            print("Error occurred while executing the script: " + e.stderr)
        
