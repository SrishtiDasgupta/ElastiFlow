"""
metrics_n3.py — full metrics + analysis for N=5 across 2 independent batches.

Batch 1 (option A — original): seeds [7, 107, 207]
Batch 2 (option B — robustness): seeds [1007, 1107, 1207]
Combined: 6 runs per corner.

For each (corner ∈ {static-edf, static-fcfs, mold-edf, mold-fcfs}, seed):
  - Same wfs (data8, data9, data5), same dispatch, same overheads.
  - Sampled per-iter (chains, epoch) from empirical R3-R7 distribution.
  - PER_EPOCH ±10 % perturbation.
  - 'static' = chains_initial lanes only if fully available; else queued.
  - 'moldable' = grab ≥1 lane partial; grow/shrink at iter boundary.

Then compute and print every metric we discussed:
  - deadline:  misses (mean±stdev), per-wf P(miss), slack distribution
  - time:      sum-flow, campaign, per-wf makespan
  - cost:      OD $, cost/HIT, cost/completed-wf
  - resource:  OD-hours total, by cluster, OD utilisation
  - overhead:  cold-start time, OD-create idle, inter-iter overhead
  - paired:    Δ(mold-static) on misses/sum-flow/cost per matched seed, stdev of Δ
  - stability: CV = stdev/mean
  - fairness:  stdev of slack across wfs within a run
  - batch:     batch-1 vs batch-2 means → robustness check
"""
import math
import random
import statistics
import heapq
from collections import defaultdict

import sim_4corners_calibrated as sim4c

DISPATCH = ['data8', 'data9', 'data5', 'data7', 'data3']
N = 5
BATCH1_SEEDS = [7, 107, 207]
BATCH2_SEEDS = [1007, 1107, 1207]
ALL_SEEDS = BATCH1_SEEDS + BATCH2_SEEDS

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

CORNERS = [('static','edf'), ('static','fcfs'), ('moldable','edf'), ('moldable','fcfs')]


def sample_iters(model, base_chains, rng):
    n_iters = rng.choice(ITER_COUNT_DIST[model])
    ranges = EPOCH_RANGES_BY_MODEL[model]; growth = CHAIN_GROWTH_BY_MODEL[model]
    out = []
    for i in range(n_iters):
        lo, hi = ranges[min(i, len(ranges)-1)]
        epoch = rng.randint(lo, hi)
        f = growth[min(i, len(growth)-1)] * rng.uniform(0.95, 1.05)
        chains = max(1, round(base_chains * f))
        out.append((chains, epoch))
    return out


def build_wfs(seed):
    rng = random.Random(seed)
    base = sim4c.WFS
    out = {}
    for name in DISPATCH:
        b = base[name]
        iters = sample_iters(b['model'], b['chains_initial'], rng)
        out[name] = dict(b)
        out[name]['iters'] = iters
        out[name]['chains_initial'] = iters[0][0]
    return out


def perturb_pe(seed):
    rng = random.Random(seed + 99999)
    return {k: v * rng.uniform(0.9, 1.1) for k, v in sim4c.PER_EPOCH.items()}


