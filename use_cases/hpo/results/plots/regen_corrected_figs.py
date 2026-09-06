"""Regenerate HPO figures whose inputs changed under the SeisSol-canonical
correction pass:

  * BMR computed completed-only (was: all-workflow including non-completed
    partial-cost overruns)
  * Utilisation includes on-demand in the denominator (was: reserved-only)
  * CPR figure dropped entirely (ill-conditioned at OMR -> 1)
  * Cross-workload Pareto re-rendered on shared axes
    (per-workflow USD cost vs OMR)

Figures regenerated (in place, overwriting):
  HPO_11_util_4corners_n7 — total-cap reference line added
  CROSS_pareto_comparison — both panels on (per-wf cost USD, OMR)

Phase C (2026-09-06): this script's writers of 02c_budget_misses_bar,
02g_utilization_time and 04_paired_diff were removed. None of them reproduced
the submitted figure; the writers of record are generate_thesis_plots.py (02c,
02g) and regen_paired_n7.py (04), see thesis/figures.yaml. Neither figure
written here is in the dissertation.

Figures REMOVED:
  02f_cpr_bar.{pdf,png}

Figures left untouched (cost rates unchanged within HPO):
  01_cost_vs_n, 01b_cost_stack, 02h_tier_zoom_elastic_edf_n7,
  HPO_06_pareto_trajectory, 02b_misses_bar, 02_misses_vs_n, 02d, 02e,
  03_sumflow_vs_n, 05_per_wf_n7, HPO_08_*
"""

import json
import statistics as st
from collections import defaultdict
from pathlib import Path
from statistics import mean as st_mean, stdev as st_stdev

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
HPO_JSON = HERE / "total_cost_per_run.json"
PLAIN_JSON = HERE.parents[3] / "use_cases" / "seissol" / "results" / "plain_results_per_run.json"
OUT = HERE

# ----- HPO architectural constants (from sim_4corners_calibrated.py post-OD-correction) -----
# Deployed-fleet ceiling: on-prem slurm 4 + reserved cloud 4 (2 g4 + 2 g5) + on-demand cloud 6 (3 g4 + 3 g5) = 14 lanes total.
CLUSTER_CAP_TOTAL = 14         # 4 + 5 + 5
RESERVED_TOTAL = 8             # 4 + 2 + 2  (slurm + g4 reserved + g5 reserved)
OD_TOTAL = 6                   # 0 + 3 + 3

