"""N=5 EDF — model both static and moldable under correct allocation rules.

Dispatch: [8, 9, 5, 7, 3]. Submit times (Poisson seed=42 first 5):
  data8: 0, data9: 271, data5: 390, data7: 472, data3: 487

Cluster: slurm=4, cluster-g4=4 (2 res + 2 OD), cluster-g5=4 (2 res + 2 OD).

Allocation rules:
- Moldable: on-prem-first hijacks ANY free slurm slots regardless of optimal_type.
- Static: on-prem-first only if score's optimal_type matches on-prem (g4).

Per-iter trajectories (chains, tinyda) from R3 measurements (high-tinyda regime).
"""
import math

EPOCH = {
    'g4': {'vgg19': 22.17, 'wide_resnet101_2': 29.66, 'convnext_large': 34.03},
    'g5': {'vgg19': 16.50, 'wide_resnet101_2': 22.30, 'convnext_large': 22.95},
    'slurm': {'vgg19': 22.17, 'wide_resnet101_2': 29.66, 'convnext_large': 34.03},  # same as g4
}
RATE = {'slurm': 0.84, 'res_g4': 0.227, 'od_g4': 0.526, 'res_g5': 0.435, 'od_g5': 1.006}

# Workflows in dispatch order
WFS = {
    'data8': dict(model='vgg19', chains0=4, deadline=6160.60, budget=1.77, submit=0,
                   iters=[(4, 10), (6, 6), (9, 8)]),
    'data9': dict(model='convnext_large', chains0=3, deadline=8473.22, budget=2.66, submit=271,
                   iters=[(3, 20), (3, 29), (3, 37), (3, 53), (3, 70)]),
    'data5': dict(model='wide_resnet101_2', chains0=3, deadline=7289.82, budget=2.15, submit=390,
                   iters=[(3, 18), (4, 20), (6, 24), (9, 25), (10, 25)]),
    'data7': dict(model='wide_resnet101_2', chains0=2, deadline=5755.80, budget=2.37, submit=472,
                   iters=[(2, 20), (3, 25), (4, 24), (6, 14)]),
    'data3': dict(model='vgg19', chains0=4, deadline=5195.84, budget=1.64, submit=487,
                   iters=[(4, 15), (6, 18), (9, 15)]),
}

SETUP = 30
COLD_START = 358

def iter_runtime(model, container, lanes, chains, tinyda):
    fam = 'g5' if 'g5' in container else 'g4'   # slurm and cloud_g4 → g4
    per_epoch = EPOCH[fam][model]
    return math.ceil(chains / max(lanes, 1)) * tinyda * per_epoch

def total_runtime(wf, lanes, container):
    """Sum runtimes across all iters at fixed lanes."""
    total = SETUP
    for k, (c, t) in enumerate(WFS[wf]['iters']):
        if k > 0:
            total += 30
        total += iter_runtime(WFS[wf]['model'], container, lanes, c, t)
    if container in ('cloud_g4', 'cloud_g5'):
        total += COLD_START
    return total

# ===== STATIC ALLOCATION =====
# Allocations (computed by hand following the score function + on-prem-first rules):
#   data8 → slurm 4 (full)
#   data9 → cloud-g4 lanes=3 (2 res + 1 OD)
#   data5 → cloud-g5 lanes=3 (slurm full at submit; on-prem-first not match optimal_type=g5)
#   data7 → cloud-g4 lanes=1 partial (cluster-g4 has 1 OD free; on-prem-first checks optimal_type=g4 but slurm full)
#   data3 → cloud-g5 lanes=1 partial (cluster-g5 has 1 OD free; on-prem-first not match optimal_type=g5)
STATIC_ALLOC = {
    'data8': ('slurm', 4),
    'data9': ('cloud_g4', 3),
    'data5': ('cloud_g5', 3),
    'data7': ('cloud_g4', 1),
    'data3': ('cloud_g5', 1),
}

# ===== MOLDABLE ALLOCATION =====
# Allocations:
#   data8 → slurm 4 (full); scales down to 2 after iter 0
#   data9 → cloud-g4 lanes=3
#   data5 → slurm partial 2 (data8 scaled down before data5 alloc, slurm has 2 free; moldable on-prem-first hijacks)
#   data7 → cloud-g4 lanes=1 partial (cluster-g4 has 1 OD free)
#   data3 → cloud-g5 lanes=4 (cluster-g5 EMPTY since data5 didn't go there — 2 res + 2 OD)
MOLDABLE_INITIAL = {
    'data8': ('slurm', 4),
    'data9': ('cloud_g4', 3),
    'data5': ('slurm', 2),       # initial; will scale 2→4 at later iter when data8 finishes
    'data7': ('cloud_g4', 1),
    'data3': ('cloud_g5', 4),    # full cluster-g5
}
# data5 effective lanes: 2 for first 2 iters, then 4 for iters 2-4 (when data8 finishes)
# data3 stays at 4 throughout (no change needed)
# data8 lanes: 4 for iter 0, then 2-3 for iters 1-2

