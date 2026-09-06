"""Headline thesis plots (1-4) for the HPO results chapter.

Data: combined (n=6) means and stdevs extracted from
use_cases/hpo/results/r7_n7_actual_vs_modeled/metrics_n{3,5,7}.py.
All runs treated uniformly — no modelled-vs-actual distinction.
Terminology: malleable (not moldable).
"""
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# --------------------------------------------------------------------- styling
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.weight": "bold",
    "axes.labelweight": "bold",
    "axes.titleweight": "bold",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "pdf.fonttype": 42,
})

TITLE_FS = 18
LABEL_FS = 16
TICK_FS = 14
LEG_FS = 14
LEG_MARKER_SCALE = 1.6
LEG_HANDLE_LEN = 3.5
ANNOT_FS = 12

# --- Total submitter cost (computed by compute_total_cost.py) ----------------
import json
_total_path = Path(__file__).resolve().parent / "total_cost_per_run.json"
_total = json.loads(_total_path.read_text())
def _t(N, mode, ord_):
    cell = _total[f"N{N}_{mode}_{ord_}"]
    return (cell["cost_total"]["mean"], cell["cost_total"]["stdev"])
def _b(N, mode, ord_, key):
    cell = _total[f"N{N}_{mode}_{ord_}"]
    return cell[key]["mean"]
def _pw(N, mode, ord_):
    """Per-workflow cost (cost_total / N): (mean, stdev)."""
    m, s = _t(N, mode, ord_)
    return (m / N, s / N)

# Static = purple family; Elastic = teal family. EDF solid, FCFS dashed.
COL = {
    "STAT EDF":  "#B91C1C",  # crimson  (default — overridden per-plot below)
    "STAT FCFS": "#F59E0B",  # amber
    "MAL EDF":   "#1D4ED8",  # royal blue
    "MAL FCFS":  "#059669",  # emerald
}
# Per-plot palettes — each chart gets its own 4-corner colour scheme so the
# figures read as visually distinct artefacts in the thesis.
PAL = {
    "cost_line":   {"STAT EDF":"#B91C1C","STAT FCFS":"#F59E0B",
                    "MAL EDF":"#1D4ED8","MAL FCFS":"#059669"},
    "cost_tier":   {"STAT EDF":"#7F1D1D","STAT FCFS":"#B45309",
                    "MAL EDF":"#1E3A8A","MAL FCFS":"#064E3B"},
    "miss_line":   {"STAT EDF":"#7E22CE","STAT FCFS":"#14B8A6",
                    "MAL EDF":"#C026D3","MAL FCFS":"#A16207"},
    "miss_bar":    {"STAT EDF":"#0EA5E9","STAT FCFS":"#DC2626",
                    "MAL EDF":"#16A34A","MAL FCFS":"#DB2777"},
    "budget_bar":  {"STAT EDF":"#9333EA","STAT FCFS":"#84CC16",
                    "MAL EDF":"#EA580C","MAL FCFS":"#0891B2"},
    "makespan":    {"STAT EDF":"#BE123C","STAT FCFS":"#65A30D",
                    "MAL EDF":"#312E81","MAL FCFS":"#B45309"},
    "cpr":         {"STAT EDF":"#DB2777","STAT FCFS":"#4D7C0F",
                    "MAL EDF":"#6D28D9","MAL FCFS":"#0F766E"},
    "util_time":   {"STAT EDF":"#DC2626","STAT FCFS":"#F97316",
                    "MAL EDF":"#2563EB","MAL FCFS":"#10B981"},
    "sumflow":     {"STAT EDF":"#0E7490","STAT FCFS":"#D97706",
                    "MAL EDF":"#BE185D","MAL FCFS":"#4D7C0F"},
    "per_wf":      {"STAT EDF":"#9F1239","STAT FCFS":"#A16207",
                    "MAL EDF":"#1E40AF","MAL FCFS":"#15803D"},
}
LS = {"STAT EDF": "-", "STAT FCFS": "--", "MAL EDF": "-", "MAL FCFS": "--"}
MARKER = {"STAT EDF": "o", "STAT FCFS": "s", "MAL EDF": "o", "MAL FCFS": "s"}

LABEL = {
    "STAT EDF":  "EDF-ST",
    "STAT FCFS": "FCFS-ST",
    "MAL EDF":   "Elastic-EDF",
    "MAL FCFS":  "Elastic-FCFS",
}

