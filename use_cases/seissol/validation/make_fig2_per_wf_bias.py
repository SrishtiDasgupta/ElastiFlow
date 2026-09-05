"""Figure 2 — Per-workflow timing deviation: Sim vs Infra B₁ and B₂.

Two grouped bars per workflow (top: vs B₁, bottom: vs B₂). Bucket-coloured
by max |bias| across the two comparisons.

Run standalone:  python3 make_fig2_per_wf_bias.py
Output: modelled/figures/thesis/thesis_fig2_per_wf_bias.png
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from thesis_figs_common import (
    apply_style, CLEAN_GREEN, CLOUD_ORANGE, SWAP_GREY,
    load_workflows_raw, load_summary, stable_order, write_label_mapping,
    FIG,
)

THRESH_TIGHT = 8.0   # % — "within ±8% in both" bucket boundary


def fig2_per_wf_bias(by_run, summary, order, wf_label):
    rows_sum = {r["workflow_id"]: r for r in summary if r["case"] == "B"}
    r2 = by_run["infra_B_r2"]; r3 = by_run["infra_B_r3"]

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

    fig, ax = plt.subplots(figsize=(18, 9))
    height = 0.36
    ann_fs = 16
    res_fs = 15
    callout_fs = 16

    bars_meta = []
    for i, wfid in enumerate(order):
        s = rows_sum[wfid]
        is_swap = s["is_swap"] == "1"
        sim_d = float(s["sim_duration_s"])
        d_r2 = r2[wfid]["duration_s"]; d_r3 = r3[wfid]["duration_s"]
        b1 = 100 * (sim_d - d_r2) / d_r2
        b2 = 100 * (sim_d - d_r3) / d_r3
        max_abs = max(abs(b1), abs(b2))
        if is_swap:
            color, hatch, bucket = SWAP_GREY, "//", "swap"
        elif max_abs > THRESH_TIGHT:
            color, hatch, bucket = CLOUD_ORANGE, "", "cloud"
        else:
            color, hatch, bucket = CLEAN_GREEN, "", "clean"
        bars_meta.append((i, wfid, b1, b2, color, hatch, bucket, s))

    # Reference bands and zero-line
    ax.axvspan(-8, 8,   color=CLEAN_GREEN, alpha=0.10, zorder=0)
    ax.axvspan(-20, 20, color=CLOUD_ORANGE, alpha=0.06, zorder=0)
    ax.axvline(0, color="black", linestyle="--", linewidth=0.8, zorder=1)

    for i, wfid, b1, b2, color, hatch, bucket, s in bars_meta:
        # Two bars per wf: B₁ (top, full opacity) and B₂ (bottom, 65% opacity)
        for offset, bias, tag in [(-height/2, b1, "INFRA$_1$"), (+height/2, b2, "INFRA$_2$")]:
            plot_bias = bias
            if bucket == "swap":
                plot_bias = np.sign(bias) * min(abs(bias), 24)
            alpha = 1.0 if offset < 0 else 0.65
            ax.barh(i + offset, plot_bias, height=height*0.95,
                    color=color, alpha=alpha, hatch=hatch,
                    edgecolor="black", linewidth=0.4, zorder=2)
            if bucket == "swap":
                tx = np.sign(bias) * 24.5
                ha = "left" if bias > 0 else "right"
                ax.text(tx, i + offset, f"{tag}: swap",
                        va="center", ha=ha, fontsize=ann_fs,
                        color="#424242", style="italic")
            else:
                tx = bias + (0.6 if bias >= 0 else -0.6)
                ha = "left" if bias >= 0 else "right"
                ax.text(tx, i + offset, f"{tag}: {bias:+.1f}%",
                        va="center", ha=ha, fontsize=ann_fs, color="black")
        # resource family tag in the left margin
        ax.text(-27.5, i, s["infra_resource"], va="center", ha="left",
                fontsize=res_fs, color="#555", family="monospace")

    # Callout on the smallest non-swap |bias| (uses B₁ comparison)
    non_swap = [b for b in bars_meta if b[6] != "swap"]
    smallest = min(non_swap, key=lambda x: abs(x[2]))
    i_s, _, b1_s, _, _, _, _, r_s = smallest
    dur = float(r_s["infra_duration_s"])
    dev_s = abs(dur * b1_s / 100.0)
    ax.annotate(f"INFRA$_1$: {b1_s:+.1f}% — {dev_s:.0f}s on {dur/60:.0f}-min workflow",
                xy=(b1_s, i_s - height/2), xytext=(15, i_s - 2),
                fontsize=callout_fs, color="black",
                arrowprops=dict(arrowstyle="->", color="black", lw=1.4))

    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([wf_label[w] for w in order])
    ax.invert_yaxis()
    ax.set_xlim(-29, 29)
    ax.set_xlabel("Relative timing deviation (sim − infra) / infra  (%)")
    ax.set_title("Per-workflow relative timing deviation:\n"
                 "Simulator Run vs Actual Runs on AWS Infrastructure Sessions INFRA$_1$ (top bar) and INFRA$_2$ (bottom bar)",
                 loc="left")
    ax.grid(axis="x", linestyle=":", alpha=0.4)

    n_clean = sum(1 for b in bars_meta if b[6] == "clean")
    n_cloud = sum(1 for b in bars_meta if b[6] == "cloud")
    n_swap  = sum(1 for b in bars_meta if b[6] == "swap")
    handles = [
        mpatches.Patch(color=CLEAN_GREEN, label=f"Within ±8% in both ({n_clean} wfs)"),
        mpatches.Patch(color=CLOUD_ORANGE,
                       label=f"Timing deviation outside ±8% in at least one session "
                             f"({n_cloud} wf{'s' if n_cloud != 1 else ''})"),
        mpatches.Patch(facecolor=SWAP_GREY, hatch="//", edgecolor="black",
                       label=f"Decision swap — excluded ({n_swap} wfs)"),
    ]
    # Place legend below x-axis label.
    fig.legend(handles=handles, loc="lower center", ncol=1,
               frameon=True, bbox_to_anchor=(0.5, -0.12))

    fig.tight_layout(rect=(0, 0.14, 1, 1))
    out = FIG / "thesis_fig2_per_wf_bias.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


def main():
    apply_style()
    by_run  = load_workflows_raw()
    summary = load_summary()
    order, wf_label = stable_order(summary)
    write_label_mapping(order, summary)
    fig2_per_wf_bias(by_run, summary, order, wf_label)


if __name__ == "__main__":
    main()