def run_corner(mode, ordering, seed, wfs, pe):
    """Run sim and also gather extra telemetry (cluster choices, OD by cluster, overhead)."""
    sim4c.WFS = wfs; sim4c.PER_EPOCH = pe
    # Re-implement with telemetry hooks (copy of sim4c.simulate)
    free = dict(sim4c.CLUSTER_CAP)
    state = {n: 'pending' for n in wfs}
    completions = {}; cluster_choice = {}
    od_intervals = []  # [cluster, n, start, end]
    cold_start_time = 0.0; od_create_idle = 0.0; inter_iter_overhead = 0.0
    eq = []
    for n, w in wfs.items():
        heapq.heappush(eq, (w['submit'], 0, 'arrive', n))
    seq = [0]
    def push(t, k, p):
        seq[0]+=1; heapq.heappush(eq, (t, seq[0], k, p))
    def n_od_used(c):
        return max(0, sim4c.CLUSTER_CAP[c] - free[c] - sim4c.RESERVED[c])

    def allocate(name, c, want, tn):
        nonlocal cold_start_time, od_create_idle
        actually = min(want, free[c])
        if actually <= 0: return 0
        pre = n_od_used(c); free[c] -= actually; post = n_od_used(c)
        od_added = post - pre; is_first_od = od_added > 0
        chains, epoch = wfs[name]['iters'][0]
        start = tn + sim4c.ALLOC_OVERHEAD_S
        if is_first_od:
            start += sim4c.OD_CREATION_S
            od_intervals.append([c, od_added, start, None])
            od_create_idle += sim4c.OD_CREATION_S
        pe_val = pe[(wfs[name]['model'], c)]
        base_dur = epoch * math.ceil(chains/max(actually,1)) * pe_val
        if is_first_od:
            cs_extra = base_dur * (sim4c.COLD_START_FACTOR - 1)
            cold_start_time += cs_extra
            dur = base_dur * sim4c.COLD_START_FACTOR
        else:
            dur = base_dur
        end = start + dur
        state[name] = ['running', c, actually, 0, end]
        cluster_choice[name] = c
        push(end, 'iter_end', name)
        return actually

    def try_alloc(tn):
        q = sorted([n for n,s in state.items() if s=='queued'],
                   key=lambda x: sim4c.WFS[x]['submit']+sim4c.WFS[x]['deadline'] if ordering=='edf'
                   else sim4c.WFS[x]['submit'])
        for name in q:
            w = wfs[name]; ci = w['chains_initial']
            for c in w['pref_clusters']:
                if mode=='static':
                    if free[c] >= ci:
                        allocate(name, c, ci, tn); break
                else:
                    if free[c] >= 1:
                        allocate(name, c, min(ci, free[c]), tn); break

    while eq:
        t, _, k, name = heapq.heappop(eq)
        if k=='arrive':
            state[name]='queued'; try_alloc(t)
        else:
            cur = state[name]
            if cur=='done': continue
            _, c, lanes, i, _ = cur
            w = wfs[name]
            ni = i+1
            if ni >= len(w['iters']):
                completions[name] = t
                pre=n_od_used(c); free[c]+=lanes; post=n_od_used(c)
                f = pre-post
                if f>0:
                    for itv in reversed(od_intervals):
                        if itv[3] is None and itv[0]==c and f>0:
                            cn=min(itv[1], f)
                            if cn==itv[1]: itv[3]=t; f-=cn
                            else: itv[1]-=cn; od_intervals.append([c,cn,itv[2],t]); f-=cn
                state[name]='done'
                try_alloc(t); continue
            cn_next, e_next = w['iters'][ni]
            new_lanes = lanes
            if mode=='moldable':
                if cn_next < lanes:
                    rel=lanes-cn_next; pre=n_od_used(c); free[c]+=rel; post=n_od_used(c)
                    f=pre-post
                    if f>0:
                        for itv in reversed(od_intervals):
                            if itv[3] is None and itv[0]==c and f>0:
                                cnn=min(itv[1],f)
                                if cnn==itv[1]: itv[3]=t; f-=cnn
                                else: itv[1]-=cnn; od_intervals.append([c,cnn,itv[2],t]); f-=cnn
                    new_lanes=cn_next; try_alloc(t)
                elif cn_next > lanes:
                    aw=cn_next-lanes; pre=n_od_used(c); add=min(aw, free[c])
                    if add>0:
                        free[c]-=add; post=n_od_used(c); oa=post-pre
                        if oa>0: od_intervals.append([c,oa,t,None]); od_create_idle += 0  # grow doesn't add 300s here
                        new_lanes += add
            start = t + sim4c.INTER_ITER_OVERHEAD_S
            inter_iter_overhead += sim4c.INTER_ITER_OVERHEAD_S
            pe_val = pe[(w['model'], c)]
            dur = e_next * math.ceil(cn_next/max(new_lanes,1)) * pe_val
            end = start + dur
            state[name] = ['running', c, new_lanes, ni, end]
            push(end, 'iter_end', name)

    sim4c.WFS = sim4c.WFS  # restore in caller
    t_end = max(completions.values(), default=0)
    od_cost = 0.0; od_hours_by_cluster = defaultdict(float)
    for c, nod, s, e in od_intervals:
        e_eff = e if e is not None else t_end
        h = nod * (e_eff - s) / 3600
        od_hours_by_cluster[c] += h
        od_cost += h * sim4c.RATE_OD.get(c, 0)
    per_wf = {}
    for name in wfs:
        w = wfs[name]
        if name in completions:
            ms_s = completions[name] - w['submit']
            slack_s = w['deadline'] - ms_s
            hit = ms_s <= w['deadline']
            per_wf[name] = {'makespan_s': ms_s, 'slack_s': slack_s, 'hit': hit,
                            'cluster': cluster_choice.get(name)}
        else:
            per_wf[name] = {'makespan_s': None, 'slack_s': None, 'hit': False,
                            'cluster': cluster_choice.get(name)}
    return {
        'misses': sum(1 for r in per_wf.values() if not r['hit']),
        'sum_flow_s': sum(r['makespan_s'] for r in per_wf.values() if r['makespan_s'] is not None),
        'campaign_s': t_end,
        'od_cost_usd': od_cost,
        'od_hours_total': sum(od_hours_by_cluster.values()),
        'od_hours_by_cluster': dict(od_hours_by_cluster),
        'cold_start_s': cold_start_time,
        'od_create_idle_s': od_create_idle,
        'inter_iter_s': inter_iter_overhead,
        'per_wf': per_wf,
    }


