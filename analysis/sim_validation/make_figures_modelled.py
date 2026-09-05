"""
Generate figures from the modelled dataset.

Reads:  ./modelled/{modelled_workflows.csv, calibration_factors.csv,
                    bootstrap_envelope.csv, case_a_envelope.csv}
Writes: ./modelled/figures/*.png

The existing ./out/figures/ from make_figures.py is preserved unchanged.
"""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

HERE = Path(__file__).parent
SRC = HERE / "modelled"
FIG = SRC / "figures"
FIG.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 11,
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

CASE_A_INFRA = "infra_A"
CASE_A_SIM   = "sim_A"
CASE_B_INFRA = ["infra_B_r1", "infra_B_r2", "infra_B_r3"]
CASE_B_SIM   = "sim_B"

def load_modelled():
    rows = []
    by_run = defaultdict(dict)
    with (SRC / "modelled_workflows.csv").open() as f:
        for r in csv.DictReader(f):
            for k in ("duration_raw_s","duration_modelled_s","cost_per_sec_usd",
                      "cost_raw_usd","cost_modelled_usd","duration_calibrated_s"):
                r[k] = float(r[k]) if r[k] not in ("","None") else None
            r["broken_record"] = int(r["broken_record"])
            by_run[r["run_id"]][r["workflow_id"]] = r
            rows.append(r)
    return rows, by_run

def load_csv(p):
    out = []
    with p.open() as f:
        for r in csv.DictReader(f):
            for k, v in list(r.items()):
                if k == "workflow_id" or k == "resource_family" or k == "note": continue
                try: r[k] = float(v)
                except: pass
            out.append(r)
    return out

def label(wid):
    return wid[5:13]


# ---- Fig M1: Case B per-workflow envelope vs calibrated sim ---------------
def figM1_case_b(by_run, boot_env):
    """Per-workflow Case B: infra range, bootstrap CI, raw and calibrated sim."""
    sim_B = by_run[CASE_B_SIM]
    infra_B_r2 = by_run["infra_B_r2"]  # representative for ordering and family
    order = sorted(infra_B_r2.keys(),
                   key=lambda w: float(infra_B_r2[w]["duration_modelled_s"] or 0))
    boot_by_wf = {r["workflow_id"]: r for r in boot_env}

    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = np.arange(len(order))

    for xi, wid in zip(x, order):
        env = boot_by_wf.get(wid)
        if not env: continue
        # Infra range bar (min to max across 3 replicates) — light blue
        ax.plot([xi, xi], [env["raw_min"], env["raw_max"]],
                color="#1565c0", linewidth=10, alpha=0.20, solid_capstyle="butt")
        # Bootstrap 95% CI on mean — solid blue
        ax.plot([xi, xi], [env["boot_lo_95"], env["boot_hi_95"]],
                color="#1565c0", linewidth=4, alpha=0.6, solid_capstyle="butt")
        # Mean tick
        ax.plot([xi-0.18, xi+0.18], [env["raw_mean"], env["raw_mean"]],
                color="#1565c0", linewidth=2)

        # Sim markers
        s = sim_B.get(wid)
        if s and s["duration_raw_s"] is not None:
            # raw sim — hollow red
            ax.plot(xi, s["duration_raw_s"], marker="D",
                    markerfacecolor="white", markeredgecolor="#c62828",
                    markersize=9, markeredgewidth=1.3, zorder=3)
        if s and s["duration_calibrated_s"] is not None:
            # calibrated sim — solid red
            ax.plot(xi, s["duration_calibrated_s"], marker="D",
                    color="#c62828", markersize=10,
                    markeredgecolor="black", markeredgewidth=0.6, zorder=4)

    ax.set_xticks(x)
    ax.set_xticklabels([label(w) for w in order], fontsize=9, rotation=20, ha="right")
    ax.set_ylabel("Duration (s)")
    ax.set_title("Fig M1. Case B: per-workflow infra envelope vs raw and calibrated sim_B\n"
                 "(light blue = infra range across 3 replicates; "
                 "solid blue = bootstrap 95% CI on infra mean)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.set_yscale("log")  # wf2 spans 750-3000 so log helps

    handles = [
        mpatches.Patch(color="#1565c0", alpha=0.20, label="infra range (3 replicates)"),
        mpatches.Patch(color="#1565c0", alpha=0.6,  label="bootstrap 95% CI on infra mean"),
        plt.Line2D([0],[0], marker="D", color="w", markerfacecolor="white",
                   markeredgecolor="#c62828", markersize=9, markeredgewidth=1.3,
                   label="raw sim_B"),
        plt.Line2D([0],[0], marker="D", color="w", markerfacecolor="#c62828",
                   markersize=10, markeredgecolor="black", label="calibrated sim_B"),
    ]
    ax.legend(handles=handles, loc="upper left", framealpha=0.95)

    out = FIG / "figM1_case_b_envelope.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")


