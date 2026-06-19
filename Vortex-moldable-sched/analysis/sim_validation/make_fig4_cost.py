"""Figure 4 — Cost fidelity: per-workflow breakdown and aggregate comparison.

Left panel: 3 grouped horizontal bars per workflow (Infra B₁, Infra B₂, Sim).
Right panel: 4 aggregate bars (Infra B₁, Infra B₂, Sim, Sim swap-corrected).

Run standalone:  python3 make_fig4_cost.py
Output: modelled/figures/thesis/thesis_fig4_cost.png
"""
import matplotlib.pyplot as plt
import numpy as np

from thesis_figs_common import (
    apply_style, INFRA_BLUE, INFRA_BLUE_LIGHT, SIM_RED, CORR_GREEN,
    load_modelled, load_summary, stable_order, write_label_mapping,
    FIG,
)


def fig4_cost(modelled_rows, summary, order, wf_label):
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

    # ---- gather costs by (run, workflow)
    def get(run):
        return {r["workflow_id"]: float(r["cost_modelled_usd"])
                for r in modelled_rows if r["run_id"] == run}
    cost_iB1 = get("infra_B_r2")   # r2 = B₁
    cost_iB2 = get("infra_B_r3")   # r3 = B₂
    cost_sB  = get("sim_B")

    fig = plt.figure(figsize=(24, 10))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.75, 1.0], wspace=0.25)
    ax_l = fig.add_subplot(gs[0])
    ax_r = fig.add_subplot(gs[1])

    # ============================================================
    # LEFT panel — per-workflow grouped horizontal bars
    # ============================================================
    y = np.arange(len(order))
    h = 0.25
    iB1 = [cost_iB1[w] for w in order]
    iB2 = [cost_iB2[w] for w in order]
    sB  = [cost_sB[w]  for w in order]

    ax_l.barh(y - h, iB1, h, color=INFRA_BLUE,       edgecolor="black",
              linewidth=0.4, label="Infra Session INFRA$_1$")
    ax_l.barh(y,     iB2, h, color=INFRA_BLUE_LIGHT, edgecolor="black",
              linewidth=0.4, label="Infra Session INFRA$_2$")
    ax_l.barh(y + h, sB,  h, color=SIM_RED,          edgecolor="black",
              linewidth=0.4, label="Simulator Run")

    # swap-row shading + per-row "decision swap" labels
    n = len(order)
    xmax = max(max(iB1), max(iB2), max(sB)) * 1.28
    for i in (n-2, n-1):
        ax_l.axhspan(i - 0.5, i + 0.5, color="#fff3e0", alpha=0.6, zorder=0)
        ax_l.text(xmax * 0.99, i, "decision swap",
                  fontsize=17, color="#e65100", ha="right", va="center",
                  style="italic",
                  bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#fb8c00"))

    # numeric value labels at bar ends
    for i, w in enumerate(order):
        ax_l.text(iB1[i] + 0.03, i - h, f"${iB1[i]:.2f}", va="center",
                  fontsize=16, color="#0d47a1")
        ax_l.text(iB2[i] + 0.03, i,     f"${iB2[i]:.2f}", va="center",
                  fontsize=16, color="#1e5a85")
        ax_l.text(sB[i]  + 0.03, i + h, f"${sB[i]:.2f}", va="center",
                  fontsize=16, color="#b71c1c")

    ax_l.set_yticks(y)
    ax_l.set_yticklabels([wf_label[w] for w in order])
    ax_l.invert_yaxis()
    ax_l.set_xlim(0, xmax)
    ax_l.set_xlabel("Cost per workflow (USD)")
    ax_l.set_title("Per-workflow cost breakdown", loc="left")
    ax_l.grid(axis="x", linestyle=":", alpha=0.4)

    # ============================================================
    # RIGHT panel — aggregate (4 bars)
    # ============================================================
    total_iB1 = sum(iB1)
    total_iB2 = sum(iB2)
    total_sim = sum(sB)
    # Swap-correction: credit swap workflows at the infra B₁ cost
    swap_wfs = order[-2:]
    swap_sim_cost   = sum(cost_sB[w]  for w in swap_wfs)
    swap_infra_cost = sum(cost_iB1[w] for w in swap_wfs)
    total_corr  = total_sim - swap_sim_cost + swap_infra_cost
    swap_delta  = total_sim - total_corr
    baseline    = (total_iB1 + total_iB2) / 2

    labels = ["INFRA$_1$", "INFRA$_2$",
              "Simulator\n", "Simulator\n(swap-corrected)"]
    vals   = [total_iB1, total_iB2, total_sim, total_corr]
    colors = [INFRA_BLUE, INFRA_BLUE_LIGHT, SIM_RED, CORR_GREEN]
    bars   = ax_r.bar(labels, vals, color=colors, edgecolor="black",
                      linewidth=0.5, width=0.6)
    for b, v, lbl in zip(bars, vals, labels):
        pct = (v - baseline) / baseline * 100
        if "Infra" in lbl:
            tag = f"${v:.2f}"
        else:
            tag = f"${v:.2f}({pct:+.0f}% \nvs infra \nmean)"
        ax_r.text(b.get_x() + b.get_width()/2, v + 0.2, tag,
                  ha="center", fontsize=16, fontweight="bold")

    yb = max(vals) + 2.0
    ax_r.annotate("", xy=(2, yb), xytext=(3, yb),
                  arrowprops=dict(arrowstyle="<->", color="#444", lw=1.0))
    ax_r.text(2.5, yb + 0.3, f"swap contribution:\n~${swap_delta:.2f}",
              ha="center", fontsize=16, color="#444", style="italic")
    ax_r.set_ylim(0, yb + 2.5)
    ax_r.set_ylabel("Total cost across 10 workflows (USD)")
    ax_r.set_title("Aggregate cost\nwith swap-correction", loc="left")
    ax_r.grid(axis="y", linestyle=":", alpha=0.4)
    ax_r.tick_params(axis="x", labelsize=18)

    # Legend below x-axis label (figure-level legend for both panels)
    handles, labels = ax_l.get_legend_handles_labels()
    fig.legend(handles=handles, labels=labels, loc="lower center", ncol=3,
               frameon=True, bbox_to_anchor=(0.5, -0.10),
               columnspacing=1.2, handletextpad=0.6, borderpad=0.6)

    fig.suptitle(
        "Cost fidelity: per-workflow breakdown and aggregate comparison",
        fontsize=24,
        fontweight="bold",
        y=1.02,
    )
    # tight_layout sometimes struggles with GridSpec + figure legends; subplots_adjust is stable.
    fig.subplots_adjust(left=0.12, right=0.98, top=0.90, bottom=0.20, wspace=0.25)
    out = FIG / "thesis_fig4_cost.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}  (B1=${total_iB1:.2f}, B2=${total_iB2:.2f}, "
          f"sim=${total_sim:.2f}, corrected=${total_corr:.2f})")


def main():
    apply_style()
    modelled = load_modelled()
    summary  = load_summary()
    order, wf_label = stable_order(summary)
    write_label_mapping(order, summary)
    fig4_cost(modelled, summary, order, wf_label)


if __name__ == "__main__":
    main()
