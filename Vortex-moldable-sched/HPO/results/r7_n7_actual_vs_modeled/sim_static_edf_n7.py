"""
sim_static_edf_n7.py — Static EDF N=7 modeled against R7 calibration.

Compares to:
  - R7 ACTUAL moldable EDF (3 HITs / 4 MISSES, sum-flow 1297 min)
  - R7 BUG-FREE moldable EDF (3 HITs / 4 MISSES, sum-flow 1142 min, only data5 changes)

Static EDF rules:
  - Drain order = EDF (by deadline)
  - Each wf gets chains_initial lanes if available; else queued.
  - Once allocated, lanes are FIXED for all iterations (no scale up/down).
  - Late iters with chains > lanes_initial run at chunks > 1 (slow).
  - When a wf finishes, lanes return to pool; queued wfs retry in EDF order.

Uses R7-calibrated per-epoch unit times from data5 actual lanes=1 measurements.
"""
import math
import heapq

T0 = 1778413716

# Same per-epoch unit (s/epoch) calibrated from R7 actuals where possible:
PER_EPOCH = {
    ('vgg19',            'slurm'):       35.0,   # data8 iter 0: 345s/(10*1)=34.5
    ('vgg19',            'cluster_g5'):  18.0,   # data1 iter 0: 854s/(16*1)=53? hmm. Adjusted: 854/(16*ceil(4/4)) = 53. But for 4-lane allocation chunks=1.
    ('vgg19',            'cluster_g4'):  25.0,
    ('wide_resnet101_2', 'cluster_g5'):  22.0,   # avg from data5 iters 1-4
    ('wide_resnet101_2', 'cluster_g4'):  30.0,
    ('convnext_large',   'cluster_g4'):  27.0,   # data9 iter 4: 5115s/(95*2)=27 (placement-group=2)
    ('convnext_large',   'cluster_g5'):  23.0,
    ('wide_resnet101_2', 'slurm'):       40.0,   # extrapolated
    ('convnext_large',   'slurm'):       50.0,   # extrapolated
}
# Per data1 iter 0 actual: 854s for chains=4 lanes=4 epoch=16 → 854/16/1 = 53.4 s/epoch on g5.
# That's higher than my 18.0 above. Let me use 53.4 for consistency with measurement.
PER_EPOCH[('vgg19', 'cluster_g5')] = 53.0

# Cold-start overhead (first iter on freshly-spawned OD): from data5 actual
# iter 0 = 80 s/epoch vs steady-state 25. So cold_start_s ≈ (80-25) * epoch * chunks
COLD_START_FACTOR = 3.2  # multiply iter 0 per_epoch by this if OD spawn

# R7 wf specs (same as bug-free analysis)
WFS = {
    'data8':  {'submit': 0,    'deadline': 6160, 'model': 'vgg19',
               'chains_initial': 4, 'iters': [(4,10),(6,11),(9,12)],
               'pref_clusters': ['slurm', 'cluster_g4', 'cluster_g5']},
    'data9':  {'submit': 271,  'deadline': 8473, 'model': 'convnext_large',
               'chains_initial': 3, 'iters': [(3,28),(3,40),(3,57),(3,65),(3,95)],
               'pref_clusters': ['cluster_g5', 'cluster_g4', 'slurm']},
    'data5':  {'submit': 390,  'deadline': 7290, 'model': 'wide_resnet101_2',
               'chains_initial': 3, 'iters': [(3,25),(4,22),(6,21),(9,30),(10,30)],
               'pref_clusters': ['cluster_g5', 'cluster_g4', 'slurm']},
    'data7':  {'submit': 472,  'deadline': 5755, 'model': 'wide_resnet101_2',
               'chains_initial': 2, 'iters': [(2,27),(3,35),(4,50),(6,50)],
               'pref_clusters': ['cluster_g5', 'cluster_g4', 'slurm']},
    'data3':  {'submit': 487,  'deadline': 5196, 'model': 'vgg19',
               'chains_initial': 4, 'iters': [(4,18),(6,9),(9,12)],
               'pref_clusters': ['slurm', 'cluster_g4', 'cluster_g5']},
    'data12': {'submit': 502,  'deadline': 5728, 'model': 'vgg19',
               'chains_initial': 4, 'iters': [(4,17),(6,18),(9,24),(13,33)],
               'pref_clusters': ['slurm', 'cluster_g4', 'cluster_g5']},
    'data1':  {'submit': 508,  'deadline': 5196, 'model': 'vgg19',
               'chains_initial': 4, 'iters': [(4,16),(6,12),(9,13)],
               'pref_clusters': ['slurm', 'cluster_g4', 'cluster_g5']},
}
CLUSTER_CAP = {'slurm': 4, 'cluster_g4': 4, 'cluster_g5': 4}
RESERVED   = {'slurm': 4, 'cluster_g4': 2, 'cluster_g5': 2}


