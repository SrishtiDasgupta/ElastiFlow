"""
sim_4corners_calibrated.py — 4 corners (Static EDF, Static FCFS, Moldable EDF, Moldable FCFS)
modeled on R7's workload using R7-calibrated per-epoch unit times.

Compares all 4 corners + the bug-free moldable EDF counterfactual.

Same per-epoch units, same wf specs, same cluster topology.
Difference between corners:
  - Ordering rule: EDF (by deadline) vs FCFS (by arrival)
  - Allocation rule: Static (fixed lanes throughout) vs Moldable (grow/shrink)

Moldable assumes bug-#2-fixed scheduler (immediate retry on scale-down).
"""
import math
import heapq

# -----------------------------------------------------------------------------
# CONSTANTS (R7-calibrated)
# -----------------------------------------------------------------------------
PER_EPOCH = {
    ('vgg19',            'slurm'):       35.0,
    ('vgg19',            'cluster_g5'):  53.0,
    ('vgg19',            'cluster_g4'):  25.0,
    ('wide_resnet101_2', 'cluster_g5'):  22.0,
    ('wide_resnet101_2', 'cluster_g4'):  30.0,
    ('wide_resnet101_2', 'slurm'):       40.0,
    ('convnext_large',   'cluster_g4'):  27.0,   # has Ray placement-group penalty
    ('convnext_large',   'cluster_g5'):  23.0,
    ('convnext_large',   'slurm'):       50.0,
}
COLD_START_FACTOR = 3.2  # iter 0 on freshly-spawned OD instance
ALLOC_OVERHEAD_S = 30
OD_CREATION_S = 300
INTER_ITER_OVERHEAD_S = 30
RATE_OD = {'cluster_g4': 0.526, 'cluster_g5': 1.006}

CLUSTER_CAP = {'slurm': 4, 'cluster_g4': 5, 'cluster_g5': 5}   # deployed-fleet ceiling (was 4/4/4); OD-spawnable per cluster = CAP - RESERVED = 0/3/3
RESERVED   = {'slurm': 4, 'cluster_g4': 2, 'cluster_g5': 2}    # reserved-only lanes per cluster (unchanged)

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

# Lane-grow chain triggers per wf (max chains it ever hits — controls grow targets in moldable).
MAX_CHAINS = {n: max(c for c, _ in w['iters']) for n, w in WFS.items()}


def order_key(name, ordering):
    if ordering == 'edf':
        return WFS[name]['submit'] + WFS[name]['deadline']
    else:  # fcfs
        return WFS[name]['submit']


def per_iter_dur(model, cluster, chains, lanes, epoch, is_first_iter_with_od=False):
    pe = PER_EPOCH[(model, cluster)]
    if is_first_iter_with_od:
        pe *= COLD_START_FACTOR
    return epoch * math.ceil(chains / max(lanes, 1)) * pe


