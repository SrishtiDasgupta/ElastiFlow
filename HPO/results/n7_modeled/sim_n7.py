"""N=7 EDF — model both static and moldable with WAIT TIMES (cluster contention).

Dispatch: [8, 9, 5, 7, 3, 12, 1]. Submit times: 0, 271, 390, 472, 487, 577, 667.

At N=7, total chains₀ demand = 4+3+3+2+4+4+4 = 24 lanes vs cluster cap 4+4+4 = 12.
Many wfs will WAIT for resources — must model this.
"""
import math, heapq

EPOCH = {
    'g4': {'vgg19': 22.17, 'wide_resnet101_2': 29.66, 'convnext_large': 34.03},
    'g5': {'vgg19': 16.50, 'wide_resnet101_2': 22.30, 'convnext_large': 22.95},
}
RATE = {'slurm': 0.84, 'res_g4': 0.227, 'od_g4': 0.526, 'res_g5': 0.435, 'od_g5': 1.006}

WFS = {
    'data8':  dict(model='vgg19',            chains0=4, deadline=6160.60, submit=0,
                    iters=[(4,10),(6,6),(9,8)]),
    'data9':  dict(model='convnext_large',   chains0=3, deadline=8473.22, submit=271,
                    iters=[(3,20),(3,29),(3,37),(3,53),(3,70)]),
    'data5':  dict(model='wide_resnet101_2', chains0=3, deadline=7289.82, submit=390,
                    iters=[(3,18),(4,20),(6,24),(9,25),(10,25)]),
    'data7':  dict(model='wide_resnet101_2', chains0=2, deadline=5755.80, submit=472,
                    iters=[(2,20),(3,25),(4,24),(6,14)]),
    'data3':  dict(model='vgg19',            chains0=4, deadline=5195.84, submit=487,
                    iters=[(4,15),(6,18),(9,15)]),
    'data12': dict(model='vgg19',            chains0=4, deadline=5728.71, submit=577,
                    iters=[(4,14),(6,16),(9,14),(13,12)]),
    'data1':  dict(model='vgg19',            chains0=4, deadline=5195.82, submit=667,
                    iters=[(4,15),(6,18),(9,15)]),
}

SETUP = 30
INTER = 30
COLD_START = 358

CAPACITIES = {'slurm': 4, 'cloud_g4': 4, 'cloud_g5': 4}

def fam(container):
    return 'g5' if 'g5' in container else 'g4'

def iter_runtime(model, container, lanes, chains, tinyda):
    return math.ceil(chains / max(lanes, 1)) * tinyda * EPOCH[fam(container)][model]

def wf_runtime(wf, container, lanes_seq):
    """Sum runtime over iters with given per-iter lanes."""
    total = SETUP
    if 'cloud' in container:
        total += COLD_START
    for k, (c, t) in enumerate(WFS[wf]['iters']):
        if k > 0: total += INTER
        ln = lanes_seq[k] if isinstance(lanes_seq, list) else lanes_seq
        total += iter_runtime(WFS[wf]['model'], container, ln, c, t)
    return total

def simulate_static(allocations):
    """Static: each wf takes given allocation, runs all iters at fixed lanes, no scaling."""
    finishes = {}
    od_intervals = []  # (cluster, num_od_instances, start, end)

    # Order by submit time; for waiting, queue them
    submit_order = sorted(WFS.keys(), key=lambda w: WFS[w]['submit'])
    container_busy_until = {c: 0 for c in CAPACITIES}   # when this container becomes available

    # Track which wf occupies which container
    container_lanes_used = {c: [] for c in CAPACITIES}   # list of (wf, finish_time, lanes)

    for wf in submit_order:
        cont, lanes = allocations[wf]
        submit_t = WFS[wf]['submit']

        # Check if container has enough free lanes at submit_t (release expired entries first)
        container_lanes_used[cont] = [e for e in container_lanes_used[cont] if e[1] > submit_t]
        used = sum(e[2] for e in container_lanes_used[cont])
        free = CAPACITIES[cont] - used

        if free >= lanes:
            start_t = submit_t
        else:
            # Wait for enough lanes to free
            # Sort container's busy entries by finish_time
            sorted_ents = sorted(container_lanes_used[cont], key=lambda e: e[1])
            cumulative = used
            for ent in sorted_ents:
                cumulative -= ent[2]
                if CAPACITIES[cont] - cumulative >= lanes:
                    start_t = ent[1]
                    break
            else:
                start_t = float('inf')

        rt = wf_runtime(wf, cont, lanes)
        finish_t = start_t + rt
        finishes[wf] = (start_t, finish_t, cont, lanes)
        container_lanes_used[cont].append((wf, finish_t, lanes))

        # Track OD instances
        if cont == 'cloud_g4':
            # data9 takes 1 OD; subsequent partial wfs in g4 take more OD
            n_od = max(0, lanes - max(0, 2 - sum(e[2] for e in container_lanes_used[cont] if e[0] != wf and 'cloud_g4' == cont and e[0] in finishes)))
            n_od = lanes if container_lanes_used[cont].index((wf, finish_t, lanes)) > 0 else max(0, lanes - 2)
            # Simpler: data9 (first cloud-g4 wf) takes 2 reserved + (lanes-2) OD; later wfs take all OD
            cloud_g4_wfs_so_far = [e for e in container_lanes_used[cont] if e[0] != wf]
            res_used = sum(min(e[2], 2) for e in cloud_g4_wfs_so_far[:1]) if cloud_g4_wfs_so_far else 0
            res_left = max(0, 2 - res_used) if not cloud_g4_wfs_so_far else 0
            n_od = max(0, lanes - res_left)
            if n_od > 0:
                od_intervals.append(('g4', n_od, start_t, finish_t))
        elif cont == 'cloud_g5':
            cloud_g5_wfs_so_far = [e for e in container_lanes_used[cont] if e[0] != wf]
            res_left = max(0, 2 - sum(min(e[2], 2) for e in cloud_g5_wfs_so_far[:1])) if not cloud_g5_wfs_so_far else 0
            n_od = max(0, lanes - res_left)
            if n_od > 0:
                od_intervals.append(('g5', n_od, start_t, finish_t))

    return finishes, od_intervals

