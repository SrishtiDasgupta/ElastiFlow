"""
Simulus simulation entry-point for license-aware schedulers.

Usage:
    python simulate_main_LA.py --scheduler {LAMF,EDF-LAMF,FCFS-ST-LA,EDF-ST-LA,EDF-HSM} \
                               --N <workflows> --seed <int> [--output-dir DIR]

When invoked without arguments, falls back to the legacy hard-coded behaviour
(EDF-HSM, TOTAL_WORKFLOWS from constants_LA.py) so existing callers keep
working.
"""
import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Argument parsing — must happen BEFORE the simulator config modules are
# imported, so we can override TOTAL_WORKFLOWS and seed np.random before any
# downstream code reads them.
# ---------------------------------------------------------------------------
_parser = argparse.ArgumentParser(
    description='Run a single (scheduler, N, seed) cell of the LA experiment.')
_parser.add_argument('--scheduler', default='EDF-HSM',
                     choices=['LAMF', 'EDF-LAMF', 'FCFS-ST-LA', 'EDF-ST-LA',
                              'EDF-HSM'],
                     help='Which LA scheduling policy to run.')
_parser.add_argument('--N', type=int, default=None,
                     help='Number of workflows. Overrides TOTAL_WORKFLOWS '
                          'in constants_LA.py.')
_parser.add_argument('--seed', type=int, default=42,
                     help='Seed for np.random (controls dispatch jitter).')
_parser.add_argument('--output-dir', default=None,
                     help='Directory to chdir into before running, so the '
                          'CSV / summary outputs of this cell do not '
                          'collide with other cells.')
_args, _unknown = _parser.parse_known_args()

# Seed before any module that uses np.random
import numpy as np  # noqa: E402
np.random.seed(_args.seed)

# Optionally chdir so output CSVs land in a per-cell dir
if _args.output_dir:
    os.makedirs(_args.output_dir, exist_ok=True)
    os.chdir(_args.output_dir)

# Override TOTAL_WORKFLOWS via monkey-patch on the constants module so every
# downstream `from config.constants_LA import TOTAL_WORKFLOWS` sees the new
# value. Must happen before any LA scheduler / dispatcher import.
from config import constants_LA  # noqa: E402
if _args.N is not None:
    constants_LA.TOTAL_WORKFLOWS = _args.N

# Iteration-0 OPTIM factor override (ablation for the f0 sensitivity sweep).
# OPTIM_FCFS_BFACTOR / DFACTOR are the same dict object shared by base
# constants.py (FCFS-LAMF) and constants_LA.py's `from .constants import *`
# (EDF-LAMF), so this in-place mutation reaches both LAMF schedulers. Must
# happen before any scheduler import below. Only affects iteration 0; HSM
# skips iter-0 and the static policies don't scale, so they are unaffected.
_iter0 = os.environ.get('LA_ITER0_FACTOR')
if _iter0 is not None:
    _f0 = float(_iter0)
    constants_LA.OPTIM_FCFS_BFACTOR[0] = _f0
    constants_LA.OPTIM_FCFS_DFACTOR[0] = _f0
    print(f'[ablation] LA iteration-0 OPTIM factor overridden to {_f0} '
          f'(BFACTOR[0]=DFACTOR[0])')

import simulus  # noqa: E402
from wf_queue.redis_queue import Redis_Queue  # noqa: E402
from scripts.dispatcher_LA import dispatcher_LA  # noqa: E402
from config.constants_LA import (  # noqa: E402
    TOTAL_WORKFLOWS, LICENSE_DISTRIBUTION, LICENSE_POOL_CAPACITY,
    SIMULATE, MOLDABLE,
)

# ---------------------------------------------------------------------------
# Conditional scheduler import based on --scheduler. Done lazily here so the
# wrong scheduler module never gets imported.
# ---------------------------------------------------------------------------
SCHEDULER_NAME = _args.scheduler
if SCHEDULER_NAME == 'LAMF':
    from scheduler.fcfs_optimized_LA import FCFS_Optimized_LA as _SchedClass
    SCHEDULER_DESC = 'License-Aware Moldable FCFS'
elif SCHEDULER_NAME == 'EDF-LAMF':
    from scheduler.edf_optimized_LA import EDF_Optimized_LA as _SchedClass
    SCHEDULER_DESC = 'License-Aware Moldable EDF'
elif SCHEDULER_NAME == 'FCFS-ST-LA':
    from scheduler.fcfs_scheduler_LA import FCFS_Scheduler_LA as _SchedClass
    SCHEDULER_DESC = 'Static FCFS with license awareness'
elif SCHEDULER_NAME == 'EDF-ST-LA':
    from scheduler.edf_scheduler_LA import EDF_Scheduler_LA as _SchedClass
    SCHEDULER_DESC = 'Static EDF with license awareness'
elif SCHEDULER_NAME == 'EDF-HSM':
    from scheduler.edf_hsm_LA import EDF_HSM_LA as _SchedClass
    SCHEDULER_DESC = 'Hybrid Static-Moldable EDF'
else:
    raise ValueError(f'unknown scheduler: {SCHEDULER_NAME}')

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
print('=' * 70)
print(f'{SCHEDULER_NAME} SIMULATION')
print('=' * 70)
print(f'Scheduler:   {SCHEDULER_DESC}')
print(f'Workflows:   {TOTAL_WORKFLOWS}')
print(f'Seed:        {_args.seed}')
print(f'CWD:         {os.getcwd()}')
print('License distribution:')
for lic, prop in LICENSE_DISTRIBUTION.items():
    print(f'  {lic:8s}: {prop*100:5.1f}%  '
          f'(capacity {LICENSE_POOL_CAPACITY[lic]} tokens)')
print(f'SIMULATE={SIMULATE}  MOLDABLE={MOLDABLE}')
print('=' * 70)

# ---------------------------------------------------------------------------
# Wire up queues, scheduler, simulator processes.
# ---------------------------------------------------------------------------
queue                  = Redis_Queue(queue_name='wf-queue')
finish_queue           = Redis_Queue(queue_name='completed-jobs-queue')
resource_request_queue = Redis_Queue(queue_name='resource-request-queue')

sched = _SchedClass(
    queue, finish_queue, resource_request_queue,
    sort_key='cost_per_iteration',
)

sim_dispatcher = simulus.simulator('dispatcher')
sim_sched      = simulus.simulator('scheduler')

wf_mb               = sim_sched.mailbox('wf_mb', 1)
completed_jobs_mb   = sim_sched.mailbox('completed_jobs_mb', 1)
resource_request_mb = sim_sched.mailbox('resource_request_mb', 1)

sim_dispatcher.process(dispatcher_LA, sim_dispatcher, 'wf_mb')
sim_sched.process(sched.run, sim_sched, wf_mb, resource_request_mb,
                  name=f'{SCHEDULER_NAME.lower()}_sched')
sim_sched.process(sched.processJobCompletion, sim_sched, completed_jobs_mb,
                  name='job_completion_sched')

g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=False)
g.run(show_runtime_report=True)

print('=' * 70)
print(f'{SCHEDULER_NAME} simulation complete (seed={_args.seed}, N={TOTAL_WORKFLOWS})')
print('=' * 70)
