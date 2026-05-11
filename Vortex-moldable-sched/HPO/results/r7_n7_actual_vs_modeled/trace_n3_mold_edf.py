"""
trace_n3_mold_edf.py — full verbose trace for N=3 Moldable EDF across 3 simulated runs.

Shows for each of the 3 runs:
  1. Inputs that varied:
     - HPO non-determinism: per-wf sampled iter count + (chains, epoch) per iter
     - PER_EPOCH perturbation: each (model, cluster) cell scaled by U(0.9, 1.1)
  2. Inputs held FIXED:
     - Workflow set (data8, data9, data5), dispatch order, submit times, deadlines
     - Cluster topology (4 lanes each for slurm/g4/g5), reserved/OD split (4/0, 2/2, 2/2)
     - Cold-start factor 3.2×, OD creation 300s, alloc overhead 30s, inter-iter 30s
  3. Per-event trace (arrive / iter_start / iter_end / wf_complete / OD spawn / OD close)
  4. Final per-wf makespan, miss, OD cost decomposition

Reuses the simulate() engine from sim_4corners_calibrated by monkey-patching
its globals (WFS + PER_EPOCH) per run, exactly as sim_3runs_each.py does.
"""
import math
import heapq
import random
import statistics
import sim_4corners_calibrated as sim4c


DISPATCH = ['data8', 'data9', 'data5']  # first 3 of R7's Poisson seed=42 order
N = 3
N_RUNS = 3

ITER_COUNT_DIST = {
    'vgg19':            [3, 3, 3, 3, 3, 3, 3, 4, 4, 5],
    'wide_resnet101_2': [2, 3, 3, 4, 4, 4, 5, 5, 5, 5],
    'convnext_large':   [1, 2, 3, 3, 3, 4, 4, 4, 5, 5],
}
EPOCH_RANGES_BY_MODEL = {
    'vgg19':            [(5, 14), (2, 18), (1, 26), (1, 30), (1, 35)],
    'wide_resnet101_2': [(15, 30), (15, 30), (15, 30), (20, 35), (25, 40)],
    'convnext_large':   [(15, 30), (15, 40), (15, 60), (20, 70), (30, 100)],
}
CHAIN_GROWTH_BY_MODEL = {
    'vgg19':            [1.0, 1.5, 2.25, 3.25, 4.0],
    'wide_resnet101_2': [1.0, 1.33, 2.0, 3.0, 3.33],
    'convnext_large':   [1.0, 1.0, 1.0, 1.0, 1.0],
}


def sample_iters(model, base_chains_initial, run_seed):
    rng = random.Random(run_seed)
    n_iters = rng.choice(ITER_COUNT_DIST[model])
    epoch_ranges = EPOCH_RANGES_BY_MODEL[model]
    growth = CHAIN_GROWTH_BY_MODEL[model]
    iters = []
    for i in range(n_iters):
        rng_e = epoch_ranges[min(i, len(epoch_ranges) - 1)]
        epoch = rng.randint(rng_e[0], rng_e[1])
        chains_factor = growth[min(i, len(growth) - 1)] * rng.uniform(0.95, 1.05)
        chains = max(1, round(base_chains_initial * chains_factor))
        iters.append((chains, epoch))
    return iters


def build_trial_wfs(run_seed):
    base = sim4c.WFS
    out = {}
    for i, name in enumerate(DISPATCH):
        b = base[name]
        iters = sample_iters(b['model'], b['chains_initial'], run_seed * 1000 + i)
        out[name] = dict(b)
        out[name]['iters'] = iters
        out[name]['chains_initial'] = iters[0][0]
    return out


def perturb_per_epoch(orig, pct, rng):
    return {k: round(v * rng.uniform(1-pct, 1+pct), 2) for k, v in orig.items()}


def fmt_iters(iters):
    return "[" + ", ".join(f"({c},{e})" for c, e in iters) + "]"


def fmt_seconds(s):
    if s >= 3600: return f"{s/3600:.2f}h ({s:.0f}s)"
    if s >= 60: return f"{s/60:.1f}m ({s:.0f}s)"
    return f"{s:.0f}s"