CORNERS = ["STAT EDF", "STAT FCFS", "MAL EDF", "MAL FCFS"]
NS = np.array([3, 5, 7])

# --------------------------------------------------------------------- numbers
# (mean, stdev) per corner per N.
MISSES = {
    "STAT EDF":  [(0.5, 0.5), (2.2, 0.8), (4.2, 0.8)],
    "STAT FCFS": [(0.5, 0.5), (2.5, 0.5), (4.5, 0.5)],
    "MAL EDF":   [(0.8, 0.4), (1.8, 0.8), (3.3, 0.8)],
    "MAL FCFS":  [(0.8, 0.4), (1.8, 0.8), (3.3, 1.0)],
}
# sum-flow in MINUTES
SUMFLOW = {
    "STAT EDF":  [(270.5, 66.4), (508.8, 64.7), (943.9, 135.7)],
    "STAT FCFS": [(270.5, 66.4), (616.2, 71.5), (991.3, 146.3)],
    "MAL EDF":   [(282.1, 65.0), (450.2, 54.0), (719.0, 97.9)],
    "MAL FCFS":  [(282.1, 65.0), (450.2, 54.0), (722.0, 103.9)],
}
# Total submitter cost (slurm TCO + reserved cloud + on-demand cloud), USD.
COST = {
    "STAT EDF":  [_pw(3, "static",   "edf"),  _pw(5, "static",   "edf"),  _pw(7, "static",   "edf")],
    "STAT FCFS": [_pw(3, "static",   "fcfs"), _pw(5, "static",   "fcfs"), _pw(7, "static",   "fcfs")],
    "MAL EDF":   [_pw(3, "moldable", "edf"),  _pw(5, "moldable", "edf"),  _pw(7, "moldable", "edf")],
    "MAL FCFS":  [_pw(3, "moldable", "fcfs"), _pw(5, "moldable", "fcfs"), _pw(7, "moldable", "fcfs")],
}
# Paired difference (Malleable − Static), per ordering, per N: (mean, stdev)
DIFF_FLOW = {  # minutes
    "EDF":  [(+11.6, 27.6), (-58.6, 34.5), (-224.9, 86.7)],
    "FCFS": [(+11.6, 27.6), (-166.0, 21.4), (-269.4, 86.8)],
}

OUT = Path(__file__).resolve().parent


def split(series):
    """Return (means, stdevs) arrays from a [(mean,std), ...] list."""
    arr = np.array(series)
    return arr[:, 0], arr[:, 1]


def style_axes(ax, ylabel, title=None):
    ax.set_xlabel("Number of workflows (N)", fontsize=LABEL_FS)
    ax.set_ylabel(ylabel, fontsize=LABEL_FS)
    if title:
        ax.set_title(title, fontsize=TITLE_FS)
    ax.set_xticks(NS)
    ax.tick_params(axis="both", labelsize=TICK_FS)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)


