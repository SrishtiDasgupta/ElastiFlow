"""Thesis plots for the plain SeisSol-TinyDA chapter.

Reads aggregated per-cell metrics from `plain_results_per_run.json`,
aggregates over 6 seeds per (variant, N), and emits 13 PDFs + PNGs
matching the visual grammar of the HPO chapter.

Plot index:
  01  cost/wf vs N                   (headline scaling)
  01b cost-tier stack @ N=400        (mechanism)
  02  scaling panel 2x2 (vs N)       (cost, miss, wait, util)
  02b miss-rate bar @ N=400          (snapshot)
  02d turnaround stack @ N=400       (wait+exec+cold-start)
  02e saturation diagnostic          (delta% per N-step)
  04  paired delta% (mold-vs-static) (head-to-head @ N=400)
  06  cost-vs-miss Pareto            (all N, frontier)
  07  scaling activity (mechanism)   (scale-up per wf + success rate)
  08  scheduler-override heatmap     (workflow engine intent vs decision)
  09  sort-key sensitivity 2x2       (_r vs _c)
  10  rank factor-pair comparison    ([50,50] vs [25,75])
  11  intra-run utilisation timeline (representative cell)
  12  per-workflow scatter @ N=400   (cost vs turnaround time distribution)
"""
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np

# --------------------------------------------------------------------- paths
HERE = Path(__file__).resolve().parent
JSON_PATH = HERE / "plain_results_per_run.json"
OUT_DIR = HERE / "plots"
OUT_DIR.mkdir(exist_ok=True)

# --------------------------------------------------------------------- styling
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.weight": "bold",
    "axes.labelweight": "bold",
    "axes.titleweight": "bold",
    "figure.titleweight": "bold",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "pdf.fonttype": 42,
})

TITLE_FS = 18
LABEL_FS = 16
TICK_FS = 14
LEG_FS = 12
ANNOT_FS = 11

def _sci_yaxis(ax):
    """Apply 1×10^x scientific notation to the y-axis and nudge label
    leftward so the ×10^n offset text does not collide with it."""
    from matplotlib.ticker import ScalarFormatter
    fmt = ScalarFormatter(useMathText=True)
    fmt.set_scientific(True)
    fmt.set_powerlimits((0, 0))
    ax.yaxis.set_major_formatter(fmt)
    ax.yaxis.get_offset_text().set_fontsize(TICK_FS)
    ax.yaxis.labelpad = 10

def _per_seed_pcts(cells, keys):
    """For each seed-cell, compute % share of each key in keys. Returns
    dict of key -> list of per-seed percentages."""
    out = {k: [] for k in keys}
    for c in cells:
        total = sum(c.get(k, 0) for k in keys)
        if total > 0:
            for k in keys:
                out[k].append(100 * c.get(k, 0) / total)
    return out

def _mean_std(vs):
    if not vs:
        return (0.0, 0.0)
    return (mean(vs), stdev(vs) if len(vs) > 1 else 0.0)

def _legend_below(ax, ncol=4, fontsize=None, y=-0.18):
    """Place legend below axes, centered. Use after all artists plotted."""
    return ax.legend(fontsize=fontsize or LEG_FS, ncol=ncol,
                     loc="upper center",
                     bbox_to_anchor=(0.5, y), framealpha=0.92)

# --------------------------------------------------------------------- variants
VARIANTS = [
    "fcfs_static_r",  "fcfs_static_c",
    "fcfs_moldable_r","fcfs_moldable_c",
    "edf_static_r",   "edf_static_c",
    "edf_moldable_r", "edf_moldable_c",
    "heft_static",
    "rank_moldable_5050", "rank_moldable_2575",
]

# Family colour scheme (consistent across all figures)
FAMILY_COL = {
    "fcfs":  "#F59E0B",  # amber
    "edf":   "#1D4ED8",  # royal blue
    "heft":  "#7E22CE",  # purple
    "rank":  "#059669",  # emerald
}
# Static = dashed, Moldable = solid
STYLE = {
    "fcfs_static_r":      dict(c=FAMILY_COL["fcfs"], ls="--", marker="o", mfc="white"),
    "fcfs_static_c":      dict(c=FAMILY_COL["fcfs"], ls="--", marker="s", mfc="white"),
    "fcfs_moldable_r":    dict(c=FAMILY_COL["fcfs"], ls="-",  marker="o"),
    "fcfs_moldable_c":    dict(c=FAMILY_COL["fcfs"], ls="-",  marker="s"),
    "edf_static_r":       dict(c=FAMILY_COL["edf"],  ls="--", marker="o", mfc="white"),
    "edf_static_c":       dict(c=FAMILY_COL["edf"],  ls="--", marker="s", mfc="white"),
    "edf_moldable_r":     dict(c=FAMILY_COL["edf"],  ls="-",  marker="o"),
    "edf_moldable_c":     dict(c=FAMILY_COL["edf"],  ls="-",  marker="s"),
    "heft_static":        dict(c=FAMILY_COL["heft"], ls="--", marker="^", mfc="white"),
    "rank_moldable_5050": dict(c=FAMILY_COL["rank"], ls="-",  marker="D"),
    "rank_moldable_2575": dict(c=FAMILY_COL["rank"], ls="-",  marker="v"),
}
LABEL = {
    "fcfs_static_r":      "FCFS-ST$_r$",
    "fcfs_static_c":      "FCFS-ST$_c$",
    "fcfs_moldable_r":    "Elastic-FCFS$_r$",
    "fcfs_moldable_c":    "Elastic-FCFS$_c$",
    "edf_static_r":       "EDF-ST$_r$",
    "edf_static_c":       "EDF-ST$_c$",
    "edf_moldable_r":     "Elastic-EDF$_r$",
    "edf_moldable_c":     "Elastic-EDF$_c$",
    "heft_static":        "HEFT-ST",
    "rank_moldable_5050": "Elastic-Rank[50,50]",
    "rank_moldable_2575": "Elastic-Rank[25,75]",
}

NS = [100, 200, 300, 400]
HEADLINE_N = 400

# Currency: internal metrics are in EUR; report in USD to match Kavitha's
# appendix convention (1 EUR = 1.10 USD).
EUR_TO_USD = 1.10
CCY = "USD"

def _to_ccy(v):
    return v * EUR_TO_USD if v is not None else v

# --------------------------------------------------------------------- load + aggregate
def load_data():
    raw = json.loads(JSON_PATH.read_text())
    ok = [c for c in raw.values() if c.get("_exit_code") == 0]
    grouped = defaultdict(list)
    for c in ok:
        grouped[(c["_variant"], c["_N"])].append(c)
    return grouped

def agg(cells, key):
    vs = [c[key] for c in cells if c.get(key) is not None]
    if not vs:
        return (float("nan"), 0.0)
    m = mean(vs)
    s = stdev(vs) if len(vs) > 1 else 0.0
    return (m, s)

DATA = load_data()

