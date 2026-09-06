import argparse
import simulus
from elastiflow.policies import resolve_hpo
from elastiflow.wf_queue.redis_queue import Redis_Queue
from elastiflow.scripts.dispatcher_HPO import dispatcher

# Parse arguments
parser = argparse.ArgumentParser(description='HPO Scheduler Simulation')
parser.add_argument('--mode', choices=['static', 'moldable'], default='static',
                    help='Scheduler mode: static or moldable (default: static)')
parser.add_argument('--algo', choices=['fcfs', 'edf'], default='fcfs',
                    help='Scheduling algorithm: fcfs or edf (default: fcfs)')
args = parser.parse_args()

SCHEDULER_MODE = args.mode
SCHEDULER_ALGO = args.algo

# Create a queue for communication
queue = Redis_Queue(queue_name='hpo-wf-queue')
finish_queue = Redis_Queue(queue_name='hpo-completed-jobs-queue')
resource_request_queue = Redis_Queue(queue_name='hpo-resource-request-queue')

# Select scheduler based on algo + mode, through the registry
sched = resolve_hpo(SCHEDULER_ALGO, SCHEDULER_MODE).load()(queue, finish_queue, resource_request_queue)
print(f"Running HPO {SCHEDULER_ALGO.upper()} {SCHEDULER_MODE.capitalize()} Scheduler Simulation")

# NOTE: Assume network latency is negligible
sim_dispatcher = simulus.simulator('dispatcher')
sim_sched = simulus.simulator('scheduler')

wf_mb = sim_sched.mailbox('wf_mb', 1)
completed_jobs_mb = sim_sched.mailbox('completed_jobs_mb', 1)
# Executor writes to resource request mb, and scheduler reads it
resource_request_mb = sim_sched.mailbox('resource_request_mb', 1)
from elastiflow.execution.backend import SimulatedBackend
from elastiflow.executor_HPO import executeWorkflowHPO, processNewResourcesHPO
from elastiflow.config.constants_HPO import COLD_START_TIME
from elastiflow.scripts.create_instance_HPO import simulated_worker_ip
backend_sched = SimulatedBackend(sim_sched, {'wf_mb': wf_mb, 'completed_jobs_mb': completed_jobs_mb, 'resource_request_mb': resource_request_mb}, execute=executeWorkflowHPO, on_resources=processNewResourcesHPO, cold_start=COLD_START_TIME, fake_ip=simulated_worker_ip, release_message='Simulated termination of {ips}')
backend_disp = SimulatedBackend(sim_dispatcher)   # sends by mailbox name from its own simulator

# P1: Dispatcher sleeps for specified time and writes to wf mailbox
sim_dispatcher.process(dispatcher, backend_disp)
# P2: Scheduler reads wf-mb at regular intervals, allocates resources, creates a mb and an exec process
sim_sched.process(sched.run, backend_sched, name='hpo_sched')
# P3: Scheduler reads MB2 and frees resources
sim_sched.process(sched.processJobCompletion, backend_sched, name='hpo_job_completion_sched')

g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=True)
g.run(show_runtime_report=True)
