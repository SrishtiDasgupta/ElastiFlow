"""
sim_sensitivity_v2.py — TRUE stability analysis: 1000 trials per N, varying:
  1. WHICH 7 wfs are dispatched (sampled from all 15 yamls data0-data14)
  2. DISPATCH ORDER (different Poisson seeds → different submit timings)
  3. HPO non-determinism (epoch per iter + iter count from empirical or defaults)
  4. PER_EPOCH calibration ±20%

Each trial = a plausible alternative campaign, not a re-roll of R7.

Output: distributions of misses/sum-flow/OD-cost per (N, corner) +
moldable-vs-static win rates with much stronger statistical grounding.
"""
import json
import math
import os
import random
import statistics
from collections import defaultdict

import sim_4corners_calibrated as sim4c

random.seed(0)
N_TRIALS = 1000

# -----------------------------------------------------------------------------
# All 15 wf yamls — base specs (model, initial_chains, initial_epoch, n_iters, deadline_s)
# Pulled from src/main/workflow/sample_workflows_HPO/data*.yaml
# -----------------------------------------------------------------------------
WF_YAMLS = {
    'data0':  {'model': 'convnext_large',    'chains_initial': 2, 'tinyda': 20, 'iters': 4, 'deadline': 7003},
    'data1':  {'model': 'vgg19',             'chains_initial': 4, 'tinyda': 12, 'iters': 3, 'deadline': 5196},
    'data2':  {'model': 'convnext_large',    'chains_initial': 2, 'tinyda': 24, 'iters': 5, 'deadline': 7722},
    'data3':  {'model': 'vgg19',             'chains_initial': 4, 'tinyda': 15, 'iters': 3, 'deadline': 5196},
    'data4':  {'model': 'convnext_large',    'chains_initial': 3, 'tinyda': 15, 'iters': 4, 'deadline': 8257},
    'data5':  {'model': 'wide_resnet101_2',  'chains_initial': 3, 'tinyda': 18, 'iters': 5, 'deadline': 7290},
    'data6':  {'model': 'convnext_large',    'chains_initial': 2, 'tinyda': 24, 'iters': 4, 'deadline': 7226},
    'data7':  {'model': 'wide_resnet101_2',  'chains_initial': 2, 'tinyda': 20, 'iters': 4, 'deadline': 5756},
    'data8':  {'model': 'vgg19',             'chains_initial': 4, 'tinyda': 10, 'iters': 3, 'deadline': 6161},
    'data9':  {'model': 'convnext_large',    'chains_initial': 3, 'tinyda': 20, 'iters': 5, 'deadline': 8473},
    'data10': {'model': 'wide_resnet101_2',  'chains_initial': 2, 'tinyda': 15, 'iters': 3, 'deadline': 5890},
    'data11': {'model': 'convnext_large',    'chains_initial': 2, 'tinyda': 18, 'iters': 4, 'deadline': 7676},
    'data12': {'model': 'vgg19',             'chains_initial': 4, 'tinyda': 15, 'iters': 4, 'deadline': 5729},
    'data13': {'model': 'convnext_large',    'chains_initial': 2, 'tinyda': 28, 'iters': 5, 'deadline': 7928},
    'data14': {'model': 'wide_resnet101_2',  'chains_initial': 3, 'tinyda': 16, 'iters': 4, 'deadline': 6717},
}

# Model-specific chain growth pattern (next_trials in iter i = chains_growth[i] * chains_initial)
# Derived from R3-R7 actuals: vgg19 grows aggressively, convnext_large stays flat, wide grows moderately
CHAIN_GROWTH_BY_MODEL = {
    'vgg19':            [1.0, 1.5, 2.25, 3.25, 4.0],   # 4→6→9→13→16
    'wide_resnet101_2': [1.0, 1.33, 2.0, 3.0, 3.33],   # 3→4→6→9→10
    'convnext_large':   [1.0, 1.0, 1.0, 1.0, 1.0],     # stable
}

# Empirical epoch ranges per model (from R3-R7 results.jsonl observations)
# vgg19: iter 0 5-14, iter 1 2-18, iter 2 1-26 → wide range
# wide_resnet101_2: iter 0 ~25, iter 1 ~22, iter 2 ~21 → smaller
# convnext_large: iter 0 19-29, iter 1 17-40, iter 2 17-57 → grows
EPOCH_RANGES_BY_MODEL = {
    'vgg19':            [(5, 14), (2, 18), (1, 26), (1, 30), (1, 35)],
    'wide_resnet101_2': [(15, 30), (15, 30), (15, 30), (20, 35), (25, 40)],
    'convnext_large':   [(15, 30), (15, 40), (15, 60), (20, 70), (30, 100)],
}

