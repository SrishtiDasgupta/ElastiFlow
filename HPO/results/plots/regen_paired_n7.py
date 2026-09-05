"""Paired elastic-static head-to-head at N=7 (thesis fig:hpo:paired).

Replaces the earlier 04_paired_diff, whose DIFF_MISS/DIFF_COST arrays were
hardcoded literals that no longer matched total_cost_per_run.json, and whose
panels (d misses, d CPR, d on-demand cost over N=3,5,7) did not correspond to
the metrics the caption and prose describe.

Everything below is computed live, per run, paired by seed index.
Left panel  : relative metrics (%)          -- cost, queue wait, turnaround, makespan
Right panel : constraint metrics (pp)       -- deadline miss, budget miss
Negative = elastic better on every metric shown.
"""
from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from statistics import mean as st_mean, stdev as st_stdev

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.weight": "bold",
    "axes.labelweight": "bold",
    "axes.titleweight": "bold",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS = 18, 16, 14, 13
EDF_COL, FCFS_COL = "#0F766E", "#5EEAD4"

HERE = Path(__file__).resolve().parent
TOTAL = json.loads((HERE / "total_cost_per_run.json").read_text())
N = 7


def runs(mode, order):
    return TOTAL[f"N{N}_{mode}_{order}"]["per_run"]


def paired(order):
    """Per-run paired deltas (elastic - static). Returns {metric: (mean, sd)}."""
    e, s = runs("moldable", order), runs("static", order)
    pct = lambda k: [100.0 * (ee[k] / ss[k] - 1.0) for ee, ss in zip(e, s)]
    pp = lambda k: [100.0 * (ee[k] - ss[k]) / N for ee, ss in zip(e, s)]
    series = {
        "Cost": pct("cost_total"),
        "Queue wait": pct("sum_wait_s"),
        "Turnaround": pct("sum_turnaround_s"),
        "Makespan": pct("makespan_s"),
        "Deadline miss": pp("misses"),
        "Budget miss": pp("budget_misses"),
    }
    return {k: (st_mean(v), st_stdev(v) if len(v) > 1 else 0.0)
            for k, v in series.items()}


def main():
    P = {o.upper(): paired(o) for o in ("edf", "fcfs")}
    panels = [
        (["Cost", "Queue wait", "Turnaround", "Makespan"],
         "Change vs static (%)"),
        (["Deadline miss", "Budget miss"],
         "Change vs static (percentage points)"),
    ]
    fig, axs = plt.subplots(
        1, 2, figsize=(15, 5.8), gridspec_kw={"width_ratios": [2, 1]})
    bar_w = 0.36
    for ax, (keys, ylabel) in zip(axs, panels):
        x = np.arange(len(keys))
        for off, fam, col in ((-bar_w / 2, "EDF", EDF_COL),
                              (+bar_w / 2, "FCFS", FCFS_COL)):
            mu = [P[fam][k][0] for k in keys]
            sd = [P[fam][k][1] for k in keys]
            ax.bar(x + off, mu, bar_w, yerr=sd, color=col, edgecolor="black",
                   linewidth=0.8, capsize=4, label=fam)
            for xi, m, e in zip(x + off, mu, sd):
                # anchor below the lower whisker so labels never collide with it
                ax.annotate(f"{m:.1f}", (xi, m - e), textcoords="offset points",
                            xytext=(0, -13), ha="center", va="top",
                            fontsize=11, fontweight="bold")
        ax.axhline(0, color="black", linewidth=1.0)
        ax.set_xticks(x)
        ax.set_xticklabels(keys)
        ax.set_ylabel(ylabel, fontsize=LABEL_FS)
        ax.tick_params(axis="both", labelsize=TICK_FS)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_fontweight("bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="y", alpha=0.25)
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo - 0.12 * (hi - lo), hi + 0.10 * (hi - lo))
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=2, fontsize=LEG_FS, frameon=True,
               bbox_to_anchor=(0.5, -0.06),
               prop={"weight": "bold", "size": LEG_FS},
               handlelength=2.0, handletextpad=0.8, columnspacing=2.0,
               borderpad=0.8)
    fig.suptitle(
        f"Paired difference (Elastic − Static) at N = {N}, "
        "per scheduler ordering",
        fontsize=TITLE_FS, fontweight="bold", y=1.02)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(HERE / f"04_paired_diff.{ext}",
                    dpi=300 if ext == "png" else None, bbox_inches="tight")
    plt.close(fig)
    for fam in ("EDF", "FCFS"):
        print(f"  {fam}: " + "  ".join(
            f"{k}={v[0]:+.2f}(sd {v[1]:.2f})" for k, v in P[fam].items()))
    print("  wrote 04_paired_diff.{pdf,png}")


if __name__ == "__main__":
    main()