# ============================================================================
# 01 — Cost per workflow vs N (headline)
# ============================================================================
def plot_01_cost_vs_n():
    fig, ax = plt.subplots(figsize=(10, 6))
    for v in VARIANTS:
        ms, sds = [], []
        for n in NS:
            m, s = agg(DATA[(v, n)], "total_cost_eur")
            ms.append(m * EUR_TO_USD / n)
            sds.append(s * EUR_TO_USD / n)
        ms, sds = np.array(ms), np.array(sds)
        ax.plot(NS, ms, label=LABEL[v], linewidth=2.2, markersize=8, **STYLE[v])
        ax.fill_between(NS, ms - sds, ms + sds, color=STYLE[v]["c"], alpha=0.08)
    ax.set_xlabel("Batch size N (workflows)", fontsize=LABEL_FS)
    ax.set_ylabel("Average cost per workflow (USD)", fontsize=LABEL_FS)
    ax.set_title("Cost-per-workflow scaling", fontsize=TITLE_FS)
    ax.set_xticks(NS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    _legend_below(ax, ncol=4)
    fig.tight_layout()
    _save(fig, "01_cost_vs_n")

# ============================================================================
# 01b — Cost-tier decomposition stacked bar @ N=400
# ============================================================================
def plot_01b_cost_stack():
    fig, ax = plt.subplots(figsize=(12, 6.5))
    x = np.arange(len(VARIANTS))
    bottoms = np.zeros(len(VARIANTS))
    tiers = [
        ("total_cost_on_prem", "On-premise",  "#10B981"),
        ("total_cost_reserved","Reserved cloud","#3B82F6"),
        ("total_cost_on_demand","On-demand cloud","#EF4444"),
    ]
    for key, label, col in tiers:
        vals = np.array([agg(DATA[(v, HEADLINE_N)], key)[0] * EUR_TO_USD for v in VARIANTS])
        ax.bar(x, vals, bottom=bottoms, label=label, color=col,
               edgecolor="white", linewidth=1.0)
        bottoms += vals
    # Total-cost std across seeds — single error bar at top of stack
    total_stds = np.array([agg(DATA[(v, HEADLINE_N)], "total_cost_eur")[1] * EUR_TO_USD
                           for v in VARIANTS])
    ax.errorbar(x, bottoms, yerr=total_stds, fmt="none", ecolor="black",
                capsize=4, lw=1.2, zorder=10)
    # Annotate %OD share above each error bar
    for i, v in enumerate(VARIANTS):
        od_pct = agg(DATA[(v, HEADLINE_N)], "cost_pct_on_demand")[0]
        ax.text(i, (bottoms[i] + total_stds[i]) * 1.02,
                f"OD {od_pct:.0f}%",
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in VARIANTS], rotation=35, ha="right")
    ax.set_ylabel("Total batch cost (USD)", fontsize=LABEL_FS)
    ax.set_title(f"Cost-tier decomposition at N = {HEADLINE_N}", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    _sci_yaxis(ax)
    # Headroom for OD% annotations + space for legend below the axes
    ax.set_ylim(top=(bottoms + total_stds).max() * 1.12)
    ax.legend(fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.28), ncol=3, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "01b_cost_stack")

# ============================================================================
# 02 — Scaling vs N — one plot per metric (cost, miss, wait, util)
# ============================================================================
def _plot_one_metric_vs_n(key, ylabel, title, fname, per_n=False, legend_below=True, sci_y=False):
    fig, ax = plt.subplots(figsize=(10, 6.5))
    for v in VARIANTS:
        ms, sds = [], []
        for n in NS:
            m, s = agg(DATA[(v, n)], key)
            if per_n:
                m, s = m * EUR_TO_USD / n, s * EUR_TO_USD / n
            ms.append(m); sds.append(s)
        ms, sds = np.array(ms), np.array(sds)
        ax.plot(NS, ms, label=LABEL[v], linewidth=2.2, markersize=8, **STYLE[v])
        ax.fill_between(NS, ms - sds, ms + sds, color=STYLE[v]["c"], alpha=0.08)
    ax.set_xticks(NS)
    ax.set_xlabel("Batch size N (workflows)", fontsize=LABEL_FS)
    ax.set_ylabel(ylabel, fontsize=LABEL_FS)
    ax.set_title(title, fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    if sci_y:
        _sci_yaxis(ax)
    if legend_below:
        ax.legend(fontsize=LEG_FS, ncol=4, loc="upper center",
                  bbox_to_anchor=(0.5, -0.18), framealpha=0.92)
    else:
        ax.legend(fontsize=LEG_FS, ncol=2, loc="best", framealpha=0.92)
    fig.tight_layout()
    _save(fig, fname)

def plot_02_cost_vs_n():
    _plot_one_metric_vs_n("total_cost_eur",
                          "Cost per workflow (USD)",
                          "Cost per workflow vs batch size N",
                          "02_cost_per_wf_vs_n", per_n=True, legend_below=True)

def plot_02_miss_vs_n():
    _plot_one_metric_vs_n("deadline_miss_rate",
                          "Deadline miss-rate",
                          "Deadline miss-rate vs batch size N",
                          "02_miss_vs_n")

def plot_02_wait_vs_n():
    _plot_one_metric_vs_n("avg_wait_time_s",
                          "Average wait time (seconds)",
                          "Wait time vs batch size N",
                          "02_wait_vs_n", sci_y=True)

def plot_02_turnaround_vs_n():
    _plot_one_metric_vs_n("avg_flowtime_s",
                          "Average turnaround per workflow\n(seconds)",
                          "Turnaround vs batch size N",
                          "02_turnaround_vs_n", sci_y=True)

def plot_02_budget_vs_n():
    _plot_one_metric_vs_n("budget_miss_rate",
                          "Budget miss-rate",
                          "Budget miss-rate vs batch size N",
                          "02_budget_vs_n")

def plot_02_util_vs_n():
    _plot_one_metric_vs_n("util_overall_pct",
                          "Resource utilisation (%)",
                          "Resource utilisation vs batch size N",
                          "02_util_vs_n")

# ============================================================================
# 02b — Deadline miss-rate bar @ N=400
# ============================================================================
def plot_02b_miss_bar():
    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = np.arange(len(VARIANTS))
    ms = np.array([agg(DATA[(v, HEADLINE_N)], "deadline_miss_rate")[0] for v in VARIANTS])
    sds= np.array([agg(DATA[(v, HEADLINE_N)], "deadline_miss_rate")[1] for v in VARIANTS])
    cols = [STYLE[v]["c"] for v in VARIANTS]
    hatch = ['//' if 'static' in v or 'heft' in v else '' for v in VARIANTS]
    bars = ax.bar(x, ms, yerr=sds, capsize=4, color=cols,
                  edgecolor="black", linewidth=1.0)
    for b, h in zip(bars, hatch):
        b.set_hatch(h)
    for i, m in enumerate(ms):
        ax.text(i, m + sds[i] + 0.005, f"{m:.2f}",
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in VARIANTS], rotation=35, ha="right")
    ax.set_ylabel("Deadline miss-rate", fontsize=LABEL_FS)
    ax.set_title(f"Deadline miss-rate at N = {HEADLINE_N}", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    # legend explaining hatch
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor="white", edgecolor="black", hatch="//", label="Static"),
                       Patch(facecolor="white", edgecolor="black", label="Elastic")],
              fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.30), ncol=2, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "02b_miss_bar")

# ============================================================================
# 02d — Turnaround stack @ N=400  (wait + execution)
# ============================================================================
def plot_02c_budget_miss_bar():
    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = np.arange(len(VARIANTS))
    ms = np.array([agg(DATA[(v, HEADLINE_N)], "budget_miss_rate")[0] for v in VARIANTS])
    sds= np.array([agg(DATA[(v, HEADLINE_N)], "budget_miss_rate")[1] for v in VARIANTS])
    cols = [STYLE[v]["c"] for v in VARIANTS]
    hatch = ['//' if 'static' in v or 'heft' in v else '' for v in VARIANTS]
    bars = ax.bar(x, ms, yerr=sds, capsize=4, color=cols,
                  edgecolor="black", linewidth=1.0)
    for b, h in zip(bars, hatch):
        b.set_hatch(h)
    for i, mval in enumerate(ms):
        ax.text(i, mval + sds[i] + 0.002, f"{mval:.3f}",
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in VARIANTS], rotation=35, ha="right")
    ax.set_ylabel("Budget miss-rate", fontsize=LABEL_FS)
    ax.set_title(f"Budget miss-rate at N = {HEADLINE_N}", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor="white", edgecolor="black", hatch="//", label="Static"),
                       Patch(facecolor="white", edgecolor="black", label="Elastic")],
              fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.30), ncol=2, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "02c_budget_miss_bar")

