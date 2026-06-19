"""R4 N=3 EDF static — CORRECTED model from R3 trajectory.

Original R4 was modeled with R3's bug-induced data5 allocation (slurm partial 1).
That was wrong — under EDF static, data5 would land on cloud-g5 lanes=3 because:
  - data8 stays at 4 slurm lanes (no moldable scale-down)
  - slurm full when data5 submits → cloud path
  - score function picks cloud-g5 (chains=3 wide_resnet, fits 4 cluster-g5 slots)
  - on-prem-first override checks optimal_type==g4 (no, it's g5) → skips on-prem

Corrected R4 = same as R6 conceptually, but uses R3's measured trajectory
(data9 tinyda grew to 53, data5 tinyda 18-25 — different from R5's smaller values).
"""
import math

# τ values
TAU = {
    'data8_slurm_lanes4': 38.5,    # R3 iter 0 measurement
    'data9_g4_lanes3': 80.0,        # R3 mean across iters 0-2
    'data5_g5_lanes3': 22.30,       # scheduler model (no measurement on g5)
}

# R3's measured per-iter trajectory
TRAJECTORY = {
    'data8': [(4, 10), (6, 6), (9, 8)],
    'data9': [(3, 20), (3, 29), (3, 37), (3, 53), (3, 70)],   # iters 3-4 extrapolated tinyda growth
    'data5': [(3, 18), (4, 20), (6, 24), (9, 25), (10, 25)],
}

# Submit times — use NATURAL Poisson seed=42 timing (NOT bug-induced delay)
SUBMIT = {'data8': 0, 'data9': 271, 'data5': 390}

DEADLINE = {'data8': 6160.60, 'data9': 8473.22, 'data5': 7289.82}
BUDGET = {'data8': 1.77, 'data9': 2.66, 'data5': 2.15}

# Static R4 corrected allocations
STATIC_LANES = {'data8': 4, 'data9': 3, 'data5': 3}
CONTAINER = {'data8': 'slurm', 'data9': 'cloud_g4', 'data5': 'cloud_g5'}
TAU_KEY = {'data8': 'data8_slurm_lanes4', 'data9': 'data9_g4_lanes3', 'data5': 'data5_g5_lanes3'}

SETUP = 30
INTER_ITER = 30
COLD_START = 359   # R3 measurement

RATE = {'slurm': 0.84, 'res_g4': 0.227, 'od_g4': 0.526, 'res_g5': 0.435, 'od_g5': 1.006}

def iter_runtime(wf, lanes, iter_k):
    chains, tinyda = TRAJECTORY[wf][iter_k]
    batches = math.ceil(chains / max(lanes, 1))
    return batches * tinyda * TAU[TAU_KEY[wf]]

def simulate_static(wf):
    lanes = STATIC_LANES[wf]
    submit = SUBMIT[wf]
    if wf in ('data9', 'data5'):
        iter0_start = submit + COLD_START + SETUP
    else:
        iter0_start = submit + SETUP
    iters = []
    t = iter0_start
    for k in range(len(TRAJECTORY[wf])):
        rt = iter_runtime(wf, lanes, k)
        iters.append({'k': k, 'lanes': lanes, 'chains': TRAJECTORY[wf][k][0],
                      'tinyda': TRAJECTORY[wf][k][1], 'start': t, 'end': t + rt, 'rt': rt})
        t = t + rt + INTER_ITER
    return iters, iters[-1]['end'], iters[-1]['end'] - submit

def main():
    print("=" * 72)
    print("R4 N=3 EDF STATIC — CORRECTED model from R3 trajectory")
    print("=" * 72)
    print("\nKey correction: data5 → cloud-g5 lanes=3 (not slurm partial 1).")
    print("Submit times use natural Poisson seed=42 (no bug-induced delay).\n")

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
        print(f"  finish={finish:.0f}s submit={SUBMIT[wf]} makespan={makespan:.0f}s")
        print(f"  deadline={deadline:.0f}s → {result} by {abs(deadline - makespan):.0f}s "
              f"({abs(deadline - makespan) / deadline * 100:.0f}%)")

    print(f"\n{'=' * 72}\nAggregate R4 corrected\n{'=' * 72}")
    misses = sum(1 for r in results.values() if r['miss'])
    sum_flow = sum(r['makespan'] for r in results.values())
    campaign = max(r['finish'] for r in results.values())
    print(f"  Deadline misses: {misses}/3")
    print(f"  Sum-of-flowtimes: {sum_flow:.0f}s")
    print(f"  Campaign wall: {campaign:.0f}s ({campaign / 3600:.2f}h)")

    od_g4 = (results['data9']['makespan'] / 3600) * RATE['od_g4']
    od_g5 = (results['data5']['makespan'] / 3600) * RATE['od_g5']
    print(f"  OD-g4 cost (data9): ${od_g4:.2f}")
    print(f"  OD-g5 cost (data5): ${od_g5:.2f}")
    print(f"  Total OD cost: ${od_g4 + od_g5:.2f}")

if __name__ == '__main__':
    main()
