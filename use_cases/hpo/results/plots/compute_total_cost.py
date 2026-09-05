"""Compute total submitter cost per (corner, N) by instrumenting the simulator.

Rates (per lane-hour) from elastiflow/config/resources_HPO.yaml:
  - Slurm on-prem (TCO):  $0.84  (see docs/history/gpu-cluster-cost.md)
  - Reserved g4dn.xlarge: $0.227
  - Reserved g5.xlarge:   $0.435
  - On-demand g4dn:       $0.526
  - On-demand g5:         $1.006

Per-cluster, per-tier lane-time is integrated from in-use lane-counts across the
simulation event timeline.  total_cost(run) = Σ_tier lane_hours × rate.

Output: writes total_cost_per_run.json with mean ± stdev across 6 seeds per cell.
"""
import json
import math
import random
import statistics
import heapq
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "use_cases" / "hpo" / "results" / "r7_n7_actual_vs_modeled"))
import sim_4corners_calibrated as sim4c  # noqa


def _resample_timeline_t(timeline, t_end, step):
    if t_end <= 0:
        return []
    n = int(t_end // step) + 1
    return [i * step for i in range(n + 1)]


def _resample_timeline_pct(timeline, t_end, step, reserved):
    if t_end <= 0 or not timeline:
        return []
    cap_total = sum(reserved.values())
    grid_ts = _resample_timeline_t(timeline, t_end, step)
    out = []
    j = 0
    last_snap = timeline[0][1]
    for gt in grid_ts:
        while j + 1 < len(timeline) and timeline[j + 1][0] <= gt:
            j += 1
            last_snap = timeline[j][1]
        in_use_reserved = sum(min(last_snap[c], reserved[c]) for c in reserved)
        out.append(100.0 * in_use_reserved / cap_total)
    return out


def _resample_timeline_lanes(timeline, t_end, step):
    """Total lanes in use across all clusters/tiers at each grid time."""
    if t_end <= 0 or not timeline:
        return []
    grid_ts = _resample_timeline_t(timeline, t_end, step)
    out = []
    j = 0
    last_snap = timeline[0][1]
    for gt in grid_ts:
        while j + 1 < len(timeline) and timeline[j + 1][0] <= gt:
            j += 1
            last_snap = timeline[j][1]
        out.append(sum(last_snap[c] for c in last_snap))
    return out


def _resample_timeline_tiers(timeline, t_end, step, reserved):
    """Per-tier nodes in use (slurm, reserved-cloud, on-demand)."""
    if t_end <= 0 or not timeline:
        return {"slurm": [], "reserved_cloud": [], "on_demand": []}
    grid_ts = _resample_timeline_t(timeline, t_end, step)
    slurm, res_cl, od = [], [], []
    j = 0
    last_snap = timeline[0][1]
    for gt in grid_ts:
        while j + 1 < len(timeline) and timeline[j + 1][0] <= gt:
            j += 1
            last_snap = timeline[j][1]
        s = last_snap.get("slurm", 0)
        g4 = last_snap.get("cluster_g4", 0)
        g5 = last_snap.get("cluster_g5", 0)
        slurm.append(min(s, reserved["slurm"]))
        res_cl.append(min(g4, reserved["cluster_g4"]) +
                      min(g5, reserved["cluster_g5"]))
        od.append(max(0, g4 - reserved["cluster_g4"]) +
                  max(0, g5 - reserved["cluster_g5"]))
    return {"slurm": slurm, "reserved_cloud": res_cl, "on_demand": od}

# -- rates --------------------------------------------------------------------
RATE_SLURM        = 0.84
RATE_RES = {"cluster_g4": 0.227, "cluster_g5": 0.435}
RATE_OD  = {"cluster_g4": 0.526, "cluster_g5": 1.006}

CORNERS = [("static", "edf"), ("static", "fcfs"),
           ("moldable", "edf"), ("moldable", "fcfs")]
SEEDS = [7, 107, 207, 1007, 1107, 1207]

# Per-workflow budgets (USD), from the original HPO yamls.
WF_BUDGET = {
    "data8":  1.77, "data9":  2.66, "data5":  2.15, "data7": 2.37,
    "data3":  1.64, "data12": 1.92, "data1":  1.74,
}

# Per-cluster reserved-tier rate; for slurm there is no OD so this is the
# whole rate.
RATE_RES_FULL = {"slurm": RATE_SLURM,
                 "cluster_g4": RATE_RES["cluster_g4"],
                 "cluster_g5": RATE_RES["cluster_g5"]}
RATE_OD_FULL = {"slurm": 0.0,
                "cluster_g4": RATE_OD["cluster_g4"],
                "cluster_g5": RATE_OD["cluster_g5"]}

DISPATCH_BY_N = {
    3: ["data8", "data9", "data5"],
    5: ["data8", "data9", "data5", "data7", "data3"],
    7: ["data8", "data9", "data5", "data7", "data3", "data12", "data1"],
}

# Per-model iter-count and epoch distributions (same as metrics_n{3,5,7}).
ITER_COUNT_DIST = {
    "vgg19":            [3, 3, 3, 3, 3, 3, 3, 4, 4, 5],
    "wide_resnet101_2": [2, 3, 3, 4, 4, 4, 5, 5, 5, 5],
    "convnext_large":   [1, 2, 3, 3, 3, 4, 4, 4, 5, 5],
}
EPOCH_RANGES_BY_MODEL = {
    "vgg19":            [(5, 14), (2, 18), (1, 26), (1, 30), (1, 35)],
    "wide_resnet101_2": [(15, 30), (15, 30), (15, 30), (20, 35), (25, 40)],
    "convnext_large":   [(15, 30), (15, 40), (15, 60), (20, 70), (30, 100)],
}
CHAIN_GROWTH_BY_MODEL = {
    "vgg19":            [1.0, 1.5, 2.25, 3.25, 4.0],
    "wide_resnet101_2": [1.0, 1.33, 2.0, 3.0, 3.33],
    "convnext_large":   [1.0, 1.0, 1.0, 1.0, 1.0],
}


def sample_iters(model, base_chains, rng):
    n_iters = rng.choice(ITER_COUNT_DIST[model])
    ranges = EPOCH_RANGES_BY_MODEL[model]
    growth = CHAIN_GROWTH_BY_MODEL[model]
    out = []
    for i in range(n_iters):
        lo, hi = ranges[min(i, len(ranges) - 1)]
        epoch = rng.randint(lo, hi)
        f = growth[min(i, len(growth) - 1)] * rng.uniform(0.95, 1.05)
        chains = max(1, round(base_chains * f))
        out.append((chains, epoch))
    return out


def build_wfs(seed, dispatch):
    rng = random.Random(seed)
    base = sim4c.WFS
    out = {}
    for name in dispatch:
        b = base[name]
        iters = sample_iters(b["model"], b["chains_initial"], rng)
        out[name] = dict(b)
        out[name]["iters"] = iters
        out[name]["chains_initial"] = iters[0][0]
    return out


def perturb_pe(seed):
    rng = random.Random(seed + 99999)
    return {k: v * rng.uniform(0.9, 1.1) for k, v in sim4c.PER_EPOCH.items()}


# -----------------------------------------------------------------------------
# Instrumented run_corner: identical event loop to metrics_n5.py, plus per-tier
# lane-second accumulators driven by every change to free[c].
# -----------------------------------------------------------------------------
def run_corner(mode, ordering, seed, wfs, pe):
    free = dict(sim4c.CLUSTER_CAP)
    state = {n: "pending" for n in wfs}
    completions = {}
    first_start = {}  # wf -> time of first allocation (running phase begin)
    od_intervals = []
    eq = []
    for n, w in wfs.items():
        heapq.heappush(eq, (w["submit"], 0, "arrive", n))
    seq = [0]

    def push(t, k, p):
        seq[0] += 1
        heapq.heappush(eq, (t, seq[0], k, p))

    def n_od_used(c):
        return max(0, sim4c.CLUSTER_CAP[c] - free[c] - sim4c.RESERVED[c])

    # Lane-time bookkeeping ----------------------------------------------------
    # We integrate in_use[c](t) split into reserved-tier and OD-tier.
    last_t = {c: 0.0 for c in sim4c.CLUSTER_CAP}
    lane_s_reserved = defaultdict(float)  # cluster -> seconds
    lane_s_od       = defaultdict(float)
    # Per-workflow cost accumulator (USD), proportional attribution.
    per_wf_cost = {n: 0.0 for n in wfs}
    # Utilization timeline: list of (t_seconds, in_use_per_cluster_dict).
    # Captured at every event-time after free[] has been updated.
    util_timeline = []
    # WE-intent / scheduler-decision counters (moldable only).
    # APPROVE: grow request granted in full.
    # MODIFY:  grow request granted in part (0 < granted < requested).
    # DENY:    grow request denied (granted == 0).
    # WE-DOWN intents (shrink) are always granted in this simulator.
    n_approve = 0
    n_modify  = 0
    n_deny    = 0
    n_shrink_granted = 0

    def in_use(c):
        return sim4c.CLUSTER_CAP[c] - free[c]

    def accumulate(c, t_now):
        dt = max(0.0, t_now - last_t[c])
        if dt > 0:
            iu = in_use(c)
            res = min(iu, sim4c.RESERVED[c])
            od  = max(0, iu - sim4c.RESERVED[c])
            lane_s_reserved[c] += dt * res
            lane_s_od[c]       += dt * od
            # Attribute this dt to each running wf on cluster c, weighted by
            # its lane count. Each wf's share is split between reserved/OD
            # tiers proportionally to cluster-wide utilisation.
            if iu > 0:
                hours = dt / 3600.0
                rate_res = RATE_RES_FULL[c]
                rate_od  = RATE_OD_FULL[c]
                for nm, st in state.items():
                    if isinstance(st, list) and st[0] == "running" and st[1] == c:
                        lanes_w = st[2]
                        share = lanes_w / iu
                        res_w = res * share
                        od_w  = od  * share
                        per_wf_cost[nm] += hours * (res_w * rate_res + od_w * rate_od)
        last_t[c] = t_now

    # -------------------------------------------------------------------------
    def allocate(name, c, want, tn):
        actually = min(want, free[c])
        if actually <= 0:
            return 0
        accumulate(c, tn)
        pre = n_od_used(c)
        free[c] -= actually
        post = n_od_used(c)
        od_added = post - pre
        is_first_od = od_added > 0
        chains, epoch = wfs[name]["iters"][0]
        start = tn + sim4c.ALLOC_OVERHEAD_S
        if is_first_od:
            start += sim4c.OD_CREATION_S
            od_intervals.append([c, od_added, start, None])
        pe_val = pe[(wfs[name]["model"], c)]
        base_dur = epoch * math.ceil(chains / max(actually, 1)) * pe_val
        dur = base_dur * (sim4c.COLD_START_FACTOR if is_first_od else 1.0)
        end = start + dur
        state[name] = ["running", c, actually, 0, end]
        if name not in first_start:
            first_start[name] = start
        push(end, "iter_end", name)
        return actually

    def try_alloc(tn):
        if ordering == "edf":
            key = lambda x: sim4c.WFS[x]["submit"] + sim4c.WFS[x]["deadline"]
        else:
            key = lambda x: sim4c.WFS[x]["submit"]
        q = sorted([n for n, s in state.items() if s == "queued"], key=key)
        for name in q:
            w = wfs[name]
            ci = w["chains_initial"]
            for c in w["pref_clusters"]:
                if mode == "static":
                    if free[c] >= ci:
                        allocate(name, c, ci, tn)
                        break
                else:
                    if free[c] >= 1:
                        allocate(name, c, min(ci, free[c]), tn)
                        break

    def snapshot(t):
        util_timeline.append((t, {c: in_use(c) for c in sim4c.CLUSTER_CAP}))

    snapshot(0.0)
    while eq:
        t, _, k, name = heapq.heappop(eq)
        if k == "arrive":
            state[name] = "queued"
            try_alloc(t)
            snapshot(t)
            continue
        cur = state[name]
        if cur == "done":
            continue
        _, c, lanes, i, _ = cur
        w = wfs[name]
        ni = i + 1
        if ni >= len(w["iters"]):
            completions[name] = t
            accumulate(c, t)
            pre = n_od_used(c)
            free[c] += lanes
            post = n_od_used(c)
            f = pre - post
            if f > 0:
                for itv in reversed(od_intervals):
                    if itv[3] is None and itv[0] == c and f > 0:
                        cn = min(itv[1], f)
                        if cn == itv[1]:
                            itv[3] = t
                            f -= cn
                        else:
                            itv[1] -= cn
                            od_intervals.append([c, cn, itv[2], t])
                            f -= cn
            state[name] = "done"
            try_alloc(t)
            continue
        cn_next, e_next = w["iters"][ni]
        new_lanes = lanes
        if mode == "moldable":
            if cn_next < lanes:
                rel = lanes - cn_next
                accumulate(c, t)
                pre = n_od_used(c)
                free[c] += rel
                post = n_od_used(c)
                f = pre - post
                if f > 0:
                    for itv in reversed(od_intervals):
                        if itv[3] is None and itv[0] == c and f > 0:
                            cnn = min(itv[1], f)
                            if cnn == itv[1]:
                                itv[3] = t
                                f -= cnn
                            else:
                                itv[1] -= cnn
                                od_intervals.append([c, cnn, itv[2], t])
                                f -= cnn
                new_lanes = cn_next
                n_shrink_granted += 1
                try_alloc(t)
            elif cn_next > lanes:
                aw = cn_next - lanes
                accumulate(c, t)
                pre = n_od_used(c)
                add = min(aw, free[c])
                if add == 0:
                    n_deny += 1
                elif add == aw:
                    n_approve += 1
                else:
                    n_modify += 1
                if add > 0:
                    free[c] -= add
                    post = n_od_used(c)
                    oa = post - pre
                    if oa > 0:
                        od_intervals.append([c, oa, t, None])
                    new_lanes += add
        start = t + sim4c.INTER_ITER_OVERHEAD_S
        pe_val = pe[(w["model"], c)]
        dur = e_next * math.ceil(cn_next / max(new_lanes, 1)) * pe_val
        end = start + dur
        state[name] = ["running", c, new_lanes, ni, end]
        push(end, "iter_end", name)
        snapshot(t)

    # final flush — close all reservoirs at last completion time
    t_end = max(completions.values(), default=0.0)
    for c in sim4c.CLUSTER_CAP:
        accumulate(c, t_end)
    snapshot(t_end)

    # Per-workflow deadline misses + budget overruns + wait/exec breakdown
    misses = 0
    budget_misses = 0
    per_wf = {}
    sum_wait = 0.0
    sum_exec = 0.0
    sum_turnaround = 0.0
    for nm in wfs:
        comp = completions.get(nm, t_end)
        sub = sim4c.WFS[nm]["submit"]
        fs = first_start.get(nm, comp)
        wait_s = max(0.0, fs - sub)
        exec_s = max(0.0, comp - fs)
        turn_s = comp - sub
        ddl_abs = sub + sim4c.WFS[nm]["deadline"]
        miss = 1 if comp > ddl_abs else 0
        misses += miss
        bdg = WF_BUDGET.get(nm, float("inf"))
        cw = per_wf_cost[nm]
        b_miss = 1 if cw > bdg else 0
        budget_misses += b_miss
        sum_wait += wait_s
        sum_exec += exec_s
        sum_turnaround += turn_s
        per_wf[nm] = {"completion_s": comp, "deadline_abs": ddl_abs,
                      "miss": miss, "cost": cw, "budget": bdg,
                      "budget_miss": b_miss,
                      "wait_s": wait_s, "exec_s": exec_s,
                      "turnaround_s": turn_s}
    makespan_s = t_end - min(sim4c.WFS[n]["submit"] for n in wfs)

    # OD cost from intervals (sanity-check vs accumulator)
    od_hrs_by_cluster = defaultdict(float)
    for c, nod, s, e in od_intervals:
        e_eff = e if e is not None else t_end
        od_hrs_by_cluster[c] += nod * (e_eff - s) / 3600.0

    slurm_hrs   = lane_s_reserved["slurm"] / 3600.0
    res_g4_hrs  = lane_s_reserved["cluster_g4"] / 3600.0
    res_g5_hrs  = lane_s_reserved["cluster_g5"] / 3600.0
    od_g4_hrs   = lane_s_od["cluster_g4"] / 3600.0
    od_g5_hrs   = lane_s_od["cluster_g5"] / 3600.0

    # Mean reserved-tier utilization (SeisSol-comparable). Average fraction of
    # the reserved fleet (slurm + reserved cloud) busy over [0, t_end].
    if t_end > 0:
        util_slurm = lane_s_reserved["slurm"] / (sim4c.RESERVED["slurm"] * t_end)
        util_g4    = lane_s_reserved["cluster_g4"] / (sim4c.RESERVED["cluster_g4"] * t_end)
        util_g5    = lane_s_reserved["cluster_g5"] / (sim4c.RESERVED["cluster_g5"] * t_end)
        cap_res_total = (sim4c.RESERVED["slurm"]
                         + sim4c.RESERVED["cluster_g4"]
                         + sim4c.RESERVED["cluster_g5"])
        lane_s_res_total = (lane_s_reserved["slurm"]
                            + lane_s_reserved["cluster_g4"]
                            + lane_s_reserved["cluster_g5"])
        util_reserved = lane_s_res_total / (cap_res_total * t_end)
    else:
        util_slurm = util_g4 = util_g5 = util_reserved = 0.0

    cost_slurm  = slurm_hrs * RATE_SLURM
    cost_res_g4 = res_g4_hrs * RATE_RES["cluster_g4"]
    cost_res_g5 = res_g5_hrs * RATE_RES["cluster_g5"]
    cost_od_g4  = od_g4_hrs * RATE_OD["cluster_g4"]
    cost_od_g5  = od_g5_hrs * RATE_OD["cluster_g5"]
    total = cost_slurm + cost_res_g4 + cost_res_g5 + cost_od_g4 + cost_od_g5

    return {
        "campaign_s": t_end,
        "slurm_hrs": slurm_hrs,
        "res_g4_hrs": res_g4_hrs,
        "res_g5_hrs": res_g5_hrs,
        "od_g4_hrs": od_g4_hrs,
        "od_g5_hrs": od_g5_hrs,
        "cost_slurm": cost_slurm,
        "cost_res_g4": cost_res_g4,
        "cost_res_g5": cost_res_g5,
        "cost_od_g4": cost_od_g4,
        "cost_od_g5": cost_od_g5,
        "cost_od_total": cost_od_g4 + cost_od_g5,
        "cost_reserved_total": cost_res_g4 + cost_res_g5,
        "cost_total": total,
        "misses": misses,
        "budget_misses": budget_misses,
        "sum_wait_s": sum_wait,
        "sum_exec_s": sum_exec,
        "sum_turnaround_s": sum_turnaround,
        "makespan_s": makespan_s,
        "util_reserved": util_reserved,
        "util_slurm": util_slurm,
        "util_res_g4": util_g4,
        "util_res_g5": util_g5,
        "util_timeline_t": _resample_timeline_t(util_timeline, t_end, 60.0),
        "util_timeline_pct": _resample_timeline_pct(util_timeline, t_end, 60.0,
                                                   sim4c.RESERVED),
        "util_timeline_lanes": _resample_timeline_lanes(util_timeline, t_end, 60.0),
        "util_timeline_tiers": _resample_timeline_tiers(util_timeline, t_end, 60.0,
                                                       sim4c.RESERVED),
        "reserved_cap_total": sum(sim4c.RESERVED.values()),
        "per_wf": per_wf,
        # WE-intent / scheduler-decision counters (moldable only; zero for static)
        "n_we_up_approve": n_approve,
        "n_we_up_modify":  n_modify,
        "n_we_up_deny":    n_deny,
        "n_we_down_granted": n_shrink_granted,
    }


def mean_stdev(xs):
    if not xs:
        return (0.0, 0.0)
    m = statistics.mean(xs)
    s = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return (m, s)


def main():
    out = {}
    for N, dispatch in DISPATCH_BY_N.items():
        for (mode, ordering) in CORNERS:
            cell_key = f"N{N}_{mode}_{ordering}"
            runs = []
            for seed in SEEDS:
                wfs = build_wfs(seed, dispatch)
                pe = perturb_pe(seed)
                sim4c.WFS = {**sim4c.WFS, **wfs}
                r = run_corner(mode, ordering, seed, wfs, pe)
                runs.append(r)
            agg = {}
            keys = [k for k, v in runs[0].items()
                    if isinstance(v, (int, float))]
            for k in keys:
                m, s = mean_stdev([r[k] for r in runs])
                agg[k] = {"mean": m, "stdev": s}
            agg["per_run"] = runs
            out[cell_key] = agg
            print(f"  {cell_key:30s} total ${agg['cost_total']['mean']:6.2f} "
                  f"± {agg['cost_total']['stdev']:.2f}   "
                  f"(OD ${agg['cost_od_total']['mean']:.2f}, "
                  f"slurm ${agg['cost_slurm']['mean']:.2f})")

    out_path = Path(__file__).resolve().parent / "total_cost_per_run.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
