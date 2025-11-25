import simulus
from scheduler.fcfs_scheduler_HPO import FCFS_Scheduler_HPO
from scheduler.fcfs_optimized_HPO import FCFS_Optimized_HPO
from wf_queue.redis_queue import Redis_Queue
from scripts.dispatcher_HPO import dispatcher

# Create a queue for communication
queue = Redis_Queue(queue_name='hpo-wf-queue')
finish_queue = Redis_Queue(queue_name='hpo-completed-jobs-queue')
resource_request_queue = Redis_Queue(queue_name='hpo-resource-request-queue')

# Choose scheduler: Static or Moldable
SCHEDULER_MODE = 'static'  # Change to 'moldable' for second run

if SCHEDULER_MODE == 'static':
    sched = FCFS_Scheduler_HPO(queue, finish_queue, resource_request_queue)
    print("Running HPO Static Scheduler Simulation")
else:
    sched = FCFS_Optimized_HPO(queue, finish_queue, resource_request_queue)
    print("Running HPO Moldable Scheduler Simulation")

# NOTE: Assume network latency is negligible
sim_dispatcher = simulus.simulator('dispatcher')
sim_sched = simulus.simulator('scheduler')

wf_mb = sim_sched.mailbox('wf_mb', 1)
completed_jobs_mb = sim_sched.mailbox('completed_jobs_mb', 1)
# Executor writes to resource request mb, and scheduler reads it
resource_request_mb = sim_sched.mailbox('resource_request_mb', 1)

# P1: Dispatcher sleeps for specified time and writes to wf mailbox
sim_dispatcher.process(dispatcher, sim_dispatcher, 'wf_mb')
# P2: Scheduler reads wf-mb at regular intervals, allocates resources, creates a mb and an exec process
sim_sched.process(sched.run, sim_sched, wf_mb, resource_request_mb, name='hpo_fcfs_sched')
# P3: Scheduler reads MB2 and frees resources
sim_sched.process(sched.processJobCompletion, sim_sched, completed_jobs_mb, name='hpo_job_completion_sched')

g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=True)
g.run(show_runtime_report=True)
