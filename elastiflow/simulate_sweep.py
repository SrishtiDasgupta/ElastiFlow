"""Parametrised driver for the SeisSol workflow sweep.

Usage:
    python simulate_sweep.py <algo> <mode> <out_dir>
                             [--closeness-tolerance VALUE]
                             [--speedup-threshold VALUE]
        algo ∈ {fcfs, edf, heft}
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
assert ALGO in {"fcfs", "edf", "heft", "rank"}
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

# Scheduler selection
if ALGO == "fcfs":
    from elastiflow.scheduler.fcfs_optimized import FCFS_Optimized as SchedCls
    sched_kwargs = {"sort_key": sort_key_str}
elif ALGO == "edf":
    from elastiflow.scheduler.earliest_deadline_edf import EarliestDeadlineEDF as SchedCls
    sched_kwargs = {"sort_key": sort_key_str}
elif ALGO == "heft":
    # St.HEFT in the appendix has no _r/_c variant — the scheduler does
    # not accept a sort_key. We deliberately ignore --sort-key for heft.
    from elastiflow.scheduler.heft_heft_req import HEFT_HEFT_REQ as SchedCls
    sched_kwargs = {}
elif ALGO == "rank":
    # Md.Rank in the appendix uses PriorityPriority with the
    # (BUDGET_FACTOR, DEADLINE_FACTOR) pair patched above.
    from elastiflow.scheduler.priority_priority import PriorityPriority as SchedCls
    sched_kwargs = {}

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
from elastiflow.execution.backend import SimulatedBackend, register
from elastiflow.executor import executeWorklow, processNewResources
register(sim_sched, SimulatedBackend(sim_sched, {'wf_mb': wf_mb, 'completed_jobs_mb': completed_jobs_mb, 'resource_request_mb': resource_request_mb}, execute=executeWorklow, on_resources=processNewResources))
register(sim_dispatcher, SimulatedBackend(sim_dispatcher))   # sends by mailbox name from its own simulator

sim_dispatcher.process(dispatcher, sim_dispatcher, "wf_mb")
sim_sched.process(sched.run, sim_sched, wf_mb, resource_request_mb, name=f"{ALGO}_sched")
sim_sched.process(sched.processJobCompletion, sim_sched, completed_jobs_mb, name="job_completion_sched")

g = simulus.sync([sim_dispatcher, sim_sched], enable_smp=False)
g.run(show_runtime_report=True)

# NOTE: metrics.py calls bare exit() after writing files, so anything after
# g.run() may never execute. Output paths are set via set_output_file() above.