def report(name, finishes, od_intervals):
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    misses = 0
    sum_flow = 0
    for wf in sorted(WFS.keys(), key=lambda w: WFS[w]['submit']):
        start, finish, cont, lanes = finishes[wf]
        ms = finish - WFS[wf]['submit']
        sum_flow += ms
        dl = WFS[wf]['deadline']
        miss = ms > dl
        if miss: misses += 1
        wait = start - WFS[wf]['submit']
        print(f"  {wf:7} {cont:10} lanes={lanes}  wait={wait:5.0f}s  makespan={ms:6.0f}s  "
              f"deadline={dl:.0f}  {'MISS' if miss else 'HIT '} by {abs(dl-ms):.0f}s")

    od_cost = sum(n * (e - s) / 3600 * RATE[f'od_{fam}'] for fam, n, s, e in od_intervals)
    campaign = max(f[1] for f in finishes.values())
    print(f"\n  Misses: {misses}/7")
    print(f"  Sum-flowtimes: {sum_flow:.0f}s")
    print(f"  Campaign wall: {campaign:.0f}s")
    print(f"  OD cost: ${od_cost:.2f}")
    print(f"  OD intervals: {[(f, n, int(s), int(e)) for f, n, s, e in od_intervals]}")
    return misses, sum_flow, od_cost, campaign

# STATIC allocations
STATIC = {
    'data8':  ('slurm',     4),
    'data9':  ('cloud_g4',  3),
    'data5':  ('cloud_g5',  3),
    'data7':  ('cloud_g4',  1),
    'data3':  ('cloud_g5',  1),
    'data12': ('slurm',     4),  # waits for data8 to finish
    'data1':  ('slurm',     4),  # waits for data12 to finish
}

# MOLDABLE allocations (after-effect of scale-downs and on-prem-first)
MOLDABLE = {
    'data8':  ('slurm',     4),    # then scales down to 2 (handled below)
    'data9':  ('cloud_g4',  3),
    'data5':  ('slurm',     2),    # data8 scaled down → slurm partial 2; scales up to 4 later
    'data7':  ('cloud_g4',  1),
    'data3':  ('cloud_g5',  4),    # cluster-g5 empty (data5 went slurm) → take 4
    'data12': ('cloud_g5',  4),    # waits for data3 to finish
    'data1':  ('slurm',     2),    # waits for data8 (and gets partial 2)
}

print(">>> STATIC N=7")
s_finishes, s_od = simulate_static(STATIC)
s_misses, s_flow, s_cost, s_wall = report('STATIC', s_finishes, s_od)

print("\n>>> MOLDABLE N=7")
m_finishes, m_od = simulate_static(MOLDABLE)
m_misses, m_flow, m_cost, m_wall = report('MOLDABLE', m_finishes, m_od)

print(f"\n{'=' * 70}\nN=7 EDF: STATIC vs MOLDABLE\n{'=' * 70}")
print(f"  Misses:    static {s_misses}/7, moldable {m_misses}/7  (Δ {m_misses - s_misses})")
print(f"  Sum-flow:  static {s_flow:.0f}, moldable {m_flow:.0f}  (Δ {(m_flow-s_flow)/s_flow*100:+.0f}%)")
print(f"  OD cost:   static ${s_cost:.2f}, moldable ${m_cost:.2f}  (Δ ${s_cost-m_cost:.2f}, {(s_cost-m_cost)/s_cost*100:.0f}% saving)")
print(f"  Campaign:  static {s_wall:.0f}s, moldable {m_wall:.0f}s")
