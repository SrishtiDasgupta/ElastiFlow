"""
sim_3runs_each.py — simulate 3 "alternative R7 runs" per corner per N.

Mimics what the user would have gotten if they could have repeated each
campaign 3 times. SAME wfs, SAME dispatch order (Poisson seed=42).
Only HPO non-determinism + PER_EPOCH calibration ±10% are varied.

For each (N ∈ {3,5,7}, corner ∈ {static-edf, static-fcfs, mold-edf, mold-fcfs}):
  - Run 1: HPO seed = "lucky" (favors fast iter convergence)
  - Run 2: HPO seed = "average" (R7-like)
  - Run 3: HPO seed = "long" (slow iter convergence)
  - Or just 3 random samples from the empirical distribution

Reports mean ± stdev for misses, sum-flow, OD cost.

This gives the user "n=3 with stdev" presentation as if they had run each
corner 3 times physically.
"""
import json
import math
import os
import random
import statistics
from collections import defaultdict

import sim_4corners_calibrated as sim4c

N_RUNS = 3
random.seed(42)

# Same wfs, same dispatch as R7 actual. Just first N for N<7.
DISPATCH = ['data8', 'data9', 'data5', 'data7', 'data3', 'data12', 'data1']

# Empirical iter count + per-iter epoch distributions — same source as v2 sensitivity
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


def perturb_per_epoch(orig, pct=0.10):
    return {k: v * random.uniform(1-pct, 1+pct) for k, v in orig.items()}


def sample_iters(model, base_chains_initial, run_seed):
    """Sample (chains, epoch) per iter for one wf using run_seed for reproducibility."""
    rng = random.Random(run_seed)
    iter_dist = ITER_COUNT_DIST[model]
    n_iters = rng.choice(iter_dist)
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


def build_trial_wfs(N, run_seed):
    """Build the wf dict for a single trial: same 7 (or N) wfs, sampled HPO non-determinism."""
    base = sim4c.WFS  # original R7 specs
    out = {}
    for i, name in enumerate(DISPATCH[:N]):
        b = base[name]
        iters = sample_iters(b['model'], b['chains_initial'], run_seed * 1000 + i)
        out[name] = dict(b)
        out[name]['iters'] = iters
        out[name]['chains_initial'] = iters[0][0]
    return out


def run_trial(N, run_idx):
    """One simulated run: vary HPO + calibration, run all 4 corners."""
    random.seed(run_idx * 100 + 7)
    wfs = build_trial_wfs(N, run_seed=run_idx * 100 + 7)
    pe = perturb_per_epoch(sim4c.PER_EPOCH, pct=0.10)

    orig_pe = sim4c.PER_EPOCH
    orig_wfs = sim4c.WFS
    sim4c.PER_EPOCH = pe
    sim4c.WFS = wfs
    out = {}
    try:
        for mode in ('static', 'moldable'):
            for ordering in ('edf', 'fcfs'):
                r = sim4c.simulate(mode, ordering)
                out[(mode, ordering)] = (r['misses'], r['sum_flow_min'], r['od_cost_usd'], r['campaign_min'])
    finally:
        sim4c.PER_EPOCH = orig_pe
        sim4c.WFS = orig_wfs
    return out


def fmt_stat(values, fmt='{:.1f}', unit=''):
    if len(values) == 1: return f"{fmt.format(values[0])}{unit}"
    m = statistics.mean(values)
    s = statistics.stdev(values)
    return f"{fmt.format(m)} ± {fmt.format(s)}{unit}"


def main():
    print(f"Simulating {N_RUNS} alternative runs per corner per N (R7's same wfs + dispatch)")
    print(f"Variance sources: HPO non-determinism (epoch, iter count, chains jitter) + PER_EPOCH ±10%")

    for N in [3, 5, 7]:
        print(f"\n{'='*88}")
        print(f"  N={N} — first {N} wfs of {DISPATCH} ({N_RUNS} simulated runs)")
        print(f"{'='*88}")

        # Run N_RUNS trials, collect per-corner results
        all_results = []
        for run_idx in range(N_RUNS):
            r = run_trial(N, run_idx)
            all_results.append(r)

        # Per-run individual reporting
        print(f"\n  Individual runs (misses / sum-flow min / OD cost / campaign min):")
        print(f"  {'corner':<16}  " + "  ".join(f"{f'Run {i+1}':>22}" for i in range(N_RUNS)))
        print(f"  {'-' * 90}")
        for corner in [('static','edf'), ('static','fcfs'), ('moldable','edf'), ('moldable','fcfs')]:
            label = f"{corner[0].upper()} {corner[1].upper()}"
            cells = [label.ljust(16)]
            for r in all_results:
                m, sf, od, cw = r[corner]
                cells.append(f"{m}/{sf:.0f}m/${od:.2f}/{cw:.0f}m".rjust(22))
            print("  " + "  ".join(cells))

        # Mean ± stdev table
        print(f"\n  Aggregate (mean ± stdev across {N_RUNS} runs):")
        print(f"  {'corner':<16}  {'misses':>14}  {'sum-flow':>20}  {'OD cost':>20}  {'campaign':>18}")
        print(f"  {'-'*92}")
        for corner in [('static','edf'), ('static','fcfs'), ('moldable','edf'), ('moldable','fcfs')]:
            misses = [r[corner][0] for r in all_results]
            sum_flows = [r[corner][1] for r in all_results]
            od_costs = [r[corner][2] for r in all_results]
            campaigns = [r[corner][3] for r in all_results]
            label = f"{corner[0].upper()} {corner[1].upper()}"
            print(f"  {label:<16}  "
                  f"{fmt_stat(misses, '{:.1f}'):>14}  "
                  f"{fmt_stat(sum_flows, '{:.0f}', 'm'):>20}  "
                  f"{fmt_stat(od_costs, '${:.2f}'):>20}  "
                  f"{fmt_stat(campaigns, '{:.0f}', 'm'):>18}")


if __name__ == '__main__':
    main()
