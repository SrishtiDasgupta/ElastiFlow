"""
Thesis-grade figures for the BMW sim-validation chapter.

Generates 4 figures into modelled/figures/thesis/:
  thesis_fig1_gantt.png        - Two-panel Gantt overlay with swap annotation
  thesis_fig2_per_wf_bias.png  - 3-bucket per-workflow bias bar chart
  thesis_fig3_mape_context.png - MAPE vs infra noise floor
  thesis_fig4_cost.png         - Per-wf + aggregate cost with swap correction

Excludes infra_B_r1 (failed run). All other figures (out/figures/*, modelled/figures/*)
remain untouched.
"""
import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

HERE = Path(__file__).parent
SRC  = HERE / "modelled"
FIG  = SRC / "figures" / "thesis"
FIG.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 12,
    "figure.dpi": 140,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
})

# ---- Colour palette per user spec --------------------------------------------
FAM_COLOR = {
    "on-prem":        "#2e7d32",   # green
    "hpc7a.24xlarge": "#1565c0",   # blue
    "hpc7a.12xlarge": "#1565c0",
    "c6i.16xlarge":   "#ef6c00",   # orange
    "c6i.32xlarge":   "#c62828",   # red
    "c7i.12xlarge":   "#6a1b9a",
}

# bucket colours
CLEAN_GREEN   = "#43a047"
CLOUD_ORANGE  = "#fb8c00"
SWAP_GREY     = "#9e9e9e"

INFRA_BLUE = "#1565c0"
SIM_RED    = "#c62828"
CORR_GREEN = "#2e7d32"


# ---- helpers ----------------------------------------------------------------
def family_of(decision: str) -> str:
    if not decision: return "?"
    if "on-prem:" in decision and "reserved" not in decision and "on-demand" not in decision:
        return "on-prem"
    m = re.search(r"(?:reserved|on-demand):(.+?)x\d+(?=\||$)", decision)
    return m.group(1) if m else "?"


def load_workflows_raw():
    """Load out/workflows.csv. Returns dict[run_id] -> dict[wfid] -> row."""
    by_run = defaultdict(dict)
    src = HERE / "out" / "workflows.csv"
    for r in csv.DictReader(open(src)):
        for k in ("alloc_t", "complete_t", "free_t", "duration_s"):
            r[k] = float(r[k]) if r[k] else None
        by_run[r["run_id"]][r["workflow_id"]] = r
    return by_run


def load_modelled():
    rows = list(csv.DictReader(open(SRC / "modelled_workflows.csv")))
    return rows


def load_summary():
    return list(csv.DictReader(open(SRC / "all_workflows_summary.csv")))


# Stable wf ordering: same-resource by infra duration ascending, then 2 swaps last
def stable_order(by_run, summary):
    caseB = [r for r in summary if r["case"] == "B"]
    same  = [r for r in caseB if r["is_swap"] == "0"]
    swap  = [r for r in caseB if r["is_swap"] == "1"]
    same.sort(key=lambda r: float(r["infra_duration_s"]))
    swap.sort(key=lambda r: r["workflow_id"])    # deterministic
    ordered = [r["workflow_id"] for r in same] + [r["workflow_id"] for r in swap]
    return ordered, {wf: f"wf{i}" for i, wf in enumerate(ordered)}


# =============================================================================
# Figure 1 — Gantt timeline overlay
# =============================================================================
def fig1_gantt(by_run, order, wf_label):
    pairs = [("infra_B_r2", "Infra Session B$_1$"),
             ("infra_B_r3", "Infra Session B$_2$"),
             ("sim_B",      "Simulator Run 1")]
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)

    swap_wfs = order[-2:]  # last two are wf8, wf9 (swaps)

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
            fam   = family_of(r["alloc_decision"])
            color = FAM_COLOR.get(fam, "#888")
            ax.barh(i, width, left=start, color=color, edgecolor="black",
                    linewidth=0.5, height=0.7)
            ax.text(start + width/2, i, wf_label[wfid],
                    ha="center", va="center", fontsize=8, color="white",
                    fontweight="bold")
        # bracket+label on swap workflows
        n = len(order)
        swap_idx = [n-2, n-1]
        for i in swap_idx:
            ax.axhspan(i-0.42, i+0.42, color="black", alpha=0.05, zorder=0)
        ax.text(ax.get_xlim()[1] if False else 3250, (swap_idx[0]+swap_idx[1])/2,
                "decision\nswap", fontsize=9, color="black",
                ha="left", va="center", style="italic",
                bbox=dict(boxstyle="round,pad=0.3", fc="#fff3e0", ec="#fb8c00"))
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels([wf_label[w] for w in order])
        ax.set_title(title, fontsize=11, loc="left")
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle=":", alpha=0.5)
        ax.set_xlim(0, 3300)
    axes[-1].set_xlabel("Seconds since first allocation")

    # legend
    used = set()
    for run, _ in pairs:
        for r in by_run[run].values():
            used.add(family_of(r["alloc_decision"]))
    handles = [mpatches.Patch(color=FAM_COLOR.get(f, "#888"), label=f)
               for f in ["on-prem","hpc7a.24xlarge","c6i.16xlarge","c6i.32xlarge"]
               if f in used]

    fig.suptitle("Sim vs Infra workflow timeline: 10 SeisSol--TinyDA workflows (Case B)",
                 fontsize=12.5, y=1.01)
    fig.legend(handles=handles, loc="lower center", ncol=len(handles),
               frameon=False, bbox_to_anchor=(0.5, -0.02))
    fig.text(0.01, -0.04,
             "wf labels are sorted by infra-observed duration (ascending); wf8 and wf9 are the two decision-swap workflows. "
             "Full ID mapping: figures/thesis/wf_label_mapping.csv",
             fontsize=7.5, color="#666", style="italic")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out = FIG / "thesis_fig1_gantt.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