def run_one(run_idx):
    print("\n" + "█" * 100)
    print(f"  RUN {run_idx+1} of {N_RUNS}  —  N=3 Moldable EDF")
    print("█" * 100)

    # === Step 1: print INPUTS THAT VARY ===
    seed = run_idx * 100 + 7
    rng = random.Random(seed)
    wfs = build_trial_wfs(seed)
    pe = perturb_per_epoch(sim4c.PER_EPOCH, 0.10, rng)

    print(f"\n  Run seed = {seed}")
    print(f"\n  [1] HPO non-determinism — sampled (chains, epoch) per iter per wf")
    print(f"  {'wf':6}  {'model':<18} {'iter_count':>10}   sampled iters (chains, epoch)")
    print(f"  {'-'*100}")
    for name in DISPATCH:
        w = wfs[name]
        base = sim4c.WFS[name]
        print(f"  {name:6}  {w['model']:<18} {len(w['iters']):>10}   {fmt_iters(w['iters'])}")
        print(f"  {'':6}  {'(baseline)':<18} {len(base['iters']):>10}   {fmt_iters(base['iters'])}")

    print(f"\n  [2] PER_EPOCH perturbation — ±10 %  (baseline → perturbed seconds per epoch)")
    print(f"  {'(model, cluster)':<40} {'base':>8}  {'perturbed':>10}  {'Δ%':>6}")
    print(f"  {'-'*70}")
    for k, base_v in sim4c.PER_EPOCH.items():
        new_v = pe[k]
        delta = (new_v - base_v) / base_v * 100
        if k[0] == wfs[DISPATCH[0]]['model'] or k[0] == wfs[DISPATCH[1]]['model'] or k[0] == wfs[DISPATCH[2]]['model']:
            print(f"  {str(k):<40} {base_v:>8.1f}  {new_v:>10.2f}  {delta:>+5.1f}")

    print(f"\n  [3] INPUTS HELD FIXED")
    print(f"  Dispatch order   : {DISPATCH}")
    print(f"  Submit times (s) : " + ", ".join(f"{n}={sim4c.WFS[n]['submit']}" for n in DISPATCH))
    print(f"  Deadlines (s)    : " + ", ".join(f"{n}={sim4c.WFS[n]['deadline']}" for n in DISPATCH))
    print(f"  Pref clusters    : " + " | ".join(f"{n}={'/'.join(sim4c.WFS[n]['pref_clusters'])}" for n in DISPATCH))
    print(f"  CLUSTER_CAP      : slurm=4, cluster_g4=4, cluster_g5=4  (reserved 4/2/2)")
    print(f"  Cold start factor: 3.2×  on iter 0 of freshly-spawned OD")
    print(f"  Overheads        : alloc=30s, inter-iter=30s, OD-create=300s")

    # === Step 2: instrument simulate() ===
    # Monkey-patch the helpers we want to log. Re-implement simulate() inline so we
    # can print on each event.
    orig_pe = sim4c.PER_EPOCH
    orig_wfs = sim4c.WFS
    sim4c.PER_EPOCH = pe
    sim4c.WFS = wfs

    # We'll instrument by reimplementing simulate() body verbatim with prints.
    from sim_4corners_calibrated import (
        CLUSTER_CAP, RESERVED, OD_CREATION_S, ALLOC_OVERHEAD_S, INTER_ITER_OVERHEAD_S,
        RATE_OD, COLD_START_FACTOR, per_iter_dur, order_key
    )
    mode, ordering = 'moldable', 'edf'

    free = dict(CLUSTER_CAP)
    state = {n: 'pending' for n in wfs}
    completions = {}
    od_intervals = []
    eq = []
    for n, w in wfs.items():
        heapq.heappush(eq, (w['submit'], 0, 'arrive', n))
    seq = [0]
    events = []

    def push(t, kind, payload):
        seq[0] += 1
        heapq.heappush(eq, (t, seq[0], kind, payload))

    def n_od_used(c):
        in_use = CLUSTER_CAP[c] - free[c]
        return max(0, in_use - RESERVED[c])

    def log(t, msg):
        events.append((t, msg))

    def allocate_to(name, c, lanes_want, t_now):
        actually = min(lanes_want, free[c])
        if actually <= 0:
            return 0
        pre_od = n_od_used(c)
        free[c] -= actually
        post_od = n_od_used(c)
        od_added = post_od - pre_od
        is_first_od = od_added > 0
        chains, epoch = wfs[name]['iters'][0]
        start_t = t_now + ALLOC_OVERHEAD_S
        if is_first_od:
            start_t += OD_CREATION_S
            od_intervals.append([c, od_added, start_t, None])
            log(t_now, f"        OD spawn: +{od_added} on {c}  (ready at t={start_t}s, +300s creation)")
        dur = per_iter_dur(wfs[name]['model'], c, chains, actually, epoch, is_first_iter_with_od=is_first_od)
        end_t = start_t + dur
        state[name] = ['running', c, actually, 0, end_t]
        push(end_t, 'iter_end', name)
        cold = " COLD3.2×" if is_first_od else ""
        log(t_now, f"      ALLOCATE {name}: {actually} lanes on {c}  iter0(chains={chains},epoch={epoch}) "
                   f"start={start_t}s end={end_t:.0f}s dur={fmt_seconds(dur)}{cold}")
        return actually

    def try_allocate_blocked(t_now):
        queued = sorted([n for n, s in state.items() if s == 'queued'], key=lambda x: order_key(x, ordering))
        for name in queued:
            w = wfs[name]
            chains_init = w['chains_initial']
            for c in w['pref_clusters']:
                if free[c] >= 1:
                    allocate_to(name, c, min(chains_init, free[c]), t_now)
                    break

    while eq:
        t, _, kind, name = heapq.heappop(eq)
        if kind == 'arrive':
            state[name] = 'queued'
            log(t, f"  ARRIVE  {name}  (free: slurm={free['slurm']} g4={free['cluster_g4']} g5={free['cluster_g5']})")
            try_allocate_blocked(t)
        elif kind == 'iter_end':
            cur = state[name]
            if cur == 'done': continue
            _, c, lanes, iter_idx, _ = cur
            w = wfs[name]
            next_idx = iter_idx + 1
            log(t, f"  ITER_END  {name}  iter{iter_idx} done on {c} with {lanes} lanes")
            if next_idx >= len(w['iters']):
                completions[name] = t
                pre_od = n_od_used(c)
                free[c] += lanes
                post_od = n_od_used(c)
                od_freed = pre_od - post_od
                if od_freed > 0:
                    log(t, f"      OD release: -{od_freed} on {c}")
                    for itv in reversed(od_intervals):
                        if itv[3] is None and itv[0] == c and od_freed > 0:
                            close_n = min(itv[1], od_freed)
                            if close_n == itv[1]:
                                itv[3] = t; od_freed -= close_n
                            else:
                                itv[1] -= close_n
                                od_intervals.append([c, close_n, itv[2], t])
                                od_freed -= close_n
                state[name] = 'done'
                log(t, f"      COMPLETE {name}  makespan={(t - w['submit'])/60:.1f}m  "
                       f"deadline={w['deadline']/60:.1f}m  "
                       f"{'HIT' if (t-w['submit']) <= w['deadline'] else 'MISS'}")
                try_allocate_blocked(t)
                continue
            chains_next, epoch_next = w['iters'][next_idx]
            new_lanes = lanes
            if chains_next < lanes:
                rel = lanes - chains_next
                pre_od = n_od_used(c)
                free[c] += rel
                post_od = n_od_used(c)
                od_freed = pre_od - post_od
                if od_freed > 0:
                    log(t, f"      SHRINK {name}: -{rel} lanes on {c}  (-{od_freed} OD released)")
                    for itv in reversed(od_intervals):
                        if itv[3] is None and itv[0] == c and od_freed > 0:
                            close_n = min(itv[1], od_freed)
                            if close_n == itv[1]:
                                itv[3] = t; od_freed -= close_n
                            else:
                                itv[1] -= close_n
                                od_intervals.append([c, close_n, itv[2], t])
                                od_freed -= close_n
                else:
                    log(t, f"      SHRINK {name}: -{rel} lanes on {c}  (still reserved-only)")
                new_lanes = chains_next
                try_allocate_blocked(t)
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
                        log(t, f"      GROW {name}: +{add} lanes on {c}  (+{od_added} OD spawned)")
                    else:
                        log(t, f"      GROW {name}: +{add} lanes on {c}  (within reserved)")
                    new_lanes += add
                else:
                    log(t, f"      GROW {name}: +0 (wanted +{add_want} but free[{c}]=0)")
            start_t = t + INTER_ITER_OVERHEAD_S
            dur = per_iter_dur(w['model'], c, chains_next, new_lanes, epoch_next)
            end_t = start_t + dur
            state[name] = ['running', c, new_lanes, next_idx, end_t]
            push(end_t, 'iter_end', name)
            log(t, f"      NEXT_ITER {name}: iter{next_idx}(chains={chains_next},epoch={epoch_next}) "
                   f"on {c}/{new_lanes}  end={end_t:.0f}s  dur={fmt_seconds(dur)}")

    print(f"\n  [4] EVENT TRACE (chronological)")
    for t, m in events:
        print(f"  t={t:>6.0f}s  {m}")

    # Cost
    t_end = max(completions.values(), default=0)
    od_cost = 0.0
    print(f"\n  [5] OD-INTERVAL → COST BREAKDOWN")
    print(f"  {'cluster':<12} {'n_od':>5} {'start (s)':>10} {'end (s)':>10} {'dur':>10} {'rate $/h':>10} {'cost':>8}")
    for c, n_od, s, e in od_intervals:
        e_eff = e if e is not None else t_end
        dur_h = (e_eff - s) / 3600
        cost = n_od * dur_h * RATE_OD.get(c, 0)
        od_cost += cost
        print(f"  {c:<12} {n_od:>5} {s:>10.0f} {e_eff:>10.0f} {fmt_seconds(e_eff-s):>10} "
              f"{RATE_OD.get(c,0):>10.3f} {'$'+f'{cost:.2f}':>8}")
    print(f"  {' '*60} TOTAL OD COST  ${od_cost:.2f}")

    # Summary
    print(f"\n  [6] RUN {run_idx+1} SUMMARY")
    print(f"  {'wf':6} {'makespan':>10} {'deadline':>10} {'margin':>10} {'status':>7}")
    misses = 0
    sum_flow = 0
    for name in DISPATCH:
        w = wfs[name]
        if name in completions:
            ms = (completions[name] - w['submit']) / 60
            dl = w['deadline'] / 60
            margin = dl - ms
            stat = 'HIT' if ms <= dl else 'MISS'
            sum_flow += ms
            if stat == 'MISS': misses += 1
            print(f"  {name:6} {ms:>8.1f}m {dl:>8.1f}m {margin:>+8.1f}m {stat:>7}")
        else:
            print(f"  {name:6} {'STUCK':>10} {w['deadline']/60:>8.1f}m  {'?':>10} {'STUCK':>7}")
    campaign = t_end / 60
    print(f"  → misses={misses}/{N}  sum-flow={sum_flow:.1f}m  campaign={campaign:.1f}m  OD-cost=${od_cost:.2f}")

    sim4c.PER_EPOCH = orig_pe
    sim4c.WFS = orig_wfs
    return {'misses': misses, 'sum_flow': sum_flow, 'campaign': campaign, 'od_cost': od_cost}


