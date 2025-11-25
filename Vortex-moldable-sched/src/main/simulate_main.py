import simulus
from scheduler.priority_priority import PriorityPriority
from scheduler.priority_fcfs import PriorityFCFS
from scheduler.earliest_deadline_edf import EarliestDeadlineEDF
from scheduler.earliest_deadline_fcfs import EarliestDeadlineFCFS
from scheduler.fcfs_optimized import FCFS_Optimized
from scheduler.heft_heft_req import HEFT_HEFT_REQ
from scheduler.heft_fcfs_req import HEFT_FCFS_REQ
from scheduler.fcfs_scheduler import FCFS_Scheduler
from wf_queue.redis_queue import Redis_Queue
from scripts.dispatcher import dispatcher

# Create a queue for communication
queue = Redis_Queue(queue_name='wf-queue')
finish_queue = Redis_Queue(queue_name='completed-jobs-queue')
resource_request_queue = Redis_Queue(queue_name='resource-request-queue')
sched = FCFS_Scheduler(queue, finish_queue, resource_request_queue, sort_key='runtime_per_iteration') # runtime_per_iteration
# sched = HEFT_FCFS_REQ(queue, finish_queue, resource_request_queue)
# sched = HEFT_HEFT_REQ(queue, finish_queue, resource_request_queue)
# sched = FCFS_Optimized(queue, finish_queue, resource_request_queue, sort_key='runtime_per_iteration')
# sched = EarliestDeadlineFCFS(queue, finish_queue, resource_request_queue, sort_key='runtime_per_iteration')
# sched = EarliestDeadlineEDF(queue, finish_queue, resource_request_queue, sort_key='runtime_per_iteration')
# sched = PriorityFCFS(queue, finish_queue, resource_request_queue)
# sched = PriorityPriority(queue, finish_queue, resource_request_queue)

# NOTE: Assume network latency is negligible
sim_dispatcher = simulus.simulator('dispatcher')
sim_sched = simulus.simulator('scheduler')

wf_mb = sim_sched.mailbox('wf_mb', 1)
completed_jobs_mb = sim_sched.mailbox('completed_jobs_mb', 1)
# Executor writes to resource request mb, and scheduler reads it
resource_request_mb = sim_sched.mailbox('resource_request_mb', 1)

# P1: Dispatcher sleepes for gaussian time and writes to wf mailbox
sim_dispatcher.process(dispatcher, sim_dispatcher, 'wf_mb')
# P2. Sceduler reads wf-mb at reguler intervals, allocates resources, creates a mb and an exec process
# PN: Exec process sleeps appropriately and writes to completed_jobs_mb
sim_sched.process(sched.run, sim_sched, wf_mb, resource_request_mb, name='fcfs_sched')
# P3: Scheduler reads MB2 and frees resources
sim_sched.process(sched.processJobCompletion, sim_sched, completed_jobs_mb, name='job_completion_sched')

g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=True)
g.run(show_runtime_report=True)