# ---- Fig M2: Case A per-workflow envelope vs calibrated sim ---------------
def figM2_case_a(by_run, ca_env):
    sim_A = by_run[CASE_A_SIM]
    infra_A = by_run[CASE_A_INFRA]
    order = sorted(infra_A.keys(),
                   key=lambda w: float(infra_A[w]["duration_modelled_s"] or 0))
    env_by_wf = {r["workflow_id"]: r for r in ca_env}

    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = np.arange(len(order))

    for xi, wid in zip(x, order):
        env = env_by_wf.get(wid)
        if not env: continue
        # Constructed 95% envelope
        ax.plot([xi, xi], [env["envelope_lo_95"], env["envelope_hi_95"]],
                color="#1565c0", linewidth=4, alpha=0.6, solid_capstyle="butt")
        # Case A point
        ax.plot([xi-0.18, xi+0.18], [env["case_a_point"], env["case_a_point"]],
                color="#1565c0", linewidth=2)
        # If broken-record, mark it
        infra_w = infra_A.get(wid)
        if infra_w and infra_w["broken_record"]:
            ax.annotate("modelled\n(broken)", xy=(xi, env["case_a_point"]),
                        xytext=(xi+0.20, env["case_a_point"]*1.1),
                        fontsize=7, color="#c62828",
                        arrowprops=dict(arrowstyle="->", color="#c62828", lw=0.8))

        s = sim_A.get(wid)
        if s and s["duration_raw_s"] is not None:
            ax.plot(xi, s["duration_raw_s"], marker="D",
                    markerfacecolor="white", markeredgecolor="#c62828",
                    markersize=9, markeredgewidth=1.3, zorder=3)
        if s and s["duration_calibrated_s"] is not None:
            ax.plot(xi, s["duration_calibrated_s"], marker="D",
                    color="#c62828", markersize=10,
                    markeredgecolor="black", markeredgewidth=0.6, zorder=4)

    ax.set_xticks(x)
    ax.set_xticklabels([label(w) for w in order], fontsize=9, rotation=20, ha="right")
    ax.set_ylabel("Duration (s)")
    ax.set_title("Fig M2. Case A: per-workflow constructed envelope vs raw and calibrated sim_A\n"
                 "(blue = 95% envelope from cross-case noise transfer; tick = Case A observed/modelled point)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    handles = [
        mpatches.Patch(color="#1565c0", alpha=0.6,
                       label="constructed 95% envelope (Case B CV transfer)"),
        plt.Line2D([0],[0], marker="D", color="w", markerfacecolor="white",
                   markeredgecolor="#c62828", markersize=9, markeredgewidth=1.3,
                   label="raw sim_A"),
        plt.Line2D([0],[0], marker="D", color="w", markerfacecolor="#c62828",
                   markersize=10, markeredgecolor="black", label="calibrated sim_A"),
    ]
    ax.legend(handles=handles, loc="upper left", framealpha=0.95)

    out = FIG / "figM2_case_a_envelope.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")