def plot_02d_turnaround_stack():
    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(VARIANTS))
    # flowtime = wait + execution; execution = flowtime - wait
    waits, execs, totals_std = [], [], []
    for v in VARIANTS:
        ft_m, ft_s = agg(DATA[(v, HEADLINE_N)], "avg_flowtime_s")
        wt_m, _    = agg(DATA[(v, HEADLINE_N)], "avg_wait_time_s")
        waits.append(wt_m)
        execs.append(max(ft_m - wt_m, 0))
        totals_std.append(ft_s)
    waits, execs, totals_std = np.array(waits), np.array(execs), np.array(totals_std)
    ax.bar(x, waits, label="Wait (queueing)",   color="#F87171",
           edgecolor="white", linewidth=1.0)
    ax.bar(x, execs, bottom=waits, label="Execution + cold-start",
           color="#34D399", edgecolor="white", linewidth=1.0)
    totals = waits + execs
    ax.errorbar(x, totals, yerr=totals_std, fmt="none", ecolor="black",
                capsize=4, lw=1.2, zorder=10)
    for i, total in enumerate(totals):
        ax.text(i, total + totals_std[i] + totals.max() * 0.01,
                f"{total/3600:.1f}h",
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in VARIANTS], rotation=35, ha="right")
    ax.set_ylabel("Average turnaround per workflow\n(seconds)", fontsize=LABEL_FS)
    ax.set_title(f"Turnaround decomposition at N = {HEADLINE_N}", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    _sci_yaxis(ax)
    _legend_below(ax, ncol=2, y=-0.30)
    fig.tight_layout()
    _save(fig, "02d_turnaround_stack")

# ============================================================================
# 02e — Saturation diagnostic: Δ% per N-step
# ============================================================================
def plot_02e_saturation():
    fig, axes = plt.subplots(3, 2, figsize=(14, 13), sharey=True)
    steps = ["100→200", "200→300", "300→400"]
    x = np.arange(len(steps))
    width = 0.17
    metrics = [
        ("total_cost_eur",    "Cost/wf",     True,  "#1D4ED8"),
        ("avg_wait_time_s",   "Wait",        False, "#EF4444"),
        ("deadline_miss_rate","Deadline",    False, "#7E22CE"),
        ("budget_miss_rate",  "Budget",      False, "#F59E0B"),
        ("batch_makespan_s",  "Makespan",    False, "#059669"),
    ]
    # 3x2 grid — row = algorithm family, column = static / elastic.
    reps = [
        "fcfs_static_c",     "fcfs_moldable_c",       # FCFS family
        "edf_static_c",      "edf_moldable_c",        # EDF family
        "heft_static",       "rank_moldable_2575",    # Rank family
    ]
    for ax, rep in zip(axes.flatten(), reps):
        for i, (key, label, per_n, col) in enumerate(metrics):
            vals, errs = [], []
            for j in range(len(NS) - 1):
                n0, n1 = NS[j], NS[j+1]
                cells0 = {c["_seed"]: c for c in DATA[(rep, n0)]}
                cells1 = {c["_seed"]: c for c in DATA[(rep, n1)]}
                seeds = sorted(set(cells0) & set(cells1))
                per_seed = []
                for s in seeds:
                    v0 = cells0[s].get(key)
                    v1 = cells1[s].get(key)
                    if v0 is None or v1 is None:
                        continue
                    if per_n:
                        v0, v1 = v0 / n0, v1 / n1
                    if v0:
                        per_seed.append(100 * (v1 - v0) / v0)
                m, s = _mean_std(per_seed)
                vals.append(m); errs.append(s)
            ax.bar(x + (i - 2.0) * width, vals, width, yerr=errs,
                   capsize=3, label=label, color=col,
                   ecolor="black", error_kw={"lw": 0.8},
                   edgecolor="white", linewidth=1.0)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(steps, fontsize=TICK_FS)
        ax.set_title(LABEL[rep], fontsize=TITLE_FS - 2)
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, axis="y", alpha=0.3)
        # symlog: linear within ±50%, log-compressed beyond — lets small
        # cost/miss/budget/makespan bars and large wait bars coexist.
        ax.set_yscale("symlog", linthresh=50)
        from matplotlib.ticker import SymmetricalLogLocator, FuncFormatter
        ax.yaxis.set_major_locator(
            SymmetricalLogLocator(linthresh=50, base=10,
                                  subs=[1.0, 2.0, 5.0]))
        ax.yaxis.set_major_formatter(FuncFormatter(
            lambda v, pos: f"{int(v):d}" if abs(v) >= 1 else "0"))
    # shared axis labels
    for ax in axes[:, 0]:
        ax.set_ylabel("Relative change Δ\n(%, symlog)", fontsize=LABEL_FS)
    for ax in axes[-1, :]:
        ax.set_xlabel("Load step", fontsize=LABEL_FS)
    # one shared legend below the figure
    handles = [plt.Rectangle((0, 0), 1, 1, color=m[3], label=m[1]) for m in metrics]
    fig.legend(handles=handles, fontsize=LEG_FS, ncol=5,
               loc="lower center", bbox_to_anchor=(0.5, -0.02), framealpha=0.92)
    fig.suptitle("Saturation diagnostic — six representative operating points",
                 fontsize=TITLE_FS, y=0.99)
    fig.tight_layout(rect=[0, 0.04, 1, 0.97])
    _save(fig, "02e_saturation")

# ============================================================================
# 02f — Miss decomposition (deadline-only / budget-only / both) at N=400
#       overall_miss = deadline OR budget (per-workflow)
#       We approximate the three slices as:
#         both           ≈ max(deadline+budget - overall, 0)
#         deadline-only  ≈ deadline - both
#         budget-only    ≈ budget   - both
# ============================================================================
def plot_02f_miss_decomposition():
    fig, ax = plt.subplots(figsize=(11.5, 6))
    x = np.arange(len(VARIANTS))
    # Compute per-seed three-way split, then mean+std across seeds.
    b_only_m, d_only_m, both_m = [], [], []
    ov_std = []
    for v in VARIANTS:
        bs, ds, bt, ov = [], [], [], []
        for c in DATA[(v, HEADLINE_N)]:
            d_ = c.get("deadline_miss_rate") or 0
            b_ = c.get("budget_miss_rate")   or 0
            o_ = c.get("overall_miss_rate")  or 0
            both = max(d_ + b_ - o_, 0)
            bs.append(max(b_ - both, 0))
            ds.append(max(d_ - both, 0))
            bt.append(both)
            ov.append(o_)
        b_only_m.append(mean(bs)); d_only_m.append(mean(ds)); both_m.append(mean(bt))
        ov_std.append(stdev(ov) if len(ov) > 1 else 0)
    b_only_m = np.array(b_only_m); d_only_m = np.array(d_only_m)
    both_m = np.array(both_m);     ov_std = np.array(ov_std)
    ax.bar(x, b_only_m, label="Budget only", color="#F59E0B",
           edgecolor="white", linewidth=1.0)
    ax.bar(x, d_only_m, bottom=b_only_m, label="Deadline only",
           color="#7E22CE", edgecolor="white", linewidth=1.0)
    ax.bar(x, both_m, bottom=b_only_m + d_only_m, label="Both",
           color="#DC2626", edgecolor="white", linewidth=1.0)
    totals = b_only_m + d_only_m + both_m
    ax.errorbar(x, totals, yerr=ov_std, fmt="none", ecolor="black",
                capsize=4, lw=1.2, zorder=10)
    for i, total in enumerate(totals):
        ax.text(i, total + ov_std[i] + 0.008, f"{total:.2f}",
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in VARIANTS], rotation=35, ha="right")
    ax.set_ylabel("Miss rate", fontsize=LABEL_FS)
    ax.set_title(f"Miss decomposition at N = {HEADLINE_N}", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.42), ncol=3, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "02f_miss_decomposition")

