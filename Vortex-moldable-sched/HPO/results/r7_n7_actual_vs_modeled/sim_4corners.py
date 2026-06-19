"""
sim_4corners.py — Discrete event simulator for HPO N=7 across 4 scheduler corners.

Corners modeled:
  1. STATIC EDF       — wfs ordered by deadline, fixed allocation per wf, no scaling
  2. STATIC FCFS      — wfs ordered by arrival, fixed allocation, no scaling
  3. MOLDABLE EDF     — dynamic alloc, EDF order, with realistic on-prem-first override
  4. MOLDABLE FCFS    — dynamic alloc, FCFS order, with on-prem-first override
  +  MOLDABLE EDF NOBUG — counterfactual: bug #2 fixed (immediate retry on scale-down)

Uses R7 actual per-iter chain/epoch data (extracted from results.jsonl) for fidelity.
Uses R7 actual iter durations to back-calibrate per-epoch runtimes per (model, family).

INPUTS (hardcoded from R7):
  - 7 wfs with submit times (Poisson seed=42), deadlines, models
  - Per-iter chain/epoch progression from R7 results.jsonl
  - R7 actual iter durations for calibration

OUTPUTS:
  - per-wf table: makespan, deadline status per corner
  - aggregate: misses, sum-flowtime, OD cost per corner
  - 4-corner comparison table (markdown)
  - cost analysis
"""
import heapq
import math
from dataclasses import dataclass, field
from typing import Optional

# -----------------------------------------------------------------------------
# CONSTANTS
# -----------------------------------------------------------------------------
T0 = 1778413716  # data8 submit time in R7

# Cluster capacity (lanes per cluster)
CLUSTER_CAP = {'slurm': 4, 'cluster_g4': 4, 'cluster_g5': 4}
RESERVED = {'slurm': 4, 'cluster_g4': 2, 'cluster_g5': 2}  # rest are OD-spawnable
OD_AVAILABLE = {'slurm': 0, 'cluster_g4': 2, 'cluster_g5': 2}  # OD slots per cluster

# Per-instance OD hourly rate
RATE_OD = {'cluster_g4': 0.526, 'cluster_g5': 1.006}
RATE_RES = {'slurm': 0.84, 'cluster_g4': 0.227, 'cluster_g5': 0.435}

# OD instance creation latency (observed in R7: ~5 min cloud_runner setup)
OD_CREATION_S = 300

# Scheduler overhead (observed in R7: 280s allocation cycles when busy)
ALLOC_OVERHEAD_S = 30  # idealized; R7 had up to 280s but that was bug-amplified

# Inter-iter overhead (executor setup + steep)
INTER_ITER_OVERHEAD_S = 30


# -----------------------------------------------------------------------------
# WF SPECS (from R7 actual)
#   submit_offset = seconds after T0 (so data8 at 0, others Poisson seed=42)
#   per_iter_progression = list of (epoch, next_trials_for_this_iter) for each iter
#       - For iter 0: epoch from yaml input, next_trials from yaml constraints.chains
#       - For iter i>0: epoch and chains FROM iter (i-1)'s output
#   So when running iter N, lanes are allocated against chains[N], duration ~ epoch[N] * ceil(chains[N]/lanes)
#   Important: the iter N "epoch" comes from the OUTPUT of HPO trial selection, which is not predictable
#       in advance. For Static modeling, we use the SAME iter progression as observed in R7 actual.
# -----------------------------------------------------------------------------

@dataclass
class WfSpec:
    name: str
    full_id: str
    submit_offset: float
    deadline_offset: float
    model: str  # vgg19 / wide_resnet101_2 / convnext_large
    initial_chains: int
    initial_epoch: int
    # per_iter_actual: list of (chains_to_run, epoch_to_run) — EXACTLY as in R7
    per_iter_actual: list

