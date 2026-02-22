from enum import Enum, auto
from abc import ABC
import sys
import os
import time
from typing import List
import subprocess
import yaml
import json

import os as _os

from config.constants_HPO import SIMULATE
from utils.sim import getTime
from utils.exec_sched import getClientInputs, getWorkflowConfig, setWorkflowComplete

_PORTS_YAML = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), 'config', 'ports.yaml')

from .steep_variables import Variable

MAX_ITERATIONS = 0

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
        self.service = service 
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
        next_trials=0
        input = self.input_parameters[0].getValue()
        # We assume the output will always be 1 element - cohesion value for next iteration
        result = None
        ind = self.workflow_iterator # workflow iterations
        try:
            print(f"[DEBUG] Getting client inputs for iteration {ind}")
            args, hosts, sim = getClientInputs(self.wf_id, input, ind)

            print(f"[DEBUG] Client inputs: {args}")
            print(f"[DEBUG] Service script: {self.service}")

            start = time.time()
            print(f"{self.wf_id} Workflow iteration {self.workflow_iterator} started at {start}")

            # Build command with JSON-formatted args (required by run_hpo.py)
            args_json = json.dumps(args)
            command = [sys.executable, self.service, args_json]
            print(f"[DEBUG] Executing command: {command[0]} {command[1]} '{args_json[:100]}...'")

            # Explicitly pass environment to subprocess (required for boto3 to find AWS credentials)
            env = os.environ.copy()
            result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)

            print(f"[DEBUG] Subprocess completed successfully")
            print(f"[DEBUG] STDOUT (first 500 chars): {result.stdout[:500]}")

            # Write full subprocess output to persistent log for diagnostics
            try:
                log_dir = "/fsx/hpo_logs"
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, f"{self.wf_id}_iter{self.workflow_iterator}.log")
                with open(log_path, 'w') as f:
                    f.write(f"=== COMMAND ===\n{' '.join(command)}\n\n")
                    f.write(f"=== STDOUT ===\n{result.stdout}\n\n")
                    f.write(f"=== STDERR ===\n{result.stderr}\n")
                print(f"[DEBUG] Full output written to {log_path}")
            except Exception as log_err:
                print(f"[WARN] Could not write log file: {log_err}")

            if sim or SIMULATE:
                result = eval(result.stdout)
                runtime = float(result['runtime'])
                deadline = getWorkflowConfig(self.wf_id)['deadline']
                sleep_time = min(runtime, max(deadline - getTime(sim), 0))
                (sim or time).sleep(sleep_time)
                if sleep_time < runtime:
                    print(f'Killing workflow {self.wf_id}')
                    return
            else:
                print(f"{self.wf_id} Workflow iteration {self.workflow_iterator} finished at {time.time()}")
                output_lines = result.stdout.splitlines()  # Split the output into lines
                # Line 0 is the wf_id printed by run_hpo.py; result dict starts at line 1
                parsed_result = None
                for line in output_lines:
                    line = line.strip()
                    if line.startswith('{'):
                        try:
                            parsed_result = json.loads(line)
                            if 'config' in parsed_result:
                                break
                        except json.JSONDecodeError:
                            continue
                if parsed_result is None or 'config' not in parsed_result:
                    # Log the raw output for debugging
                    print(f"[ERROR] No valid config in run_hpo.py output:")
                    for line in output_lines:
                        print(f"  | {line}")
                    if parsed_result and 'error' in parsed_result:
                        print(f"[ERROR] Runner error: {parsed_result['error']}")
                    setWorkflowComplete(self.wf_id, True)
                    return
                result = parsed_result
                next_trials = result['config']['next_trials']
                print(result)

                # Append per-iteration result to JSONL file for diagnostics
                try:
                    results_path = os.path.join("/fsx/hpo_logs", f"{self.wf_id}_results.jsonl")
                    os.makedirs("/fsx/hpo_logs", exist_ok=True)
                    with open(results_path, 'a') as f:
                        f.write(json.dumps({"iteration": self.workflow_iterator, "result": result, "ts": time.time()}) + "\n")
                except Exception as log_err:
                    print(f"[WARN] Could not write results JSONL: {log_err}")

            # if on-prem, add back the port that was assigned for the next iteration or another workflow to use
            if not SIMULATE and len(args['hosts'].get('on-prem', [])) != 0:       
                port = args['port']
                with open(_PORTS_YAML, "r") as f:
                    data = yaml.safe_load(f)
                ports = data.get("onprem_ports", [])
                ports.append(port)
                data["onprem_ports"] = ports
                with open(_PORTS_YAML, "w") as f:
                    yaml.safe_dump(data, f) 
            self.workflow_iterator += 1 # increment the iterator for the workflow
            if next_trials>0 and self.workflow_iterator<self.workflow_iterations:
                self.output_parameters[0].append((result['config'],  hosts))
            else:
                # Else condition terminates the execution since a new input value is not appended
                setWorkflowComplete(self.wf_id, True)
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] ============================================")
            print(f"[ERROR] Service script failed!")
            print(f"[ERROR] Command: {e.cmd}")
            print(f"[ERROR] Return code: {e.returncode}")
            print(f"[ERROR] STDOUT:\n{e.stdout}")
            print(f"[ERROR] STDERR:\n{e.stderr}")
            print(f"[ERROR] ============================================")
            setWorkflowComplete(self.wf_id, True)  # Mark as complete to prevent hanging
        except Exception as e:
            print(f"[ERROR] Unexpected error in workflow iteration: {e}")
            import traceback
            traceback.print_exc()
            setWorkflowComplete(self.wf_id, True)
        