# ============================================================================
# 04 — Paired Δ% (moldable vs static) @ N=400
# ============================================================================
def plot_04_paired_delta():
    pairs = [
        ("FCFS$_c$", "fcfs_moldable_c",    "fcfs_static_c"),
        ("FCFS$_r$", "fcfs_moldable_r",    "fcfs_static_r"),
        ("EDF$_c$",  "edf_moldable_c",     "edf_static_c"),
        ("EDF$_r$",  "edf_moldable_r",     "edf_static_r"),
        ("Rank",     "rank_moldable_2575", "heft_static"),
    ]
    metrics_pct = [
        ("total_cost_eur",   "Δ Cost"),
        ("avg_wait_time_s",  "Δ Wait"),
        ("batch_makespan_s", "Δ Makespan"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    x = np.arange(len(pairs))
    width = 0.25

    # Left panel: % deltas
    for i, (key, label) in enumerate(metrics_pct):
        vals, errs = [], []
        for _, mv, sv in pairs:
            mvs = {c["_seed"]: c for c in DATA[(mv, HEADLINE_N)]}
            svs = {c["_seed"]: c for c in DATA[(sv, HEADLINE_N)]}
            seeds = sorted(set(mvs) & set(svs))
            ds = [(mvs[s][key] - svs[s][key]) / svs[s][key] * 100
                  for s in seeds if svs[s][key]]
            vals.append(mean(ds))
            errs.append(stdev(ds) if len(ds) > 1 else 0)
        axes[0].bar(x + (i - 1) * width, vals, width, yerr=errs, capsize=4,
                    label=label, edgecolor="white", linewidth=1.0)
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels([p[0] for p in pairs], fontsize=LABEL_FS)
    axes[0].set_ylabel("Δ (%, Elastic − Static)", fontsize=LABEL_FS)
    axes[0].set_title(f"Relative deltas @ N={HEADLINE_N}", fontsize=TITLE_FS)
    axes[0].grid(True, axis="y", alpha=0.3)
    axes[0].legend(fontsize=LEG_FS, loc="upper center",
                   bbox_to_anchor=(0.5, -0.18), ncol=3, framealpha=0.92)
    axes[0].tick_params(labelsize=TICK_FS)

    # Right panel: absolute miss-rate deltas (pp) — deadline AND budget side-by-side
    width_r = 0.35
    miss_metrics = [
        ("deadline_miss_rate", "Δ Deadline", "#7E22CE"),
        ("budget_miss_rate",   "Δ Budget",   "#F59E0B"),
    ]
    for j, (key, label, col) in enumerate(miss_metrics):
        vals, errs = [], []
        for _, mv, sv in pairs:
            mvs = {c["_seed"]: c for c in DATA[(mv, HEADLINE_N)]}
            svs = {c["_seed"]: c for c in DATA[(sv, HEADLINE_N)]}
            seeds = sorted(set(mvs) & set(svs))
            ds = [(mvs[s][key] - svs[s][key]) * 100 for s in seeds]
            vals.append(mean(ds))
            errs.append(stdev(ds) if len(ds) > 1 else 0)
        axes[1].bar(x + (j - 0.5) * width_r, vals, width_r,
                    yerr=errs, capsize=4, color=col, label=label,
                    edgecolor="white", linewidth=1.0)
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([p[0] for p in pairs], fontsize=LABEL_FS)
    axes[1].set_ylabel("Δ Miss-rate\n(percentage points)", fontsize=LABEL_FS)
    axes[1].set_title(f"Miss-rate trade-off @ N={HEADLINE_N}", fontsize=TITLE_FS)
    axes[1].grid(True, axis="y", alpha=0.3)
    axes[1].legend(fontsize=LEG_FS, loc="upper center",
                   bbox_to_anchor=(0.5, -0.18), ncol=2, framealpha=0.92)
    axes[1].tick_params(labelsize=TICK_FS)
    fig.tight_layout()
    _save(fig, "04_paired_delta")

# ============================================================================
# 06 — Cost-vs-miss Pareto frontier
# ============================================================================
def plot_06_pareto():
    """Cost-vs-OVERALL-miss snapshot at N=400. Uses overall_miss_rate
    (deadline OR budget) as the most honest summary metric. The
    deadline-vs-budget breakdown is exposed separately in 02f.

    Regime names (Hard-SLO / Balanced / Cost-first) are not drawn on the
    plot — they belong in the chapter prose. The plot shows only:
    frontier points (large coloured dots), dominated points (small grey),
    the frontier line, and the dominated region."""
    fig, ax = plt.subplots(figsize=(13.5, 8))

    # Frontier composition (same as deadline-only Pareto)
    FRONTIER = ["edf_static_c", "edf_moldable_c", "rank_moldable_5050"]
    frontier_set = set(FRONTIER)
    FRONTIER_COL = {
        "edf_static_c":       "#1D4ED8",
        "edf_moldable_c":     "#059669",
        "rank_moldable_5050": "#D97706",
    }

    # N=400 (cost, overall_miss) for every variant, with std across seeds
    pts = {}
    errs = {}
    for v in VARIANTS:
        cm, cs = agg(DATA[(v, NS[-1])], "total_cost_eur")
        mm, ms = agg(DATA[(v, NS[-1])], "overall_miss_rate")
        pts[v] = (cm * EUR_TO_USD / NS[-1], mm)
        errs[v] = (cs * EUR_TO_USD / NS[-1], ms)

    # Per-variant label offsets (in display points). All dominated points
    # have positive dx so labels live inside the shaded dominated region
    # (to the right of the frontier), never on/past the y-axis.
    OFFSET = {
        # Frontier — pushed high above so the dominated cluster has room.
        "edf_static_c":        ( 0,   38),
        "edf_moldable_c":      ( 0,   38),
        "rank_moldable_5050":  ( 0,   72),
        # Elastic cluster — fanned in three distinct directions so no two
        # labels share the same patch of plot.
        "fcfs_moldable_c":     ( 35,  38),   # up-right
        "fcfs_moldable_r":     ( 95, -22),   # right, well below
        "rank_moldable_2575":  (110,  10),   # far right, level
        "edf_moldable_r":      ( 55, -15),   # right, below (own zone)
        # Static cluster
        "fcfs_static_c":       ( 60,   8),
        "fcfs_static_r":       ( 55, -20),
        "edf_static_r":        ( 55,  10),
        "heft_static":         ( 55,   0),
    }

    # ------- Shaded dominated region -------
    anchors = sorted([(pts[v][0], pts[v][1], v) for v in FRONTIER])
    fx = [a[0] for a in anchors]
    fy = [a[1] for a in anchors]
    xmax = max(c for c, _ in pts.values()) * 1.22
    ymax = max(m for _, m in pts.values()) * 1.28
    xmin = min(c for c, _ in pts.values()) * 0.94
    shade_x = [fx[0]] + fx + [xmax, xmax]
    shade_y = [ymax]  + fy + [fy[-1], ymax]
    ax.fill(shade_x, shade_y, color="#FCA5A5", alpha=0.18, zorder=0)
    ax.text(xmax * 0.985, ymax * 0.94, "Dominated region",
            ha="right", va="top", fontsize=13, style="italic",
            color="#7F1D1D", alpha=0.7)

    # ------- Frontier line -------
    ax.plot(fx, fy, "k-", linewidth=2.2, alpha=0.9, zorder=2)

    # ------- Dominated points: small grey dots with leader arrows -------
    for v, (c, m) in pts.items():
        if v in frontier_set:
            continue
        cx_err, my_err = errs[v]
        ax.errorbar([c], [m], xerr=[cx_err], yerr=[my_err], fmt="none",
                    ecolor="#9CA3AF", alpha=0.7, lw=1.0, capsize=2, zorder=2)
        ax.scatter([c], [m], s=110, color="#9CA3AF",
                   edgecolor="#4B5563", linewidth=1.0, zorder=3, alpha=0.9)
        dx, dy = OFFSET[v]
        ax.annotate(LABEL[v], (c, m),
                    xytext=(dx, dy), textcoords="offset points",
                    fontsize=11, color="#1F2937", alpha=0.95,
                    ha="center" if abs(dx) < 5 else ("left" if dx > 0 else "right"),
                    arrowprops=dict(arrowstyle="-", color="#9CA3AF",
                                    lw=0.8, alpha=0.6,
                                    shrinkA=0, shrinkB=3))

    # ------- Frontier points: large coloured dots, bold labels -------
    # Per-variant rotation (Elastic-Rank[50,50] runs vertical to save width)
    ROT = {"rank_moldable_5050": 90}
    for c, m, v in anchors:
        col = FRONTIER_COL[v]
        cx_err, my_err = errs[v]
        ax.errorbar([c], [m], xerr=[cx_err], yerr=[my_err], fmt="none",
                    ecolor=col, alpha=0.8, lw=1.5, capsize=3, zorder=4)
        ax.scatter([c], [m], s=420, color=col,
                   edgecolor="black", linewidth=2.0, zorder=5)
        dx, dy = OFFSET[v]
        ax.annotate(LABEL[v], (c, m),
                    xytext=(dx, dy), textcoords="offset points",
                    ha="center", fontsize=13, fontweight="bold",
                    color=col, rotation=ROT.get(v, 0),
                    bbox=dict(boxstyle="round,pad=0.3",
                              fc="white", ec=col, linewidth=1.5))

    ax.set_xlabel("Average cost per workflow (USD)", fontsize=LABEL_FS)
    ax.set_ylabel("Overall miss-rate (budget OR deadline)", fontsize=LABEL_FS)
    ax.set_title("Policy frontier at N = 400", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(-0.02, ymax)
    fig.tight_layout()
    _save(fig, "06_pareto")

# ============================================================================
# (Retired) 06b — superseded; 06 now uses overall_miss_rate directly.
# Kept only as a no-op stub for any external callers.
# ============================================================================
def _retired_plot_06b_pareto_overall():
    fig, ax = plt.subplots(figsize=(13, 7.5))

    # Compute (cost, overall_miss) at N=400 for every variant
    pts = {}
    for v in VARIANTS:
        c = agg(DATA[(v, NS[-1])], "total_cost_eur")[0] * EUR_TO_USD / NS[-1]
        m = agg(DATA[(v, NS[-1])], "overall_miss_rate")[0]
        pts[v] = (c, m)

    # Compute the Pareto frontier dynamically (rather than hard-coding)
    items = sorted(pts.items(), key=lambda kv: kv[1][0])  # by cost ascending
    frontier = []
    best_miss = float("inf")
    # Walking left→right, accept any point with strictly lower miss
    for v, (c, m) in items[::-1]:  # right→left: highest cost first
        pass
    # Simpler frontier: walk left→right, keep min-miss-so-far running
    frontier_pts = []
    running_min = float("inf")
    for v, (c, m) in sorted(pts.items(), key=lambda kv: kv[1][0]):
        if m < running_min:
            frontier_pts.append((c, m, v))
            running_min = m
    frontier_set = {v for _, _, v in frontier_pts}

    # ------- Shaded dominated region -------
    xmax = max(c for c, _ in pts.values()) * 1.06
    ymax = max(m for _, m in pts.values()) * 1.12
    fx = [a[0] for a in frontier_pts]
    fy = [a[1] for a in frontier_pts]
    shade_x = [fx[0]] + fx + [xmax, xmax]
    shade_y = [ymax]  + fy + [fy[-1], ymax]
    ax.fill(shade_x, shade_y, color="#FCA5A5", alpha=0.18, zorder=0)
    ax.text(xmax * 0.985, ymax * 0.95, "Dominated region",
            ha="right", va="top", fontsize=12, style="italic",
            color="#7F1D1D", alpha=0.7)

    # ------- Frontier line -------
    ax.plot(fx, fy, "k-", linewidth=2.2, alpha=0.9, zorder=2)

    # ------- Dominated points -------
    for v, (c, m) in pts.items():
        if v in frontier_set:
            continue
        ax.scatter([c], [m], s=110, color="#9CA3AF",
                   edgecolor="#4B5563", linewidth=1.0, zorder=3, alpha=0.85)
        ax.annotate(LABEL[v], (c, m),
                    xytext=(7, 4), textcoords="offset points",
                    fontsize=10, color="#374151", alpha=0.95)

    # ------- Frontier points -------
    palette = ["#1D4ED8", "#059669", "#D97706", "#7E22CE", "#BE123C"]
    for i, (c, m, v) in enumerate(frontier_pts):
        col = palette[i % len(palette)]
        ax.scatter([c], [m], s=420, color=col,
                   edgecolor="black", linewidth=2.0, zorder=5)
        ax.annotate(LABEL[v], (c, m),
                    xytext=(0, 18), textcoords="offset points",
                    ha="center", fontsize=12, fontweight="bold",
                    color=col,
                    bbox=dict(boxstyle="round,pad=0.3",
                              fc="white", ec=col, linewidth=1.5))

    ax.set_xlabel("Average cost per workflow (USD)", fontsize=LABEL_FS)
    ax.set_ylabel("Overall miss-rate (budget OR deadline)", fontsize=LABEL_FS)
    ax.set_title(f"Policy frontier on OVERALL miss at N = {NS[-1]}",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(min(c for c, _ in pts.values()) * 0.92, xmax)
    ax.set_ylim(-0.02, ymax)
    fig.tight_layout()
    _save(fig, "06b_pareto_overall")

# ============================================================================
# 07 — Scaling activity (moldable mechanism)
# ============================================================================
def plot_07_scaling_activity():
    """Scaling event frequency — up and down — across N.

    Grant-rate-by-tier panels were removed because the values are
    near-invariant across schedulers (the scaling MECHANISM is shared
    base-class code; only timing varies by family). That uniformity is
    a prose point. Tier composition of granted nodes (where the variants
    DO differ) is shown in 07b and 07c."""
    moldable_vs = [v for v in VARIANTS if "moldable" in v]
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.5))

    for ax, key, title, ylab in [
        (axes[0], "scale_up_attempts",   "Scale-up frequency",
                                          "Scale-up attempts per workflow"),
        (axes[1], "scale_down_attempts", "Scale-down frequency",
                                          "Scale-down attempts per workflow"),
    ]:
        for v in moldable_vs:
            ms, sds = [], []
            for n in NS:
                m, s = agg(DATA[(v, n)], key)
                ms.append(m / n); sds.append(s / n)
            ms, sds = np.array(ms), np.array(sds)
            ax.plot(NS, ms, linewidth=2.2, markersize=8, label=LABEL[v], **STYLE[v])
            ax.fill_between(NS, ms - sds, ms + sds, color=STYLE[v]["c"], alpha=0.08)
        ax.set_xticks(NS)
        ax.set_xlabel("N (workflows)", fontsize=LABEL_FS)
        ax.set_ylabel(ylab, fontsize=LABEL_FS)
        ax.set_title(title, fontsize=TITLE_FS)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=TICK_FS)
    # one shared legend below the figure
    handles = [plt.Line2D([0],[0], label=LABEL[v], **STYLE[v]) for v in moldable_vs]
    fig.legend(handles=handles, fontsize=LEG_FS, ncol=3,
               loc="lower center", bbox_to_anchor=(0.5, -0.02),
               framealpha=0.92)
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    _save(fig, "07_scaling_activity")

# ============================================================================
# 07b — Tier composition of GRANTED scale-up nodes @ N=400 (snapshot)
# ============================================================================
def plot_07b_grant_tier_stack():
    """Stacked bar per Elastic variant: of all nodes granted via scale-up,
    what % came from on-prem / reserved cloud / on-demand cloud?
    This is the 'why does Elastic-EDF use less OD than Elastic-FCFS'
    answer — the chapter's mechanism plot.
    Parallel structure to 01b_cost_stack."""
    moldable_vs = [v for v in VARIANTS if "moldable" in v]
    fig, ax = plt.subplots(figsize=(11, 6.5))
    x = np.arange(len(moldable_vs))
    tiers = [
        ("scale_up_grants_on_prem",    "On-premise",      "#10B981"),
        ("scale_up_grants_reserved",   "Reserved cloud",  "#3B82F6"),
        ("scale_up_grants_on_demand",  "On-demand cloud", "#EF4444"),
    ]
    keys = [t[0] for t in tiers]
    # Per-seed % share, then mean+std across seeds.
    seg_mean = {k: [] for k in keys}
    seg_std  = {k: [] for k in keys}
    od_means, od_stds = [], []
    for v in moldable_vs:
        per_seed = _per_seed_pcts(DATA[(v, HEADLINE_N)], keys)
        for k in keys:
            m, s = _mean_std(per_seed[k])
            seg_mean[k].append(m); seg_std[k].append(s)
        # OD share separately for annotation
        m_od, s_od = _mean_std(per_seed["scale_up_grants_on_demand"])
        od_means.append(m_od); od_stds.append(s_od)
    bottoms = np.zeros(len(moldable_vs))
    for key, label, col in tiers:
        vals = np.array(seg_mean[key])
        ax.bar(x, vals, bottom=bottoms, label=label, color=col,
               edgecolor="white", linewidth=1.0)
        bottoms += vals
    # Error bar on top of stack (cap of total; per-seed totals ≈ 100 so this
    # primarily reflects the OD segment variance).
    top_std = np.array(seg_std["scale_up_grants_on_demand"])
    ax.errorbar(x, bottoms, yerr=top_std, fmt="none", ecolor="black",
                capsize=4, lw=1.2, zorder=10)
    # Annotate OD share ± std
    for i, v in enumerate(moldable_vs):
        ax.text(i, 102 + top_std[i] * 0.5,
                f"OD {od_means[i]:.0f}±{od_stds[i]:.0f}%",
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in moldable_vs], rotation=30, ha="right")
    ax.set_ylabel("Granted nodes by tier (% of total)", fontsize=LABEL_FS)
    ax.set_title(f"Scale-up grant composition by tier at N = {HEADLINE_N}",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(0, 112)
    ax.legend(fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.28), ncol=3, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "07b_grant_tier_stack")

