"""R6 N=3 FCFS static — modeled from R5 measured trajectory.

Same dispatch + submit times as R5. NO scale-ups, NO scale-downs.

KEY DIFFERENCE from R5 moldable allocation:
  - R5 moldable: data5 → slurm partial 2 (because data8 had scaled DOWN to 2,
    freeing 2 slurm slots; moldable's on-prem-first override hijacked data5 there).
  - R6 static: data8 stays at 4 (no scale-down) → slurm FULL when data5 submits →
    static's on-prem-first checks optimal_type (g5), doesn't match on-prem (g4) →
    skips on-prem → data5 → cloud-g5 lanes=3.

This actually gives data5 a BETTER allocation under static than under moldable
(because cloud-g5 is faster than slurm partial 2). The cost: static spawns 2 OD
instances (1 OD-g4 for data9 + 1 OD-g5 for data5) while moldable only spawns 1.
"""
import math

# Empirical τ from R5 (per-iter rate, s/epoch) — different per cluster
TAU = {
    'data8_slurm_lanes4': 38.5,    # vgg19 @ slurm lanes=4 (R5 iter 0 measurement)
    'data9_g4_lanes3': 70.8,        # convnext @ cloud-g4 lanes=3 (R5 mean over iters 0-2)
    'data5_g5_lanes3': 22.30,       # wide_resnet @ cloud-g5 lanes=3 (scheduler model — no measurement)
}

# Per-iter trajectory (chains, tinyda) from R5 actual
TRAJECTORY = {
    'data8': [(4, 10), (6, 5), (9, 4)],                       # 3 iters; tinyda from R5 measured
    'data9': [(3, 20), (3, 19), (3, 22), (3, 27), (3, 33)],    # iters 3-4 extrapolated
    'data5': [(3, 18), (4, 14), (6, 12), (9, 12), (10, 8)],    # 5 iters; tinyda from R5 measured
}

# Submit times (relative to data8 t=0)
SUBMIT = {'data8': 0, 'data9': 271, 'data5': 393}  # natural seed=42 timing

# yaml deadlines (×3 contention) and budgets (×1.5)
DEADLINE = {'data8': 6160.60, 'data9': 8473.22, 'data5': 7289.82}
BUDGET = {'data8': 1.77, 'data9': 2.66, 'data5': 2.15}

# R6 static allocations (different from R5 moldable!)
STATIC_LANES = {'data8': 4, 'data9': 3, 'data5': 3}      # data5 goes to cloud-g5 lanes=3
CONTAINER = {'data8': 'slurm', 'data9': 'cloud_g4', 'data5': 'cloud_g5'}
TAU_KEY = {'data8': 'data8_slurm_lanes4', 'data9': 'data9_g4_lanes3', 'data5': 'data5_g5_lanes3'}

SETUP = 30
INTER_ITER = 30
COLD_START = 358    # measured from R5

RATE = {'slurm': 0.84, 'res_g4': 0.227, 'od_g4': 0.526, 'res_g5': 0.435, 'od_g5': 1.006}

def iter_runtime(wf, lanes, iter_k):
    chains, tinyda = TRAJECTORY[wf][iter_k]
    batches = math.ceil(chains / max(lanes, 1))
    return batches * tinyda * TAU[TAU_KEY[wf]]

def simulate_static(wf):
    lanes = STATIC_LANES[wf]
    submit = SUBMIT[wf]
    # data9 and data5 both need OD cold-start (data9 = OD-g4, data5 = OD-g5)
    if wf in ('data9', 'data5'):
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

    return iters, iters[-1]['end'], iters[-1]['end'] - submit

def main():
    print("=" * 72)
    print("R6 N=3 FCFS STATIC — modeled from R5 trajectory + empirical τ")
    print("=" * 72)
    print("\nNote: data5 lands on cloud-g5 lanes=3 (NOT slurm partial as in R5 moldable).")
    print("Reason: static FCFS on-prem-first only fires when score's optimal_type matches")
    print("on-prem instance type. Score picks g5 for data5 → on-prem (g4) doesn't match → cloud.\n")

    results = {}
    for wf in ['data8', 'data9', 'data5']:
        iters, finish, makespan = simulate_static(wf)
        deadline = DEADLINE[wf]
        miss = makespan > deadline
        results[wf] = dict(iters=iters, finish=finish, makespan=makespan, miss=miss, deadline=deadline)
        print(f"\n--- {wf} ({CONTAINER[wf]}, lanes={STATIC_LANES[wf]}) ---")
        for it in iters:
            print(f"  iter {it['k']}: chains={it['chains']} tinyda={it['tinyda']} "
                  f"lanes={it['lanes']} [{it['start']:>6.0f}..{it['end']:>6.0f}] "
                  f"({it['rt']:>6.0f}s)")
        result = "MISS" if miss else "HIT "
        print(f"  finish={finish:.0f}s  submit={SUBMIT[wf]}  makespan={makespan:.0f}s")
        print(f"  deadline={deadline:.0f}s → {result} by {abs(deadline - makespan):.0f}s "
              f"({abs(deadline - makespan) / deadline * 100:.0f}%)")

    print(f"\n{'=' * 72}\nAggregate R6 (FCFS static)\n{'=' * 72}")
    misses = sum(1 for r in results.values() if r['miss'])
    sum_flow = sum(r['makespan'] for r in results.values())
    campaign = max(r['finish'] for r in results.values())
    print(f"  Deadline misses: {misses}/3")
    print(f"  Sum-of-flowtimes: {sum_flow:.0f}s")
    print(f"  Campaign wall: {campaign:.0f}s ({campaign / 3600:.2f}h)")

    # Cost: 1 OD-g4 (data9) + 1 OD-g5 (data5)
    od_g4_cost = (results['data9']['makespan'] / 3600) * RATE['od_g4']
    od_g5_cost = (results['data5']['makespan'] / 3600) * RATE['od_g5']
    print(f"  OD-g4 cost (data9): ${od_g4_cost:.2f}")
    print(f"  OD-g5 cost (data5): ${od_g5_cost:.2f}")
    print(f"  Total OD cost: ${od_g4_cost + od_g5_cost:.2f}")

if __name__ == '__main__':
    main()
