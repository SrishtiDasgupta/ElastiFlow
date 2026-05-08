"""N=3 simulation under the ACTUAL scheduler policy (no-migration, cluster-cap=4).

Differs from sim_cap10.py / sim_n7.py which used a homogeneous-pool assumption.
Here:
  - 3 containers: slurm (4), cluster-g4 (4), cluster-g5 (4) — strict cap each.
  - On-prem-first allocation (slurm preferred while any slot free).
  - No migration (assigned at submit, stays for life).
  - Moldable scale-up grants only within the wf's container.
  - Scheduler runtime model = power-law per-epoch × tinydaIterations.

Dispatch [8, 5, 9] (Poisson seed=42 first 3 gaps = 0, 271, 119 → submit times 0, 271, 390).
"""
import math

# Per-instance epoch runtimes (s/epoch, 1 worker per trial)
EPOCH = {
    'g4': {'vgg19': 22.17, 'wide_resnet101_2': 29.66, 'convnext_large': 34.03},
    'g5': {'vgg19': 16.50, 'wide_resnet101_2': 22.30, 'convnext_large': 22.95},
}

# Hourly costs ($/h)
RATE = {
    'slurm': 0.526, 'res_g4': 0.227, 'od_g4': 0.526,
    'res_g5': 0.435, 'od_g5': 1.006,
}

# Workflows: chains and tinyda per iter (from R1 measurements / synthesis)
WFS = {
    'data8': dict(model='vgg19', chains0=4, deadline=6160.60, budget=1.77, submit=0,
                  iters=[(4,12),(6,15),(9,12)]),
    # Dispatch order [8, 9, 5]: data9 second (t=271), data5 third (t=390)
    'data9': dict(model='convnext_large', chains0=3, deadline=8473.22, budget=2.66, submit=271,
                  iters=[(3,20),(4,18),(6,17),(9,11),(10,13)]),
    'data5': dict(model='wide_resnet101_2', chains0=3, deadline=7289.82, budget=2.15, submit=390,
                  iters=[(3,18),(4,20),(6,24),(9,25),(10,25)]),
}

SETUP = 30
INTER = 30

def iter_runtime(model, container, lanes, chains, tinyda):
    """Time for one iter at given chains/tinyda on given container with lanes."""
    fam = 'g4' if container in ('slurm', 'g4') else 'g5'
    per_epoch = EPOCH[fam][model]
    batches = math.ceil(chains / max(lanes, 1))
    return batches * tinyda * per_epoch