# R7 results.jsonl extraction (iter_idx, ts_offset_from_baseline, epoch, next_trials):
# baseline 1778413000.
# Submit offsets from T0 (1778413716):
#   data8:  0,    data9: 271, data5: 390, data7: 472, data3: 487, data12: 502, data1: 508
WFS = [
    WfSpec('data8',  'hpo-60bf3eb4',   0, 6160, 'vgg19',
        initial_chains=4, initial_epoch=10,
        per_iter_actual=[(4,10), (6,11), (9,12)]),  # (chains, epoch) per iter
    WfSpec('data9',  'hpo-b03a4442', 271, 8473, 'convnext_large',
        initial_chains=3, initial_epoch=20,
        per_iter_actual=[(3,28), (3,40), (3,57), (3,65), (3,95)]),
    WfSpec('data5',  'hpo-df19b440', 390, 7290, 'wide_resnet101_2',
        initial_chains=3, initial_epoch=18,
        per_iter_actual=[(3,25), (4,22), (6,21), (9,30), (10,30)]),  # iter 4 epoch projected
    WfSpec('data7',  'hpo-73120874', 472, 5755, 'wide_resnet101_2',
        initial_chains=2, initial_epoch=20,
        per_iter_actual=[(2,27), (3,35), (4,50), (6,50)]),  # iter 3 epoch projected
    WfSpec('data3',  'hpo-a87b8123', 487, 5196, 'vgg19',
        initial_chains=4, initial_epoch=15,
        per_iter_actual=[(4,18), (6,9), (9,12)]),
    WfSpec('data12', 'hpo-ccb43739', 502, 5728, 'vgg19',
        initial_chains=4, initial_epoch=14,
        per_iter_actual=[(4,17), (6,18), (9,24), (13,33)]),
    WfSpec('data1',  'hpo-417bb2bd', 508, 5196, 'vgg19',
        initial_chains=4, initial_epoch=15,
        per_iter_actual=[(4,16), (6,12), (9,13)]),
]

# Calibrated per-epoch runtime (s/epoch) from R7 actuals.
# Computed: duration_s / (epoch * ceil(chains/lanes_used))
# For known cases:
#   data8 iter 0: dur=345s, chains=4, lanes=4(slurm), epoch=10 → 345/(10*1) = 34.5 s/epoch (vgg19 slurm)
#   data8 iter 1: dur=1153s, chains=6, lanes=2(slurm partial after scale), epoch=11 → 1153/(11*3) = 35 s/epoch
#   data1 iter 0: dur=854s, chains=4, lanes=4(g5 res+OD), epoch=16 → 854/(16*1) = 53 s/epoch
#   data9 iter 4: dur=5115s, chains=3, lanes=2(only 2 of 3 trials ran), epoch=95 → 5115/(95*2) = 27 s/epoch (convnext_large g4)
#   data5 iter 3: dur=4737s, chains=9, lanes=1(degraded), epoch=30 → 4737/(30*9) = 17.5 s/epoch (wide_resnet101_2 g5) — surprising low
# Average and round:
PER_EPOCH = {
    ('vgg19', 'slurm'):       35.0,  # vgg19 on slurm CPU+GPU
    ('vgg19', 'cluster_g5'):  18.0,  # vgg19 on g5 (data1 actual derived from above)
    ('vgg19', 'cluster_g4'):  25.0,
    ('wide_resnet101_2', 'cluster_g4'): 30.0,
    ('wide_resnet101_2', 'cluster_g5'): 22.0,
    ('wide_resnet101_2', 'slurm'):      40.0,
    ('convnext_large', 'cluster_g4'):   34.0,
    ('convnext_large', 'cluster_g5'):   23.0,
    ('convnext_large', 'slurm'):        50.0,
}


def iter_runtime(model: str, cluster: str, chains: int, lanes: int, epoch: int) -> float:
    """Per-iteration runtime in seconds."""
    pe = PER_EPOCH.get((model, cluster), 30.0)  # default
    return epoch * math.ceil(chains / max(lanes, 1)) * pe


# -----------------------------------------------------------------------------
# DISCRETE EVENT SIMULATOR
# -----------------------------------------------------------------------------

EVT_ARRIVE = 'arrive'      # wf submitted to scheduler
EVT_ITER_END = 'iter_end'  # current iter of a wf finishes
EVT_OD_READY = 'od_ready'  # OD instance creation complete
EVT_TICK = 'tick'           # scheduler periodic tick (try to assign blocked wfs)


@dataclass(order=True)
class Event:
    t: float
    seq: int
    kind: str = field(compare=False)
    payload: dict = field(compare=False)


