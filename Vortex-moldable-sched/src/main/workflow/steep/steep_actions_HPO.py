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
from utils import negotiation_log

_PORTS_YAML = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), 'config', 'ports.yaml')

from .steep_variables import Variable

MAX_ITERATIONS = 0
MAX_RETRIES = 2          # Retry failed iterations (e.g. cloud SSH timeout)
RETRY_DELAY_SECS = 60   # Wait before retry (let cloud instances finish booting)

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
    
    def _run_subprocess(self, args, command, env):
        """Run the HPO subprocess with retry logic for transient cloud failures.
        Returns (parsed_result, sim_flag) on success, raises on permanent failure."""
        sim = args.get('_sim_ref')  # stored by caller
        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _t_iter_start = time.time()
                print(f"{self.wf_id} iteration {self.workflow_iterator} attempt {attempt}/{MAX_RETRIES} started at {_t_iter_start}")
                negotiation_log.log('iteration',
                                    wf_id=self.wf_id,
                                    iter_idx=self.workflow_iterator,
                                    attempt=attempt,
                                    t_iteration_started=_t_iter_start)
                result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)

                print(f"[DEBUG] Subprocess completed successfully")
                print(f"[DEBUG] STDOUT (first 500 chars): {result.stdout[:500]}")

                # Write full subprocess output to persistent log
                try:
                    log_dir = "/fsx/hpo_logs"
                    os.makedirs(log_dir, exist_ok=True)
                    log_path = os.path.join(log_dir, f"{self.wf_id}_iter{self.workflow_iterator}_attempt{attempt}.log")
                    with open(log_path, 'w') as f:
                        f.write(f"=== COMMAND ===\n{' '.join(command)}\n\n")
                        f.write(f"=== STDOUT ===\n{result.stdout}\n\n")
                        f.write(f"=== STDERR ===\n{result.stderr}\n")
                    print(f"[DEBUG] Full output written to {log_path}")
                except Exception as log_err:
                    print(f"[WARN] Could not write log file: {log_err}")

                if sim or SIMULATE:
                    return result, True  # caller handles sim path

                # Parse output for config
                output_lines = result.stdout.splitlines()
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

                if parsed_result is not None and 'config' in parsed_result:
                    return parsed_result, False  # success

                # No config — log and retry
                print(f"[ERROR] No valid config in run_hpo.py output (attempt {attempt}/{MAX_RETRIES}):")
                for line in output_lines:
                    print(f"  | {line}")
                if parsed_result and 'error' in parsed_result:
                    print(f"[ERROR] Runner error: {parsed_result['error']}")
                last_error = f"No valid config in output"

            except subprocess.CalledProcessError as e:
                print(f"[ERROR] Service script failed (attempt {attempt}/{MAX_RETRIES})!")
                print(f"[ERROR] Return code: {e.returncode}")
                print(f"[ERROR] STDOUT:\n{e.stdout}")
                print(f"[ERROR] STDERR:\n{e.stderr}")
                last_error = f"Subprocess exit code {e.returncode}"
                # Write error output to persistent log (instance may be terminated soon)
                try:
                    log_dir = "/fsx/hpo_logs"
                    os.makedirs(log_dir, exist_ok=True)
                    log_path = os.path.join(log_dir, f"{self.wf_id}_iter{self.workflow_iterator}_attempt{attempt}_FAILED.log")
                    with open(log_path, 'w') as f:
                        f.write(f"=== COMMAND ===\n{' '.join(command)}\n\n")
                        f.write(f"=== RETURN CODE ===\n{e.returncode}\n\n")
                        f.write(f"=== STDOUT ===\n{e.stdout}\n\n")
                        f.write(f"=== STDERR ===\n{e.stderr}\n")
                except Exception:
                    pass

            # Retry after delay (cloud instances may need time to become SSH-ready)
            if attempt < MAX_RETRIES:
                print(f"[RETRY] Waiting {RETRY_DELAY_SECS}s before retry {attempt+1}/{MAX_RETRIES}...")
                time.sleep(RETRY_DELAY_SECS)

        raise RuntimeError(f"{self.wf_id} iteration {self.workflow_iterator} failed after {MAX_RETRIES} attempts: {last_error}")

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

            # Build command with JSON-formatted args (required by run_hpo.py)
            args_json = json.dumps(args)
            command = [sys.executable, self.service, args_json]
            print(f"[DEBUG] Executing command: {command[0]} {command[1]} '{args_json[:100]}...'")

            # Explicitly pass environment to subprocess (required for boto3 to find AWS credentials)
            env = os.environ.copy()

            # Store sim ref for _run_subprocess
            args['_sim_ref'] = sim

            # Run with retry logic
            result, is_sim = self._run_subprocess(args, command, env)

            if is_sim:
                # Simulation path
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
        except Exception as e:
            print(f"[ERROR] ============================================")
            print(f"[ERROR] Workflow {self.wf_id} iteration {self.workflow_iterator} FAILED: {e}")
            import traceback
            tb = traceback.format_exc()
            print(tb)
            print(f"[ERROR] ============================================")
            # Write error to persistent log (instance may be terminated soon)
            try:
                log_dir = "/fsx/hpo_logs"
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, f"{self.wf_id}_iter{self.workflow_iterator}_ERROR.log")
                with open(log_path, 'w') as f:
                    f.write(f"=== WORKFLOW ERROR ===\n{e}\n\n=== TRACEBACK ===\n{tb}\n")
            except Exception:
                pass
            setWorkflowComplete(self.wf_id, True)  # Mark complete to prevent executor hanging
        