def simulate(mode):
    """mode: 'static' or 'moldable'"""
    # Container occupancy: dict container -> list of (wf, lanes, alloc_type)
    # For tracking on-demand intervals: (wf, container, count, start_t, end_t)
    occ = {'slurm': 0, 'g4': 0, 'g5': 0}
    cap = {'slurm': 4, 'g4': 4, 'g5': 4}
    wf_state = {}     # wf -> dict(container, lanes, alloc_breakdown, current_iter, finish_t, iter_log)
    od_intervals = []   # (wf, container, n, start, end)

    # Process submits in order
    submits = sorted(WFS.items(), key=lambda kv: kv[1]['submit'])
    # Track simulation events
    events = []   # (time, event_type, wf)
    for wf, d in submits:
        events.append((d['submit'], 'submit', wf))

    while events:
        events.sort()
        t, etype, wf = events.pop(0)
        d = WFS[wf]

        if etype == 'submit':
            # Decide container
            container = None
            lanes = 0
            alloc = []   # list of (slot_type, count) for cost tracking
            chains0 = d['chains0']

            # On-prem first
            if occ['slurm'] < cap['slurm']:
                free = cap['slurm'] - occ['slurm']
                lanes = min(chains0, free)   # may be partial
                occ['slurm'] += lanes
                container = 'slurm'
                alloc = [('slurm', lanes)]
            else:
                # Cloud path: pick cheapest score
                # For simplicity, replicate the logic: pick g4 vs g5 based on score
                # Score includes cost (cheap reserved → on-demand mix)
                best_score = float('inf')
                best_cont = None
                best_alloc = None
                for c in ('g4', 'g5'):
                    free = cap[c] - occ[c]
                    if free <= 0:
                        continue
                    n = min(chains0, free)
                    rt = iter_runtime(d['model'], c, n, chains0, d['iters'][0][1])
                    # Cost mix: assume reserved first, then on-demand
                    n_res = min(n, 2)
                    n_od = n - n_res
                    rate = (n_res * RATE[f'res_{c}'] + n_od * RATE[f'od_{c}']) / max(n, 1) * n
                    cost = (rt / 3600) * rate
                    score = cost + (rt / d['deadline']) * 0.1
                    if score < best_score:
                        best_score = score
                        best_cont = c
                        # alloc breakdown
                        best_alloc = [(f'res_{c}', n_res), (f'od_{c}', n_od)] if n_od > 0 else [(f'res_{c}', n_res)]
                if best_cont is None:
                    print(f"  {wf}: NO ALLOCATION POSSIBLE")
                    continue
                container = best_cont
                lanes = sum(c for _, c in best_alloc)
                occ[container] += lanes
                alloc = best_alloc
                # Track on-demand intervals
                for slot, n in alloc:
                    if slot.startswith('od_'):
                        od_intervals.append([wf, container, n, t, None])

            iter_start = t + SETUP
            chains_k, tinyda_k = d['iters'][0]
            rt = iter_runtime(d['model'], container, lanes, chains_k, tinyda_k)
            iter_end = iter_start + rt
            wf_state[wf] = dict(container=container, lanes=lanes, alloc=alloc,
                                iter=0, iter_log=[(0, iter_start, iter_end, lanes)])
            events.append((iter_end, 'iter_done', wf))

        elif etype == 'iter_done':
            st = wf_state[wf]
            d = WFS[wf]
            k = st['iter']
            if k + 1 >= len(d['iters']):
                # Done
                st['finish_t'] = t
                # Free lanes
                occ[st['container']] -= st['lanes']
                # Close OD intervals
                for itv in od_intervals:
                    if itv[0] == wf and itv[4] is None:
                        itv[4] = t
                continue

            nk = k + 1
            chains_k, tinyda_k = d['iters'][nk]
            cur = st['lanes']
            container = st['container']

            # Moldable scale-up?
            if mode == 'moldable' and chains_k > cur:
                free = cap[container] - occ[container]
                grant = min(chains_k - cur, free)
                if grant > 0:
                    # Add lanes (assume on-demand if reserved exhausted)
                    new = grant
                    if container in ('g4', 'g5'):
                        # All cloud scale-ups go to on-demand (assume reserved was used initially)
                        st['alloc'].append((f'od_{container}', new))
                        od_intervals.append([wf, container, new, t, None])
                    else:
                        st['alloc'].append(('slurm', new))
                    occ[container] += new
                    st['lanes'] = cur + new
                    cur = st['lanes']

            rt = iter_runtime(d['model'], container, cur, chains_k, tinyda_k)
            iter_start = t + INTER
            iter_end = iter_start + rt
            st['iter_log'].append((nk, iter_start, iter_end, cur))
            st['iter'] = nk
            events.append((iter_end, 'iter_done', wf))

    return wf_state, od_intervals

def report(mode, state, od_intervals):
    print(f"\n{'='*70}\n{mode.upper()}  N=3 dispatch [8, 5, 9]\n{'='*70}")
    total_od_cost = 0
    for wf, st in state.items():
        d = WFS[wf]
        ms = st['finish_t'] - d['submit']
        dl = d['deadline']
        status = 'HIT ' if ms <= dl else 'MISS'
        margin = dl - ms
        print(f"\n{wf} on {st['container']:5} chains₀={d['chains0']}, deadline={dl:.0f}s")
        for k, s, e, l in st['iter_log']:
            print(f"  iter{k}: lanes={l}  [{s:>5.0f}-{e:>5.0f}]  ({e-s:>5.0f}s)")
        print(f"  finish: {st['finish_t']:.0f}s  (submit={d['submit']}, makespan={ms:.0f}s)  {status} by {abs(margin):.0f}s")

    print(f"\n--- On-demand intervals ---")
    for wf, c, n, s, e in od_intervals:
        if e is None: e = max(st['finish_t'] for st in state.values())
        rate = RATE[f'od_{c}']
        cost = n * (e - s) / 3600 * rate
        total_od_cost += cost
        print(f"  {wf} {c}: {n} OD lanes from {s:.0f}–{e:.0f}s = {(e-s)/3600:.2f}h × ${rate}/h × {n} = ${cost:.2f}")

    print(f"\n--- Aggregate ---")
    makespan = max(st['finish_t'] for st in state.values())
    misses = sum(1 for wf, st in state.items() if st['finish_t'] - WFS[wf]['submit'] > WFS[wf]['deadline'])
    sum_flow = sum(st['finish_t'] - WFS[wf]['submit'] for wf, st in state.items())
    print(f"  Total makespan: {makespan:.0f}s  ({makespan/3600:.2f}h)")
    print(f"  Deadline misses: {misses}/3")
    print(f"  Sum of flowtimes: {sum_flow:.0f}s")
    print(f"  Total on-demand cost: ${total_od_cost:.2f}")

if __name__ == '__main__':
    s_state, s_od = simulate('static')
    report('static', s_state, s_od)
    m_state, m_od = simulate('moldable')
    report('moldable', m_state, m_od)