def line_plot(data, ylabel, fname, title, ylim=None, annotate=None,
              palette=None):
    pal = palette if palette is not None else COL
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for corner in CORNERS:
        mu, sd = split(data[corner])
        ax.errorbar(
            NS, mu, yerr=sd,
            label=LABEL[corner], color=pal[corner],
            linestyle=LS[corner], marker=MARKER[corner],
            linewidth=2.4, markersize=9, capsize=5, capthick=1.8,
            markeredgecolor="black", markeredgewidth=0.8,
        )
    style_axes(ax, ylabel, title)
    if ylim:
        ax.set_ylim(*ylim)
    if annotate:
        annotate(ax)
    leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28),
                    frameon=True, fontsize=LEG_FS, ncol=4,
                    markerscale=LEG_MARKER_SCALE,
                    handlelength=LEG_HANDLE_LEN, handletextpad=0.8,
                    columnspacing=2.0, borderpad=0.8)
    for t in leg.get_texts():
        t.set_fontweight("bold")
    fig.tight_layout()
    fig.savefig(OUT / f"{fname}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{fname}.pdf", bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------- Plot 1
def annot_cost(ax):
    ax.axhline(0, color="0.7", linewidth=0.8, zorder=0)
    # Highlight cost crossover region between static and malleable
    ax.axvspan(3, 5, color="#F0FDFA", alpha=0.7, zorder=0)
    ax.text(4, 1.5, "cost crossover",
            ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold",
            color="#0F766E")


line_plot(
    COST,
    ylabel="$\\bar{\\gamma}$ (cost per workflow, USD)",
    fname="01_cost_vs_n",
    title="Cost per workflow vs workload size",
    ylim=(0, 4),
    annotate=annot_cost,
    palette=PAL["cost_line"],
)


# --------------------------------------------------------------- Plot 1b stack
# Stacked bars: slurm (TCO) + reserved cloud + on-demand per corner per N.
fig, axs = plt.subplots(1, 3, figsize=(16, 5.5), sharey=True)
TIERS = [
    ("cost_slurm",            "On-premise cluster",   "#475569"),
    ("cost_reserved_total",   "Reserved cloud",       "#4F46E5"),
    ("cost_od_total",         "On-demand cloud",      "#F97316"),
]
short = {("static",   "edf"):  "EDF-ST",
         ("static",   "fcfs"): "FCFS-ST",
         ("moldable", "edf"):  "Elastic\nEDF",
         ("moldable", "fcfs"): "Elastic\nFCFS"}
corner_order = [("static", "edf"), ("static", "fcfs"),
                ("moldable", "edf"), ("moldable", "fcfs")]
x_pos = np.arange(len(corner_order))

for ax, N in zip(axs, [3, 5, 7]):
    bottoms = np.zeros(len(corner_order))
    for tier_key, tier_label, tier_col in TIERS:
        vals = np.array([_b(N, m, o, tier_key) / N for (m, o) in corner_order])
        ax.bar(x_pos, vals, bottom=bottoms, color=tier_col,
               edgecolor="black", linewidth=0.7,
               label=tier_label if N == 3 else None)
        bottoms += vals
    # error bar on the stack total + scatter of the 6 per-run totals
    for i, (m, o) in enumerate(corner_order):
        cell = _total[f"N{N}_{m}_{o}"]
        total_mean = cell["cost_total"]["mean"] / N
        total_std  = cell["cost_total"]["stdev"] / N
        run_totals = [r["cost_total"] / N for r in cell["per_run"]]
        # ±σ bar centred on the mean
        ax.errorbar(i, total_mean, yerr=total_std, fmt="none",
                    ecolor="black", elinewidth=1.4, capsize=6,
                    capthick=1.4, zorder=5)
        # per-run dots (jittered horizontally so they don't overlap)
        jitter = np.linspace(-0.12, 0.12, len(run_totals))
        ax.scatter(np.full(len(run_totals), i) + jitter, run_totals,
                   s=22, color="white", edgecolor="black",
                   linewidth=0.9, zorder=6)
        ax.text(i, total_mean + total_std + 0.08, f"${total_mean:.2f}",
                ha="center", va="bottom",
                fontsize=11, fontweight="bold")
    ax.set_xticks(x_pos)
    ax.set_xticklabels([short[c] for c in corner_order],
                       fontweight="bold", fontsize=TICK_FS)
    ax.set_title(f"N = {N}", fontsize=TITLE_FS)
    ax.tick_params(axis="y", labelsize=TICK_FS)
    for lbl in ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)

axs[0].set_ylabel("$\\bar{\\gamma}$ (cost per workflow, USD)", fontsize=LABEL_FS)
fig.legend(loc="lower center", ncol=3, fontsize=LEG_FS, frameon=True,
           bbox_to_anchor=(0.5, -0.20), prop={"weight": "bold", "size": LEG_FS},
           markerscale=LEG_MARKER_SCALE, handlelength=LEG_HANDLE_LEN,
           handletextpad=0.8, columnspacing=2.0, borderpad=0.8)