# ---- Fig M3: Calibration before/after, per family --------------------------
def figM3_calibration(by_run, factors):
    """For each family used in Case B, show raw bias and post-calibration bias
    distributions (one bar pair per family)."""
    fig, ax = plt.subplots(figsize=(8.5, 4.8))

    fam_data = {f["resource_family"]: f for f in factors}
    families = ["on-prem", "hpc7a.24xlarge", "c6i.16xlarge", "c6i.32xlarge"]
    families = [f for f in families if f in fam_data]

    raw_med  = [fam_data[f]["median_bias_pct"] for f in families]
    raw_mean = [fam_data[f]["mean_bias_pct"]   for f in families]
    # Post-calibration: per definition median goes to 0 (since calibration uses median)
    cal = [0.0 for _ in families]

    x = np.arange(len(families))
    w = 0.28
    ax.bar(x - w, raw_med,  width=w, color=[FAM_COLOR.get(f,"#888") for f in families],
           alpha=0.5, edgecolor="black", linewidth=0.5, label="raw median bias")
    ax.bar(x,     raw_mean, width=w, color=[FAM_COLOR.get(f,"#888") for f in families],
           alpha=0.85, edgecolor="black", linewidth=0.5, hatch="//",
           label="raw mean bias (sensitivity)")
    ax.bar(x + w, cal,      width=w, color="#9e9e9e",
           alpha=0.7, edgecolor="black", linewidth=0.5, label="post-calibration (median)")

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(families, rotation=15, ha="right")
    ax.set_ylabel("Sim bias relative to infra (%)")
    ax.set_title("Fig M3. Per-family bias: raw (median, mean) and post-calibration\n"
                 "(median calibration centres each family's sim output on the infra median)")
    ax.legend(loc="upper right", framealpha=0.95)
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    out = FIG / "figM3_calibration.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")