def edf_key(name):
    """Sort key: deadline+submit (absolute deadline)."""
    return WFS[name]['submit'] + WFS[name]['deadline']


def iter_dur_s(model, cluster, chains, lanes, epoch, is_first_iter_with_od=False):
    pe = PER_EPOCH[(model, cluster)]
    if is_first_iter_with_od:
        pe = pe * COLD_START_FACTOR
    return epoch * math.ceil(chains / max(lanes, 1)) * pe


def simulate_static_edf():
    """Discrete event simulation."""
    # State: cluster free lanes
    free = dict(CLUSTER_CAP)
    # Wf state: 'queued' / ('running', cluster, lanes, iter_idx, iter_end_t) / 'done'
    state = {n: 'queued' for n in WFS}
    completions = {}  # name -> finish_t
    od_intervals = []  # (cluster, n_od, start, end)

    # Event queue: (t, evt, payload)
    eq = []
    for n, w in WFS.items():
        heapq.heappush(eq, (w['submit'], 'arrive', n))

    def try_allocate_blocked(t_now):
        """For all queued wfs in EDF order, try to allocate full chains_initial lanes."""
        queued = sorted([n for n, s in state.items() if s == 'queued'], key=edf_key)
        for name in queued:
            w = WFS[name]
            chains_init = w['chains_initial']
            for c in w['pref_clusters']:
                if free[c] >= chains_init:
                    # Allocate full
                    free[c] -= chains_init
                    n_od = max(0, chains_init - max(0, RESERVED[c] - (CLUSTER_CAP[c] - free[c] - chains_init)))
                    n_od = max(0, chains_init - RESERVED[c]) if free[c] + chains_init <= CLUSTER_CAP[c] else 0
                    # Simpler: count od as max(0, lanes_in_use - reserved)
                    in_use = CLUSTER_CAP[c] - free[c]
                    n_od = max(0, in_use - RESERVED[c])
                    is_first_od = c in ('cluster_g4', 'cluster_g5') and n_od > 0
                    start_t = t_now + 30  # alloc overhead
                    if is_first_od:
                        start_t += 300  # OD creation latency
                    chains, epoch = w['iters'][0]
                    dur = iter_dur_s(w['model'], c, chains, chains_init, epoch, is_first_iter_with_od=is_first_od)
                    end_t = start_t + dur
                    state[name] = ('running', c, chains_init, 0, end_t)
                    if n_od > 0:
                        od_intervals.append([c, n_od, start_t, None])
                    heapq.heappush(eq, (end_t, 'iter_end', name))
                    break

    while eq:
        t, evt, name = heapq.heappop(eq)
        if evt == 'arrive':
            try_allocate_blocked(t)
        elif evt == 'iter_end':
            cur = state[name]
            if isinstance(cur, str):  # already done
                continue
            _, c, lanes, iter_idx, _ = cur
            w = WFS[name]
            next_idx = iter_idx + 1
            if next_idx >= len(w['iters']):
                # Done
                completions[name] = t
                free[c] += lanes
                state[name] = 'done'
                # Close OD intervals for this wf if any (proportional)
                for itv in od_intervals:
                    if itv[3] is None and itv[0] == c:
                        itv[3] = t
                        break
                # Try to allocate queued wfs
                try_allocate_blocked(t)
            else:
                # Static: same lanes, just compute next iter
                chains, epoch = w['iters'][next_idx]
                start_t = t + 30
                dur = iter_dur_s(w['model'], c, chains, lanes, epoch)
                end_t = start_t + dur
                state[name] = ('running', c, lanes, next_idx, end_t)
                heapq.heappush(eq, (end_t, 'iter_end', name))

    # OD cost
    RATE_OD = {'cluster_g4': 0.526, 'cluster_g5': 1.006}
    t_end = max(completions.values(), default=0)
    od_cost = 0.0
    for c, n, s, e in od_intervals:
        e = e if e is not None else t_end
        od_cost += n * (e - s) / 3600 * RATE_OD.get(c, 0)

    # Build report
    results = {}
    for name in WFS:
        w = WFS[name]
        if name in completions:
            ms = (completions[name] - w['submit']) / 60
            dl = w['deadline'] / 60
            st = 'HIT' if ms <= dl else 'MISS'
            margin = dl - ms
        else:
            ms = '?'
            dl = w['deadline'] / 60
            st = 'STUCK'
            margin = '?'
        results[name] = {'makespan_min': ms, 'deadline_min': dl, 'status': st, 'margin_min': margin}
    misses = sum(1 for r in results.values() if r['status'] == 'MISS')
    sum_flow = sum(r['makespan_min'] for r in results.values() if isinstance(r['makespan_min'], (int, float)))
    return {
        'wf_results': results,
        'misses': misses,
        'sum_flow_min': round(sum_flow, 1),
        'campaign_min': round(t_end / 60, 1),
        'od_cost_usd': round(od_cost, 2),
    }


