"""
Generate the 4 thesis-chapter fidelity figures from the parsed CSVs.

Outputs to ./out/figures/:
  fig1_gantt_overlay.png      - sim_v2 vs infra_v2 timeline alignment
  fig2_duration_scatter.png   - sim vs infra duration, colored by family
  fig3_family_bias.png        - per-family mean bias with range bars
  fig4_noise_floor.png        - infra-vs-infra range with sim overlay
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
OUT = HERE / "out"
FIG = OUT / "figures"
FIG.mkdir(exist_ok=True)

# ---- shared style ----------------------------------------------------------
plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
    "legend.fontsize": 9,
    "figure.dpi": 130,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
})

FAM_COLOR = {
    "on-prem":        "#2e7d32",
    "hpc7a.24xlarge": "#1565c0",
    "hpc7a.12xlarge": "#0288d1",
    "c6i.16xlarge":   "#ef6c00",
    "c6i.32xlarge":   "#c62828",
    "c7i.12xlarge":   "#6a1b9a",
}

def family_of(decision: str) -> str:
    if not decision: return "?"
    if "on-prem:" in decision and "reserved" not in decision and "on-demand" not in decision:
        return "on-prem"
    # decision format: "reserved:c6i.32xlargex1|on-demand:c6i.32xlargex1"
    # the count separator is a literal x followed by digits at the end of a
    # tier segment, so use non-greedy match terminated by x\d+(?=\||$).
    m = re.search(r"(?:reserved|on-demand):(.+?)x\d+(?=\||$)", decision)
    if not m: return "?"
    return m.group(1)

def short(uuid):
    return uuid[5:13]

# ---- load --------------------------------------------------------------------
def load_workflows():
    by_run = defaultdict(dict)
    for r in csv.DictReader((OUT/"workflows.csv").open()):
        for k in ("alloc_t","complete_t","free_t","duration_s"):
            r[k] = float(r[k]) if r[k] not in ("","None") else None
        by_run[r["run_id"]][r["workflow_id"]] = r
    return by_run

def load_durations():
    return list(csv.DictReader((OUT/"deviation_durations.csv").open()))

def load_runs():
    return list(csv.DictReader((OUT/"runs.csv").open()))

def load_noise():
    rows = []
    for r in csv.DictReader((OUT/"noise_floor.csv").open()):
        for k in ("mean_s","std_s","min_s","max_s","range_s","cv_pct"):
            r[k] = float(r[k]) if r[k] else None
        rows.append(r)
    return rows

# ---- Fig 1: Gantt overlay ---------------------------------------------------
def fig1_gantt(by_run):
    """Two-panel Gantt: infra_B_r2 (top) and sim_B (bottom)."""
    pairs = [("infra_B_r2", "Infra (Case B, replicate r2)"),
             ("sim_B",      "Simulator (Case B)")]
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True)

    # Use stable workflow order based on infra_B_r2's allocation time
    order = sorted(by_run["infra_B_r2"].keys(),
                   key=lambda w: by_run["infra_B_r2"][w]["alloc_t"] or 0)
    wf_to_label = {w: f"wf{i}" for i, w in enumerate(order)}

    for ax, (run_id, title) in zip(axes, pairs):
        run = by_run[run_id]
        # Determine origin time so both panels use [0, makespan] axis
        t0 = min(r["alloc_t"] for r in run.values() if r["alloc_t"] is not None)
        for i, wfid in enumerate(order):
            r = run.get(wfid)
            if not r or r["alloc_t"] is None: continue
            end = r["complete_t"] if r["complete_t"] is not None else r["free_t"]
            if end is None: continue
            start = r["alloc_t"] - t0
            width = end - r["alloc_t"]
            fam = family_of(r["alloc_decision"])
            color = FAM_COLOR.get(fam, "#888888")
            ax.barh(i, width, left=start, color=color, edgecolor="black",
                    linewidth=0.4, height=0.7)
            ax.text(start + width/2, i, wf_to_label[wfid],
                    ha="center", va="center", fontsize=8, color="white",
                    fontweight="bold")
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels([wf_to_label[w] for w in order])
        ax.set_title(f"{title}: workflow execution timeline")
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle=":", alpha=0.5)
    axes[-1].set_xlabel("Seconds since first allocation")

    # Legend (only families actually used in this figure, to avoid clutter)
    used_fams = set()
    for run in pairs:
        for r in by_run[run[0]].values():
            used_fams.add(family_of(r["alloc_decision"]))
    handles = [mpatches.Patch(color=FAM_COLOR.get(f, "#888"), label=f)
               for f in sorted(used_fams) if f in FAM_COLOR]

    fig.suptitle("Fig 1. Sim vs Infra timeline alignment (10 BMW workflows, Case B config)",
                 fontsize=12, y=1.00)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles),
               frameon=False, bbox_to_anchor=(0.5, 0.0))
    out = FIG / "fig1_gantt_overlay.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")

# ---- Fig 2: Duration scatter ------------------------------------------------
def fig2_scatter(durations):
    """Scatter of sim vs infra duration. Use B_r2 and B_r3 as the two reference
    pairs against the canonical sim_B."""
    fig, ax = plt.subplots(figsize=(6.5, 6))

    by_fam = defaultdict(lambda: {"x": [], "y": [], "labels": []})
    for r in durations:
        if r["pair"] not in ("B_r2", "B_r3"): continue
        if not r["same_pool"] or r["same_pool"] == "0":
            # decision swap -- not a clean duration comparison; skip in scatter
            # (but draw separately as gray X marks)
            by_fam["__swap__"]["x"].append(float(r["ref_duration_s"]))
            by_fam["__swap__"]["y"].append(float(r["cmp_duration_s"]))
            by_fam["__swap__"]["labels"].append(short(r["workflow_id"]))
            continue
        fam = r["ref_pool"]
        if fam.startswith("cloud:"):
            fam = fam[len("cloud:"):]
        by_fam[fam]["x"].append(float(r["ref_duration_s"]))
        by_fam[fam]["y"].append(float(r["cmp_duration_s"]))
        by_fam[fam]["labels"].append(short(r["workflow_id"]))

    # Diagonal reference
    all_vals = []
    for d in by_fam.values():
        all_vals.extend(d["x"]); all_vals.extend(d["y"])
    lo, hi = 0, max(all_vals) * 1.05
    ax.plot([lo, hi], [lo, hi], color="black", linestyle="--", linewidth=1,
            label="perfect agreement", alpha=0.6)
    # ±20% bands
    ax.fill_between([lo, hi], [lo*0.8, hi*0.8], [lo*1.2, hi*1.2],
                     color="gray", alpha=0.10, label="±20% band")

    for fam, d in by_fam.items():
        if fam == "__swap__":
            ax.scatter(d["x"], d["y"], marker="x", s=70,
                       color="#888888", label="decision swap (excluded)",
                       linewidths=2)
        else:
            ax.scatter(d["x"], d["y"], s=70, alpha=0.85,
                       color=FAM_COLOR.get(fam, "#444444"),
                       edgecolor="black", linewidth=0.5,
                       label=fam)

    ax.set_xlabel("Infra duration (s)")
    ax.set_ylabel("Simulator duration (s)")
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.set_aspect("equal")
    ax.set_title("Fig 2. Sim vs infra per-workflow duration\n(sim_B vs infra_B_r2 / infra_B_r3)")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.grid(linestyle=":", alpha=0.5)

    out = FIG / "fig2_duration_scatter.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")

# ---- Fig 3: Per-family bias --------------------------------------------------
def fig3_bias(durations):
    """Mean bias per family with min/max range bars."""
    by_fam = defaultdict(list)
    # Case A is excluded: its infra log has two broken records (workflows freed
    # after 30s, the polling interval, due to a real-infra failure) that
    # produce nonsense bias values. Uncalibrated sim is also excluded -- the
    # canonical sim is sim_B and that's what should drive the bias bars.
    for r in durations:
        if r["pair"] == "A": continue
        if "uncalib" in r["pair"]: continue
        if r["ref_run"].startswith("infra") and r["cmp_run"].startswith("sim") \
                and r["same_pool"] == "1":
            ref = float(r["ref_duration_s"])
            cmp_ = float(r["cmp_duration_s"])
            if ref == 0: continue
            bias = 100 * (cmp_ - ref) / ref
            fam = r["ref_pool"]
            if fam.startswith("cloud:"):
                fam = fam[len("cloud:"):]
            by_fam[fam].append(bias)

    # Order: on-prem, hpc7a.24, c6i.16, c6i.32, hpc7a.12
    order = ["on-prem", "hpc7a.24xlarge", "hpc7a.12xlarge",
             "c6i.16xlarge", "c6i.32xlarge"]
    order = [f for f in order if f in by_fam]

    fig, ax = plt.subplots(figsize=(8, 4.8))
    means = [statistics.mean(by_fam[f]) for f in order]
    mins  = [min(by_fam[f]) for f in order]
    maxs  = [max(by_fam[f]) for f in order]
    ns    = [len(by_fam[f]) for f in order]

    x = np.arange(len(order))
    colors = [FAM_COLOR.get(f, "#888") for f in order]

    ax.bar(x, means, color=colors, edgecolor="black", linewidth=0.5,
           width=0.6, alpha=0.9)

    # y-axis truncation: cap at ±75% so the c6i bias is legible.
    # Whiskers that exceed the cap are drawn to the cap and annotated.
    Y_CAP = 75
    for xi, m, lo, hi in zip(x, means, mins, maxs):
        plot_lo = max(lo, -Y_CAP)
        plot_hi = min(hi, Y_CAP)
        ax.plot([xi, xi], [plot_lo, plot_hi], color="black", linewidth=1.5)
        ax.plot([xi-0.08, xi+0.08], [plot_lo, plot_lo], color="black", linewidth=1.5)
        if hi <= Y_CAP:
            ax.plot([xi-0.08, xi+0.08], [plot_hi, plot_hi], color="black", linewidth=1.5)
        else:
            # arrow indicating truncated upper whisker, with actual value
            ax.annotate(f"max: +{hi:.0f}%",
                        xy=(xi, Y_CAP), xytext=(xi+0.05, Y_CAP-8),
                        fontsize=8, color="#c62828", ha="left",
                        arrowprops=dict(arrowstyle="->", color="#c62828",
                                        lw=1.0, shrinkA=0, shrinkB=0))

    # mean labels
    for xi, m, n in zip(x, means, ns):
        offset = 4 if m >= 0 else -4
        va = "bottom" if m >= 0 else "top"
        ax.text(xi, m + offset, f"{m:+.1f}%\n(n={n})",
                ha="center", va=va, fontsize=9)

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(order, rotation=15, ha="right")
    ax.set_ylabel("Sim bias relative to infra: (sim − infra) / infra (%)")
    ax.set_title("Fig 3. Simulator timing bias by resource family (Case B, calibrated sim_B)\n"
                 "(bars = mean; whiskers = min/max across paired comparisons)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.set_ylim(-Y_CAP, Y_CAP)
    # Footnote about the truncated outlier
    ax.text(0.01, -0.18,
            "Note: on-prem upper whisker truncated. The +297% maximum is from "
            "workflow wf2, a co-tenancy event in which the same workflow on the "
            "same on-prem node ran ~4× slower in V2/V3 than in V_updated\n"
            "(see DEVIATION_REPORT.md §B.3). Excluding wf2, the on-prem upper whisker "
            "falls to +21%.",
            transform=ax.transAxes, fontsize=8, color="#555", ha="left", va="top")

    out = FIG / "fig3_family_bias.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")

# ---- Fig 4: Noise floor + sim overlay ---------------------------------------
def fig4_noise_floor(by_run, noise):
    """Per-workflow infra range (V_updated, V2, V3) with sim_v2 / sim_v2_replica
    overlaid. Two subplots: on-prem workflows (top) and cloud workflows (bottom),
    each with its own y-scale so the c6i bias is legible.

    The wf2 outlier (range 750-3000s) is annotated in the on-prem panel rather
    than allowed to dominate the y-scale.
    """
    # Stable wf order based on infra_B_r2 alloc time
    order_uuids = sorted(by_run["infra_B_r2"].keys(),
                         key=lambda w: by_run["infra_B_r2"][w]["alloc_t"] or 0)
    labels_all = {w: f"wf{i}" for i, w in enumerate(order_uuids)}

    noise_by_wf = {r["workflow_id"]: r for r in noise}

    # Partition workflows by family (using infra_B_r2's allocation as ground truth)
    onprem_uuids = [w for w in order_uuids
                    if family_of(by_run["infra_B_r2"][w]["alloc_decision"]) == "on-prem"]
    cloud_uuids  = [w for w in order_uuids
                    if family_of(by_run["infra_B_r2"][w]["alloc_decision"]) != "on-prem"]
    # wf2 is the co-tenancy outlier; cap the on-prem y-axis below its 3000s max
    # but keep the workflow in the panel with a truncated bar + annotation
    WF2_PREFIX = "test-a03a8e35"
    ONPREM_YCAP = 1300  # comfortably above all non-wf2 on-prem points

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)

    def draw_panel(ax, uuids, ycap=None, panel_label=""):
        x = np.arange(len(uuids))
        for xi, wfid in zip(x, uuids):
            n = noise_by_wf.get(wfid)
            if not n: continue
            lo, hi, mean = n["min_s"], n["max_s"], n["mean_s"]
            plot_hi = hi if ycap is None else min(hi, ycap)
            ax.plot([xi, xi], [lo, plot_hi], color="#1565c0", linewidth=10,
                    alpha=0.35, solid_capstyle="butt")
            ax.plot([xi-0.20, xi+0.20], [mean, mean] if (ycap is None or mean<=ycap) else [ycap*0.99, ycap*0.99],
                    color="#1565c0", linewidth=2)
            # truncation annotation on bar
            if ycap is not None and hi > ycap:
                ax.annotate(f"max {hi:.0f}s\n(co-tenancy)",
                            xy=(xi, ycap), xytext=(xi+0.18, ycap*0.85),
                            fontsize=8, color="#c62828", ha="left",
                            arrowprops=dict(arrowstyle="->", color="#c62828", lw=1.0))
            # canonical sim_B marker
            sim_r = by_run["sim_B"].get(wfid)
            if sim_r and sim_r["duration_s"] is not None:
                v = sim_r["duration_s"]
                if ycap is None or v <= ycap:
                    ax.plot(xi, v, marker="D", color="#c62828", markersize=10,
                            markeredgecolor="black", markeredgewidth=0.6, zorder=3)
            # uncalibrated sim_B as a sensitivity reference (smaller, hollow)
            sim_unc = by_run["sim_B_uncalib"].get(wfid)
            if sim_unc and sim_unc["duration_s"] is not None:
                v = sim_unc["duration_s"]
                if ycap is None or v <= ycap:
                    ax.plot(xi, v, marker="o", markerfacecolor="white",
                            markeredgecolor="#c62828", markersize=8,
                            markeredgewidth=1.2, zorder=2)
        ax.set_xticks(x)
        # Label "wfN (instance type)"
        xlabels = []
        for wfid in uuids:
            base = labels_all[wfid]
            fam = family_of(by_run["infra_B_r2"][wfid]["alloc_decision"])
            xlabels.append(f"{base}\n{fam}")
        ax.set_xticklabels(xlabels, fontsize=9)
        ax.set_ylabel("Duration (s)")
        if ycap is not None:
            ax.set_ylim(0, ycap)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        ax.set_title(panel_label, fontsize=10, loc="left")

    draw_panel(axes[0], onprem_uuids, ycap=ONPREM_YCAP,
               panel_label="(a) On-prem workflows — sim_B sits inside or close to the infra range")
    draw_panel(axes[1], cloud_uuids, ycap=None,
               panel_label="(b) Cloud workflows — sim_B (filled red) is close to infra range; "
                           "sim_B_uncalib (hollow) shown as calibration sensitivity")

    fig.suptitle("Fig 4. Sim vs infra-vs-infra noise floor (Case B, per workflow)",
                 fontsize=12)

    legend_handles = [
        mpatches.Patch(color="#1565c0", alpha=0.35,
                       label="infra range (infra_B_r1, _r2, _r3)"),
        plt.Line2D([0],[0], marker="D", color="w", markerfacecolor="#c62828",
                   markersize=10, markeredgecolor="black", label="sim_B (canonical)"),
        plt.Line2D([0],[0], marker="o", color="w", markerfacecolor="white",
                   markeredgecolor="#c62828", markersize=8, markeredgewidth=1.2,
                   label="sim_B_uncalib (sensitivity)"),
    ]
    fig.legend(handles=legend_handles, loc="upper right", ncol=3, frameon=False,
               bbox_to_anchor=(0.99, 0.97))

    out = FIG / "fig4_noise_floor.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")

# ---- Fig 5: Aggregate metrics ------------------------------------------------
def fig5_aggregates(runs):
    """Grouped bars per aggregate metric (makespan, cost, wait, utilization)
    across the matched-config infra runs and the sim runs they pair with."""
    # Case B replicates only (Case A is excluded as caveated).
    # Show canonical sim_B prominently and sim_B_uncalib as a sensitivity bar.
    runs_to_show = ["infra_B_r1", "infra_B_r2", "infra_B_r3",
                    "sim_B", "sim_B_uncalib"]
    pretty = {
        "infra_B_r1":    "infra_B\nr1",
        "infra_B_r2":    "infra_B\nr2",
        "infra_B_r3":    "infra_B\nr3",
        "sim_B":         "sim_B\n(canonical)",
        "sim_B_uncalib": "sim_B\n(uncalib)",
    }
    color = {
        "infra_B_r1":    "#90caf9",
        "infra_B_r2":    "#1565c0",
        "infra_B_r3":    "#0d47a1",
        "sim_B":         "#c62828",
        "sim_B_uncalib": "#ef9a9a",
    }

    # Filter to the rows we want
    by_id = {r["run_id"]: r for r in runs}
    used = [r for rid in runs_to_show if (r := by_id.get(rid))]

    metrics = [
        ("avg_makespan", "Makespan (s)",         "Average makespan per workflow"),
        ("avg_cost",     "Cost ($)",             "Average cost per workflow"),
        ("avg_wait",     "Wait time (s)",        "Average scheduling wait per workflow"),
        ("avg_util",     "Utilization (cores)",  "Average resource utilization"),
    ]

    fig, axes = plt.subplots(1, 4, figsize=(13, 4), constrained_layout=True)

    for ax, (key, ylab, title) in zip(axes, metrics):
        vals = [float(r[key]) if r[key] not in ("", "None", None) else None
                for r in used]
        x = np.arange(len(used))
        colors = [color[r["run_id"]] for r in used]
        bars = ax.bar(x, vals, color=colors, edgecolor="black",
                      linewidth=0.4, width=0.7, alpha=0.95)
        # Value labels
        for xi, v in zip(x, vals):
            if v is None: continue
            fmt = f"{v:.2f}" if key == "avg_cost" else f"{v:.1f}"
            ax.text(xi, v, fmt, ha="center", va="bottom", fontsize=8.5)

        # Shade infra mean band so the comparison is visual
        infra_vals = [v for v, r in zip(vals, used)
                      if r["env"] == "infra" and v is not None]
        if infra_vals:
            mean = sum(infra_vals) / len(infra_vals)
            lo, hi = min(infra_vals), max(infra_vals)
            ax.axhspan(lo, hi, color="#1565c0", alpha=0.08, zorder=0)
            ax.axhline(mean, color="#1565c0", linewidth=1, linestyle="--",
                       alpha=0.6)

        ax.set_xticks(x)
        ax.set_xticklabels([pretty[r["run_id"]] for r in used], fontsize=8)
        ax.set_ylabel(ylab)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        # Tight headroom for value labels
        ymax = max(v for v in vals if v is not None)
        ax.set_ylim(0, ymax * 1.18)

    fig.suptitle("Fig 5. Aggregate run metrics — sim_B (canonical) vs three Case B infra replicates\n"
                 "(blue band = infra range; dashed = infra mean; sim_B_uncalib shown for "
                 "calibration sensitivity)",
                 fontsize=11)
    out = FIG / "fig5_aggregate_metrics.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")

# ---- main --------------------------------------------------------------------
def main():
    by_run = load_workflows()
    durations = load_durations()
    runs = load_runs()
    noise = load_noise()

    print(f"Generating figures into {FIG}/")
    fig1_gantt(by_run)
    fig2_scatter(durations)
    fig3_bias(durations)
    fig4_noise_floor(by_run, noise)
    fig5_aggregates(runs)
    print("Done.")

if __name__ == "__main__":
    main()