# =============================================================================
# Figure 2 — Per-workflow timing deviation (3-bucket bar chart)
# =============================================================================
def fig2_per_wf_bias(by_run, summary, order, wf_label):
    """Two grouped bars per workflow: bias vs Infra B₁ (r2) and bias vs Infra B₂ (r3).
    Bucket colour-coded by max |bias| across the two comparisons."""
    rows_sum = {r["workflow_id"]: r for r in summary if r["case"] == "B"}
    r2 = by_run["infra_B_r2"]; r3 = by_run["infra_B_r3"]
    sim = by_run["sim_B"]

    fig, ax = plt.subplots(figsize=(10.5, 6.5))
    THRESH_TIGHT = 8.0
    height = 0.36
    bars_meta = []
    for i, wfid in enumerate(order):
        s = rows_sum[wfid]
        is_swap = s["is_swap"] == "1"
        sim_d  = float(s["sim_duration_s"])
        d_r2 = r2[wfid]["duration_s"]; d_r3 = r3[wfid]["duration_s"]
        b1 = 100 * (sim_d - d_r2) / d_r2
        b2 = 100 * (sim_d - d_r3) / d_r3
        max_abs = max(abs(b1), abs(b2))
        if is_swap:
            color = SWAP_GREY; hatch = "//"; bucket = "swap"
        elif max_abs > THRESH_TIGHT:
            color = CLOUD_ORANGE; hatch = ""; bucket = "cloud"
        else:
            color = CLEAN_GREEN; hatch = ""; bucket = "clean"
        bars_meta.append((i, wfid, b1, b2, color, hatch, bucket, s))

    ax.axvspan(-8, 8,   color=CLEAN_GREEN, alpha=0.10, zorder=0)
    ax.axvspan(-20, 20, color=CLOUD_ORANGE, alpha=0.06, zorder=0)
    ax.axvline(0, color="black", linestyle="--", linewidth=0.8, zorder=1)

    for i, wfid, b1, b2, color, hatch, bucket, s in bars_meta:
        # Two bars per wf — vs B₁ (top), vs B₂ (bottom)
        for offset, bias, tag in [(-height/2, b1, "B$_1$"), (+height/2, b2, "B$_2$")]:
            plot_bias = bias
            if bucket == "swap":
                plot_bias = np.sign(bias) * min(abs(bias), 24)
            # darker shade for B₂ row
            face = color
            alpha = 1.0 if offset < 0 else 0.65
            ax.barh(i + offset, plot_bias, height=height*0.95,
                    color=face, alpha=alpha, hatch=hatch,
                    edgecolor="black", linewidth=0.4, zorder=2)
            if bucket == "swap":
                txt = f"{tag}: swap"
                tx = np.sign(bias) * 24.5
                ha = "left" if bias > 0 else "right"
                ax.text(tx, i + offset, txt, va="center", ha=ha,
                        fontsize=7.5, color="#424242", style="italic")
            else:
                tx = bias + (0.6 if bias >= 0 else -0.6)
                ha = "left" if bias >= 0 else "right"
                ax.text(tx, i + offset, f"{tag}: {bias:+.1f}%",
                        va="center", ha=ha, fontsize=8, color="black")
        # resource family tag
        ax.text(-27.5, i, s["infra_resource"], va="center", ha="left",
                fontsize=8, color="#555", family="monospace")

    # callout on smallest non-swap bias (use B₁ comparison for callout)
    non_swap = [b for b in bars_meta if b[6] != "swap"]
    smallest = min(non_swap, key=lambda x: abs(x[2]))
    i_s, _, b1_s, _, _, _, _, r_s = smallest
    dur = float(r_s["infra_duration_s"])
    dev_s = abs(dur * b1_s / 100.0)
    ax.annotate(f"B$_1$: {b1_s:+.1f}% — {dev_s:.0f}s on {dur/60:.0f}-min workflow",
                xy=(b1_s, i_s - height/2), xytext=(15, i_s - 2),
                fontsize=8.5, color="#1565c0",
                arrowprops=dict(arrowstyle="->", color="#1565c0", lw=0.8))

    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([wf_label[w] for w in order])
    ax.invert_yaxis()
    ax.set_xlim(-29, 29)
    ax.set_xlabel("Relative timing deviation (sim − infra) / infra  (%)")
    ax.set_title("Per-workflow relative timing deviation:\nSimulator Run 1 vs Infra Sessions B$_1$ (top bar) and B$_2$ (bottom bar)",
                 fontsize=11.5, loc="left")
    ax.grid(axis="x", linestyle=":", alpha=0.4)

    n_clean = sum(1 for b in bars_meta if b[6] == "clean")
    n_cloud = sum(1 for b in bars_meta if b[6] == "cloud")
    n_swap  = sum(1 for b in bars_meta if b[6] == "swap")
    handles = [
        mpatches.Patch(color=CLEAN_GREEN,  label=f"Within ±8% in both ({n_clean} wfs)"),
        mpatches.Patch(color=CLOUD_ORANGE, label=f"Timing deviation outside ±8% in at least one session ({n_cloud} wf{'s' if n_cloud != 1 else ''})"),
        mpatches.Patch(facecolor=SWAP_GREY, hatch="//", edgecolor="black",
                       label=f"Decision swap — excluded ({n_swap} wfs)"),
    ]
    ax.legend(handles=handles, loc="lower right", frameon=True, fontsize=9)

    fig.text(0.01, -0.02,
             "wf labels are sorted by infra-observed duration (ascending); wf8 and wf9 are the two decision-swap workflows.",
             fontsize=7.5, color="#666", style="italic")
    fig.tight_layout()
    out = FIG / "thesis_fig2_per_wf_bias.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}")