fig.suptitle("Per-workflow cost breakdown by tier", fontsize=TITLE_FS, y=1.02,
             fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "01b_cost_stack.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "01b_cost_stack.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------------- Plot 2b / 2c — misses bar grids
def _miss_bar_grid(field, ylabel, title, fname, ymax, palette):
    fig, axs = plt.subplots(1, 3, figsize=(16, 5.5), sharey=True)
    corner_colors = [palette["STAT EDF"], palette["STAT FCFS"],
                     palette["MAL EDF"], palette["MAL FCFS"]]
    for ax, N in zip(axs, [3, 5, 7]):
        for i, (m, o) in enumerate(corner_order):
            cell = _total[f"N{N}_{m}_{o}"]
            mean = cell[field]["mean"]
            std  = cell[field]["stdev"]
            runs = [r[field] for r in cell["per_run"]]
            ax.bar(i, mean, color=corner_colors[i],
                   edgecolor="black", linewidth=0.7,
                   label=LABEL[["STAT EDF", "STAT FCFS",
                                "MAL EDF", "MAL FCFS"][i]] if N == 3 else None)
            ax.errorbar(i, mean, yerr=std, fmt="none",
                        ecolor="black", elinewidth=1.4, capsize=6,
                        capthick=1.4, zorder=5)
            jitter = np.linspace(-0.12, 0.12, len(runs))
            ax.scatter(np.full(len(runs), i) + jitter, runs,
                       s=22, color="white", edgecolor="black",
                       linewidth=0.9, zorder=6)
            ax.text(i, mean + std + 0.12,
                    f"{mean:.1f}", ha="center", va="bottom",
                    fontsize=11, fontweight="bold")
        # reference line: total workflows (= worst-case misses)
        ax.axhline(N, color="0.5", linestyle=":", linewidth=1.2,
                   zorder=1)
        ax.text(3.6, N, f"max = {N}", ha="right", va="bottom",
                color="0.4", fontsize=11, fontweight="bold")
        ax.set_xticks(np.arange(len(corner_order)))
        ax.set_xticklabels([short[c] for c in corner_order],
                           fontweight="bold", fontsize=TICK_FS)
        ax.set_title(f"N = {N}", fontsize=TITLE_FS)
        ax.tick_params(axis="y", labelsize=TICK_FS)
        for lbl in ax.get_yticklabels():
            lbl.set_fontweight("bold")
        ax.set_ylim(0, ymax)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="y", alpha=0.25)
    axs[0].set_ylabel(ylabel, fontsize=LABEL_FS)
    fig.legend(loc="lower center", ncol=4, fontsize=LEG_FS, frameon=True,
               bbox_to_anchor=(0.5, -0.20), prop={"weight": "bold", "size": LEG_FS},
               markerscale=LEG_MARKER_SCALE, handlelength=LEG_HANDLE_LEN,
               handletextpad=0.8, columnspacing=2.0, borderpad=0.8)
    fig.suptitle(title, fontsize=TITLE_FS, y=1.02, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / f"{fname}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{fname}.pdf", bbox_inches="tight")
    plt.close(fig)


_miss_bar_grid("misses", "Deadline misses (out of N)",
               "Deadline misses by scheduling policy",
               "02b_misses_bar", ymax=7.5, palette=PAL["miss_bar"])
_miss_bar_grid("budget_misses", "Budget overruns (out of N)",
               "Per-workflow budget overruns by scheduling policy",
               "02c_budget_misses_bar", ymax=7.5, palette=PAL["budget_bar"])


# --------------------------------------- Plot 2d — turnaround stack + makespan
fig, axs = plt.subplots(1, 3, figsize=(16, 5.5), sharey=False)
WAIT_COL = "#C084FC"   # purple — queueing
EXEC_COL = "#0F766E"   # teal   — running
MAKESPAN_COL = "#F97316"  # orange dashed marker

for ax, N in zip(axs, [3, 5, 7]):
    x_pos = np.arange(len(corner_order))
    waits, execs, mksp = [], [], []
    for (m, o) in corner_order:
        cell = _total[f"N{N}_{m}_{o}"]
        waits.append(cell["sum_wait_s"]["mean"] / 60.0)
        execs.append(cell["sum_exec_s"]["mean"] / 60.0)
        mksp.append(cell["makespan_s"]["mean"] / 60.0)
    waits = np.array(waits)
    execs = np.array(execs)
    mksp = np.array(mksp)
    ax.bar(x_pos, waits, color=WAIT_COL, edgecolor="black",
           linewidth=0.7, label="Σ wait time" if N == 3 else None)
    ax.bar(x_pos, execs, bottom=waits, color=EXEC_COL,
           edgecolor="black", linewidth=0.7,
           label="Σ execute time" if N == 3 else None)
    # Per-corner per-seed scatter on stack total (= sum turnaround)
    for i, (m, o) in enumerate(corner_order):
        cell = _total[f"N{N}_{m}_{o}"]
        run_totals = [r["sum_turnaround_s"] / 60.0 for r in cell["per_run"]]
        jitter = np.linspace(-0.12, 0.12, len(run_totals))
        ax.scatter(np.full(len(run_totals), i) + jitter, run_totals,
                   s=22, color="white", edgecolor="black",
                   linewidth=0.9, zorder=6)
        ax.text(i, waits[i] + execs[i] + 6,
                f"{waits[i] + execs[i]:.0f}",
                ha="center", va="bottom",
                fontsize=11, fontweight="bold")
    ax.set_xticks(x_pos)
    ax.set_xticklabels([short[c] for c in corner_order],
                       fontweight="bold", fontsize=TICK_FS)
    ax.set_title(f"N = {N}", fontsize=TITLE_FS)
    ax.tick_params(axis="y", labelsize=TICK_FS)
    for lbl in ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)

