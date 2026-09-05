"""
sim_sensitivity.py — sensitivity analysis for the 4-corner sim.

Sources of variance modeled (from R3-R7 actual measurements):
  1. PER_EPOCH (calibration uncertainty): ±20% uniform
  2. epoch per iter per wf (HPO trial non-determinism): sampled from empirical
     distribution observed in past runs
  3. iter count per wf (HPO termination non-determinism): sampled from
     empirical distribution
  4. chains progression (next_trials): mostly deterministic, ±10% jitter

For each trial, we perturb the model and re-run all 4 corners across
N=3,5,7. Aggregate over N_TRIALS to produce confidence-interval claims.

Output: distributions of (misses, sum-flow, OD cost) per (N, corner),
plus "win-rate" of moldable vs static.
"""
import json
_REPO = __import__('pathlib').Path(__file__).resolve().parents[4]
import math
import random
import statistics
import os
from collections import defaultdict

import sim_4corners_calibrated as sim4c

random.seed(0)
N_TRIALS = 100

# -----------------------------------------------------------------------------
# Empirical distributions from R3-R7 results.jsonl
# -----------------------------------------------------------------------------
HPO_LOGS = str(_REPO / 'use_cases/hpo/results/r7_n7_edf_mold/hpo_logs')
WF_FILES = {
    'data8':  'hpo-60bf3eb4', 'data9': 'hpo-b03a4442',
    'data5':  'hpo-df19b440', 'data7': 'hpo-73120874',
    'data3':  'hpo-a87b8123', 'data12':'hpo-ccb43739',
    'data1':  'hpo-417bb2bd',
}


def load_empirical_distributions():
    """Per wf, extract distribution of (per-iter epoch, iter_count)."""
    dist = {}
    for wf_name, full_id in WF_FILES.items():
        path = f'{HPO_LOGS}/{full_id}_results.jsonl'
        if not os.path.exists(path):
            dist[wf_name] = None; continue
        # Group entries by run (>3600s gap = new run)
        runs = []
        cur_run = []
        prev_ts = 0
        for l in open(path):
            e = json.loads(l)
            ts = e['ts']
            if cur_run and ts - prev_ts > 3600:
                runs.append(cur_run); cur_run = []
            cur_run.append({
                'iter': e['iteration'],
                'epoch': e['result']['config'].get('epoch', e['result']['config'].get('epochs')),
                'next_trials': e['result']['config'].get('next_trials'),
            })
            prev_ts = ts
        if cur_run: runs.append(cur_run)
        # Per-iter epoch distribution + iter count distribution
        per_iter_epochs = defaultdict(list)
        iter_counts = []
        for run in runs:
            iters_in_run = sorted(set(r['iter'] for r in run))
            iter_counts.append(len(iters_in_run))
            for r in run:
                per_iter_epochs[r['iter']].append(r['epoch'])
        dist[wf_name] = {
            'per_iter_epochs': dict(per_iter_epochs),
            'iter_counts': iter_counts,
        }
    return dist


def perturb_per_epoch(orig, pct=0.20):
    return {k: v * random.uniform(1-pct, 1+pct) for k, v in orig.items()}


def perturb_wfs(base_wfs, dist, epoch_jitter=0.5, chains_jitter=0.10):
    """Generate perturbed wf specs.
    - epoch[i]: sample from empirical distribution if available, else jitter ±50%
    - chains[i]: ±10% jitter (mostly stable)
    - iter count: sample from empirical distribution if ≥2 runs available
    """
    perturbed = {}
    for name, w in base_wfs.items():
        d = dist.get(name)
        original_iters = list(w['iters'])
        if d and d['iter_counts'] and len(d['iter_counts']) >= 2:
            n_iters = random.choice(d['iter_counts'])
            n_iters = max(1, min(n_iters, 5))  # clamp
        else:
            n_iters = len(original_iters)
        new_iters = []
        for i in range(n_iters):
            if i < len(original_iters):
                base_chains, base_epoch = original_iters[i]
            else:
                # extrapolate from last
                base_chains, base_epoch = original_iters[-1]
            # Sample epoch from empirical if available
            if d and i in d['per_iter_epochs'] and len(d['per_iter_epochs'][i]) >= 2:
                epoch = random.choice(d['per_iter_epochs'][i])
            else:
                epoch = max(1, int(base_epoch * random.uniform(1-epoch_jitter, 1+epoch_jitter)))
            chains = max(1, int(round(base_chains * random.uniform(1-chains_jitter, 1+chains_jitter))))
            new_iters.append((chains, epoch))
        perturbed[name] = dict(w)
        perturbed[name]['iters'] = new_iters
        perturbed[name]['chains_initial'] = new_iters[0][0]
    return perturbed