def run_all():
    """Run every (corner, seed). Returns nested dict: results[corner][seed] = output."""
    orig_wfs = sim4c.WFS; orig_pe = sim4c.PER_EPOCH
    results = {c: {} for c in CORNERS}
    for seed in ALL_SEEDS:
        wfs = build_wfs(seed); pe = perturb_pe(seed)
        for mode, ordering in CORNERS:
            sim4c.WFS = wfs; sim4c.PER_EPOCH = pe
            results[(mode, ordering)][seed] = run_corner(mode, ordering, seed, wfs, pe)
    sim4c.WFS = orig_wfs; sim4c.PER_EPOCH = orig_pe
    return results


def mean_stdev(xs):
    if not xs: return (0,0)
    m = statistics.mean(xs)
    s = statistics.stdev(xs) if len(xs)>1 else 0
    return (m, s)


def fmt_ms(m, s, unit='', fmt='{:.1f}'):
    return f"{fmt.format(m)}{unit} ± {fmt.format(s)}{unit}"


def metric_table(results, seeds, label):
    print(f"\n{'='*100}")
    print(f"  {label}  (n={len(seeds)} runs, seeds={seeds})")
    print(f"{'='*100}")
    # 1. Aggregate per corner
    print(f"\n  [a] AGGREGATE METRICS PER CORNER")
    print(f"  {'corner':<14} {'misses':>14} {'sum-flow (m)':>18} {'campaign (m)':>18} "
          f"{'OD cost ($)':>18} {'CV(cost)':>10}")
    print(f"  {'-'*92}")
    for corner in CORNERS:
        runs = [results[corner][s] for s in seeds]
        misses_ms = mean_stdev([r['misses'] for r in runs])
        sf_ms = mean_stdev([r['sum_flow_s']/60 for r in runs])
        cw_ms = mean_stdev([r['campaign_s']/60 for r in runs])
        od_ms = mean_stdev([r['od_cost_usd'] for r in runs])
        cv = od_ms[1]/od_ms[0] if od_ms[0]>0 else 0
        label_c = f"{corner[0][:4].upper()} {corner[1].upper()}"
        print(f"  {label_c:<14} {fmt_ms(*misses_ms,'',fmt='{:.1f}'):>14} "
              f"{fmt_ms(*sf_ms,'m',fmt='{:.1f}'):>18} "
              f"{fmt_ms(*cw_ms,'m',fmt='{:.1f}'):>18} "
              f"{fmt_ms(*od_ms,'',fmt='${:.2f}'):>18} "
              f"{cv:>9.2f}")

    # 2. Per-wf miss probability + makespan
    print(f"\n  [b] PER-WF: P(miss) AND MAKESPAN (mean ± stdev, minutes)")
    header = f"  {'corner':<14}"
    for name in DISPATCH:
        header += f"  {name+' P(miss)':>14}  {name+' makespan':>18}"
    print(header)
    print(f"  {'-'*100}")
    for corner in CORNERS:
        runs = [results[corner][s] for s in seeds]
        row = f"  {corner[0][:4].upper()+' '+corner[1].upper():<14}"
        for name in DISPATCH:
            misses = [0 if r['per_wf'][name]['hit'] else 1 for r in runs]
            p_miss = sum(misses)/len(misses)
            ms_vals = [r['per_wf'][name]['makespan_s']/60 for r in runs
                       if r['per_wf'][name]['makespan_s'] is not None]
            m_m, m_s = mean_stdev(ms_vals)
            row += f"  {p_miss:>14.0%}  {f'{m_m:.1f}±{m_s:.1f}m':>18}"
        print(row)

    # 3. OD hours by cluster + overhead
    print(f"\n  [c] RESOURCE BREAKDOWN (mean per run)")
    print(f"  {'corner':<14} {'OD-h g4':>10} {'OD-h g5':>10} {'cold-start':>14} "
          f"{'OD-create-idle':>16} {'inter-iter':>12}")
    print(f"  {'-'*92}")
    for corner in CORNERS:
        runs = [results[corner][s] for s in seeds]
        g4 = statistics.mean([r['od_hours_by_cluster'].get('cluster_g4', 0) for r in runs])
        g5 = statistics.mean([r['od_hours_by_cluster'].get('cluster_g5', 0) for r in runs])
        cs = statistics.mean([r['cold_start_s']/60 for r in runs])
        idle = statistics.mean([r['od_create_idle_s']/60 for r in runs])
        ii = statistics.mean([r['inter_iter_s']/60 for r in runs])
        label_c = f"{corner[0][:4].upper()} {corner[1].upper()}"
        print(f"  {label_c:<14} {g4:>10.2f} {g5:>10.2f} {cs:>12.1f}m "
              f"{idle:>14.1f}m {ii:>10.1f}m")

    # 4. Paired difference: mold − static per seed, per ordering
    print(f"\n  [d] PAIRED DIFFERENCE (moldable − static, matched seed → tight CI on the gap)")
    print(f"  {'ordering':<10} {'Δmisses':>14} {'Δsum-flow (m)':>22} {'Δcampaign (m)':>22} "
          f"{'Δcost ($)':>20}")
    print(f"  {'-'*92}")
    for ordering in ['edf', 'fcfs']:
        d_miss = [results[('moldable',ordering)][s]['misses'] -
                  results[('static',ordering)][s]['misses'] for s in seeds]
        d_sf = [(results[('moldable',ordering)][s]['sum_flow_s'] -
                 results[('static',ordering)][s]['sum_flow_s'])/60 for s in seeds]
        d_cw = [(results[('moldable',ordering)][s]['campaign_s'] -
                 results[('static',ordering)][s]['campaign_s'])/60 for s in seeds]
        d_od = [results[('moldable',ordering)][s]['od_cost_usd'] -
                results[('static',ordering)][s]['od_cost_usd'] for s in seeds]
        print(f"  {ordering.upper():<10} {fmt_ms(*mean_stdev(d_miss),fmt='{:+.2f}'):>14} "
              f"{fmt_ms(*mean_stdev(d_sf),'m',fmt='{:+.1f}'):>22} "
              f"{fmt_ms(*mean_stdev(d_cw),'m',fmt='{:+.1f}'):>22} "
              f"{fmt_ms(*mean_stdev(d_od),fmt='{:+.2f}'):>20}")

    # 5. Slack distribution per corner (fairness)
    print(f"\n  [e] SLACK FAIRNESS — stdev of slack across wfs *within a run* (mean over runs)")
    print(f"  {'corner':<14} {'mean intra-run slack stdev (m)':>34}")
    print(f"  {'-'*52}")
    for corner in CORNERS:
        runs = [results[corner][s] for s in seeds]
        intra = []
        for r in runs:
            slacks = [r['per_wf'][n]['slack_s']/60 for n in DISPATCH
                      if r['per_wf'][n]['slack_s'] is not None]
            if len(slacks) >= 2:
                intra.append(statistics.stdev(slacks))
        if intra:
            label_c = f"{corner[0][:4].upper()} {corner[1].upper()}"
            print(f"  {label_c:<14} {statistics.mean(intra):>32.1f}m")