axs[0].set_ylabel("Time (minutes)", fontsize=LABEL_FS)
fig.legend(loc="lower center", ncol=3, fontsize=LEG_FS, frameon=True,
           bbox_to_anchor=(0.5, -0.20), prop={"weight": "bold", "size": LEG_FS},
           markerscale=LEG_MARKER_SCALE, handlelength=LEG_HANDLE_LEN,
           handletextpad=0.8, columnspacing=2.0, borderpad=0.8)
fig.suptitle("Turnaround time breakdown (Σ wait + Σ execute)",
             fontsize=TITLE_FS, y=1.02, fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "02d_turnaround_stack.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "02d_turnaround_stack.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------- Plot 2e — makespan bar chart
fig, axs = plt.subplots(1, 3, figsize=(16, 5.0), sharey=True)
_p2e = PAL["makespan"]
for ax, N in zip(axs, [3, 5, 7]):
    x_pos = np.arange(len(corner_order))
    means, stds, per_seed = [], [], []
    for (m, o) in corner_order:
        cell = _total[f"N{N}_{m}_{o}"]
        means.append(cell["makespan_s"]["mean"] / 60.0)
        stds.append(cell["makespan_s"]["stdev"] / 60.0)
        per_seed.append([r["makespan_s"] / 60.0 for r in cell["per_run"]])
    bar_colors = []
    for (m, o) in corner_order:
        if m == "static" and o == "edf":   bar_colors.append(_p2e["STAT EDF"])
        elif m == "static" and o == "fcfs": bar_colors.append(_p2e["STAT FCFS"])
        elif m == "moldable" and o == "edf": bar_colors.append(_p2e["MAL EDF"])
        else:                                bar_colors.append(_p2e["MAL FCFS"])
    ax.bar(x_pos, means, yerr=stds, color=bar_colors, edgecolor="black",
           linewidth=0.7, capsize=4,
           error_kw={"elinewidth": 1.2, "ecolor": "black"})
    for i, runs_i in enumerate(per_seed):
        jitter = np.linspace(-0.12, 0.12, len(runs_i))
        ax.scatter(np.full(len(runs_i), i) + jitter, runs_i,
                   s=22, color="white", edgecolor="black",
                   linewidth=0.9, zorder=6)
    for i, mv in enumerate(means):
        ax.text(i, mv + stds[i] + 3, f"{mv:.0f}",
                ha="center", va="bottom",
                fontsize=11, fontweight="bold")
    ax.set_xticks(x_pos)
    ax.set_xticklabels([short[c] for c in corner_order],
                       fontweight="bold", fontsize=TICK_FS)
    ax.set_title(f"N = {N}", fontsize=TITLE_FS)
    ax.tick_params(axis="y", labelsize=TICK_FS)
    for lbl in ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)

axs[0].set_ylabel("Makespan (minutes)", fontsize=LABEL_FS)
fig.suptitle("Makespan (wall-clock) by scheduling policy",
             fontsize=TITLE_FS, y=1.02, fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "02e_makespan_bar.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "02e_makespan_bar.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------- Plot 2f — Cost-Performance Ratio
# CPR = (1 - miss_rate) / avg_cost_per_wf, higher is better.
fig, axs = plt.subplots(1, 3, figsize=(16, 5.0), sharey=True)
_p2f = PAL["cpr"]
for ax, N in zip(axs, [3, 5, 7]):
    x_pos = np.arange(len(corner_order))
    means, stds, per_seed = [], [], []
    for (m, o) in corner_order:
        cell = _total[f"N{N}_{m}_{o}"]
        cprs = []
        for r in cell["per_run"]:
            avg_cost = r["cost_total"] / N
            perf = 1.0 - (r["misses"] / N)
            cprs.append(perf / avg_cost if avg_cost > 0 else 0.0)
        cprs = np.array(cprs)
        means.append(cprs.mean())
        stds.append(cprs.std(ddof=1) if len(cprs) > 1 else 0.0)
        per_seed.append(cprs.tolist())
    bar_colors = []
    for (m, o) in corner_order:
        if m == "static" and o == "edf":     bar_colors.append(_p2f["STAT EDF"])
        elif m == "static" and o == "fcfs":  bar_colors.append(_p2f["STAT FCFS"])
        elif m == "moldable" and o == "edf": bar_colors.append(_p2f["MAL EDF"])
        else:                                 bar_colors.append(_p2f["MAL FCFS"])
    ax.bar(x_pos, means, yerr=stds, color=bar_colors, edgecolor="black",
           linewidth=0.7, capsize=4,
           error_kw={"elinewidth": 1.2, "ecolor": "black"})
    for i, runs_i in enumerate(per_seed):
        jitter = np.linspace(-0.12, 0.12, len(runs_i))
        ax.scatter(np.full(len(runs_i), i) + jitter, runs_i,
                   s=22, color="white", edgecolor="black",
                   linewidth=0.9, zorder=6)
    for i, mv in enumerate(means):
        ax.text(i, mv + stds[i] + 0.02 * max(means + [1e-6]),
                f"{mv:.2f}", ha="center", va="bottom",
                fontsize=11, fontweight="bold")
    ax.set_xticks(x_pos)
    ax.set_xticklabels([short[c] for c in corner_order],
                       fontweight="bold", fontsize=TICK_FS)
    ax.set_title(f"N = {N}", fontsize=TITLE_FS)
    ax.tick_params(axis="y", labelsize=TICK_FS)
    for lbl in ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)