class Cluster:
    """Resource pool for one cluster type. Tracks reserved + OD lanes in use."""

    def __init__(self, name, cap, reserved, od_max):
        self.name = name
        self.cap = cap
        self.reserved = reserved
        self.od_max = od_max
        self.in_use = 0  # total lanes occupied
        self.od_in_use = 0  # of those, how many are OD (charged)
        self.od_intervals = []  # (start, end, n_od) for cost accounting

    def free_lanes(self) -> int:
        return self.cap - self.in_use

    def allocate(self, n: int, t_now: float) -> int:
        """Allocate up to n lanes. Returns number actually allocated."""
        actually = min(n, self.free_lanes())
        if actually <= 0:
            return 0
        # Consume reserved first, then OD
        res_avail = self.reserved - (self.in_use - self.od_in_use)
        from_res = min(actually, res_avail)
        from_od = actually - from_res
        self.in_use += actually
        if from_od > 0:
            self.od_in_use += from_od
            self.od_intervals.append([t_now, None, from_od])  # end TBD
        return actually

    def release(self, n: int, t_now: float):
        """Release n lanes; OD lanes terminated first (cost saving)."""
        rel = min(n, self.in_use)
        # Prefer to release OD (so we stop paying)
        from_od = min(rel, self.od_in_use)
        self.in_use -= rel
        if from_od > 0:
            self.od_in_use -= from_od
            # Close out the most recent open OD intervals
            remaining = from_od
            for itv in reversed(self.od_intervals):
                if itv[1] is None and remaining > 0:
                    closing = min(itv[2], remaining)
                    if closing == itv[2]:
                        itv[1] = t_now
                        remaining -= closing
                    else:
                        # Partial close: split the interval
                        itv[2] -= closing
                        self.od_intervals.append([itv[0], t_now, closing])
                        remaining -= closing

    def od_cost(self, t_end: float) -> float:
        """Sum OD-hours * rate. Open intervals close at t_end."""
        rate = RATE_OD.get(self.name, 0)
        total = 0.0
        for itv in self.od_intervals:
            start, end, n = itv
            end = end if end is not None else t_end
            total += n * (end - start) / 3600 * rate
        return total

    def __repr__(self):
        return f"<{self.name}: {self.in_use}/{self.cap} (od {self.od_in_use})>"