def simulate(mode, ordering):
    """mode in {'static', 'moldable'}, ordering in {'edf', 'fcfs'}."""
    free = dict(CLUSTER_CAP)
    state = {n: 'pending' for n in WFS}  # 'pending' = not yet arrived
    completions = {}
    od_intervals = []  # [cluster, n_od, start, end_or_None]
    eq = []
    for n, w in WFS.items():
        heapq.heappush(eq, (w['submit'], 0, 'arrive', n))
    seq = 0

    def push(t, kind, payload):
        nonlocal seq
        seq += 1
        heapq.heappush(eq, (t, seq, kind, payload))

    def n_od_used(c):
        in_use = CLUSTER_CAP[c] - free[c]
        return max(0, in_use - RESERVED[c])

    def allocate_to(name, c, lanes_want, t_now):
        """Allocate lanes_want lanes from cluster c to wf name."""
        nonlocal free
        actually = min(lanes_want, free[c])
        if actually <= 0:
            return 0
        pre_od = n_od_used(c)
        free[c] -= actually
        post_od = n_od_used(c)
        od_added = post_od - pre_od
        is_first_od = od_added > 0
        # Schedule iter 0 end
        chains_init = WFS[name]['chains_initial']
        chains, epoch = WFS[name]['iters'][0]
        start_t = t_now + ALLOC_OVERHEAD_S
        if is_first_od:
            start_t += OD_CREATION_S
            od_intervals.append([c, od_added, start_t, None])
        dur = per_iter_dur(WFS[name]['model'], c, chains, actually, epoch, is_first_iter_with_od=is_first_od)
        end_t = start_t + dur
        state[name] = ['running', c, actually, 0, end_t]
        push(end_t, 'iter_end', name)
        return actually

    def try_allocate_blocked(t_now):
        queued = sorted([n for n, s in state.items() if s == 'queued'], key=lambda x: order_key(x, ordering))
        for name in queued:
            w = WFS[name]
            chains_init = w['chains_initial']
            for c in w['pref_clusters']:
                # Static: need full chains_init lanes. Moldable: accept partial (>=1).
                if mode == 'static':
                    if free[c] >= chains_init:
                        allocate_to(name, c, chains_init, t_now)
                        break
                else:  # moldable
                    if free[c] >= 1:
                        allocate_to(name, c, min(chains_init, free[c]), t_now)
                        break

    def try_grow_running(t_now):
        """For moldable: try to grow running wfs that have chains > current lanes."""
        if mode != 'moldable':
            return
        # Iterate by ordering preference (EDF/FCFS) so that more urgent grows happen first
        running = sorted([n for n, s in state.items() if isinstance(s, list) and s[0] == 'running'],
                         key=lambda x: order_key(x, ordering))
        for name in running:
            st = state[name]
            _, c, lanes, iter_idx, end_t = st
            wf = WFS[name]
            # Only grow if current iter has chains > lanes (for next iter's fill, if applicable)
            # Or if we're in mid-iter — we can't change running iter, so skip
            # In real moldable, growth happens at iter boundary. Model that.
            # Here at iter end: if chains_next > lanes, try grow.
            pass  # handled in iter_end transition

    while eq:
        t, _, kind, name = heapq.heappop(eq)
        if kind == 'arrive':
            state[name] = 'queued'  # mark as arrived
            try_allocate_blocked(t)
        elif kind == 'iter_end':
            cur = state[name]
            if cur == 'done':
                continue
            _, c, lanes, iter_idx, _ = cur
            w = WFS[name]
            next_idx = iter_idx + 1
            if next_idx >= len(w['iters']):
                # Workflow complete
                completions[name] = t
                pre_od = n_od_used(c)
                free[c] += lanes
                post_od = n_od_used(c)
                # Close OD intervals in proportion (simplified: close newest first)
                od_freed = pre_od - post_od
                if od_freed > 0:
                    for itv in reversed(od_intervals):
                        if itv[3] is None and itv[0] == c and od_freed > 0:
                            close_n = min(itv[1], od_freed)
                            if close_n == itv[1]:
                                itv[3] = t
                                od_freed -= close_n
                            else:
                                # partial close: split
                                itv[1] -= close_n
                                od_intervals.append([c, close_n, itv[2], t])
                                od_freed -= close_n
                state[name] = 'done'
                try_allocate_blocked(t)  # bug-free retry
                continue
            # Static: same lanes
            chains_next, epoch_next = w['iters'][next_idx]
            new_lanes = lanes
            if mode == 'moldable':
                # Try shrink if chains decreased
                if chains_next < lanes:
                    rel = lanes - chains_next
                    pre_od = n_od_used(c)
                    free[c] += rel
                    post_od = n_od_used(c)
                    od_freed = pre_od - post_od
                    if od_freed > 0:
                        for itv in reversed(od_intervals):
                            if itv[3] is None and itv[0] == c and od_freed > 0:
                                close_n = min(itv[1], od_freed)
                                if close_n == itv[1]:
                                    itv[3] = t
                                    od_freed -= close_n
                                else:
                                    itv[1] -= close_n
                                    od_intervals.append([c, close_n, itv[2], t])
                                    od_freed -= close_n
                    new_lanes = chains_next
                    try_allocate_blocked(t)  # bug-free: trigger retry
                # Try grow if chains increased
                elif chains_next > lanes:
                    add_want = chains_next - lanes
                    pre_od = n_od_used(c)
                    add = min(add_want, free[c])
                    if add > 0:
                        free[c] -= add
                        post_od = n_od_used(c)
                        od_added = post_od - pre_od
                        if od_added > 0:
                            od_intervals.append([c, od_added, t, None])
                        new_lanes += add
            # Schedule next iter
            start_t = t + INTER_ITER_OVERHEAD_S
            dur = per_iter_dur(w['model'], c, chains_next, new_lanes, epoch_next)
            end_t = start_t + dur
            state[name] = ['running', c, new_lanes, next_idx, end_t]
            push(end_t, 'iter_end', name)

    # OD cost accounting
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
            ms, dl, st, margin = '?', w['deadline']/60, 'STUCK', '?'
        results[name] = {'makespan_min': ms, 'deadline_min': dl, 'status': st, 'margin_min': margin}
    misses = sum(1 for r in results.values() if r['status'] == 'MISS')
    sum_flow = sum(r['makespan_min'] for r in results.values() if isinstance(r['makespan_min'], (int, float)))
    return {
        'corner': f"{mode.upper()} {ordering.upper()}",
        'wf_results': results,
        'misses': misses,
        'sum_flow_min': round(sum_flow, 1),
        'campaign_min': round(t_end / 60, 1),
        'od_cost_usd': round(od_cost, 2),
    }