axs[0].set_ylabel("CPR (1/USD)", fontsize=LABEL_FS)
fig.suptitle("Cost-Performance Ratio",
             fontsize=TITLE_FS, y=1.02, fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "02f_cpr_bar.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "02f_cpr_bar.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------- Plot 2g — Reserved-tier utilization
# over time. Step function, mean across the 6 seeds per cell. One line per
# corner, three panels (N = 3/5/7). Shows when the fleet fills, when it drains.
fig, axs = plt.subplots(1, 3, figsize=(18, 5.5), sharey=True)
corner_keymap = {("static","edf"):"STAT EDF", ("static","fcfs"):"STAT FCFS",
                 ("moldable","edf"):"MAL EDF", ("moldable","fcfs"):"MAL FCFS"}
RES_CAP = _total["N3_static_edf"]["per_run"][0]["reserved_cap_total"]  # = 8
ymax_global = RES_CAP
for N in [3, 5, 7]:
    for (m, o) in corner_order:
        runs = _total[f"N{N}_{m}_{o}"]["per_run"]
        for r in runs:
            ymax_global = max(ymax_global, max(r["util_timeline_lanes"]))
for ax, N in zip(axs, [3, 5, 7]):
    last_ts = None
    for (m, o) in corner_order:
        cell = _total[f"N{N}_{m}_{o}"]
        runs = cell["per_run"]
        lens = [len(r["util_timeline_lanes"]) for r in runs]
        L = min(lens)
        if L == 0:
            continue
        ts  = np.array(runs[0]["util_timeline_t"][:L]) / 60.0  # minutes
        mat = np.array([r["util_timeline_lanes"][:L] for r in runs])
        mean_lanes = mat.mean(axis=0)
        ck = corner_keymap[(m, o)]
        ax.plot(ts, mean_lanes, color=PAL["util_time"][ck], linewidth=2.4,
                linestyle=LS[ck], label=LABEL[ck] if N == 3 else None)
        last_ts = ts
    ax.axhline(RES_CAP, linestyle="--", color="#92400E", linewidth=1.4,
               zorder=1,
               label=f"Reserved cap ({RES_CAP} nodes)" if N == 3 else None)
    ax.set_xlabel("Time (minutes)", fontsize=LABEL_FS)
    ax.set_title(f"N = {N}", fontsize=TITLE_FS)
    ax.tick_params(axis="both", labelsize=TICK_FS)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, alpha=0.25)
    ax.set_ylim(0, ymax_global + 1)

axs[0].set_ylabel("Active nodes", fontsize=LABEL_FS)
fig.legend(loc="lower center", ncol=5, fontsize=LEG_FS, frameon=True,
           bbox_to_anchor=(0.5, -0.22), prop={"weight": "bold", "size": LEG_FS},
           markerscale=LEG_MARKER_SCALE, handlelength=LEG_HANDLE_LEN,
           handletextpad=0.8, columnspacing=2.0, borderpad=0.8)