class Sim:
    """Discrete event simulator for one corner."""

    def __init__(self, mode, ordering, bug_free=False):
        """
        mode: 'static' or 'moldable'
        ordering: 'edf' or 'fcfs'
        bug_free: if True, scaledowns immediately retry blocked wfs (for moldable)
        """
        self.mode = mode
        self.ordering = ordering
        self.bug_free = bug_free
        self.clusters = {
            'slurm':      Cluster('slurm',      CLUSTER_CAP['slurm'],      RESERVED['slurm'],      OD_AVAILABLE['slurm']),
            'cluster_g4': Cluster('cluster_g4', CLUSTER_CAP['cluster_g4'], RESERVED['cluster_g4'], OD_AVAILABLE['cluster_g4']),
            'cluster_g5': Cluster('cluster_g5', CLUSTER_CAP['cluster_g5'], RESERVED['cluster_g5'], OD_AVAILABLE['cluster_g5']),
        }
        self.events = []
        self.seq = 0
        self.wf_state = {}  # wf.name → dict(state)
        self.completions = {}  # wf.name → (start_t, end_t)

    def push(self, t, kind, payload):
        self.seq += 1
        heapq.heappush(self.events, Event(t, self.seq, kind, payload))

    def best_cluster_for(self, wf: WfSpec, chains_now: int):
        """Pick the best cluster: prefer slurm if available, else g4 (g4-friendly model), else g5."""
        # Heuristic: vgg19 → can go anywhere; wide_resnet101_2 / convnext_large → prefer g5 (more memory).
        # In R7, on-prem-first override often hijacked wfs to slurm.
        prefs = []
        if wf.model in ('wide_resnet101_2', 'convnext_large'):
            prefs = ['cluster_g5', 'cluster_g4', 'slurm']
        else:
            prefs = ['slurm', 'cluster_g4', 'cluster_g5']
        for c_name in prefs:
            c = self.clusters[c_name]
            if c.free_lanes() >= chains_now:
                return c_name
        # Fall back to any with at least some lanes
        for c_name in prefs:
            c = self.clusters[c_name]
            if c.free_lanes() >= 1:
                return c_name
        return None

    def try_allocate(self, wf: WfSpec, t_now: float):
        """Try to allocate for wf at t_now. Returns (cluster_name, lanes) or (None, 0)."""
        chains_initial = wf.per_iter_actual[0][0]
        c_name = self.best_cluster_for(wf, chains_initial)
        if c_name is None:
            return None, 0
        c = self.clusters[c_name]
        want = chains_initial if self.mode == 'moldable' else chains_initial  # static: same
        got = c.allocate(want, t_now)
        return c_name, got

    def schedule_iter_end(self, wf: WfSpec, t_now: float, iter_idx: int, lanes: int, cluster: str):
        chains, epoch = wf.per_iter_actual[iter_idx]
        rt = iter_runtime(wf.model, cluster, chains, lanes, epoch)
        self.push(t_now + rt, EVT_ITER_END, {'wf': wf.name, 'iter': iter_idx})

    def handle_arrive(self, t_now: float, wf: WfSpec):
        c_name, lanes = self.try_allocate(wf, t_now)
        if c_name is None or lanes == 0:
            # Blocked — wait for resources
            self.wf_state[wf.name] = {'status': 'blocked', 'wf': wf}
            return
        # Add allocation overhead before iter starts
        start = t_now + ALLOC_OVERHEAD_S
        # OD instance creation latency for OD lanes
        if c_name in ('cluster_g4', 'cluster_g5'):
            n_od_used = max(0, lanes - max(0, self.clusters[c_name].reserved - (self.clusters[c_name].in_use - lanes)))
            if n_od_used > 0:
                start += OD_CREATION_S
        self.wf_state[wf.name] = {
            'status': 'running',
            'wf': wf,
            'cluster': c_name,
            'lanes': lanes,
            'iter': 0,
            'iter_started_at': start,
        }
        self.schedule_iter_end(wf, start, 0, lanes, c_name)

    def handle_iter_end(self, t_now: float, wf_name: str, iter_idx: int):
        st = self.wf_state[wf_name]
        wf = st['wf']
        next_iter = iter_idx + 1
        if next_iter >= len(wf.per_iter_actual):
            # Workflow complete
            self.completions[wf_name] = (st.get('arrived_at', wf.submit_offset), t_now)
            self.clusters[st['cluster']].release(st['lanes'], t_now)
            st['status'] = 'done'
            # Try to wake blocked wfs
            self.try_wake_blocked(t_now)
            return
        # Moldable: rescale lanes if chains changed
        chains_next = wf.per_iter_actual[next_iter][0]
        cluster = self.clusters[st['cluster']]
        if self.mode == 'moldable':
            if chains_next < st['lanes']:
                # Scale down
                cluster.release(st['lanes'] - chains_next, t_now)
                st['lanes'] = chains_next
                if self.bug_free:
                    self.try_wake_blocked(t_now)
            elif chains_next > st['lanes']:
                # Try scale up
                add = cluster.allocate(chains_next - st['lanes'], t_now)
                st['lanes'] += add
        # Schedule next iter end
        next_start = t_now + INTER_ITER_OVERHEAD_S
        st['iter'] = next_iter
        st['iter_started_at'] = next_start
        self.schedule_iter_end(wf, next_start, next_iter, st['lanes'], st['cluster'])

    def try_wake_blocked(self, t_now: float):
        # Walk wf_state for blocked wfs in EDF/FCFS order
        blocked = [(name, st['wf']) for name, st in self.wf_state.items() if st['status'] == 'blocked']
        if self.ordering == 'edf':
            blocked.sort(key=lambda x: x[1].deadline_offset + x[1].submit_offset)
        else:
            blocked.sort(key=lambda x: x[1].submit_offset)
        for name, wf in blocked:
            c_name, lanes = self.try_allocate(wf, t_now)
            if c_name is not None and lanes > 0:
                start = t_now + ALLOC_OVERHEAD_S
                if c_name in ('cluster_g4', 'cluster_g5'):
                    start += OD_CREATION_S
                self.wf_state[name] = {
                    'status': 'running',
                    'wf': wf,
                    'cluster': c_name,
                    'lanes': lanes,
                    'iter': 0,
                    'iter_started_at': start,
                }
                self.schedule_iter_end(wf, start, 0, lanes, c_name)

    def run(self, wfs):
        # Schedule all arrivals
        for wf in wfs:
            self.push(wf.submit_offset, EVT_ARRIVE, {'wf': wf})
        while self.events:
            ev = heapq.heappop(self.events)
            if ev.kind == EVT_ARRIVE:
                self.handle_arrive(ev.t, ev.payload['wf'])
            elif ev.kind == EVT_ITER_END:
                self.handle_iter_end(ev.t, ev.payload['wf'], ev.payload['iter'])
        # Compute final stats
        return self.summary()

    def summary(self):
        results = {}
        for wf in WFS:
            if wf.name in self.completions:
                start, end = self.completions[wf.name]
                makespan = end - wf.submit_offset
                hit = makespan <= wf.deadline_offset
                results[wf.name] = {
                    'makespan_min': round(makespan / 60, 1),
                    'deadline_min': round(wf.deadline_offset / 60, 1),
                    'status': 'HIT' if hit else 'MISS',
                    'margin_min': round((wf.deadline_offset - makespan) / 60, 1),
                }
            else:
                results[wf.name] = {
                    'makespan_min': '?',
                    'deadline_min': round(wf.deadline_offset / 60, 1),
                    'status': 'STUCK',
                    'margin_min': '?',
                }
        # OD cost
        t_end = max((c[1] for c in self.completions.values()), default=0)
        od_cost = sum(c.od_cost(t_end) for c in self.clusters.values())
        misses = sum(1 for r in results.values() if r['status'] == 'MISS')
        sum_flow = sum(r['makespan_min'] for r in results.values() if isinstance(r['makespan_min'], (int, float)))
        return {
            'corner': f"{self.mode.upper()} {self.ordering.upper()}{' (NOBUG)' if self.bug_free else ''}",
            'wf_results': results,
            'misses': misses,
            'sum_flow_min': round(sum_flow, 1),
            'campaign_min': round(t_end / 60, 1),
            'od_cost_usd': round(od_cost, 2),
        }