# =============================================================================
# Figure 3 — MAPE context vs infra noise floor
# =============================================================================
def fig3_mape_context(by_run, summary):
    """3 bars: infra B₁ vs B₂; sim vs infra B; infra A vs infra B."""
    # Bar 1: infra B₁ vs B₂  (r2 vs r3)  - per-workflow MAPE
    r2 = by_run["infra_B_r2"]; r3 = by_run["infra_B_r3"]
    pct = []
    for wf in r2:
        if wf in r3 and r2[wf]["duration_s"] and r3[wf]["duration_s"]:
            d2 = r2[wf]["duration_s"]; d3 = r3[wf]["duration_s"]
            mean_d = (d2 + d3) / 2
            pct.append(100 * abs(d2 - d3) / mean_d)
    mape_infra_repeat = statistics.mean(pct)

    # Bar 2: sim vs infra B (r2/r3 mean), same-resource workflows only (swap-excluded)
    caseB = [r for r in summary if r["case"] == "B"]
    same_b = [abs(float(r["bias_pct"])) for r in caseB if r["is_swap"] == "0"]
    all_b  = [abs(float(r["bias_pct"])) for r in caseB]
    mape_sim_all      = statistics.mean(all_b)
    mape_sim_same     = statistics.mean(same_b)

    # Bar 3: infra A vs infra B per-workflow MAPE (same workflow IDs)
    A  = by_run["infra_A"]
    Bm = {wf: statistics.mean([r2[wf]["duration_s"], r3[wf]["duration_s"]])
          for wf in r2 if wf in r3 and r2[wf]["duration_s"] and r3[wf]["duration_s"]}
    pct = []
    for wf, db in Bm.items():
        if wf in A and A[wf]["duration_s"] and A[wf]["duration_s"] >= 60:  # exclude broken polling-30s records
            da = A[wf]["duration_s"]
            pct.append(100 * abs(da - db) / db)
    mape_infra_cross = statistics.mean(pct)

    fig, ax = plt.subplots(figsize=(7, 5.5))
    labels = ["Infra: same session\n(B$_1$ vs B$_2$)",
              f"Simulator vs Infra\n(swap-excluded, {len(same_b)} wfs)"]
    vals   = [mape_infra_repeat, mape_sim_same]
    colors = ["#90caf9", "#0d47a1"]
    bars   = ax.bar(labels, vals, color=colors, edgecolor="black", linewidth=0.6, width=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width()/2, v + 0.15, f"{v:.1f}%",
                ha="center", fontsize=10.5, fontweight="bold")

    ax.set_ylabel("Mean Absolute Percentage Error (MAPE, %)")
    ax.set_ylim(0, max(vals) * 1.5)
    ax.set_title("Simulator timing error vs\ninfra session-to-session repeatability",
                 fontsize=11.5, loc="left")
    ax.grid(axis="y", linestyle=":", alpha=0.4)

    fig.tight_layout()
    out = FIG / "thesis_fig3_mape_context.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}  (B-repeat={mape_infra_repeat:.1f}%, sim={mape_sim_same:.1f}% [all-wf={mape_sim_all:.1f}%], A-vs-B={mape_infra_cross:.1f}%)")
    return mape_infra_repeat, mape_sim_same, mape_infra_cross