fig.suptitle("Compute nodes in use over time",
             fontsize=TITLE_FS, y=1.02, fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "02g_utilization_time.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "02g_utilization_time.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------- Plot 2h — tier composition zoom-in
# One representative case: Elastic-EDF at N=7. Stacked area of nodes-in-use by
# tier (on-prem slurm / reserved cloud / on-demand). Mean across the 6 seeds.
fig, ax = plt.subplots(figsize=(10, 5.5))
cell = _total["N7_moldable_edf"]
runs = cell["per_run"]
L = min(len(r["util_timeline_t"]) for r in runs)
ts = np.array(runs[0]["util_timeline_t"][:L]) / 60.0
slurm_mat = np.array([r["util_timeline_tiers"]["slurm"][:L] for r in runs])
res_mat   = np.array([r["util_timeline_tiers"]["reserved_cloud"][:L] for r in runs])
od_mat    = np.array([r["util_timeline_tiers"]["on_demand"][:L] for r in runs])
slurm_m = slurm_mat.mean(axis=0)
res_m   = res_mat.mean(axis=0)
od_m    = od_mat.mean(axis=0)
ax.stackplot(ts, slurm_m, res_m, od_m,
             labels=["On-premise (4 nodes)",
                     "Reserved cloud (g4 + g5, 4 nodes)",
                     "On-demand"],
             colors=["#475569", "#4F46E5", "#F97316"],
             alpha=0.92, edgecolor="black", linewidth=0.4)
ax.axhline(RES_CAP, linestyle="--", color="#92400E", linewidth=1.4)
ax.text(ts[-1] * 0.99, RES_CAP + 0.18,
        "reserved cap",
        ha="right", va="bottom", fontsize=11, color="#92400E",
        fontweight="bold")
ax.set_xlabel("Time (minutes)", fontsize=LABEL_FS)
ax.set_ylabel("Active nodes", fontsize=LABEL_FS)
ax.tick_params(axis="both", labelsize=TICK_FS)
for lbl in ax.get_xticklabels() + ax.get_yticklabels():
    lbl.set_fontweight("bold")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.grid(True, alpha=0.25)
leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25),
                ncol=3, fontsize=LEG_FS, frameon=True,
                prop={"weight": "bold", "size": LEG_FS},
                markerscale=LEG_MARKER_SCALE, handlelength=LEG_HANDLE_LEN,
                handletextpad=0.8, columnspacing=2.0, borderpad=0.8)
ax.set_title("Tier composition — Elastic-EDF, N = 7",
             fontsize=TITLE_FS, fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "02h_tier_zoom_elastic_edf_n7.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "02h_tier_zoom_elastic_edf_n7.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------- Plot 5 — Per-workflow breakdown N=7
# One panel per corner. For each of the 7 workflows: stacked bar (Σ wait + Σ
# exec, mean across 6 seeds) with deadline-miss marker (red triangle above)
# and budget-overrun marker (orange diamond).
WF_ORDER_N7 = ["data8", "data9", "data5", "data7", "data3", "data12", "data1"]
fig, axs = plt.subplots(1, 4, figsize=(20, 5.5), sharey=True)
corner_titles = {("static","edf"):"Static-EDF", ("static","fcfs"):"Static-FCFS",
                 ("moldable","edf"):"Elastic-EDF", ("moldable","fcfs"):"Elastic-FCFS"}
for ax, (m, o) in zip(axs, corner_order):
    cell = _total[f"N7_{m}_{o}"]
    waits, execs, miss_rate, bmiss_rate = [], [], [], []
    for wf in WF_ORDER_N7:
        ws  = [r["per_wf"][wf]["wait_s"] / 60.0 for r in cell["per_run"]]
        es  = [r["per_wf"][wf]["exec_s"] / 60.0 for r in cell["per_run"]]
        ms  = [r["per_wf"][wf]["miss"]        for r in cell["per_run"]]
        bms = [r["per_wf"][wf]["budget_miss"] for r in cell["per_run"]]
        waits.append(np.mean(ws))
        execs.append(np.mean(es))
        miss_rate.append(np.mean(ms))
        bmiss_rate.append(np.mean(bms))
    waits = np.array(waits); execs = np.array(execs)
    x_pos = np.arange(len(WF_ORDER_N7))
    _p5 = PAL["per_wf"]
    if m == "static" and o == "edf":     col = _p5["STAT EDF"]
    elif m == "static" and o == "fcfs":  col = _p5["STAT FCFS"]
    elif m == "moldable" and o == "edf": col = _p5["MAL EDF"]
    else:                                 col = _p5["MAL FCFS"]
    ax.bar(x_pos, waits, color="#C084FC", edgecolor="black",
           linewidth=0.6,
           label="Wait time" if (m, o) == corner_order[0] else None)
    ax.bar(x_pos, execs, bottom=waits, color="#0F766E", edgecolor="black",
           linewidth=0.6,
           label="Execute time" if (m, o) == corner_order[0] else None)
    # Miss markers: red triangle = deadline miss (% = miss frequency across
    # the 6 seeds); orange diamond = budget overrun (mean cost > yaml budget).
    for i, (mr, br) in enumerate(zip(miss_rate, bmiss_rate)):
        top = waits[i] + execs[i]
        if mr > 0:
            ax.scatter(i, top + 14, marker="v", s=80,
                       color="#DC2626", edgecolor="black", linewidth=0.8,
                       zorder=7,
                       label="Deadline-miss rate (% of runs that missed deadline)"
                       if (i == 0 and (m, o) == corner_order[0]) else None)
            ax.text(i, top + 22, f"{mr:.0%}", ha="center", va="bottom",
                    fontsize=9, color="#DC2626", fontweight="bold")
        if br > 0:
            ax.scatter(i, top + 6, marker="D", s=60,
                       color="#F97316", edgecolor="black", linewidth=0.8,
                       zorder=7,
                       label="Budget overrun"
                       if (i == 0 and (m, o) == corner_order[0]) else None)
    wf_short = [f"WF{i+1}" for i in range(len(WF_ORDER_N7))]
    ax.set_xticks(x_pos)
    ax.set_xticklabels(wf_short, rotation=0,
                       fontweight="bold", fontsize=TICK_FS - 1)
    ax.set_title(corner_titles[(m, o)], fontsize=TITLE_FS, color=col,
                 fontweight="bold")
    ax.tick_params(axis="y", labelsize=TICK_FS)
    for lbl in ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)

