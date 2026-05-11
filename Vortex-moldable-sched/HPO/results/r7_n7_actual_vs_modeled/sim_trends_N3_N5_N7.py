"""
sim_trends_N3_N5_N7.py — N=3, N=5, N=7 trends across all 4 scheduling corners.

Uses the same R7-calibrated per-epoch unit times and the same wf specs.
For each N ∈ {3, 5, 7}, takes the first N wfs in dispatch order
(data8, data9, data5, data7, data3, data12, data1 — Poisson seed=42).

Reports for each (N, corner):
  - Misses, sum-flow, campaign wall, OD cost
  - Compares trends across N

Highlights how moldable advantage scales (or doesn't) with N.
"""
import math
import heapq

# Reuse the same constants + sim from sim_4corners_calibrated.py by importing
import sim_4corners_calibrated as sim4c

# Dispatch order (Poisson seed=42)
DISPATCH_ORDER = ['data8', 'data9', 'data5', 'data7', 'data3', 'data12', 'data1']


def run_corner_with_subset(mode, ordering, n_wfs):
    """Patch sim4c.WFS to first n_wfs, run, return summary."""
    # Snapshot full WFS, restrict, restore
    full_wfs = sim4c.WFS
    subset = {name: full_wfs[name] for name in DISPATCH_ORDER[:n_wfs]}
    sim4c.WFS = subset
    try:
        result = sim4c.simulate(mode, ordering)
    finally:
        sim4c.WFS = full_wfs
    return result


def main():
    corners = [('static', 'edf'), ('static', 'fcfs'), ('moldable', 'edf'), ('moldable', 'fcfs')]
    Ns = [3, 5, 7]

    # Run all combinations
    results = {}
    for n in Ns:
        for mode, ordering in corners:
            key = (n, mode, ordering)
            results[key] = run_corner_with_subset(mode, ordering, n)

    # Print trends per corner across N
    print("=" * 80)
    print("  TRENDS ACROSS N=3, 5, 7 — same R7-calibrated model")
    print("=" * 80)
    for mode, ordering in corners:
        label = f"{mode.upper()} {ordering.upper()}"
        print(f"\n  {label}")
        print(f"  {'N':>3}  {'misses':>8}  {'sum-flow':>10}  {'campaign':>10}  {'OD cost':>10}")
        print(f"  {'-' * 50}")
        for n in Ns:
            r = results[(n, mode, ordering)]
            print(f"  {n:>3}  {r['misses']}/{n:<5}  {r['sum_flow_min']:>8.0f}m   {r['campaign_min']:>7.0f}m   ${r['od_cost_usd']:>6.2f}")

    # Cross-corner comparison per N
    print("\n" + "=" * 80)
    print("  CROSS-CORNER COMPARISON PER N")
    print("=" * 80)
    for n in Ns:
        print(f"\n  N={n}")
        print(f"  {'corner':<18}  {'misses':>8}  {'sum-flow':>10}  {'campaign':>10}  {'OD cost':>10}")
        print(f"  {'-' * 60}")
        for mode, ordering in corners:
            r = results[(n, mode, ordering)]
            label = f"{mode.upper()} {ordering.upper()}"
            print(f"  {label:<18}  {r['misses']}/{n:<5}  {r['sum_flow_min']:>8.0f}m   {r['campaign_min']:>7.0f}m   ${r['od_cost_usd']:>6.2f}")

    # Moldable cost savings vs Static, per N
    print("\n" + "=" * 80)
    print("  MOLDABLE COST SAVING vs STATIC (per N, per ordering)")
    print("=" * 80)
    print(f"  {'N':>3}  {'EDF: stat→mold cost':>22}  {'savings':>10}  {'FCFS: stat→mold cost':>22}  {'savings':>10}")
    print(f"  {'-' * 80}")
    for n in Ns:
        s_edf = results[(n, 'static', 'edf')]['od_cost_usd']
        m_edf = results[(n, 'moldable', 'edf')]['od_cost_usd']
        s_fcfs = results[(n, 'static', 'fcfs')]['od_cost_usd']
        m_fcfs = results[(n, 'moldable', 'fcfs')]['od_cost_usd']
        edf_save_pct = (s_edf - m_edf) / s_edf * 100 if s_edf > 0 else 0
        fcfs_save_pct = (s_fcfs - m_fcfs) / s_fcfs * 100 if s_fcfs > 0 else 0
        print(f"  {n:>3}    ${s_edf:>5.2f} → ${m_edf:>5.2f}      {edf_save_pct:>+5.0f} %"
              f"      ${s_fcfs:>5.2f} → ${m_fcfs:>5.2f}      {fcfs_save_pct:>+5.0f} %")

    # Sum-flow trend
    print("\n" + "=" * 80)
    print("  SUM-FLOW TREND (minutes)")
    print("=" * 80)
    print(f"  {'N':>3}  {'Static EDF':>12}  {'Static FCFS':>12}  {'Mold EDF':>12}  {'Mold FCFS':>12}")
    print(f"  {'-' * 70}")
    for n in Ns:
        cells = [f"{n:>3}"]
        for mode, ordering in corners:
            r = results[(n, mode, ordering)]
            cells.append(f"{r['sum_flow_min']:>10.0f}m")
        print(f"  " + "  ".join(f"{c:>12}" for c in cells))

    # Misses trend
    print("\n" + "=" * 80)
    print("  MISSES TREND (count out of N)")
    print("=" * 80)
    print(f"  {'N':>3}  {'Static EDF':>12}  {'Static FCFS':>12}  {'Mold EDF':>12}  {'Mold FCFS':>12}")
    print(f"  {'-' * 70}")
    for n in Ns:
        cells = [f"{n:>3}"]
        for mode, ordering in corners:
            r = results[(n, mode, ordering)]
            cells.append(f"{r['misses']}/{n}")
        print(f"  " + "  ".join(f"{c:>12}" for c in cells))


if __name__ == '__main__':
    main()