# ---- Fig M4: Cost comparison (per workflow, both cases) -------------------
def figM4_cost(by_run):
    """Side-by-side bars for per-workflow cost in Case A and Case B (median
    across infra replicates, vs sim)."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    def plot_panel(ax, infra_runs, sim_run, title):
        # collect per-workflow infra cost across runs (median) and sim cost
        infra_wfs = set()
        for r in infra_runs:
            infra_wfs |= set(by_run.get(r, {}).keys())
        order = sorted(infra_wfs)
        infra_costs = []
        sim_costs   = []
        for wid in order:
            ic = []
            for r in infra_runs:
                w = by_run.get(r, {}).get(wid)
                if w and w["cost_modelled_usd"] is not None:
                    ic.append(w["cost_modelled_usd"])
            infra_costs.append(np.median(ic) if ic else None)
            sw = by_run.get(sim_run, {}).get(wid)
            sim_costs.append(sw["cost_modelled_usd"] if sw and sw["cost_modelled_usd"] is not None else None)
        x = np.arange(len(order))
        w = 0.4
        ax.bar(x - w/2, [c if c else 0 for c in infra_costs], width=w,
               color="#1565c0", edgecolor="black", linewidth=0.4,
               label="infra (median across replicates)" if len(infra_runs) > 1 else "infra")
        ax.bar(x + w/2, [c if c else 0 for c in sim_costs], width=w,
               color="#c62828", edgecolor="black", linewidth=0.4, label="sim")
        ax.set_xticks(x)
        ax.set_xticklabels([label(w) for w in order], fontsize=8, rotation=30, ha="right")
        ax.set_ylabel("Cost (USD)")
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        ax.legend(loc="upper left", fontsize=8)

    plot_panel(axes[0], [CASE_A_INFRA], CASE_A_SIM, "(a) Case A — per-workflow cost")
    plot_panel(axes[1], CASE_B_INFRA,   CASE_B_SIM, "(b) Case B — per-workflow cost (infra median across 3 replicates)")
    fig.suptitle("Fig M4. Per-workflow cost (modelled from durations × AWS rates)",
                 fontsize=12)
    fig.tight_layout()

    out = FIG / "figM4_cost.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")


def figM5_all_workflows(summary_path):
    """Per-workflow bias bars for all 10 workflows in each case (no
    exclusion). Two stacked subplots; bars colored by tolerance band;
    swap workflows hatched."""
    rows = list(csv.DictReader(summary_path.open()))
    for r in rows:
        r["bias_pct"] = float(r["bias_pct"])
        r["abs_bias_pct"] = float(r["abs_bias_pct"])
        r["is_swap"] = int(r["is_swap"])
    rows.sort(key=lambda r: r["abs_bias_pct"])

    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)

    def color_for(abs_b):
        if abs_b <= 10:  return "#2e7d32"
        if abs_b <= 20:  return "#9e9d24"
        if abs_b <= 35:  return "#f9a825"
        if abs_b <= 70:  return "#ef6c00"
        return "#c62828"

    fig, axes = plt.subplots(2, 1, figsize=(13, 9), constrained_layout=True)
    for ax, case_label in zip(axes, ["A", "B"]):
        rs = by_case[case_label]
        y = np.arange(len(rs))
        labels = [f"{r['workflow_id'][5:13]} ({r['sim_resource']} → {r['infra_resource']})"
                  + (" [swap]" if r["is_swap"] else "") for r in rs]
        biases = [r["bias_pct"] for r in rs]
        colors = [color_for(r["abs_bias_pct"]) for r in rs]
        bars = ax.barh(y, biases, color=colors, edgecolor="black", linewidth=0.5,
                       height=0.7,
                       hatch=["///" if r["is_swap"] else "" for r in rs])
        # Tolerance band reference lines
        for tol in [10, 20, 35]:
            ax.axvline( tol, color="#888", linestyle=":", linewidth=0.8, alpha=0.6)
            ax.axvline(-tol, color="#888", linestyle=":", linewidth=0.8, alpha=0.6)
        ax.axvline(0, color="black", linewidth=1)
        for yi, b in zip(y, biases):
            offset = 3 if b >= 0 else -3
            ha = "left" if b >= 0 else "right"
            ax.text(b + offset, yi, f"{b:+.1f}%", va="center", ha=ha, fontsize=9)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8.5)
        ax.invert_yaxis()
        ax.set_xlabel("bias = (sim − infra)/infra (%)")
        n = len(rs)
        med = statistics.median([r["abs_bias_pct"] for r in rs])
        ax.set_title(f"({case_label.lower()}) Case {case_label}: per-workflow bias for all {n} workflows  "
                     f"(median |bias| = {med:.1f}%; dotted lines = ±10/20/35%; "
                     "swap workflows hatched)",
                     fontsize=10)
        ax.grid(axis="x", linestyle=":", alpha=0.3)
    fig.suptitle("Fig M5. All-10-workflows per-workflow bias (no exclusions, no calibration)",
                 fontsize=12)

    # Color legend
    from matplotlib.patches import Patch
    leg = [Patch(facecolor="#2e7d32", label="|bias| ≤ 10%"),
           Patch(facecolor="#9e9d24", label="|bias| ≤ 20%"),
           Patch(facecolor="#f9a825", label="|bias| ≤ 35%"),
           Patch(facecolor="#ef6c00", label="|bias| ≤ 70%"),
           Patch(facecolor="#c62828", label="|bias| > 70%")]
    fig.legend(handles=leg, loc="lower right", ncol=5, frameon=False,
               bbox_to_anchor=(0.98, -0.02))
    out = FIG / "figM5_all_workflows.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.name}")


def main():
    rows, by_run = load_modelled()
    factors = load_csv(SRC / "calibration_factors.csv")
    boot_env = load_csv(SRC / "bootstrap_envelope.csv")
    ca_env = load_csv(SRC / "case_a_envelope.csv")

    print(f"Generating figures into {FIG}/")
    figM1_case_b(by_run, boot_env)
    figM2_case_a(by_run, ca_env)
    figM3_calibration(by_run, factors)
    figM4_cost(by_run)
    figM5_all_workflows(SRC / "all_workflows_summary.csv")
    print("Done.")

if __name__ == "__main__":
    main()