# =============================================================================
# Figure 4 — Cost decomposition (per-wf left, aggregate right with swap correction)
# =============================================================================
def fig4_cost(modelled_rows, summary, order, wf_label):
    """Left: per-workflow cost (infra B₁ vs sim B).
       Right: 3-bar aggregate (infra, sim, swap-corrected sim)."""
    # cost by (run, wf)
    def get(run):
        return {r["workflow_id"]: float(r["cost_modelled_usd"])
                for r in modelled_rows if r["run_id"] == run}
    cost_iB1 = get("infra_B_r2")   # treat r2 as B₁ canonical
    cost_iB2 = get("infra_B_r3")   # r3 = B₂
    cost_sB  = get("sim_B")

    caseB = {r["workflow_id"]: r for r in summary if r["case"] == "B"}

    fig = plt.figure(figsize=(14.5, 6.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.7, 1.0], wspace=0.28)
    ax_l = fig.add_subplot(gs[0])
    ax_r = fig.add_subplot(gs[1])

    # ---- LEFT panel: 3 grouped horizontal bars per workflow
    y = np.arange(len(order))
    h = 0.25
    iB1 = [cost_iB1[w] for w in order]
    iB2 = [cost_iB2[w] for w in order]
    sB  = [cost_sB[w]  for w in order]
    # darker→lighter infra; red sim
    INFRA_BLUE_LIGHT = "#5d99c6"
    ax_l.barh(y - h, iB1, h, color=INFRA_BLUE, edgecolor="black",
              linewidth=0.4, label="Infra Session B$_1$")
    ax_l.barh(y,     iB2, h, color=INFRA_BLUE_LIGHT, edgecolor="black",
              linewidth=0.4, label="Infra Session B$_2$")
    ax_l.barh(y + h, sB,  h, color=SIM_RED, edgecolor="black",
              linewidth=0.4, label="Simulator Run 1")

    # shade swap rows and annotate each with its own "decision swap" label
    n = len(order)
    xmax = max(max(iB1), max(iB2), max(sB)) * 1.28
    for i in (n-2, n-1):
        ax_l.axhspan(i - 0.5, i + 0.5, color="#fff3e0", alpha=0.6, zorder=0)
        ax_l.text(xmax * 0.99, i, "decision swap",
                  fontsize=9, color="#e65100", ha="right", va="center", style="italic",
                  bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#fb8c00"))

    # value labels
    for i, w in enumerate(order):
        ax_l.text(iB1[i] + 0.03, i - h, f"${iB1[i]:.2f}", va="center",
                  fontsize=7.5, color="#0d47a1")
        ax_l.text(iB2[i] + 0.03, i,     f"${iB2[i]:.2f}", va="center",
                  fontsize=7.5, color="#1e5a85")
        ax_l.text(sB[i]  + 0.03, i + h, f"${sB[i]:.2f}", va="center",
                  fontsize=7.5, color="#b71c1c")

    ax_l.set_yticks(y)
    ax_l.set_yticklabels([wf_label[w] for w in order])
    ax_l.invert_yaxis()
    ax_l.set_xlim(0, xmax)
    ax_l.set_xlabel("Cost per workflow (USD)")
    ax_l.set_title("Per-workflow cost breakdown", fontsize=11.5, loc="left")
    ax_l.legend(loc="lower right", frameon=True, fontsize=9)
    ax_l.grid(axis="x", linestyle=":", alpha=0.4)

    # ---- RIGHT panel: aggregate (4 bars)
    total_iB1   = sum(iB1)
    total_iB2   = sum(iB2)
    total_sim   = sum(sB)
    # swap-corrected: credit swap wfs at infra B₁ cost
    swap_wfs = order[-2:]
    swap_sim_cost   = sum(cost_sB[w]  for w in swap_wfs)
    swap_infra_cost = sum(cost_iB1[w] for w in swap_wfs)
    total_corr = total_sim - swap_sim_cost + swap_infra_cost
    swap_delta = total_sim - total_corr

    # baseline for % comparison = mean of two infra sessions
    baseline = (total_iB1 + total_iB2) / 2

    labels = ["Infra\nSession B$_1$", "Infra\nSession B$_2$", "Simulator\nRun 1", "Simulator\n(swap-corrected)"]
    vals   = [total_iB1, total_iB2, total_sim, total_corr]
    colors = [INFRA_BLUE, INFRA_BLUE_LIGHT, SIM_RED, CORR_GREEN]
    bars   = ax_r.bar(labels, vals, color=colors, edgecolor="black", linewidth=0.5, width=0.6)
    for b, v, lbl in zip(bars, vals, labels):
        pct = (v - baseline) / baseline * 100
        if "Infra" in lbl:
            tag = f"${v:.2f}"
        else:
            tag = f"${v:.2f}\n({pct:+.0f}% vs infra mean)"
        ax_r.text(b.get_x() + b.get_width()/2, v + 0.2, tag,
                  ha="center", fontsize=9, fontweight="bold")
    # bracket between sim and swap-corrected
    yb = max(vals) + 2.0
    ax_r.annotate("", xy=(2, yb), xytext=(3, yb),
                  arrowprops=dict(arrowstyle="<->", color="#444", lw=1.0))
    ax_r.text(2.5, yb + 0.3,
              f"swap contribution:\n~${swap_delta:.2f}",
              ha="center", fontsize=9, color="#444", style="italic")
    ax_r.set_ylim(0, yb + 2.5)
    ax_r.set_ylabel("Total cost across 10 workflows (USD)")
    ax_r.set_title("Aggregate cost\nwith swap-correction", fontsize=11.5, loc="left")
    ax_r.grid(axis="y", linestyle=":", alpha=0.4)
    ax_r.tick_params(axis="x", labelsize=8.5)

    fig.suptitle("Cost fidelity: per-workflow breakdown and aggregate comparison",
                 fontsize=13, y=1.02)
    fig.text(0.01, -0.03,
             "wf labels are sorted by infra-observed duration (ascending); wf8 and wf9 are the two decision-swap workflows.\n"
             "Costs use the per-instance, per-second rates from the BMW-campaign resources.yaml (reserved/on-demand split per family,\n"
             "summed across all co-allocated instances per workflow); FSx, EBS, data transfer, and provisioning are excluded.\n"
             "wf6 ran on hpc7a.24xlarge (1× reserved $3.63/hr + 1× on-demand $7.73/hr = $11.36/hr); it dominates because of instance rate, not duration.",
             fontsize=7.5, color="#666", style="italic")
    fig.tight_layout()
    out = FIG / "thesis_fig4_cost.png"
    fig.savefig(out); plt.close(fig)
    print(f"  wrote {out.name}  (B1=${total_iB1:.2f}, B2=${total_iB2:.2f}, sim=${total_sim:.2f}, corrected=${total_corr:.2f})")


# ---- main -------------------------------------------------------------------
def main():
    by_run   = load_workflows_raw()
    modelled = load_modelled()
    summary  = load_summary()
    order, wf_label = stable_order(by_run, summary)

    print(f"Generating thesis figures into {FIG}/")
    print(f"  workflow ordering (wf0..wf{len(order)-1}, swaps last):")
    for i, w in enumerate(order):
        tag = " (swap)" if i >= len(order)-2 else ""
        print(f"    {wf_label[w]} = {w[:8]}{tag}")

    # Write the wf-label mapping for traceability (referenced in figure footers)
    map_csv = FIG / "wf_label_mapping.csv"
    with open(map_csv, "w") as f:
        f.write("wf_label,workflow_id_full,is_swap,infra_duration_s_caseB\n")
        sumB = {r["workflow_id"]: r for r in summary if r["case"] == "B"}
        for i, w in enumerate(order):
            r = sumB[w]
            f.write(f"wf{i},{w},{r['is_swap']},{r['infra_duration_s']}\n")
    print(f"  wrote {map_csv.name}")

    fig1_gantt(by_run, order, wf_label)
    fig2_per_wf_bias(by_run, summary, order, wf_label)
    fig3_mape_context(by_run, summary)
    fig4_cost(modelled, summary, order, wf_label)
    print("Done.")


if __name__ == "__main__":
    main()