def report(name, allocations, allow_data5_scale_up=False, allow_data8_scale_down=False):
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    total_od_cost = 0
    misses = 0
    makespans = {}
    for wf, (container, lanes) in allocations.items():
        # Special handling for moldable data5/data8 trajectory
        if name == 'MOLDABLE' and wf == 'data5':
            # iters 0,1 at lanes=2; iters 2,3,4 at lanes=4 (after data8 finishes)
            wf_total = SETUP
            for k, (c, t) in enumerate(WFS[wf]['iters']):
                if k > 0: wf_total += 30
                ln = 2 if k < 2 else 4   # scale up at iter 2 boundary
                wf_total += iter_runtime(WFS[wf]['model'], 'slurm', ln, c, t)
            makespan = wf_total
        elif name == 'MOLDABLE' and wf == 'data8':
            # iter 0 at lanes=4, iter 1 at lanes=2 (scale-down), iter 2 at lanes=3 (scale-up by data5)
            # Actually slurm shared with data5 (which has 2). So data8 gets 2 throughout iters 1,2.
            wf_total = SETUP
            for k, (c, t) in enumerate(WFS[wf]['iters']):
                if k > 0: wf_total += 30
                ln = 4 if k == 0 else 2    # scale down to 2, stays there because data5 occupies 2
                wf_total += iter_runtime(WFS[wf]['model'], 'slurm', ln, c, t)
            makespan = wf_total
        else:
            makespan = total_runtime(wf, lanes, container)
        makespans[wf] = makespan
        deadline = WFS[wf]['deadline']
        miss = makespan > deadline
        if miss: misses += 1
        result = "MISS" if miss else "HIT "
        print(f"  {wf:7} {container:10} lanes={lanes}  makespan={makespan:6.0f}s  "
              f"deadline={deadline:.0f}  {result} by {abs(deadline-makespan):.0f}s")
        # Cost: OD instances held for full wf lifetime
        if container == 'cloud_g4':
            # data9 has 1 OD-g4; data7 has 1 OD-g4 (or 1 res-g4 + ??? — assume reserved is data9's)
            # Simple: any cloud allocation here means 1 OD instance per wf except data9's first
            pass

    # OD cost calculation (per allocation)
    # Static: 1 OD-g4 (data9) + 1 OD-g5 (data5) + 1 OD-g4 (data7) + 1 OD-g5 (data3) = 4 instances
    # Moldable: 1 OD-g4 (data9) + 1 OD-g4 (data7) + 2 OD-g5 (data3 at lanes=4) = 4 instances
    if name == 'STATIC':
        od_cost = (
            (makespans['data9'] / 3600) * RATE['od_g4'] +    # data9 OD-g4
            (makespans['data5'] / 3600) * RATE['od_g5'] +    # data5 OD-g5
            (makespans['data7'] / 3600) * RATE['od_g4'] +    # data7 OD-g4 (the 2nd)
            (makespans['data3'] / 3600) * RATE['od_g5']      # data3 OD-g5 (the 2nd)
        )
    else:  # MOLDABLE
        od_cost = (
            (makespans['data9'] / 3600) * RATE['od_g4'] +
            (makespans['data7'] / 3600) * RATE['od_g4'] +
            2 * (makespans['data3'] / 3600) * RATE['od_g5']  # data3 has 2 OD-g5 instances
        )

    print(f"\n  Misses: {misses}/5")
    print(f"  Sum-of-flowtimes: {sum(makespans.values()):.0f}s")
    print(f"  Campaign wall: {max(WFS[w]['submit'] + makespans[w] for w in makespans):.0f}s")
    print(f"  OD cost: ${od_cost:.2f}")

    return makespans, misses, od_cost

if __name__ == '__main__':
    s_ms, s_misses, s_cost = report('STATIC', STATIC_ALLOC)
    m_ms, m_misses, m_cost = report('MOLDABLE', MOLDABLE_INITIAL)

    print(f"\n{'=' * 70}\nSTATIC vs MOLDABLE at N=5 EDF\n{'=' * 70}")
    print(f"{'wf':7} {'static':>8} {'moldable':>8}  diff")
    for wf in WFS:
        diff = m_ms[wf] - s_ms[wf]
        print(f"{wf:7} {s_ms[wf]:>8.0f} {m_ms[wf]:>8.0f}  {diff:+.0f} ({diff/s_ms[wf]*100:+.0f}%)")
    print(f"\n  Misses:  static {s_misses}/5, moldable {m_misses}/5")
    print(f"  OD cost: static ${s_cost:.2f}, moldable ${m_cost:.2f}, saving ${s_cost-m_cost:.2f} ({(s_cost-m_cost)/s_cost*100:.0f}%)")
