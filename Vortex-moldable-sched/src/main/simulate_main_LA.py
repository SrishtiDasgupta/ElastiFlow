
"""
SimPy Simulation Entry Point for LAMF (License-Aware Moldable FCFS)

Runs LAMF scheduler in simulation mode using Simulus library.
All workflows require licenses (ANSYS, ABAQUS, or LSDYNA).
"""

import simulus
from scheduler.fcfs_optimized_LA import FCFS_Optimized_LA
from wf_queue.redis_queue import Redis_Queue
from scripts.dispatcher_LA import dispatcher_LA
from config.constants_LA import (
    TOTAL_WORKFLOWS,
    LICENSE_DISTRIBUTION,
    LICENSE_POOL_CAPACITY,
    SIMULATE,
    MOLDABLE
)

print('=' * 70)
print('LAMF SIMULATION MODE')
print('=' * 70)
print('Scheduler: License-Aware Moldable FCFS (LAMF)')
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

# Create LAMF scheduler with cost-based sorting
sched = FCFS_Optimized_LA(
    queue,
    finish_queue,
    resource_request_queue,
    sort_key='cost_per_iteration'
)

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

# P2: LAMF scheduler reads wf_mb at regular intervals, allocates compute + licenses
sim_sched.process(sched.run, sim_sched, wf_mb, resource_request_mb, name='lamf_sched')
print('  [✓] LAMF scheduler process (dual-resource allocation)')

# P3: Scheduler reads completed_jobs_mb and frees compute + licenses
sim_sched.process(sched.processJobCompletion, sim_sched, completed_jobs_mb, name='job_completion_sched')
print('  [✓] Completion processor (releases compute + licenses)')

print('')
print('=' * 70)
print('STARTING LAMF SIMULATION')
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
print('=' * 70)