# ============================================================================
# 07c — Tier composition of granted scale-up nodes vs N (per variant)
#       2x3 small multiples — one per Elastic variant
# ============================================================================
def plot_07c_grant_tier_vs_n():
    moldable_vs = [v for v in VARIANTS if "moldable" in v]
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), sharey=True)
    tiers = [
        ("scale_up_grants_on_prem",    "On-premise",      "#10B981"),
        ("scale_up_grants_reserved",   "Reserved cloud",  "#3B82F6"),
        ("scale_up_grants_on_demand",  "On-demand cloud", "#EF4444"),
    ]
    keys = [t[0] for t in tiers]
    for ax, v in zip(axes.flatten(), moldable_vs):
        x = np.arange(len(NS))
        # per-seed % then mean+std across seeds, per N
        seg_mean = {k: [] for k in keys}
        seg_std  = {k: [] for k in keys}
        for n in NS:
            per_seed = _per_seed_pcts(DATA[(v, n)], keys)
            for k in keys:
                m, s = _mean_std(per_seed[k])
                seg_mean[k].append(m); seg_std[k].append(s)
        bottoms = np.zeros(len(NS))
        for key, label, col in tiers:
            vals = np.array(seg_mean[key])
            ax.bar(x, vals, bottom=bottoms, label=label, color=col,
                   edgecolor="white", linewidth=1.0)
            bottoms += vals
        # error bar on top using OD-segment std (cleanest single tick)
        top_std = np.array(seg_std["scale_up_grants_on_demand"])
        ax.errorbar(x, bottoms, yerr=top_std, fmt="none", ecolor="black",
                    capsize=3, lw=0.9, zorder=10)
        ax.set_xticks(x)
        ax.set_xticklabels([f"N={n}" for n in NS], fontsize=TICK_FS - 2)
        ax.set_title(LABEL[v], fontsize=TITLE_FS - 2)
        ax.tick_params(labelsize=TICK_FS - 2)
        ax.grid(True, axis="y", alpha=0.3)
        ax.set_ylim(0, 105)
    for ax in axes[:, 0]:
        ax.set_ylabel("Granted nodes\nby tier (%)", fontsize=LABEL_FS)
    # shared legend below
    handles = [plt.Rectangle((0, 0), 1, 1, color=t[2], label=t[1]) for t in tiers]
    fig.legend(handles=handles, fontsize=LEG_FS, ncol=3,
               loc="lower center", bbox_to_anchor=(0.5, -0.02),
               framealpha=0.92)
    fig.suptitle("Scale-up grant composition by tier — evolution across N",
                 fontsize=TITLE_FS, y=0.99)
    fig.tight_layout(rect=[0, 0.04, 1, 0.97])
    _save(fig, "07c_grant_tier_vs_n")