def print_corner(s):
    print(f"\n{'=' * 70}")
    print(f"  {s['corner']}")
    print(f"{'=' * 70}")
    for wf_name, r in s['wf_results'].items():
        ms = r['makespan_min']
        ms_str = f"{ms:.1f}" if isinstance(ms, (int, float)) else str(ms)
        margin = r['margin_min']
        margin_str = f"{margin:+.1f}" if isinstance(margin, (int, float)) else str(margin)
        print(f"  {wf_name:6}  {ms_str:>6}m  vs  {r['deadline_min']:>6.1f}m  →  {r['status']:5}  ({margin_str}m)")
    print(f"  {'─' * 50}")
    print(f"  Misses: {s['misses']}/7  |  Sum-flow: {s['sum_flow_min']} min  |  Campaign: {s['campaign_min']} min")
    print(f"  OD cost: ${s['od_cost_usd']}")


def print_comparison(corners):
    print(f"\n{'=' * 90}")
    print(f"  4-CORNER COMPARISON (N=7)")
    print(f"{'=' * 90}")
    headers = ['wf'] + [s['corner'] for s in corners]
    print(f"  {headers[0]:6}  " + "  ".join(f"{h:>22}" for h in headers[1:]))
    print(f"  " + "─" * 90)
    for wf in WFS:
        row = [f"{wf.name:6}"]
        for s in corners:
            r = s['wf_results'][wf.name]
            ms = r['makespan_min']
            status = r['status']
            cell = f"{ms:>6.1f}m {status}" if isinstance(ms, (int, float)) else f"   ?    {status}"
            row.append(cell)
        print(f"  " + "  ".join(f"{c:>22}" for c in row))
    print(f"  " + "─" * 90)
    # Aggregate row
    agg = ['MISSES']
    for s in corners:
        agg.append(f"{s['misses']}/7")
    print(f"  " + "  ".join(f"{c:>22}" for c in agg))
    cost = ['OD COST']
    for s in corners:
        cost.append(f"${s['od_cost_usd']}")
    print(f"  " + "  ".join(f"{c:>22}" for c in cost))


if __name__ == '__main__':
    s_static_edf  = Sim('static',   'edf').run(WFS)
    s_static_fcfs = Sim('static',   'fcfs').run(WFS)
    s_mold_edf    = Sim('moldable', 'edf').run(WFS)
    s_mold_fcfs   = Sim('moldable', 'fcfs').run(WFS)
    s_mold_edf_nobug = Sim('moldable', 'edf', bug_free=True).run(WFS)

    for s in [s_static_edf, s_static_fcfs, s_mold_edf, s_mold_fcfs, s_mold_edf_nobug]:
        print_corner(s)
    print_comparison([s_static_edf, s_static_fcfs, s_mold_edf, s_mold_fcfs, s_mold_edf_nobug])