def main():
    print("=" * 80)
    print("  STATIC EDF N=7 — modeled with R7 calibration")
    print("=" * 80)
    s = simulate_static_edf()
    print(f"  {'wf':6}  {'makespan':>10}  {'deadline':>10}  {'status':>6}  margin")
    print("  " + "-" * 60)
    for name in WFS:
        r = s['wf_results'][name]
        ms = r['makespan_min']
        ms_s = f"{ms:.1f}m" if isinstance(ms, (int, float)) else str(ms)
        margin = r['margin_min']
        m_s = f"{margin:+.1f}m" if isinstance(margin, (int, float)) else str(margin)
        print(f"  {name:6}  {ms_s:>10}  {r['deadline_min']:>8.1f}m  {r['status']:>6}  {m_s}")
    print("  " + "-" * 60)
    print(f"  Misses: {s['misses']}/7  |  Sum-flow: {s['sum_flow_min']} min  |  Campaign: {s['campaign_min']} min  |  OD cost: ${s['od_cost_usd']}")
    print()
    print("=" * 80)
    print("  COMPARISON: STATIC EDF vs R7 BUG-FREE MOLDABLE EDF")
    print("=" * 80)
    BUG_FREE_MOLD = {
        'data8':  {'ms': 61.2, 'st': 'HIT'},
        'data9':  {'ms': 276.8, 'st': 'MISS'},
        'data5':  {'ms': 186.7, 'st': 'MISS'},   # bug-free improvement
        'data7':  {'ms': 366.1, 'st': 'MISS'},
        'data3':  {'ms': 56.0, 'st': 'HIT'},
        'data12': {'ms': 140.9, 'st': 'MISS'},
        'data1':  {'ms': 53.3, 'st': 'HIT'},
    }
    print(f"  {'wf':6}  {'STATIC EDF':>16}  {'BUG-FREE MOLD':>16}  Δ")
    print("  " + "-" * 60)
    for name in WFS:
        r = s['wf_results'][name]
        st_ms = r['makespan_min']
        st_str = f"{st_ms:.0f}m {r['status']}" if isinstance(st_ms, (int, float)) else f"? {r['status']}"
        bf_ms = BUG_FREE_MOLD[name]['ms']
        bf_str = f"{bf_ms:.0f}m {BUG_FREE_MOLD[name]['st']}"
        if isinstance(st_ms, (int, float)):
            delta = st_ms - bf_ms
            d_str = f"{delta:+.0f}m"
        else:
            d_str = "?"
        print(f"  {name:6}  {st_str:>16}  {bf_str:>16}  {d_str}")
    bf_misses = sum(1 for v in BUG_FREE_MOLD.values() if v['st'] == 'MISS')
    bf_sum = sum(v['ms'] for v in BUG_FREE_MOLD.values())
    print("  " + "-" * 60)
    print(f"  STATIC EDF   : {s['misses']}/7 misses, sum-flow {s['sum_flow_min']}m, OD cost ${s['od_cost_usd']}")
    print(f"  MOLDABLE EDF : {bf_misses}/7 misses, sum-flow {bf_sum:.0f}m  (bug-free)")


if __name__ == '__main__':
    main()
