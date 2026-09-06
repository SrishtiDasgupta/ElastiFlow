"""
Thesis-grade figures for the BMW sim-validation chapter.

Generates 4 figures into modelled/figures/thesis/:
  thesis_fig1_gantt.png        - Two-panel Gantt overlay with swap annotation
  thesis_fig2_per_wf_bias.png  - 3-bucket per-workflow bias bar chart
  thesis_fig3_mape_context.png - MAPE vs infra noise floor
  thesis_fig4_cost.png         - Per-wf + aggregate cost with swap correction

Excludes infra_B_r1 (failed run). All other figures (out/figures/*, modelled/figures/*)
remain untouched.

Phase C (2026-09-06): thesis_fig1_gantt.png and thesis_fig4_cost.png are written by
make_fig1_gantt.py and make_fig4_cost.py, their writers of record (the latter reproduces
the submitted figure byte for byte); this script's copies of those two figures were
removed, see thesis/figures.yaml. It still writes thesis_fig2_per_wf_bias.png and
thesis_fig3_mape_context.png.
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

    fig2_per_wf_bias(by_run, summary, order, wf_label)
    fig3_mape_context(by_run, summary)
    print("Done.")


if __name__ == "__main__":
    main()