axs[0].set_ylabel("Time per workflow (minutes)", fontsize=LABEL_FS)
fig.legend(loc="lower center", ncol=4, fontsize=LEG_FS, frameon=True,
           bbox_to_anchor=(0.5, -0.22), prop={"weight": "bold", "size": LEG_FS},
           markerscale=LEG_MARKER_SCALE, handlelength=LEG_HANDLE_LEN,
           handletextpad=0.8, columnspacing=2.0, borderpad=0.8)
fig.suptitle("Per-workflow breakdown at N = 7",
             fontsize=TITLE_FS, y=1.02, fontweight="bold")
fig.tight_layout()
fig.savefig(OUT / "05_per_wf_n7.png", dpi=300, bbox_inches="tight")
fig.savefig(OUT / "05_per_wf_n7.pdf", bbox_inches="tight")
plt.close(fig)


# --------------------------------------------------------------------- Plot 2
def annot_miss(ax):
    ax.set_ylim(0, 6)
    ax.text(7, 4.35, "Static",
            color=PAL["miss_line"]["STAT EDF"], fontsize=ANNOT_FS, fontweight="bold",
            ha="right", va="bottom")
    ax.text(7, 3.0, "Elastic",
            color=PAL["miss_line"]["MAL EDF"], fontsize=ANNOT_FS, fontweight="bold",
            ha="right", va="top")


line_plot(
    MISSES,
    ylabel="Deadline misses (out of N)",
    fname="02_misses_vs_n",
    title="Deadline misses with growing workload",
    annotate=annot_miss,
    palette=PAL["miss_line"],
)


# --------------------------------------------------------------------- Plot 3
def annot_flow(ax):
    # Annotate growth multiplier between N=3 and N=7
    stat_fcfs_growth = SUMFLOW["STAT FCFS"][2][0] / SUMFLOW["STAT FCFS"][0][0]
    mal_edf_growth = SUMFLOW["MAL EDF"][2][0] / SUMFLOW["MAL EDF"][0][0]
    ax.text(
        0.98, 0.05,
        f"Growth N=3 → N=7\n"
        f"  FCFS-ST:      ×{stat_fcfs_growth:.2f}\n"
        f"  Elastic-EDF:  ×{mal_edf_growth:.2f}",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=ANNOT_FS, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="0.6"),
    )


line_plot(
    SUMFLOW,
    ylabel="Sum of flow-times (minutes)",
    fname="03_sumflow_vs_n",
    title="Aggregate workflow latency vs workload size",
    annotate=annot_flow,
    palette=PAL["sumflow"],
)


print("Wrote plots to", OUT)