# Per the empirical analysis: iter count distribution per model
ITER_COUNT_DIST = {
    'vgg19':            [3, 3, 3, 3, 3, 3, 3, 4, 4, 5],  # mostly 3, sometimes 4-5
    'wide_resnet101_2': [2, 3, 3, 4, 4, 4, 5, 5, 5, 5],
    'convnext_large':   [1, 2, 3, 3, 3, 4, 4, 4, 5, 5],
}


def gen_poisson_dispatch(n_wfs, avg_delay=90, seed=42):
    """Generate Poisson submit times for n_wfs (in seconds, starting at 0)."""
    rng = random.Random(seed)
    times = [0.0]
    t = 0.0
    for _ in range(n_wfs - 1):
        t += rng.expovariate(1.0 / avg_delay)
        times.append(t)
    return times


def perturb_per_epoch(orig, pct=0.20):
    return {k: v * random.uniform(1-pct, 1+pct) for k, v in orig.items()}


def build_wf_spec(name, base, submit, dispatch_seed):
    """Build a perturbed sim4c-style wf spec.
    - n_iters: sample from model's distribution (fall back to yaml's iters)
    - epoch[i]: sample from model's empirical epoch range
    - chains[i] = chains_initial * growth[i] (slight ±10% jitter)
    """
    model = base['model']
    rng = random.Random(dispatch_seed * 1000 + hash(name) % 1000)
    iter_dist = ITER_COUNT_DIST[model]
    n_iters = rng.choice(iter_dist)
    epoch_ranges = EPOCH_RANGES_BY_MODEL[model]
    growth = CHAIN_GROWTH_BY_MODEL[model]
    iters = []
    for i in range(n_iters):
        rng_e = epoch_ranges[min(i, len(epoch_ranges) - 1)]
        epoch = rng.randint(rng_e[0], rng_e[1])
        chains_factor = growth[min(i, len(growth) - 1)] * rng.uniform(0.9, 1.1)
        chains = max(1, round(base['chains_initial'] * chains_factor))
        iters.append((chains, epoch))
    pref_clusters = (['cluster_g5', 'cluster_g4', 'slurm']
                     if model in ('wide_resnet101_2', 'convnext_large')
                     else ['slurm', 'cluster_g4', 'cluster_g5'])
    return {
        'submit': submit,
        'deadline': base['deadline'],
        'model': model,
        'chains_initial': iters[0][0],
        'iters': iters,
        'pref_clusters': pref_clusters,
    }


def run_trial(N, dispatch_seed, hpo_seed, calibration_seed):
    """One trial: sample N wfs from all 15, generate Poisson dispatch, perturb HPO + PER_EPOCH."""
    # Sample N wfs from the 15
    rng = random.Random(dispatch_seed)
    sampled = rng.sample(list(WF_YAMLS.keys()), N)
    # Generate Poisson dispatch
    submits = gen_poisson_dispatch(N, avg_delay=90, seed=dispatch_seed)
    # Build wf specs (HPO non-determinism uses hpo_seed via global random state below)
    random.seed(hpo_seed)
    wfs = {name: build_wf_spec(name, WF_YAMLS[name], submits[i], hpo_seed)
           for i, name in enumerate(sampled)}
    # Perturb PER_EPOCH
    random.seed(calibration_seed)
    pert_pe = perturb_per_epoch(sim4c.PER_EPOCH)

    # Patch sim4c globals
    orig_pe = sim4c.PER_EPOCH
    orig_wfs = sim4c.WFS
    sim4c.PER_EPOCH = pert_pe
    sim4c.WFS = wfs

    out = {}
    try:
        for mode in ('static', 'moldable'):
            for ordering in ('edf', 'fcfs'):
                r = sim4c.simulate(mode, ordering)
                out[(mode, ordering)] = (r['misses'], r['sum_flow_min'], r['od_cost_usd'])
    finally:
        sim4c.PER_EPOCH = orig_pe
        sim4c.WFS = orig_wfs
    return out