def main():
    print("=" * 100)
    print("  FULL TRACE — N=3 Moldable EDF — 3 simulated alternative runs")
    print("=" * 100)
    print("""
  GOAL: Show exactly what is varied across the 3 runs and what the model does
        with those variations. Run 1/2/3 share the SAME workflows (data8,
        data9, data5), SAME deadlines, SAME dispatch order, SAME cluster
        topology. They DIFFER in:
            (a) sampled iter count + (chains, epoch) per iter (HPO non-det)
            (b) PER_EPOCH ±10 % (calibration uncertainty)
""")

    results = []
    for i in range(N_RUNS):
        results.append(run_one(i))

    # Aggregate
    print("\n" + "=" * 100)
    print("  AGGREGATE ACROSS 3 RUNS")
    print("=" * 100)
    print(f"\n  {'metric':<14} {'Run 1':>12} {'Run 2':>12} {'Run 3':>12} "
          f"{'mean':>10} {'stdev':>10}")
    print("  " + "-"*80)
    for key, label, fmt in [('misses','misses','{:.1f}'),
                             ('sum_flow','sum-flow (m)','{:.1f}'),
                             ('campaign','campaign (m)','{:.1f}'),
                             ('od_cost','OD cost ($)','{:.2f}')]:
        vals = [r[key] for r in results]
        m = statistics.mean(vals)
        s = statistics.stdev(vals) if len(vals)>1 else 0
        cells = [fmt.format(v) for v in vals]
        print(f"  {label:<14} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12} "
              f"{fmt.format(m):>10} {fmt.format(s):>10}")


if __name__ == '__main__':
    main()