# ----- styling (matched to generate_thesis_plots.py) -----
plt.rcParams.update({
    "font.family": "sans-serif", "font.weight": "bold",
    "axes.labelweight": "bold", "axes.titleweight": "bold",
    "figure.titleweight": "bold", "figure.facecolor": "white",
    "axes.facecolor": "white", "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS, ANNOT_FS = 18, 16, 14, 12, 11
LEG_MARKER_SCALE, LEG_HANDLE_LEN = 1.6, 2.0

CORNERS = [("static", "edf"), ("static", "fcfs"),
           ("moldable", "edf"), ("moldable", "fcfs")]
KEY = {("static","edf"):"STAT EDF", ("static","fcfs"):"STAT FCFS",
       ("moldable","edf"):"MAL EDF", ("moldable","fcfs"):"MAL FCFS"}
LABEL = {"STAT EDF":"EDF-ST", "STAT FCFS":"FCFS-ST",
         "MAL EDF":"Elastic-EDF", "MAL FCFS":"Elastic-FCFS"}
short = {("static","edf"):"EDF-ST", ("static","fcfs"):"FCFS-ST",
         ("moldable","edf"):"E-EDF", ("moldable","fcfs"):"E-FCFS"}

PAL_BUDGET = {"STAT EDF":"#9333EA","STAT FCFS":"#84CC16",
              "MAL EDF":"#EA580C","MAL FCFS":"#0891B2"}
PAL_UTIL_TIME = {"STAT EDF":"#DC2626","STAT FCFS":"#F97316",
                 "MAL EDF":"#2563EB","MAL FCFS":"#10B981"}
LS = {"STAT EDF":"-","STAT FCFS":"--","MAL EDF":"-","MAL FCFS":"--"}
EDF_COL, FCFS_COL = "#0F766E", "#5EEAD4"

# ============================================================ corrected metrics
_total = json.loads(HPO_JSON.read_text())

def _completed_only_budget_misses(per_wf, makespan_s):
    """Count workflows that COMPLETED (completion_s < makespan_s) AND
    exceeded their budget. SeisSol-canonical BMR numerator definition."""
    return sum(
        1 for w in per_wf.values()
        if abs(w["completion_s"] - makespan_s) > 0.5 and w["budget_miss"] == 1
    )

def _all_tier_util(run):
    """Active lane-hours / provisioned lane-hours (provisioned = 12)."""
    active_h = (run["slurm_hrs"] + run["res_g4_hrs"] + run["res_g5_hrs"]
                + run["od_g4_hrs"] + run["od_g5_hrs"])
    provisioned_h = CLUSTER_CAP_TOTAL * (run["makespan_s"] / 3600.0)
    return active_h / provisioned_h if provisioned_h > 0 else 0.0

def _omr_per_run(per_wf, N):
    return sum(1 for w in per_wf.values() if w["miss"] or w["budget_miss"]) / N

def cell(N, m, o):
    return _total[f"N{N}_{m}_{o}"]

def per_run_corrected(N, m, o):
    out = []
    for r in cell(N, m, o)["per_run"]:
        per_wf = r["per_wf"]
        mks = r["makespan_s"]
        bmr_count = _completed_only_budget_misses(per_wf, mks)
        out.append(dict(
            cost_per_wf=r["cost_total"] / N,
            cost_total=r["cost_total"],
            dmr=r["misses"] / N,
            bmr_count=bmr_count,
            bmr=bmr_count / N,
            omr=_omr_per_run(per_wf, N),
            util_all=_all_tier_util(r),
            util_reserved=r["util_reserved"],   # for changelog
            makespan_s=mks, wait_per_wf=r["sum_wait_s"] / N,
        ))
    return out

def cell_mean_std(rs, key):
    vs = [r[key] for r in rs]
    return st_mean(vs), (st_stdev(vs) if len(vs) > 1 else 0.0)

# ===================================================== fig HPO_11 (corrected)
def plot_hpo_11_util_4corners_corrected():
    """Four-corner per-tier timeline at N=7 with total-cap reference."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharex=True, sharey=True)
    overall_xmax = 0.0
    HPO_LABEL = {"STAT EDF":"Static-EDF","STAT FCFS":"Static-FCFS",
                 "MAL EDF":"Elastic-EDF","MAL FCFS":"Elastic-FCFS"}
    corner_keys = [(KEY[c], c, HPO_LABEL[KEY[c]]) for c in CORNERS]
    for ax, (key, c, label) in zip(axes.flatten(), corner_keys):
        runs = cell(7, *c)["per_run"]
        if not runs:
            ax.text(0.5, 0.5, f"No data for {label}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=14, color="#888"); continue
        run = runs[0]
        ts = np.array(run["util_timeline_t"]) / 60.0
        tiers = run["util_timeline_tiers"]
        slurm = np.array(tiers["slurm"])
        res = np.array(tiers["reserved_cloud"])
        od = np.array(tiers["on_demand"])
        ax.plot(ts, slurm, label="On-premise (cap 4)",
                color="#10B981", linewidth=2.2)
        ax.plot(ts, res, label="Reserved cloud (cap 4)",
                color="#3B82F6", linewidth=2.2)
        ax.plot(ts, od, label="On-demand (cap 4)",
                color="#EF4444", linewidth=2.2)
        ax.fill_between(ts, 0, slurm, color="#10B981", alpha=0.15)
        ax.fill_between(ts, 0, res, color="#3B82F6", alpha=0.15)
        ax.fill_between(ts, 0, od, color="#EF4444", alpha=0.15)
        # NEW: total-cap reference at 12 lanes (all tiers)
        ax.axhline(CLUSTER_CAP_TOTAL, linestyle=":", color="#7F1D1D",
                   linewidth=1.4,
                   label=f"All-tier cap ({CLUSTER_CAP_TOTAL})")
        cost = run.get("cost_total", float("nan"))
        ax.set_title(f"{label}    (total cost: {cost:,.2f} USD)",
                     fontsize=TITLE_FS - 2, loc="left")
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
        in_use = (slurm + res + od) > 0
        if in_use.any():
            last_idx = np.where(in_use)[0][-1]
            overall_xmax = max(overall_xmax, float(ts[last_idx]))
    for ax in axes[-1, :]:
        ax.set_xlabel("Elapsed time (minutes)", fontsize=LABEL_FS)
    for ax in axes[:, 0]:
        ax.set_ylabel("Nodes in use", fontsize=LABEL_FS)
    for ax in axes.flatten():
        ax.set_xlim(0, overall_xmax * 1.05)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, 0.0),
               framealpha=0.92)
    fig.suptitle("Fleet utilisation over time — four corners at N = 7\n"
                 f"(one representative run per corner; all-tier capacity = {CLUSTER_CAP_TOTAL} nodes)",
                 fontsize=TITLE_FS, y=1.00)
    fig.tight_layout(rect=[0, 0.07, 1, 0.96])
    fig.savefig(OUT / "HPO_11_util_4corners_n7.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / "HPO_11_util_4corners_n7.pdf", bbox_inches="tight")
    plt.close(fig)
    print("  wrote HPO_11_util_4corners_n7 (all-tier cap line added)")

# ============================================ CROSS_pareto on shared axes
def plot_cross_pareto_shared_axes():
    """Both panels in (per-wf cost USD, OMR). SeisSol left @ N=400; HPO right @ N=7."""
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    # ----- LEFT: SeisSol N=400 -----
    pd_raw = json.loads(PLAIN_JSON.read_text())
    import re
    grp = defaultdict(list)
    for k, v in pd_raw.items():
        m = re.match(r"(.+)__N(\d+)__seed", k)
        if not m or v.get("_exit_code") != 0: continue
        pol, n = m.group(1), int(m.group(2))
        grp[(pol, n)].append(v)
    EUR = 1.10
    VARS = ["fcfs_static_r","fcfs_static_c","fcfs_moldable_r","fcfs_moldable_c",
            "edf_static_r","edf_static_c","edf_moldable_r","edf_moldable_c",
            "heft_static","rank_moldable_5050","rank_moldable_2575"]
    LBL = {"fcfs_static_r":"FCFS-ST$_r$","fcfs_static_c":"FCFS-ST",
           "fcfs_moldable_r":"Elastic-FCFS$_r$","fcfs_moldable_c":"Elastic-FCFS",
           "edf_static_r":"EDF-ST$_r$","edf_static_c":"EDF-ST",
           "edf_moldable_r":"Elastic-EDF$_r$","edf_moldable_c":"Elastic-EDF",
           "heft_static":"HEFT-ST",
           "rank_moldable_5050":"Elastic-Rank[50,50]",
           "rank_moldable_2575":"Elastic-Rank[25,75]"}
    pts_seis = {}
    for v in VARS:
        cells = grp[(v, 400)]
        if not cells: continue
        cost = st_mean(c["total_cost_eur"] for c in cells) * EUR / 400
        omr = st_mean(c["overall_miss_rate"] for c in cells)
        pts_seis[v] = (cost, omr)
    SEIS_FRONT = ["edf_moldable_c", "edf_static_c", "rank_moldable_5050"]
    SEIS_FCOL = {"edf_moldable_c":"#059669","edf_static_c":"#1D4ED8",
                 "rank_moldable_5050":"#D97706"}
    ax = axes[0]
    for v, (c, m) in pts_seis.items():
        if v in SEIS_FRONT:
            ax.scatter([c],[m], s=260, color=SEIS_FCOL[v], edgecolor="black",
                       linewidth=1.8, zorder=5)
            ax.annotate(LBL[v], (c, m), xytext=(0, 14),
                        textcoords="offset points",
                        ha="center", fontsize=11, fontweight="bold",
                        color=SEIS_FCOL[v])
        else:
            ax.scatter([c],[m], s=70, color="#9CA3AF",
                       edgecolor="#4B5563", linewidth=0.8, alpha=0.8, zorder=3)
            ax.annotate(LBL[v], (c, m), xytext=(6, 6),
                        textcoords="offset points",
                        fontsize=9, color="#374151", alpha=0.8)
    fpts = sorted([(pts_seis[v][0], pts_seis[v][1]) for v in SEIS_FRONT])
    ax.plot([p[0] for p in fpts], [p[1] for p in fpts],
            "k--", linewidth=1.8, alpha=0.7, zorder=2)
    ax.set_xlabel("$\\bar{\\gamma}$ (cost per workflow, USD)", fontsize=LABEL_FS)
    ax.set_ylabel("OMR  (budget OR deadline)", fontsize=LABEL_FS)
    ax.set_title("Plain SeisSol @ N = 400", fontsize=TITLE_FS - 2)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)

    # ----- RIGHT: HPO N=7 on the SAME axes (per-wf cost USD, OMR) -----
    HPO_COL = {"STAT EDF":"#B91C1C","STAT FCFS":"#F59E0B",
               "MAL EDF":"#1D4ED8","MAL FCFS":"#059669"}
    HPO_MARKER = {"STAT EDF":"o","STAT FCFS":"s","MAL EDF":"D","MAL FCFS":"^"}
    HPO_LABEL = {"STAT EDF":"Static-EDF","STAT FCFS":"Static-FCFS",
                 "MAL EDF":"Elastic-EDF","MAL FCFS":"Elastic-FCFS"}
    pts_hpo = {}
    errs_hpo = {}
    for c in CORNERS:
        rs = per_run_corrected(7, *c)
        cost_m, cost_s = cell_mean_std(rs, "cost_per_wf")
        omr_m, omr_s = cell_mean_std(rs, "omr")
        pts_hpo[KEY[c]] = (cost_m, omr_m)
        errs_hpo[KEY[c]] = (cost_s, omr_s)
    # HPO frontier on (cost, omr)
    hpo_front = []
    for k, p in pts_hpo.items():
        cc, oo = p
        dominated = any(
            (p2[0] <= cc and p2[1] <= oo and (p2[0] < cc or p2[1] < oo))
            for k2, p2 in pts_hpo.items() if k2 != k
        )
        if not dominated: hpo_front.append(k)
    ax = axes[1]
    OFFSET = {"STAT EDF":(8,8),"STAT FCFS":(8,8),
              "MAL EDF":(10,18),"MAL FCFS":(10,-22)}
    for ck, (c, m) in pts_hpo.items():
        ce, me = errs_hpo[ck]
        on_front = ck in hpo_front
        ax.errorbar([c],[m], xerr=[ce], yerr=[me], fmt="none",
                    ecolor=HPO_COL[ck], alpha=0.5, capsize=3, lw=1.0)
        ax.scatter([c],[m], s=260 if on_front else 110,
                   color=HPO_COL[ck], marker=HPO_MARKER[ck],
                   edgecolor="black",
                   linewidth=1.8 if on_front else 1.0,
                   alpha=1.0 if on_front else 0.6, zorder=5)
        dx, dy = OFFSET[ck]
        ax.annotate(HPO_LABEL[ck], (c, m),
                    xytext=(dx, dy), textcoords="offset points",
                    fontsize=11,
                    fontweight="bold" if on_front else "normal",
                    color=HPO_COL[ck])
    # frontier line on RIGHT
    fp = sorted([(pts_hpo[k][0], pts_hpo[k][1]) for k in hpo_front])
    if len(fp) >= 2:
        ax.plot([p[0] for p in fp], [p[1] for p in fp],
                "k--", linewidth=1.8, alpha=0.7, zorder=2)
    ax.set_xlabel("$\\bar{\\gamma}$ (cost per workflow, USD)", fontsize=LABEL_FS)
    ax.set_ylabel("OMR  (budget OR deadline)", fontsize=LABEL_FS)
    ax.set_title("HPO @ N = 7", fontsize=TITLE_FS - 2)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)

    fig.suptitle(
        "Cross-workload Pareto — per-workflow USD cost vs OMR\n"
        "Elastic dominates the frontier in both workloads; HPO is in the high-OMR saturation regime",
        fontsize=TITLE_FS, y=1.00)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(OUT / "CROSS_pareto_comparison.png",
                dpi=300, bbox_inches="tight")
    fig.savefig(OUT / "CROSS_pareto_comparison.pdf", bbox_inches="tight")
    plt.close(fig)
    print("  wrote CROSS_pareto_comparison (both panels on shared per-wf USD vs OMR axes)")

# =================================================================== main
def main():
    print("Regenerating corrected HPO figures into", OUT)
    plot_hpo_11_util_4corners_corrected()
    plot_cross_pareto_shared_axes()

    # Remove CPR figure (no longer valid for HPO)
    removed = []
    for ext in ("pdf", "png"):
        p = OUT / f"02f_cpr_bar.{ext}"
        if p.exists():
            p.unlink(); removed.append(p.name)
    if removed:
        print(f"  removed: {', '.join(removed)}")
    else:
        print("  02f_cpr_bar: already absent")
    print("Done.")

if __name__ == "__main__":
    main()