# ============================================================================
# 08 — Scheduler response marginals (Workflow Engine intent → decision)
#       Two stacked-bar panels, one per intent type:
#         (left)  WE intends UP  → full grant / partial grant / denied
#         (right) WE intends DOWN → executed / kept-alive
#       Each variant's stack sums to 100% — these are true marginals,
#       computed without needing a joint counter.
#       N ≥ 200 aggregate (skip N=100, low signal).
# ============================================================================
def plot_08_intent_satisfaction():
    """For every WE-UP intent, show the TRUE four-way outcome:
      granted-full | granted-partial | denied | overridden-to-DOWN.

    The 'overridden-to-DOWN' segment is the headline finding: in plain
    SeisSol, ~80% of WE-UP intents are silently converted by the rich
    processFreeRequest into a scale-down action (the workflow had budget
    or capacity slack the scheduler chose to release).

    WE-DOWN intents are 100% executed (no override), so they are mentioned
    in prose and not given their own panel."""
    moldable_vs = [v for v in VARIANTS if "moldable" in v]
    fig, ax = plt.subplots(figsize=(12, 6.5))
    x = np.arange(len(moldable_vs))
    # Per-seed % then mean+std across seeds. Aggregate seeds across N≥200
    # by treating each (seed, N) cell as one observation.
    full_m, full_s = [], []
    partial_m, partial_s = [], []
    denied_m, denied_s = [], []
    override_m, override_s = [], []
    for v in moldable_vs:
        full_pc, partial_pc, denied_pc, override_pc = [], [], [], []
        for n in [200, 300, 400]:
            for c in DATA[(v, n)]:
                ru = c.get("executor_request_up", 0)
                if not ru:
                    continue
                sd_att = c.get("scale_down_attempts", 0)
                rd     = c.get("executor_request_down", 0)
                override_down = max(sd_att - rd, 0)
                full_pc.append(100 * c.get("scale_up_full", 0)    / ru)
                partial_pc.append(100 * c.get("scale_up_partial", 0) / ru)
                denied_pc.append(100 * c.get("scale_up_denied", 0)   / ru)
                override_pc.append(100 * override_down / ru)
        for lst, mlist, slist in [
            (full_pc, full_m, full_s),
            (partial_pc, partial_m, partial_s),
            (denied_pc, denied_m, denied_s),
            (override_pc, override_m, override_s),
        ]:
            m, s = _mean_std(lst)
            mlist.append(m); slist.append(s)
    bottoms = np.zeros(len(moldable_vs))
    segments = [
        (full_m,     full_s,     "APPROVE",              "#10B981"),
        (partial_m,  partial_s,  "MODIFY",               "#3B82F6"),
        (denied_m,   denied_s,   "DENY",                 "#EF4444"),
        (override_m, override_s, "MODIFY (scale down)",  "#F59E0B"),
    ]
    for vals, stds, label, col in segments:
        vals = np.array(vals); stds = np.array(stds)
        ax.bar(x, vals, bottom=bottoms, label=label, color=col,
               edgecolor="white", linewidth=1.0)
        for i, val in enumerate(vals):
            if val >= 4:
                ax.text(i, bottoms[i] + val / 2,
                        f"{val:.0f}±{stds[i]:.0f}%",
                        ha="center", va="center",
                        fontsize=ANNOT_FS - 1, fontweight="bold", color="white")
        bottoms += vals
    # Error bar on top of stack — uses APPROVE-segment std as proxy (per-seed
    # APPROVE share is the most informative single uncertainty here).
    ax.errorbar(x, bottoms, yerr=np.array(full_s), fmt="none", ecolor="black",
                capsize=4, lw=1.2, zorder=10)
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[v] for v in moldable_vs], rotation=30, ha="right")
    ax.set_ylabel("Share of WE-UP intents (%)", fontsize=LABEL_FS)
    ax.set_title("Outcome of every Workflow-Engine UP intent (N ≥ 200)",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(0, 110)
    ax.legend(fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.28), ncol=4, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "08_intent_satisfaction")

