"""Figure 1 — Three-panel Gantt timeline: Infra B₁, Infra B₂, Simulator Run 1.

Run standalone:  python3 make_fig1_gantt.py
Output: modelled/figures/thesis/thesis_fig1_gantt.png
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from thesis_figs_common import (
    apply_style,
    load_workflows_raw, load_summary, stable_order, write_label_mapping,
    FIG,
)


def fig1_gantt(by_run, order, wf_label):
    pairs = [("infra_B_r2", "Infra Session INFRA$_1$"),
             ("infra_B_r3", "Infra Session INFRA$_2$"),
             ("sim_B",      "Simulator Run")]
    # Match thesis-wide styling used in urgency_weights.py
    plt.rcParams.update(
        {
            "font.weight": "bold",
            "axes.titleweight": "bold",
            "axes.labelweight": "bold",
            "mathtext.default": "bf",
            "axes.titlesize": 24,
            "axes.labelsize": 22,
            "xtick.labelsize": 20,
            "ytick.labelsize": 20,
            "legend.fontsize": 20,
        }
    )

    # Larger image dimensions to improve y-label readability.
    fig, axes = plt.subplots(3, 1, figsize=(24, 14), sharex=True)

    # Stable, per-workflow colors (consistent across all panels)
    cmap = plt.get_cmap("tab10")
    wf_color = {wfid: cmap(i % 10) for i, wfid in enumerate(order)}

    for ax, (run_id, title) in zip(axes, pairs):
        run = by_run[run_id]
        t0 = min(r["alloc_t"] for r in run.values() if r["alloc_t"] is not None)
        for i, wfid in enumerate(order):
            r = run.get(wfid)
            if not r or r["alloc_t"] is None: continue
            end = r["complete_t"] if r["complete_t"] is not None else (
                r["alloc_t"] + (r["duration_s"] or 0))
            start = r["alloc_t"] - t0
            width = end - r["alloc_t"]
            color = wf_color.get(wfid, "#888")
            ax.barh(i, width, left=start, color=color, edgecolor="black",
                    linewidth=0.9, height=0.86)
            ax.text(start + width/2, i, wf_label[wfid],
                    ha="center", va="center", fontsize=16, color="black",
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
        # swap-row shading + labels
        n = len(order)
        for i in (n-2, n-1):
            ax.axhspan(i-0.42, i+0.42, color="black", alpha=0.05, zorder=0)
            ax.text(3250, i, "decision swap", fontsize=17, color="#e65100",
                    ha="right", va="center", style="italic",
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#fb8c00"))
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels([wf_label[w] for w in order])
        ax.set_title(title, loc="left")
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle=":", alpha=0.5)
        ax.set_xlim(0, 3300)
    axes[-1].set_xlabel("Seconds since first allocation")

    # legend (workflow colors; consistent across panels)
    handles = [mpatches.Patch(color=wf_color[w], label=wf_label[w]) for w in order]

    fig.suptitle(
        "Timeline for Simulator vs Actual Run on AWS Infrastructure for 10 SeisSol--TinyDA workflows",
        fontsize=24,
        fontweight="bold",
        y=1.01,
    )
    fig.legend(handles=handles, loc="lower center", ncol=5,
               frameon=False, bbox_to_anchor=(0.5, -0.02),
               columnspacing=1.2, handletextpad=0.6, borderpad=0.4)
    # Extra left margin for y-axis tick labels; reserve bottom for the legend.
    fig.subplots_adjust(left=0.18, bottom=0.14, hspace=0.35)
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    out = FIG / "thesis_fig1_gantt.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


def main():
    apply_style()
    by_run  = load_workflows_raw()
    summary = load_summary()
    order, wf_label = stable_order(summary)
    write_label_mapping(order, summary)
    fig1_gantt(by_run, order, wf_label)


if __name__ == "__main__":
    main()
