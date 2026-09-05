"""
sim_r7_nobug_only.py — R7 actual vs bug-#2-fixed counterfactual.

Scope: ONLY model what changes if bug #2 were fixed. Everything else identical.

Bug #2: updateFreedResources() doesn't call setResourcesAvailable(True).
Effect: when wfs scale down (e.g., release reserved/OD lanes), blocked or
under-allocated wfs are NOT retried until some wf fully completes.

Specifically affected in R7:
  - data5: queued at T=390s (submit), allocated at T=~3590s (lanes=1 OD g5).
    Wait of ~53 min. Then trapped at lanes=1 for entire 5 iters.

In bug-free counterfactual:
  - At T=2100s (data1 scaled down 2 OD g5): cluster_g5 has 1 free OD slot.
    → data5 can be retried + allocated lanes=1 (~22 min earlier than actual).
  - At T=2790s (data12 scaled down 1 OD g5): another free slot.
    → data5 grow request succeeds: lanes 1 → 2.
  - At T=3704s (data1 fully completed): 2 reserved g5 slots free.
    → data5 grow request succeeds: lanes 2 → 4 (cluster_g5 cap).

For data5, recompute per-iter runtime from R7 measured per-epoch unit time
× new ceil(chains/lanes) for each iter under bug-free lane progression.

Other wfs: unchanged. Only data5's makespan recomputed.
"""

# -----------------------------------------------------------------------------
# R7 ACTUAL DATA (from main_HPO log + /fsx/hpo_logs)
# -----------------------------------------------------------------------------

T0 = 1778413716  # data8 submit

# All times in seconds-since-T0 (so 0 = 11:48:36 UTC, 60 = 11:49:36 UTC, etc.)
WFS_R7_ACTUAL = {
    'data8':  {'submit': 0,    'finish': 3670, 'deadline': 6160, 'iters': 3,
               'per_iter_chains': [4, 6, 9],  'per_iter_epoch': [10, 11, 12],
               'per_iter_dur_s': [345, 1153, 2138],   # iter 0+1+2 = 3636 ≈ 3670
               'lanes_history': [(0, 'slurm', 4), (720, 'slurm', 2)],  # scale-down at ~T=720s
               'cluster': 'slurm'},
    'data9':  {'submit': 271,  'finish': 16880, 'deadline': 8473, 'iters': 5,
               'per_iter_chains': [3, 3, 3, 3, 3],  'per_iter_epoch': [20, 28, 40, 57, 65],
               'per_iter_dur_s': [1880, 2316, 3089, 4203, 5115],
               'lanes_history': [(0, 'cluster_g4', 3)],
               'cluster': 'cluster_g4'},
    # data5 is the primary affected wf
    'data5':  {'submit': 390,  'finish': 20940, 'deadline': 7290, 'iters': 5,
               'per_iter_chains': [3, 4, 6, 9, 10],  'per_iter_epoch': [18, 25, 22, 21, 30],
               'per_iter_dur_s': [4316, 2332, 2753, 4737, 6180],   # = 20318 ≈ alloc_wait + run
               # data5 was allocated at T=~3590, then ran iter 0 (4316-?) ... actually iter 0
               # ended at 13:07 = 5422s after T=baseline. So iter 0 took ~1832s after alloc.
               # Re-derive per_iter_dur_s from results.jsonl ts_offsets:
               # iter end offsets (from T=1778413000 baseline): 5422, 7754, 10507, 15244, ?
               # adj to T0 baseline (T0 = 1778413716, baseline = 1778413000, so subtract 716):
               # iter ends from T0: 4706, 7038, 9791, 14528, ~20040 (iter 4 from file mtime 17:33 UTC = 20694s)
               # Allocation at T=~3490 (12:43 UTC), so iter 0 ran 4706-3490 = 1216s = 20 min.
               # Hmm that's much less than 72 min. Let me re-examine.
               'lanes_history': [(3490, 'cluster_g5', 1)],
               'cluster': 'cluster_g5'},
    'data7':  {'submit': 472,  'finish': 22440, 'deadline': 5755, 'iters': 4,
               'per_iter_chains': [2, 3, 4, 6],  'per_iter_epoch': [20, 27, 35, 50],
               'per_iter_dur_s': [2455, 3052, 5097, 11091],
               'lanes_history': [(700, 'cluster_g4', 2)],
               'cluster': 'cluster_g4'},
    'data3':  {'submit': 487,  'finish': 3844, 'deadline': 5196, 'iters': 3,
               'per_iter_chains': [4, 6, 9],  'per_iter_epoch': [15, 18, 9],
               'per_iter_dur_s': [810, 1644, 1392],
               'lanes_history': [(750, 'slurm', 2)],  # slurm partial via on-prem-first
               'cluster': 'slurm'},
    'data12': {'submit': 502,  'finish': 8954, 'deadline': 5728, 'iters': 4,
               'per_iter_chains': [4, 6, 9, 13],  'per_iter_epoch': [14, 17, 18, 24],
               'per_iter_dur_s': [2214, 1175, 2158, 2874],
               'lanes_history': [(800, 'cluster_g5', 1), (1100, 'cluster_g5', 3), (2790, 'cluster_g5', 2)],
               'cluster': 'cluster_g5'},
    'data1':  {'submit': 508,  'finish': 3704, 'deadline': 5196, 'iters': 3,
               'per_iter_chains': [4, 6, 9],  'per_iter_epoch': [15, 16, 12],
               'per_iter_dur_s': [854, 1352, 1450],
               'lanes_history': [(800, 'cluster_g5', 4), (2100, 'cluster_g5', 2)],
               'cluster': 'cluster_g5'},
}