# ============================================================================
# 08b — Override-to-DOWN rate vs N
#       For each Elastic variant, the % of WE-UP intents that the scheduler
#       converted into a scale-down — plotted across load levels.
#       Does the scheduler become more or less aggressive as N grows?
# ============================================================================
def plot_08b_override_vs_n():
    moldable_vs = [v for v in VARIANTS if "moldable" in v]
    fig, ax = plt.subplots(figsize=(10, 6.5))
    for v in moldable_vs:
        ys, errs = [], []
        for n in NS:
            per_seed = []
            for c in DATA[(v, n)]:
                ru = c.get("executor_request_up", 0)
                sd = c.get("scale_down_attempts", 0)
                rd = c.get("executor_request_down", 0)
                if ru > 0:
                    per_seed.append(100 * max(sd - rd, 0) / ru)
            if per_seed:
                ys.append(mean(per_seed))
                errs.append(stdev(per_seed) if len(per_seed) > 1 else 0)
            else:
                ys.append(0); errs.append(0)
        ys, errs = np.array(ys), np.array(errs)
        ax.plot(NS, ys, linewidth=2.2, markersize=8, label=LABEL[v], **STYLE[v])
        ax.fill_between(NS, ys - errs, ys + errs,
                        color=STYLE[v]["c"], alpha=0.08)
    ax.set_xticks(NS)
    ax.set_xlabel("Batch size N (workflows)", fontsize=LABEL_FS)
    ax.set_ylabel("WE-UP intents overridden to DOWN (%)", fontsize=LABEL_FS)
    ax.set_title("Scheduler override aggressiveness vs load",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    _legend_below(ax, ncol=3)
    fig.tight_layout()
    _save(fig, "08b_override_vs_n")

# ============================================================================
# 08c — Mechanism → outcome scatter
#       For each Elastic variant at N=400, plot:
#         x = override-to-DOWN rate (mechanism, % of WE-UP)
#         y = total OD cost (outcome, USD per batch)
#       Tests the hypothesis: more aggressive override ⇒ lower OD cost.
# ============================================================================
def plot_08c_override_vs_cost():
    moldable_vs = [v for v in VARIANTS if "moldable" in v]
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for v in moldable_vs:
        xs, ys = [], []
        for c in DATA[(v, HEADLINE_N)]:
            ru = c.get("executor_request_up", 0)
            sd = c.get("scale_down_attempts", 0)
            rd = c.get("executor_request_down", 0)
            if ru > 0:
                xs.append(100 * max(sd - rd, 0) / ru)
                ys.append(c["total_cost_on_demand"] * EUR_TO_USD)
        x_mean, x_std = mean(xs), (stdev(xs) if len(xs) > 1 else 0)
        y_mean, y_std = mean(ys), (stdev(ys) if len(ys) > 1 else 0)
        ax.errorbar([x_mean], [y_mean], xerr=[x_std], yerr=[y_std],
                    fmt=STYLE[v]["marker"], markersize=15,
                    color=STYLE[v]["c"],
                    markeredgecolor="black", markeredgewidth=1.5,
                    linewidth=1.5, capsize=4, label=LABEL[v])
    ax.set_xlabel("Override-to-DOWN rate (% of WE-UP intents)",
                  fontsize=LABEL_FS)
    ax.set_ylabel(f"Total on-demand cost at N = {HEADLINE_N} (USD)",
                  fontsize=LABEL_FS)
    ax.set_title("Override aggressiveness vs on-demand spend",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    _legend_below(ax, ncol=3)
    fig.tight_layout()
    _save(fig, "08c_override_vs_cost")

# ============================================================================
# 09 — Sort-key sensitivity 2x2
# ============================================================================
def plot_09_sortkey_panel():
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    pairs = [
        ("FCFS-ST",       "fcfs_static_r",   "fcfs_static_c"),
        ("Elastic-FCFS",  "fcfs_moldable_r", "fcfs_moldable_c"),
        ("EDF-ST",        "edf_static_r",    "edf_static_c"),
        ("Elastic-EDF",   "edf_moldable_r",  "edf_moldable_c"),
    ]
    for ax, (title, vr, vc) in zip(axes.flatten(), pairs):
        x = np.arange(len(NS))
        width = 0.35
        cr = [agg(DATA[(vr, n)], "total_cost_eur")[0]*EUR_TO_USD/n for n in NS]
        cc = [agg(DATA[(vc, n)], "total_cost_eur")[0]*EUR_TO_USD/n for n in NS]
        cre = [agg(DATA[(vr, n)], "total_cost_eur")[1]*EUR_TO_USD/n for n in NS]
        cce = [agg(DATA[(vc, n)], "total_cost_eur")[1]*EUR_TO_USD/n for n in NS]
        mr  = [agg(DATA[(vr, n)], "deadline_miss_rate")[0] for n in NS]
        mc  = [agg(DATA[(vc, n)], "deadline_miss_rate")[0] for n in NS]
        mre = [agg(DATA[(vr, n)], "deadline_miss_rate")[1] for n in NS]
        mce = [agg(DATA[(vc, n)], "deadline_miss_rate")[1] for n in NS]
        ax.bar(x - width/2, cr, width, yerr=cre, capsize=3,
               label="_r (runtime)", color="#1D4ED8",
               edgecolor="white", linewidth=1.0,
               error_kw={"lw": 0.8, "ecolor": "black"})
        ax.bar(x + width/2, cc, width, yerr=cce, capsize=3,
               label="_c (cost)",    color="#F59E0B",
               edgecolor="white", linewidth=1.0,
               error_kw={"lw": 0.8, "ecolor": "black"})
        ax.set_xticks(x)
        ax.set_xticklabels([f"N={n}" for n in NS])
        ax.set_ylabel("Cost per workflow (USD)", fontsize=LABEL_FS-2)
        ax.set_title(title, fontsize=TITLE_FS-2)
        ax.grid(True, axis="y", alpha=0.3)
        # secondary axis for miss
        ax2 = ax.twinx()
        ax2.errorbar(x - width/2, mr, yerr=mre, fmt="o-", color="#7E22CE",
                     label="miss _r", linewidth=2.0, markersize=8, capsize=3)
        ax2.errorbar(x + width/2, mc, yerr=mce, fmt="s-", color="#DC2626",
                     label="miss _c", linewidth=2.0, markersize=8, capsize=3)
        ax2.set_ylabel("Miss-rate", fontsize=LABEL_FS-2)
        ax2.set_ylim(0, max(max(mr), max(mc), 0.05) * 1.3)
        # combined legend
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=LEG_FS, loc="upper center",
                  bbox_to_anchor=(0.5, -0.22), ncol=4)
        ax.tick_params(labelsize=TICK_FS-2)
        ax2.tick_params(labelsize=TICK_FS-2)
    fig.suptitle("Sort-key sensitivity: runtime ($_r$) vs cost ($_c$)",
                 fontsize=TITLE_FS, y=1.0)
    fig.tight_layout()
    _save(fig, "09_sortkey_panel")

# ============================================================================
# 10 — Rank factor-pair comparison
# ============================================================================
def plot_10_rank_pair():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    rs = ["rank_moldable_5050", "rank_moldable_2575"]
    cols = ["#0EA5E9", "#DB2777"]
    # left: cost
    for v, c in zip(rs, cols):
        ms = [agg(DATA[(v, n)], "total_cost_eur")[0]*EUR_TO_USD/n for n in NS]
        sds= [agg(DATA[(v, n)], "total_cost_eur")[1]*EUR_TO_USD/n for n in NS]
        axes[0].errorbar(NS, ms, yerr=sds, marker="o", linewidth=2.2,
                         markersize=9, color=c, capsize=4, label=LABEL[v])
    axes[0].set_xticks(NS)
    axes[0].set_xlabel("N", fontsize=LABEL_FS)
    axes[0].set_ylabel("Cost per workflow (USD)", fontsize=LABEL_FS)
    axes[0].set_title("Cost vs N", fontsize=TITLE_FS)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=LEG_FS, loc="upper center",
                   bbox_to_anchor=(0.5, -0.20), ncol=2, framealpha=0.92)
    axes[0].tick_params(labelsize=TICK_FS)
    # right: miss
    for v, c in zip(rs, cols):
        ms = [agg(DATA[(v, n)], "deadline_miss_rate")[0] for n in NS]
        sds= [agg(DATA[(v, n)], "deadline_miss_rate")[1] for n in NS]
        axes[1].errorbar(NS, ms, yerr=sds, marker="o", linewidth=2.2,
                         markersize=9, color=c, capsize=4, label=LABEL[v])
    axes[1].set_xticks(NS)
    axes[1].set_xlabel("N", fontsize=LABEL_FS)
    axes[1].set_ylabel("Deadline miss-rate", fontsize=LABEL_FS)
    axes[1].set_title("Deadline miss-rate vs N", fontsize=TITLE_FS)
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(fontsize=LEG_FS, loc="upper center",
                   bbox_to_anchor=(0.5, -0.20), ncol=2, framealpha=0.92)
    axes[1].tick_params(labelsize=TICK_FS)
    fig.suptitle("Rank factor-pair: [50,50] vs [25,75]", fontsize=TITLE_FS, y=1.02)
    fig.tight_layout()
    _save(fig, "10_rank_pair")