def aggregate(trials):
    agg = {}
    for corner in trials[0].keys():
        misses = [t[corner][0] for t in trials]
        sum_flows = [t[corner][1] for t in trials]
        od_costs = [t[corner][2] for t in trials]
        agg[corner] = {
            'misses_mean': statistics.mean(misses),
            'misses_stdev': statistics.stdev(misses) if len(misses)>1 else 0,
            'misses_med': statistics.median(misses),
            'misses_p5': sorted(misses)[int(0.05*len(misses))],
            'misses_p95': sorted(misses)[int(0.95*len(misses))],
            'sum_flow_mean': statistics.mean(sum_flows),
            'sum_flow_stdev': statistics.stdev(sum_flows) if len(sum_flows)>1 else 0,
            'sum_flow_p5': sorted(sum_flows)[int(0.05*len(sum_flows))],
            'sum_flow_p95': sorted(sum_flows)[int(0.95*len(sum_flows))],
            'od_cost_mean': statistics.mean(od_costs),
            'od_cost_stdev': statistics.stdev(od_costs) if len(od_costs)>1 else 0,
            'od_cost_p5': sorted(od_costs)[int(0.05*len(od_costs))],
            'od_cost_p95': sorted(od_costs)[int(0.95*len(od_costs))],
        }
    return agg


def main():
    print(f"Sensitivity v2: {N_TRIALS} trials per N, sampling N wfs from {len(WF_YAMLS)} yamls + Poisson dispatch")
    print(f"  vgg19 wfs available: {sum(1 for w in WF_YAMLS.values() if w['model']=='vgg19')}")
    print(f"  wide_resnet101_2 available: {sum(1 for w in WF_YAMLS.values() if w['model']=='wide_resnet101_2')}")
    print(f"  convnext_large available: {sum(1 for w in WF_YAMLS.values() if w['model']=='convnext_large')}")

    for N in [3, 5, 7]:
        print(f"\n{'='*84}")
        print(f"  N={N} — {N_TRIALS} trials (random wf subset + Poisson seed + HPO + calibration)")
        print(f"{'='*84}")
        trials = []
        moldable_wins_cost = {ordering: 0 for ordering in ('edf', 'fcfs')}
        moldable_wins_misses = {ordering: 0 for ordering in ('edf', 'fcfs')}
        edf_wins_misses_static = 0
        edf_wins_misses_mold = 0
        for t in range(N_TRIALS):
            r = run_trial(N, dispatch_seed=t, hpo_seed=t*7+3, calibration_seed=t*13+5)
            trials.append(r)
            for ordering in ('edf', 'fcfs'):
                s = r[('static', ordering)]
                m = r[('moldable', ordering)]
                if m[2] < s[2]: moldable_wins_cost[ordering] += 1
                if m[0] < s[0]: moldable_wins_misses[ordering] += 1
            if r[('static', 'edf')][0] < r[('static', 'fcfs')][0]:
                edf_wins_misses_static += 1
            if r[('moldable', 'edf')][0] < r[('moldable', 'fcfs')][0]:
                edf_wins_misses_mold += 1

        agg = aggregate(trials)
        print(f"\n  {'corner':<16}  {'misses (mean ± stdev)':>22}  {'sum-flow':>22}  {'OD cost':>30}")
        print(f"  {'-'*98}")
        for corner in [('static','edf'), ('static','fcfs'), ('moldable','edf'), ('moldable','fcfs')]:
            a = agg[corner]
            label = f"{corner[0].upper()} {corner[1].upper()}"
            ms = f"{a['misses_mean']:.2f} ± {a['misses_stdev']:.2f}  ({a['misses_p5']}-{a['misses_p95']})"
            sf = f"{a['sum_flow_mean']:.0f} ± {a['sum_flow_stdev']:.0f} m"
            od = f"${a['od_cost_mean']:.2f} ± ${a['od_cost_stdev']:.2f} (p5-p95: ${a['od_cost_p5']:.2f}-${a['od_cost_p95']:.2f})"
            print(f"  {label:<16}  {ms:>22}  {sf:>22}  {od:>30}")
        print(f"\n  Moldable beats Static across {N_TRIALS} trials:")
        for ordering in ('edf', 'fcfs'):
            wc = moldable_wins_cost[ordering]
            wm = moldable_wins_misses[ordering]
            print(f"    {ordering.upper()}: COST cheaper in {wc}/{N_TRIALS} ({wc/N_TRIALS*100:.0f}%)  |  "
                  f"MISSES fewer in {wm}/{N_TRIALS} ({wm/N_TRIALS*100:.0f}%)")
        print(f"\n  EDF beats FCFS on miss count:")
        print(f"    Static:   {edf_wins_misses_static}/{N_TRIALS} ({edf_wins_misses_static/N_TRIALS*100:.0f}%)")
        print(f"    Moldable: {edf_wins_misses_mold}/{N_TRIALS} ({edf_wins_misses_mold/N_TRIALS*100:.0f}%)")


if __name__ == '__main__':
    main()
