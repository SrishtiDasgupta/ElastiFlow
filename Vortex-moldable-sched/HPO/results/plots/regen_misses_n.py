"""Deadline misses vs N (thesis fig:hpo:misses_vs_n).

The MISSES array in generate_thesis_plots.py was a hardcoded literal that had
drifted from total_cost_per_run.json in every cell (e.g. at N=7 it plotted
4.2/4.5/3.3/3.3 where the data gives 3.33/3.33/2.67/2.50), so the figure
disagreed with the DMR values quoted in the prose. Recomputed live here.
Style matches line_plot() in generate_thesis_plots.py.
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
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS, ANNOT_FS = 18, 16, 14, 14, 12
LEG_MARKER_SCALE, LEG_HANDLE_LEN = 1.6, 3.5

HERE = Path(__file__).resolve().parent
TOTAL = json.loads((HERE / "total_cost_per_run.json").read_text())
NS = np.array([3, 5, 7])
CORNERS = ["STAT EDF", "STAT FCFS", "MAL EDF", "MAL FCFS"]
KEY = {"STAT EDF": ("static", "edf"), "STAT FCFS": ("static", "fcfs"),
       "MAL EDF": ("moldable", "edf"), "MAL FCFS": ("moldable", "fcfs")}
LABEL = {"STAT EDF": "EDF-ST", "STAT FCFS": "FCFS-ST",
         "MAL EDF": "Elastic-EDF", "MAL FCFS": "Elastic-FCFS"}
LS = {"STAT EDF": "-", "STAT FCFS": "--", "MAL EDF": "-", "MAL FCFS": "--"}
MARKER = {"STAT EDF": "o", "STAT FCFS": "s", "MAL EDF": "o", "MAL FCFS": "s"}
PAL = {"STAT EDF": "#7E22CE", "STAT FCFS": "#14B8A6",
       "MAL EDF": "#C026D3", "MAL FCFS": "#A16207"}


def misses(corner):
    mode, order = KEY[corner]
    out = []
    for N in NS:
        vs = [r["misses"] for r in TOTAL[f"N{N}_{mode}_{order}"]["per_run"]]
        out.append((st_mean(vs), st_stdev(vs) if len(vs) > 1 else 0.0))
    return out


def main():
    data = {c: misses(c) for c in CORNERS}
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for c in CORNERS:
        arr = np.array(data[c])
        ax.errorbar(NS, arr[:, 0], yerr=arr[:, 1], label=LABEL[c],
                    color=PAL[c], linestyle=LS[c], marker=MARKER[c],
                    linewidth=2.4, markersize=9, capsize=5, capthick=1.8,
                    markeredgecolor="black", markeredgewidth=0.8)
    ax.set_xlabel("Number of workflows (N)", fontsize=LABEL_FS)
    ax.set_ylabel("Deadline misses (out of N)", fontsize=LABEL_FS)
    ax.set_title("Deadline misses with growing workload", fontsize=TITLE_FS)
    ax.set_xticks(NS)
    ax.tick_params(axis="both", labelsize=TICK_FS)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontweight("bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.25)
    ax.set_ylim(0, 5)
    stat_hi = max(data["STAT EDF"][-1][0], data["STAT FCFS"][-1][0])
    mal_lo = min(data["MAL EDF"][-1][0], data["MAL FCFS"][-1][0])
    ax.text(7, stat_hi + 0.55, "Static", color=PAL["STAT EDF"],
            fontsize=ANNOT_FS, fontweight="bold", ha="right", va="bottom")
    ax.text(7, mal_lo - 0.30, "Elastic", color=PAL["MAL EDF"],
            fontsize=ANNOT_FS, fontweight="bold", ha="right", va="top")
    leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28),
                    frameon=True, fontsize=LEG_FS, ncol=4,
                    markerscale=LEG_MARKER_SCALE,
                    handlelength=LEG_HANDLE_LEN, handletextpad=0.8,
                    columnspacing=2.0, borderpad=0.8)
    for t in leg.get_texts():
        t.set_fontweight("bold")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(HERE / f"02_misses_vs_n.{ext}",
                    dpi=300 if ext == "png" else None, bbox_inches="tight")
    plt.close(fig)
    for c in CORNERS:
        print(f"  {LABEL[c]:20s} " + "  ".join(
            f"N={n}: {m:.2f}+-{s:.2f}" for n, (m, s) in zip(NS, data[c])))
    print("  wrote 02_misses_vs_n.{pdf,png}")


if __name__ == "__main__":
    main()
