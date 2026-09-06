"""Parametrised driver for the SeisSol workflow sweep.

Usage:
    python simulate_sweep.py <algo> <mode> <out_dir>
                             [--closeness-tolerance VALUE]
                             [--speedup-threshold VALUE]
        algo ∈ {fcfs, edf, heft, rank}, or any SeisSol policy name of
               elastiflow/policies.py (the uncited policies, by module name)
        mode ∈ {moldable, static}
        out_dir: directory where the metrics CSV(s) will be moved
        --closeness-tolerance VALUE  override CLOSENESS_TOLERANCE (default 0.15)
        --speedup-threshold VALUE    override SPEEDUP_THRESHOLD   (default 1.4)

Overrides patch `config.constants` BEFORE any scheduler module imports it,
so the new values take effect via the standard `from config.constants
import ...` early-binding pattern in scheduler.py / fcfs_optimized.py.
"""
import argparse
import glob
import os
import shutil
import sys

# argparse: keep positional args compatible with the original signature
# but accept the new ablation flags as optional named args.
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument('algo')
_parser.add_argument('mode')
_parser.add_argument('out_dir')
_parser.add_argument('--closeness-tolerance', type=float, default=None,
                     help='Override CLOSENESS_TOLERANCE (default: from constants.py)')
_parser.add_argument('--speedup-threshold', type=float, default=None,
                     help='Override SPEEDUP_THRESHOLD (default: from constants.py)')
_parser.add_argument('--seed', type=int, default=None,
                     help='Override SEED used by the dispatcher (np.random)')
_parser.add_argument('--sort-key', choices=['runtime', 'cost'], default='runtime',
                     help='Resource-manager sort key for fcfs/edf (default runtime). '
                          'Maps to the appendix _r / _c variants.')
_parser.add_argument('--rank-budget', type=float, default=None,
                     help='BUDGET_FACTOR for the rank scheduler (e.g. 5.0 for [50,50], 2.5 for [25,75])')
_parser.add_argument('--rank-deadline', type=float, default=None,
                     help='DEADLINE_FACTOR for the rank scheduler (e.g. 5.0 for [50,50], 7.5 for [25,75])')
_parser.add_argument('--N', type=int, default=None,
                     help='Override TOTAL_WORKFLOWS (default: from constants.py)')
_parser.add_argument('--chains-per-node', type=int, default=None,
                     help='Override CHAINS_PER_NODE: max chains the moldable '
                          'scale-down loop packs per node (default 3). Used for '
                          'the k-sensitivity ablation.')
_parser.add_argument('--iter0-factor', type=float, default=None,
                     help='Override OPTIM_FCFS_BFACTOR[0]/DFACTOR[0] (iteration-0 '
                          'budget/deadline residual factor). Diagnostic for '
                          'whether FACTOR[0] is ever operative.')
_args = _parser.parse_args()
ALGO, MODE, OUT_DIR = _args.algo, _args.mode, _args.out_dir
from elastiflow.policies import resolve_seissol
POLICY = resolve_seissol(ALGO, MODE, _args.sort_key)   # fails here on an unknown name; imports nothing yet
# rank is always moldable in the appendix (Md.Rank only); we still allow
# `static` here so the harness can sanity-check the static variant if
# ever wanted, but the canonical sweep will only use rank+moldable.
assert MODE in {"moldable", "static"}

# Override config.constants BEFORE any other import pulls it
import elastiflow.config.constants as C
C.MOLDABLE = (MODE == "moldable")
if _args.closeness_tolerance is not None:
    C.CLOSENESS_TOLERANCE = _args.closeness_tolerance
    print(f'[ablation] CLOSENESS_TOLERANCE overridden to {C.CLOSENESS_TOLERANCE}')
if _args.speedup_threshold is not None:
    C.SPEEDUP_THRESHOLD = _args.speedup_threshold
    print(f'[ablation] SPEEDUP_THRESHOLD overridden to {C.SPEEDUP_THRESHOLD}')
if _args.seed is not None:
    C.SEED = _args.seed
    print(f'[seed] SEED overridden to {C.SEED}')
# Rank scheduler factor overrides — appendix label like "[50,50]"
# corresponds to (BUDGET_FACTOR, DEADLINE_FACTOR) = (5.0, 5.0); "[25,75]"
# to (2.5, 7.5). Patched before priority_priority is imported.
if _args.rank_budget is not None:
    C.BUDGET_FACTOR = _args.rank_budget
    print(f'[rank] BUDGET_FACTOR overridden to {C.BUDGET_FACTOR}')