def main():
    print("="*100)
    print("  N=5 METRICS — Batch 1 (option A) + Batch 2 (option B)")
    print("="*100)
    print(f"""
  Batch 1 seeds: {BATCH1_SEEDS}     (the 3 runs we walked through earlier)
  Batch 2 seeds: {BATCH2_SEEDS}  (independent triplet — robustness check)
  Combined:      n=6 per corner

  Workflows: data8 (vgg19), data9 (convnext_large), data5 (wide_resnet101_2)
  Same dispatch order, same submit times, same deadlines, same cluster topology.
  Each run draws fresh HPO non-determinism + ±10 % PER_EPOCH perturbation.
""")
    results = run_all()
    metric_table(results, BATCH1_SEEDS, "BATCH 1 (original option-A triplet)")
    metric_table(results, BATCH2_SEEDS, "BATCH 2 (option-B robustness triplet)")
    metric_table(results, ALL_SEEDS,     "COMBINED (n=6)")

    # Batch robustness: do the per-corner means agree across batches?
    print(f"\n{'='*100}")
    print(f"  BATCH-1 vs BATCH-2 ROBUSTNESS CHECK")
    print(f"{'='*100}")
    print(f"""
  If batch-1 mean and batch-2 mean agree within combined stdev,
  the reported n=6 mean is robust to seed choice (not a lucky triplet).
""")
    print(f"  {'corner':<14} {'metric':<14} {'B1 mean':>12} {'B2 mean':>12} {'combined ±σ':>16} {'agree?':>8}")
    print(f"  {'-'*80}")
    for corner in CORNERS:
        for key, label, scale in [('misses','misses',1),
                                   ('sum_flow_s','sum-flow (m)',1/60),
                                   ('od_cost_usd','OD cost ($)',1)]:
            v1 = [results[corner][s][key]*scale for s in BATCH1_SEEDS]
            v2 = [results[corner][s][key]*scale for s in BATCH2_SEEDS]
            vc = v1+v2
            m1 = statistics.mean(v1); m2 = statistics.mean(v2)
            mc = statistics.mean(vc); sc = statistics.stdev(vc) if len(vc)>1 else 0
            agree = abs(m1-m2) <= sc
            cl = f"{corner[0][:4].upper()} {corner[1].upper()}"
            fmt = '${:.2f}' if 'cost' in label else '{:.1f}'
            print(f"  {cl:<14} {label:<14} {fmt.format(m1):>12} {fmt.format(m2):>12} "
                  f"{fmt.format(mc)+' ± '+fmt.format(sc):>16} {'YES' if agree else 'NO':>8}")


if __name__ == '__main__':
    main()