def fmt_corner(s):
    out = []
    out.append(f"\n{'='*72}")
    out.append(f"  {s['corner']}")
    out.append(f"{'='*72}")
    out.append(f"  {'wf':6}  {'makespan':>10}  {'deadline':>10}  {'status':>6}  margin")
    out.append("  " + "-"*60)
    for name in WFS:
        r = s['wf_results'][name]
        ms = r['makespan_min']
        ms_s = f"{ms:.1f}m" if isinstance(ms, (int, float)) else str(ms)
        margin = r['margin_min']
        m_s = f"{margin:+.1f}m" if isinstance(margin, (int, float)) else str(margin)
        out.append(f"  {name:6}  {ms_s:>10}  {r['deadline_min']:>8.1f}m  {r['status']:>6}  {m_s}")
    out.append("  " + "-"*60)
    out.append(f"  Misses: {s['misses']}/7  |  Sum-flow: {s['sum_flow_min']} min  |  Campaign: {s['campaign_min']} min  |  OD cost: ${s['od_cost_usd']}")
    return "\n".join(out)


def fmt_comparison(corners):
    out = []
    out.append(f"\n{'='*100}")
    out.append("  4-CORNER COMPARISON (N=7, R7-calibrated)")
    out.append(f"{'='*100}")
    header = "  " + f"{'wf':6}  " + "  ".join(f"{c['corner']:>17}" for c in corners)
    out.append(header)
    out.append("  " + "-"*98)
    for name in WFS:
        cells = [f"{name:6}"]
        for c in corners:
            r = c['wf_results'][name]
            ms = r['makespan_min']
            cells.append(f"{ms:>5.0f}m {r['status']:5}" if isinstance(ms, (int, float)) else f"{'?':>6} {r['status']:5}")
        out.append("  " + "  ".join(f"{c:>17}" for c in cells))
    out.append("  " + "-"*98)
    misses_row = ['MISSES'] + [f"{c['misses']}/7" for c in corners]
    out.append("  " + "  ".join(f"{c:>17}" for c in misses_row))
    cost_row = ['OD COST'] + [f"${c['od_cost_usd']}" for c in corners]
    out.append("  " + "  ".join(f"{c:>17}" for c in cost_row))
    sf_row = ['SUM-FLOW'] + [f"{c['sum_flow_min']}m" for c in corners]
    out.append("  " + "  ".join(f"{c:>17}" for c in sf_row))
    cw_row = ['CAMPAIGN'] + [f"{c['campaign_min']}m" for c in corners]
    out.append("  " + "  ".join(f"{c:>17}" for c in cw_row))
    return "\n".join(out)


if __name__ == '__main__':
    s_static_edf  = simulate('static',   'edf')
    s_static_fcfs = simulate('static',   'fcfs')
    s_mold_edf    = simulate('moldable', 'edf')
    s_mold_fcfs   = simulate('moldable', 'fcfs')
    for s in [s_static_edf, s_static_fcfs, s_mold_edf, s_mold_fcfs]:
        print(fmt_corner(s))
    print(fmt_comparison([s_static_edf, s_static_fcfs, s_mold_edf, s_mold_fcfs]))