if _args.rank_deadline is not None:
    C.DEADLINE_FACTOR = _args.rank_deadline
    print(f'[rank] DEADLINE_FACTOR overridden to {C.DEADLINE_FACTOR}')
if _args.N is not None:
    C.TOTAL_WORKFLOWS = _args.N
    print(f'[N] TOTAL_WORKFLOWS overridden to {C.TOTAL_WORKFLOWS}')
if _args.chains_per_node is not None:
    C.CHAINS_PER_NODE = _args.chains_per_node
    print(f'[ablation] CHAINS_PER_NODE overridden to {C.CHAINS_PER_NODE}')
if _args.iter0_factor is not None:
    C.OPTIM_FCFS_BFACTOR[0] = _args.iter0_factor
    C.OPTIM_FCFS_DFACTOR[0] = _args.iter0_factor
    print(f'[ablation] OPTIM_FCFS_*FACTOR[0] overridden to {_args.iter0_factor}')

import simulus
from elastiflow.wf_queue.redis_queue import Redis_Queue
from elastiflow.scripts.dispatcher import dispatcher

# Map --sort-key {runtime,cost} to the resource_manager key the
# constructors expect. This corresponds to the appendix's _r / _c
# variants (St.FCFS_r / St.FCFS_c / St.EDF_r / St.EDF_c, etc.).
sort_key_str = (
    'runtime_per_iteration' if _args.sort_key == 'runtime' else 'cost_per_iteration'
)

# Scheduler selection through the registry. The class is imported only now,
# after the constants were patched above. HEFT-ST has no _r/_c variant and
# takes no sort key (--sort-key is ignored for it); Elastic-Rank takes the
# (BUDGET_FACTOR, DEADLINE_FACTOR) pair patched above.
SchedCls = POLICY.load()
sched_kwargs = POLICY.kwargs(sort_key_str)

queue = Redis_Queue(queue_name="wf-queue")
finish_queue = Redis_Queue(queue_name="completed-jobs-queue")
resource_request_queue = Redis_Queue(queue_name="resource-request-queue")

sched = SchedCls(queue, finish_queue, resource_request_queue, **sched_kwargs)
# Tag the output files so cells with different sort-key / rank-factor
# don't collide in the output directory.
tag_parts = [ALGO, MODE]
if ALGO in ('fcfs', 'edf'):
    tag_parts.append(_args.sort_key)
elif ALGO == 'rank':
    b = _args.rank_budget if _args.rank_budget is not None else C.BUDGET_FACTOR
    d = _args.rank_deadline if _args.rank_deadline is not None else C.DEADLINE_FACTOR
    tag_parts.append(f"b{b}_d{d}")
tag_parts.append(f"{C.TOTAL_WORKFLOWS}wf")
if _args.chains_per_node is not None:
    tag_parts.append(f"k{_args.chains_per_node}")
if _args.seed is not None:
    tag_parts.append(f"seed{_args.seed}")
tag = "_".join(tag_parts)
os.makedirs(OUT_DIR, exist_ok=True)
sched.metrics.set_output_file(os.path.join(OUT_DIR, tag))

sim_dispatcher = simulus.simulator("dispatcher")
sim_sched = simulus.simulator("scheduler")

wf_mb = sim_sched.mailbox("wf_mb", 1)
completed_jobs_mb = sim_sched.mailbox("completed_jobs_mb", 1)
resource_request_mb = sim_sched.mailbox("resource_request_mb", 1)
from elastiflow.execution.backend import SimulatedBackend
from elastiflow.executor import executeWorklow, processNewResources
from elastiflow.config.constants import COLD_START_TIME
from elastiflow.scripts.create_instance import simulated_ip
from elastiflow.scripts.tinyda_runtime import iteration_runtime
backend_sched = SimulatedBackend(sim_sched, {'wf_mb': wf_mb, 'completed_jobs_mb': completed_jobs_mb, 'resource_request_mb': resource_request_mb}, execute=executeWorklow, on_resources=processNewResources, cold_start=COLD_START_TIME, fake_ip=simulated_ip, executor_overhead=7.7, runtime_model=iteration_runtime)
backend_disp = SimulatedBackend(sim_dispatcher)   # sends by mailbox name from its own simulator

sim_dispatcher.process(dispatcher, backend_disp)
sim_sched.process(sched.run, backend_sched, name=f"{ALGO}_sched")
sim_sched.process(sched.processJobCompletion, backend_sched, name="job_completion_sched")

g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=False)
g.run(show_runtime_report=True)

# NOTE: metrics.py calls bare exit() after writing files, so anything after
# g.run() may never execute. Output paths are set via set_output_file() above.