# ============================================================================
# 11 — Intra-run utilisation timeline (placeholder; relies on per-cell timeseries
#      stored in sweep_runs/<cell>/util.csv — if absent, we draw a stub.)
# ============================================================================
def _read_resources_csv(rep_dir):
    """Read a sweep_runs/<cell>/*_resources.csv and return arrays
    (t_hours, op_used, rs_used, od_used, op_cap, rs_cap, od_cap)."""
    import csv as _csv
    csv_path = next(rep_dir.glob("*_resources.csv"), None)
    if csv_path is None:
        return None
    ts, op_free, rs_free, od_free = [], [], [], []
    with open(csv_path) as f:
        r = _csv.DictReader(f)
        for row in r:
            t = float(row["Timestamp"])
            if t > 1e8:    # row 0 is wall-clock; skip
                continue
            ts.append(t)
            op_free.append(float(row["On-prem"]))
            rs_free.append(float(row["Reserved"]))
            od_free.append(float(row["On-demand"]))
    ts = np.array(ts); op_free = np.array(op_free)
    rs_free = np.array(rs_free); od_free = np.array(od_free)
    op_cap, rs_cap, od_cap = op_free.max(), rs_free.max(), od_free.max()
    return (ts / 3600.0,
            op_cap - op_free, rs_cap - rs_free, od_cap - od_free,
            op_cap, rs_cap, od_cap)

def plot_11_util_timeline():
    """Fleet utilisation over time for the three Pareto operating points
    side-by-side. Reads each cell's *_resources.csv. The contrast between
    Elastic and Static is the chapter's "why elasticity saves cost"
    intuition figure — Static keeps OD nodes alive much longer."""
    # Three Pareto operating points (label, dir, regime)
    panels = [
        ("rank_moldable_5050",  "Cost-first"),
        ("edf_moldable_c",      "Balanced"),
        ("edf_static_c",        "Hard-SLO"),
    ]
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharex=True)
    last_active = 0.0
    od_costs = {}
    for ax, (variant, regime) in zip(axes, panels):
        rep_dir = HERE / "sweep_runs" / f"{variant}__N400__seed7"
        data = _read_resources_csv(rep_dir) if rep_dir.exists() else None
        if data is None:
            ax.text(0.5, 0.5, f"No CSV for {variant}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=14, color="#888")
            continue
        t_h, op_used, rs_used, od_used, op_cap, rs_cap, od_cap = data
        ax.plot(t_h, op_used, label=f"On-premise (cap {op_cap:.0f})",
                color="#10B981", linewidth=2.2)
        ax.plot(t_h, rs_used, label=f"Reserved (cap {rs_cap:.0f})",
                color="#3B82F6", linewidth=2.2)
        ax.plot(t_h, od_used, label=f"On-demand (cap {od_cap:.0f})",
                color="#EF4444", linewidth=2.2)
        ax.fill_between(t_h, 0, op_used, color="#10B981", alpha=0.15)
        ax.fill_between(t_h, 0, rs_used, color="#3B82F6", alpha=0.15)
        ax.fill_between(t_h, 0, od_used, color="#EF4444", alpha=0.15)
        ax.set_ylabel("Nodes in use", fontsize=LABEL_FS - 1)
        # Pull OD cost (from this seed) to annotate the panel
        seed_cell = next((c for c in DATA[(variant, 400)] if c["_seed"] == 7), None)
        od_cost_usd = (seed_cell["total_cost_on_demand"] * EUR_TO_USD
                       if seed_cell else float("nan"))
        od_costs[variant] = od_cost_usd
        ax.set_title(
            f"{regime}: {LABEL[variant]}    "
            f"(on-demand cost: {od_cost_usd:,.0f} USD)",
            fontsize=TITLE_FS - 2, loc="left")
        ax.tick_params(labelsize=TICK_FS - 1)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0, top=max(op_cap, rs_cap, od_cap) * 1.05)
        ax.legend(fontsize=LEG_FS, loc="upper right", ncol=3,
                  framealpha=0.92)
        # Trim x to where any tier last had ≥1 node in use
        in_use = (op_used + rs_used + od_used) > 0
        if in_use.any():
            last_idx = np.where(in_use)[0][-1]
            last_active = max(last_active, float(t_h[last_idx]))
    axes[-1].set_xlabel("Elapsed time (hours)", fontsize=LABEL_FS)
    for ax in axes:
        ax.set_xlim(0, last_active * 1.05)
    fig.suptitle("Fleet utilisation over time — three Pareto policies\n"
                 "(N=400, one representative run)",
                 fontsize=TITLE_FS, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    _save(fig, "11_util_timeline")

# ============================================================================
# 12 — Per-workflow scatter @ N=400
#       (placeholder — requires per-wf cost/turnaround which is not in the
#        aggregated JSON. Falls back to a per-cell average scatter.)
# ============================================================================
def plot_12_per_wf_scatter():
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for v in VARIANTS:
        for c in DATA[(v, HEADLINE_N)]:
            ax.scatter(c["total_cost_eur"] * EUR_TO_USD / c["_N"],
                       c["avg_flowtime_s"],
                       s=80, alpha=0.7,
                       color=STYLE[v]["c"],
                       marker=STYLE[v]["marker"],
                       edgecolors="black", linewidths=0.5)
    handles = [plt.Line2D([0],[0], lw=0, label=LABEL[v],
                          marker=STYLE[v]["marker"], color=STYLE[v]["c"],
                          markersize=10, mfc=STYLE[v]["c"]) for v in VARIANTS]
    ax.legend(handles=handles, fontsize=LEG_FS, ncol=4,
              loc="upper center", bbox_to_anchor=(0.5, -0.18), framealpha=0.92)
    ax.set_xlabel("Cost per workflow (USD)", fontsize=LABEL_FS)
    ax.set_ylabel("Average turnaround time per workflow\n(seconds)",
                  fontsize=LABEL_FS)
    ax.set_title(f"Cost vs turnaround time — individual runs @ N={HEADLINE_N}",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    _sci_yaxis(ax)
    fig.tight_layout()
    _save(fig, "12_per_wf_scatter")

# --------------------------------------------------------------------- save helper
def _save(fig, name):
    pdf = OUT_DIR / f"{name}.pdf"
    png = OUT_DIR / f"{name}.png"
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {pdf.name} + {png.name}")

# --------------------------------------------------------------------- main
def main():
    print(f"Loading data from {JSON_PATH.name}")
    print(f"Output dir: {OUT_DIR}")
    print()
    plotters = [
        plot_01_cost_vs_n,
        plot_01b_cost_stack,
        plot_02_cost_vs_n,
        plot_02_miss_vs_n,
        plot_02_wait_vs_n,
        plot_02_turnaround_vs_n,
        plot_02_budget_vs_n,
        plot_02_util_vs_n,
        plot_02b_miss_bar,
        plot_02c_budget_miss_bar,
        plot_02d_turnaround_stack,
        plot_02e_saturation,
        plot_02f_miss_decomposition,
        plot_04_paired_delta,
        plot_06_pareto,
        plot_07_scaling_activity,
        plot_07b_grant_tier_stack,
        plot_07c_grant_tier_vs_n,
        plot_08_intent_satisfaction,
        plot_09_sortkey_panel,
        plot_10_rank_pair,
        plot_11_util_timeline,
        plot_12_per_wf_scatter,
    ]
    for p in plotters:
        print(f"[{p.__name__}]")
        p()
    print(f"\nDone — {len(plotters)} figures written to {OUT_DIR}")

if __name__ == "__main__":
    main()
