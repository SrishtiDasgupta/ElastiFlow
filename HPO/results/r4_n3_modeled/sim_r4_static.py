"""R4 N=3 static EDF — modeled from R3 measured trajectory.

Approach:
  - Same dispatch [8, 9, 5], same submit times as R3 actual.
  - Same initial allocations (since static and moldable diverge ONLY at scale-up):
      data8 → slurm 4 (full)
      data9 → cloud-g4 lanes=3
      data5 → slurm partial 1
  - NO scale-ups, NO scale-downs.
  - Use empirical per-wf τ measured from R3 actual (not the scheduler's power-law model).

Per-iter chain/tinyda counts come from R3's actual HPO pipeline output (since the
HPO trajectory depends on the model's accuracy outputs which are deterministic
under the same model+seed).

Output: per-wf static makespan + deadline-miss + cost.
"""
import math

# Empirical per-wf τ (s/epoch), measured from R3 actual iters 0–N.
# Computed as runtime / (ceil(chains/lanes) × tinyda).
TAU = {
    'data8': 45.1,    # vgg19, mean across 3 iters at varied lanes
    'data9': 79.85,   # convnext on cloud-g4, mean across 3 iters
    'data5': 16.46,   # wide_resnet on slurm, mean across 5 iters
}

# Per-iter trajectory (chains, tinyda) from R3 actual measurements
# (HPO pipeline output, deterministic for these wfs)
TRAJECTORY = {
    'data8': [(4, 10), (6, 6), (9, 8)],                           # 3 iters
    'data9': [(3, 20), (3, 29), (3, 37), (3, 53), (3, 70)],       # 5 iters; iters 3-4 extrapolated (chains stayed at 3, tinyda projected)
    'data5': [(3, 18), (4, 18), (6, 23), (9, 17), (10, 21)],      # 5 iters
}

# Submit times (relative, seconds from t=0 = data8 submit)
SUBMIT = {'data8': 0, 'data9': 271, 'data5': 1870}  # data5 at +1870 due to manual resubmit (bug-induced)

# yaml deadlines (already ×1.5 bumped, ×3 contention)
DEADLINE = {'data8': 6160.60, 'data9': 8473.22, 'data5': 7289.82}

# yaml budgets (×1.5)
BUDGET = {'data8': 1.77, 'data9': 2.66, 'data5': 2.15}

# Static lanes (= initial allocation, fixed throughout)
STATIC_LANES = {'data8': 4, 'data9': 3, 'data5': 1}

SETUP = 30
INTER_ITER = 30
COLD_START = 359   # measured from R3 OD-g4 i-03932f84...

# Cost rates
RATE = {'slurm': 0.84, 'res_g4': 0.227, 'od_g4': 0.526, 'res_g5': 0.435, 'od_g5': 1.006}

# Container assignments (from R3 actual)
CONTAINER = {'data8': 'slurm', 'data9': 'cloud_g4', 'data5': 'slurm'}

def iter_runtime(wf, lanes, iter_k):
    """Static runtime for iter k of wf at given lanes (using measured τ)."""
    chains, tinyda = TRAJECTORY[wf][iter_k]
    batches = math.ceil(chains / max(lanes, 1))
    return batches * tinyda * TAU[wf]

def simulate_static(wf):
    """Trace static allocation: same lanes throughout, no scale events."""
    lanes = STATIC_LANES[wf]
    submit = SUBMIT[wf]

    # Setup delay
    if wf == 'data9':
        # data9 needs 1 OD-g4 cold-start before first iter
        iter0_start = submit + COLD_START + SETUP
    else:
        iter0_start = submit + SETUP

    iters = []
    t = iter0_start
    for k in range(len(TRAJECTORY[wf])):
        rt = iter_runtime(wf, lanes, k)
        end = t + rt
        iters.append({'k': k, 'lanes': lanes, 'chains': TRAJECTORY[wf][k][0],
                      'tinyda': TRAJECTORY[wf][k][1], 'start': t, 'end': end, 'rt': rt})
        t = end + INTER_ITER

    finish = iters[-1]['end']
    makespan = finish - submit
    return iters, finish, makespan

def main():
    print("=" * 72)
    print("R4 N=3 STATIC EDF — modeled from R3 trajectory + empirical τ")
    print("=" * 72)

    results = {}
    for wf in ['data8', 'data9', 'data5']:
        iters, finish, makespan = simulate_static(wf)
        deadline = DEADLINE[wf]
        miss = makespan > deadline
        results[wf] = dict(iters=iters, finish=finish, makespan=makespan,
                            miss=miss, deadline=deadline)
        print(f"\n--- {wf} (container={CONTAINER[wf]}, static lanes={STATIC_LANES[wf]}) ---")
        for it in iters:
            print(f"  iter {it['k']}: chains={it['chains']} tinyda={it['tinyda']} "
                  f"lanes={it['lanes']} [{it['start']:>6.0f}..{it['end']:>6.0f}] "
                  f"({it['rt']:>6.0f}s)")
        result = "MISS" if miss else "HIT "
        margin = abs(deadline - makespan)
        print(f"  finish={finish:.0f}s, submit={SUBMIT[wf]}, makespan={makespan:.0f}s")
        print(f"  deadline={deadline:.0f}s → {result} by {margin:.0f}s "
              f"({margin / deadline * 100:.0f}%)")

    # Aggregate
    print(f"\n{'=' * 72}\nAggregate R4 (static) metrics\n{'=' * 72}")
    misses = sum(1 for r in results.values() if r['miss'])
    sum_flow = sum(r['makespan'] for r in results.values())
    campaign_wall = max(r['finish'] for r in results.values())
    print(f"  Total deadline misses: {misses}/3")
    print(f"  Sum-of-flowtimes: {sum_flow:.0f}s")
    print(f"  Campaign wall (longest finish from t=0): {campaign_wall:.0f}s "
          f"({campaign_wall / 3600:.2f}h)")

    # Cost: same as R3 actual = $1.05 (1 OD-g4 for data9, full lifetime)
    od_cost = (results['data9']['makespan'] / 3600) * RATE['od_g4']  # 1 instance for data9 lifetime
    print(f"  On-demand cost: ${od_cost:.2f} (1 OD-g4 for data9 makespan)")

    return results

if __name__ == '__main__':
    main()