def run_trial(N, dist, base_per_epoch, base_wfs):
    """One trial: perturb config, run 4 corners at given N. Returns dict of corner→(misses,sum_flow,od_cost)."""
    pert_pe = perturb_per_epoch(base_per_epoch)
    pert_wfs = perturb_wfs(base_wfs, dist)

    # Patch globals for sim4c
    orig_pe = sim4c.PER_EPOCH
    orig_wfs = sim4c.WFS
    sim4c.PER_EPOCH = pert_pe
    sim4c.WFS = pert_wfs

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
    """trials: list of dicts (corner→(misses, sum_flow, od_cost)).
    Returns per-corner dict with mean/median/stdev/range."""
    agg = {}
    for corner in trials[0].keys():
        misses = [t[corner][0] for t in trials]
        sum_flows = [t[corner][1] for t in trials]
        od_costs = [t[corner][2] for t in trials]
        agg[corner] = {
            'misses_mean': statistics.mean(misses),
            'misses_med': statistics.median(misses),
            'misses_min': min(misses),
            'misses_max': max(misses),
            'sum_flow_mean': statistics.mean(sum_flows),
            'sum_flow_stdev': statistics.stdev(sum_flows) if len(sum_flows) > 1 else 0,
            'od_cost_mean': statistics.mean(od_costs),
            'od_cost_stdev': statistics.stdev(od_costs) if len(od_costs) > 1 else 0,
            'od_cost_p5': sorted(od_costs)[int(0.05 * len(od_costs))],
            'od_cost_p95': sorted(od_costs)[int(0.95 * len(od_costs))],
        }
    return agg


def main():
    print("Loading empirical distributions from R3-R7 results.jsonl ...")
    dist = load_empirical_distributions()
    for name, d in dist.items():
        if d:
            print(f"  {name}: iter_counts={d['iter_counts']}, "
                  f"per_iter_epoch_samples={ {k: len(v) for k,v in d['per_iter_epochs'].items()} }")

    base_per_epoch = dict(sim4c.PER_EPOCH)
    base_wfs_full = dict(sim4c.WFS)
    DISPATCH = ['data8', 'data9', 'data5', 'data7', 'data3', 'data12', 'data1']

    print(f"\nRunning {N_TRIALS} sensitivity trials per N ...")
    for N in [3, 5, 7]:
        print(f"\n{'='*72}")
        print(f"  N={N} — sensitivity ({N_TRIALS} trials)")
        print(f"{'='*72}")
        base_wfs = {n: base_wfs_full[n] for n in DISPATCH[:N]}
        trials = []
        moldable_wins_cost = {ordering: 0 for ordering in ('edf', 'fcfs')}
        moldable_wins_misses = {ordering: 0 for ordering in ('edf', 'fcfs')}
        for t in range(N_TRIALS):
            r = run_trial(N, dist, base_per_epoch, base_wfs)
            trials.append(r)
            for ordering in ('edf', 'fcfs'):
                s = r[('static', ordering)]
                m = r[('moldable', ordering)]
                if m[2] < s[2]: moldable_wins_cost[ordering] += 1
                if m[0] < s[0]: moldable_wins_misses[ordering] += 1

        agg = aggregate(trials)
        print(f"\n  {'corner':<18}  {'misses (med)':>14}  {'sum-flow':>20}  {'OD cost':>20}")
        print(f"  {'-'*82}")
        for corner in [('static','edf'), ('static','fcfs'), ('moldable','edf'), ('moldable','fcfs')]:
            a = agg[corner]
            label = f"{corner[0].upper()} {corner[1].upper()}"
            misses_str = f"{a['misses_mean']:.1f} ({a['misses_min']}-{a['misses_max']})"
            sf_str = f"{a['sum_flow_mean']:.0f} ± {a['sum_flow_stdev']:.0f} min"
            od_str = f"${a['od_cost_mean']:.2f} ± ${a['od_cost_stdev']:.2f} (p5-95: ${a['od_cost_p5']:.2f}-${a['od_cost_p95']:.2f})"
            print(f"  {label:<18}  {misses_str:>14}  {sf_str:>20}  {od_str:>20}")
        print(f"\n  Moldable-vs-Static cost win-rate (across {N_TRIALS} trials):")
        for ordering in ('edf', 'fcfs'):
            wr = moldable_wins_cost[ordering] / N_TRIALS * 100
            print(f"    {ordering.upper()}: moldable cheaper in {moldable_wins_cost[ordering]}/{N_TRIALS} trials = {wr:.0f}%")
        print(f"  Moldable-vs-Static fewer-misses win-rate:")
        for ordering in ('edf', 'fcfs'):
            wr = moldable_wins_misses[ordering] / N_TRIALS * 100
            print(f"    {ordering.upper()}: moldable fewer misses in {moldable_wins_misses[ordering]}/{N_TRIALS} trials = {wr:.0f}%")


if __name__ == '__main__':
    main()
