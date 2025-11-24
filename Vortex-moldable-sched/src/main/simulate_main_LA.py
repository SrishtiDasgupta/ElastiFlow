
"""
SimPy Simulation Entry Point for License-Aware Schedulers

Runs license-aware schedulers in simulation mode using Simulus library.
All workflows require licenses (ANSYS, ABAQUS, or LSDYNA).

Supported schedulers:
  - LAMF: License-Aware Moldable FCFS
  - DDM-EDF: Deadline-Driven Moldable EDF
  - EDF-LA Baseline: Static EDF (no moldability)
"""

import simulus
#from scheduler.fcfs_optimized_LA import FCFS_Optimized_LA
#from scheduler.edf_optimized_LA import EDF_Optimized_LA
from scheduler.edf_hsm_LA import EDF_HSM_LA
#from scheduler.fcfs_scheduler_LA import FCFS_Scheduler_LA
#from scheduler.edf_scheduler_LA import EDF_Scheduler_LA
from wf_queue.redis_queue import Redis_Queue
from scripts.dispatcher_LA import dispatcher_LA
from config.constants_LA import (
    TOTAL_WORKFLOWS,
    LICENSE_DISTRIBUTION,
    LICENSE_POOL_CAPACITY,
    SIMULATE,
    MOLDABLE
)

# ============================================================================
# SCHEDULER SELECTION: Comment/Uncomment to select scheduler
# ============================================================================

# Option 1: LAMF (License-Aware Moldable FCFS)
#SCHEDULER_NAME = 'LAMF'
#SCHEDULER_DESC = 'License-Aware Moldable FCFS'

# Option 2: HSM (Hybrid Static-Moldable)
SCHEDULER_NAME = 'EDF-HSM'
SCHEDULER_DESC = 'Hybrid Static-Moldable: Static iteration 0, Moldable iterations 1-5'

# Option 3: EDF-LA Baseline (Static EDF without moldability)
#SCHEDULER_NAME = 'EDF-LA-Baseline'
#SCHEDULER_DESC = 'Static EDF with License Awareness (No Moldability)'

# Option 4: FCFS-LA Baseline (Static FCFS without moldability)
# SCHEDULER_NAME = 'FCFS-LA-Baseline'
# SCHEDULER_DESC = 'License-Aware FCFS (No Moldability)'

# ============================================================================

print('=' * 70)
print(f'{SCHEDULER_NAME} SIMULATION MODE')
print('=' * 70)
print(f'Scheduler: {SCHEDULER_DESC}')
print('Mode: SimPy Simulation')
print(f'Workflows: {TOTAL_WORKFLOWS}')
print('License Requirement: ALL workflows need licenses (ANSYS/ABAQUS/LSDYNA)')
print('')
print('License Distribution:')
for lic_type, proportion in LICENSE_DISTRIBUTION.items():
    print(f'  - {lic_type:8s}: {proportion*100:5.1f}% (capacity: {LICENSE_POOL_CAPACITY[lic_type]} tokens)')
print('')
print(f'Settings: SIMULATE={SIMULATE}, MOLDABLE={MOLDABLE}')
print('=' * 70)
print('')

# Create Redis queues for communication (still used in simulation)
queue = Redis_Queue(queue_name='wf-queue')
finish_queue = Redis_Queue(queue_name='completed-jobs-queue')
resource_request_queue = Redis_Queue(queue_name='resource-request-queue')

# ============================================================================
# SCHEDULER INSTANTIATION: Comment/Uncomment to match selection above
# ============================================================================

# Option 1: LAMF scheduler (MOLDABLE)
""" sched = FCFS_Optimized_LA(
    queue,
    finish_queue,
    resource_request_queue,
    sort_key='cost_per_iteration'
) """

# Option 2: HSM scheduler (Hybrid Static-Moldable: Static iter 0, Moldable iters 1-5)
sched = EDF_HSM_LA(
    queue,
    finish_queue,
    resource_request_queue,
    sort_key='cost_per_iteration'
)

# Option 3: EDF-LA Baseline scheduler (STATIC EDF - NO MOLDABILITY)
""" sched = EDF_Scheduler_LA(
    queue,
    finish_queue,
    resource_request_queue,
    sort_key='cost_per_iteration'
) """

# Option 4: FCFS-LA Baseline scheduler (STATIC FCFS - NO MOLDABILITY)
""" sched = FCFS_Scheduler_LA(
    queue,
    finish_queue,
    resource_request_queue,
    sort_key='cost_per_iteration'
) """

print('Initializing Simulus simulators...')

# Create simulators (NOTE: assume network latency is negligible)
sim_dispatcher = simulus.simulator('dispatcher')
sim_sched = simulus.simulator('scheduler')

# Create mailboxes for inter-process communication
wf_mb = sim_sched.mailbox('wf_mb', 1)
completed_jobs_mb = sim_sched.mailbox('completed_jobs_mb', 1)
resource_request_mb = sim_sched.mailbox('resource_request_mb', 1)

print('Creating simulation processes...')

# P1: Dispatcher sleeps for gaussian time and writes to wf mailbox
sim_dispatcher.process(dispatcher_LA, sim_dispatcher, 'wf_mb')
print('  [✓] Dispatcher process (loads license-aware workflows)')

# P2: Scheduler reads wf_mb at regular intervals, allocates compute + licenses
sim_sched.process(sched.run, sim_sched, wf_mb, resource_request_mb, name=f'{SCHEDULER_NAME.lower()}_sched')
print(f'  [✓] {SCHEDULER_NAME} scheduler process (dual-resource allocation)')

# P3: Scheduler reads completed_jobs_mb and frees compute + licenses
sim_sched.process(sched.processJobCompletion, sim_sched, completed_jobs_mb, name='job_completion_sched')
print('  [✓] Completion processor (releases compute + licenses)')

print('')
print('=' * 70)
print(f'STARTING {SCHEDULER_NAME} SIMULATION')
print('=' * 70)
print('')

# Synchronize simulators and run
g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=False)
g.run(show_runtime_report=True)

print('')
print('=' * 70)
print('SIMULATION COMPLETE')
print('=' * 70)
print('Check metrics output for:')
print('  - License utilization per pool (ANSYS/ABAQUS/LSDYNA)')
print('  - Dual-resource allocation efficiency')
print('  - Moldable scale-up/scale-down events')
print('  - Cost and deadline compliance')
if SCHEDULER_NAME == 'DDM-EDF':
    print('  - Deadline urgency distribution (CRITICAL/WARNING/SAFE/EXCESS)')
    print('  - Preemptive reallocation events')
print('=' * 70)