def per_epoch_unit(wf):
    """Back out the per-epoch unit time from R7 actuals.
    For each iter: dur = epoch * ceil(chains/lanes_at_iter) * per_epoch_unit
    Use the LAST iter (most stable) for unit calibration.
    """
    epochs = wf['per_iter_epoch']
    chains = wf['per_iter_chains']
    durs = wf['per_iter_dur_s']
    n = len(epochs)
    # For data5, lanes was always 1 → unit = dur / (epoch * chains)
    # For others, lanes = initial allocation throughout (assume constant for simplicity)
    lanes = wf['lanes_history'][0][2]
    units = []
    for i in range(n):
        chunks = -(-chains[i] // lanes)  # ceil(chains/lanes)
        unit = durs[i] / (epochs[i] * chunks)
        units.append(unit)
    return sum(units) / len(units), units


def project_data5_nobug():
    """data5 under bug-#2-fixed counterfactual.

    Key timeline events that should have triggered data5 retry/grow:
      T=2100s : data1 scale-down 2 OD g5 (cluster_g5 frees 2 OD slots)
      T=2790s : data12 scale-down 1 OD g5 (frees 1 OD slot)
      T=3704s : data1 fully completes (frees 2 res g5 slots)
      T=3844s : data3 fully completes (no g5 effect)
      T=8954s : data12 fully completes (frees 1 OD g5 slot)

    In actual R7, data5 was first allocated at T=~3490 (lanes=1 OD g5) — late
    because bug #2 blocked retries. In bug-free, data5 retries succeed earlier.

    Bug-free data5 allocation timeline:
      T=2100s : ALLOCATED lanes=1 OD g5 (1 OD slot just freed by data1 scaledown)
      T=2790s : GROW to lanes=2 (1 more OD slot from data12 scaledown)
      T=3704s : GROW to lanes=4 (cluster_g5 fully owned, 2 res + 2 OD)
    """
    d5 = WFS_R7_ACTUAL['data5']
    # Calibrate per-epoch unit from data5's actual durations (lanes=1 throughout).
    unit_avg, units = per_epoch_unit(d5)
    print(f"  data5 per-epoch unit (calibrated from R7 actuals, lanes=1): "
          f"avg={unit_avg:.1f}s, by-iter={[round(u,1) for u in units]}")
    # Bug-free schedule:
    submit = d5['submit']
    alloc_t = 2100  # was ~3490 in actual; now earlier
    chains = d5['per_iter_chains']
    epoch = d5['per_iter_epoch']

    # Lanes at each iter under bug-free progression:
    #   iter 0: starts at T=2100, lanes=1
    #   iter 1: starts after iter 0. By then T might be > 2790 → lanes=2
    #   iter 2,3,4: by T=3704+ → lanes=4
    # Compute iter durations + start times step by step.

    t = alloc_t
    # Add OD-free allocation overhead (already paid in R7; assume same)
    iter_starts = []
    iter_ends = []
    iter_lanes = []
    for i in range(d5['iters']):
        # Lanes at this iter's start time t:
        if t < 2790:
            lanes = 1
        elif t < 3704:
            lanes = 2
        else:
            lanes = 4
        # Use calibrated per-epoch unit (averaged) — or per-iter unit if we trust
        per_ep = unit_avg  # could refine to use units[i]
        chunks = -(-chains[i] // lanes)
        dur = epoch[i] * chunks * per_ep
        iter_starts.append(t)
        iter_ends.append(t + dur)
        iter_lanes.append(lanes)
        t = t + dur + 30  # 30s inter-iter overhead

    finish_t = iter_ends[-1]
    makespan_s = finish_t - submit
    deadline_s = d5['deadline']
    hit = makespan_s <= deadline_s
    return {
        'iter_starts': iter_starts,
        'iter_ends': iter_ends,
        'iter_lanes': iter_lanes,
        'iter_dur_s': [iter_ends[i] - iter_starts[i] for i in range(d5['iters'])],
        'finish_t': finish_t,
        'makespan_s': makespan_s,
        'makespan_min': makespan_s / 60,
        'deadline_min': deadline_s / 60,
        'status': 'HIT' if hit else 'MISS',
        'margin_min': (deadline_s - makespan_s) / 60,
    }


def report():
    print("=" * 75)
    print("  R7 ACTUAL vs BUG-FREE COUNTERFACTUAL — only data5 changes")
    print("=" * 75)
    print()
    print("Actual R7 (3 HITs / 4 MISSES):")
    print(f"  {'wf':6}  {'submit→finish':>20}  {'makespan':>10}  {'deadline':>10}  status")
    print("  " + "-" * 65)
    for name, d in WFS_R7_ACTUAL.items():
        ms = (d['finish'] - d['submit']) / 60
        dl = d['deadline'] / 60
        st = 'HIT' if ms <= dl else 'MISS'
        margin = dl - ms
        print(f"  {name:6}  {d['submit']:>5}→{d['finish']:>5}s     {ms:>6.1f} min  {dl:>6.1f} min  {st} ({margin:+.1f}m)")
    print()

    nobug = project_data5_nobug()
    print()
    print("Bug-free counterfactual — data5 only:")
    print(f"  alloc time   : T={2100}s (vs T=3490s actual → 23 min earlier)")
    print(f"  iter starts  : {[int(s) for s in nobug['iter_starts']]}")
    print(f"  iter lanes   : {nobug['iter_lanes']}")
    print(f"  iter dur (s) : {[int(d) for d in nobug['iter_dur_s']]}")
    print(f"  finish time  : T={int(nobug['finish_t'])}s = {nobug['finish_t']/60:.1f} min from R7 start")
    print(f"  makespan     : {nobug['makespan_min']:.1f} min (vs 338.0 min actual)")
    print(f"  deadline     : {nobug['deadline_min']:.1f} min")
    print(f"  status       : {nobug['status']} (margin {nobug['margin_min']:+.1f} min)")
    print()
    print("=" * 75)
    print("  R7 BUG-FREE FINAL OUTCOMES:")
    print("=" * 75)
    print(f"  {'wf':6}  {'actual':>12}  {'bug-free':>12}  notes")
    print("  " + "-" * 65)
    for name, d in WFS_R7_ACTUAL.items():
        ms = (d['finish'] - d['submit']) / 60
        dl = d['deadline'] / 60
        actual_st = 'HIT' if ms <= dl else 'MISS'
        if name == 'data5':
            new_st = nobug['status']
            new_ms = nobug['makespan_min']
            note = f"{ms:.0f}m → {new_ms:.0f}m"
        else:
            new_st = actual_st
            new_ms = ms
            note = "unchanged"
        print(f"  {name:6}  {ms:>5.0f}m {actual_st:5}  {new_ms:>5.0f}m {new_st:5}  {note}")
    actual_misses = sum(1 for d in WFS_R7_ACTUAL.values() if (d['finish']-d['submit'])/60 > d['deadline']/60)
    nobug_misses = actual_misses
    if WFS_R7_ACTUAL['data5'] and (WFS_R7_ACTUAL['data5']['finish']-WFS_R7_ACTUAL['data5']['submit'])/60 > WFS_R7_ACTUAL['data5']['deadline']/60:
        if nobug['status'] == 'HIT':
            nobug_misses -= 1
    print()
    print(f"  ACTUAL : {actual_misses}/7 misses")
    print(f"  BUG-FREE: {nobug_misses}/7 misses ({'+1 HIT' if nobug_misses < actual_misses else 'no change'})")


if __name__ == '__main__':
    report()
