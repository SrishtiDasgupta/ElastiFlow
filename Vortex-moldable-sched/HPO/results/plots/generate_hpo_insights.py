"""Supplementary HPO plots — three additions to match the Plain SeisSol
chapter's analytical depth.

Outputs (written to HPO/results/plots/):
  HPO_06_pareto_trajectory.{pdf,png}    Pareto evolution across N
  HPO_11_util_4corners_n7.{pdf,png}     four-corner timeline at N=7
  CROSS_pareto_comparison.{pdf,png}     side-by-side Plain SeisSol vs HPO
"""
import json
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np

# ----------------------------------------------------------- paths & styling
HERE = Path(__file__).resolve().parent
HPO_JSON = HERE / "total_cost_per_run.json"
PLAIN_JSON = (HERE.parent.parent.parent / "plain_results"
              / "plain_results_per_run.json")
OUT_DIR = HERE
OUT_DIR.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.weight": "bold",
    "axes.labelweight": "bold",
    "axes.titleweight": "bold",
    "figure.titleweight": "bold",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS, ANNOT_FS = 18, 16, 14, 12, 11

# ----------------------------------------------------------- HPO data
# n=6 numbers from THESIS_TABLE.md (cost in USD, misses count, sum-flow in min)
HPO = {
    "STAT EDF":  {3: dict(cost=(2.39, 0.67), miss=(0.5, 0.5), sf=(270.5, 66.4)),
                   5: dict(cost=(2.39, 0.67), miss=(2.2, 0.8), sf=(508.8, 64.7)),
                   7: dict(cost=(6.95, 1.14), miss=(4.2, 0.8), sf=(943.9, 135.7))},
    "STAT FCFS": {3: dict(cost=(2.39, 0.67), miss=(0.5, 0.5), sf=(270.5, 66.4)),
                   5: dict(cost=(6.38, 0.98), miss=(2.5, 0.5), sf=(616.2, 71.5)),
                   7: dict(cost=(7.97, 1.62), miss=(4.5, 0.5), sf=(991.3, 146.3))},
    "MAL EDF":   {3: dict(cost=(4.09, 1.28), miss=(0.8, 0.4), sf=(282.1, 65.0)),
                   5: dict(cost=(5.32, 0.97), miss=(1.8, 0.8), sf=(450.2, 54.0)),
                   7: dict(cost=(6.92, 1.03), miss=(3.3, 0.8), sf=(719.0, 97.9))},
    "MAL FCFS":  {3: dict(cost=(4.09, 1.28), miss=(0.8, 0.4), sf=(282.1, 65.0)),
                   5: dict(cost=(5.32, 0.97), miss=(1.8, 0.8), sf=(450.2, 54.0)),
                   7: dict(cost=(6.67, 0.88), miss=(3.3, 1.0), sf=(722.0, 103.9))},
}
HPO_LABEL = {"STAT EDF":  "EDF-ST$_c$",
             "STAT FCFS": "FCFS-ST$_c$",
             "MAL EDF":   "Elastic-EDF$_c$",
             "MAL FCFS":  "Elastic-FCFS$_c$"}
HPO_COL = {"STAT EDF": "#B91C1C", "STAT FCFS": "#F59E0B",
           "MAL EDF":  "#1D4ED8", "MAL FCFS":  "#059669"}
HPO_MARKER = {"STAT EDF": "o", "STAT FCFS": "s",
              "MAL EDF":  "D", "MAL FCFS":  "v"}
HPO_NS = [3, 5, 7]

# ============================================================================
# Plot 1 — HPO Pareto trajectory across N
# ============================================================================
def plot_hpo_pareto_trajectory():
    fig, ax = plt.subplots(figsize=(12, 7.5))
    # Plot trajectories
    for corner in ["STAT EDF", "STAT FCFS", "MAL EDF", "MAL FCFS"]:
        xs = [HPO[corner][n]["cost"][0] for n in HPO_NS]
        ys = [HPO[corner][n]["miss"][0] for n in HPO_NS]
        xe = [HPO[corner][n]["cost"][1] for n in HPO_NS]
        ye = [HPO[corner][n]["miss"][1] for n in HPO_NS]
        ax.errorbar(xs, ys, xerr=xe, yerr=ye, fmt="none",
                    ecolor=HPO_COL[corner], alpha=0.4, capsize=3, lw=1.0)
        ax.plot(xs, ys, "-", color=HPO_COL[corner], linewidth=2.0, alpha=0.5,
                zorder=2)
        ax.scatter(xs, ys, s=180, color=HPO_COL[corner],
                   marker=HPO_MARKER[corner], edgecolor="black",
                   linewidth=1.5, label=HPO_LABEL[corner], zorder=5)
        # Mark each N with a small label next to the marker
        for n, x, y in zip(HPO_NS, xs, ys):
            ax.annotate(f"N={n}", (x, y),
                        xytext=(8, 8), textcoords="offset points",
                        fontsize=10, color=HPO_COL[corner], alpha=0.9,
                        fontweight="bold")
    # Frontier annotations per N (small text)
    frontier_text = {
        3: "N=3: Static wins\n(both axes)",
        5: "N=5: Frontier splits\n(Static for cost,\n Elastic for misses)",
        7: "N=7: Elastic dominates\n(both axes)",
    }
    # Compute average corner positions per N for label placement
    label_pos = {
        3: (1.5, 0.05),
        5: (6.6, 2.45),
        7: (5.6, 4.5),
    }
    for n, (px, py) in label_pos.items():
        ax.text(px, py, frontier_text[n],
                fontsize=11, fontstyle="italic", color="#4B5563",
                bbox=dict(boxstyle="round,pad=0.4", fc="#F9FAFB",
                          ec="#9CA3AF", linewidth=1.0, alpha=0.9))
    ax.set_xlabel("Total batch cost (USD)", fontsize=LABEL_FS)
    ax.set_ylabel("Deadline misses (count)", fontsize=LABEL_FS)
    ax.set_title("HPO Pareto trajectory across batch size N",
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 9.5)
    ax.set_ylim(-0.3, 5.5)
    ax.legend(fontsize=LEG_FS, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), framealpha=0.92)
    fig.tight_layout()
    _save(fig, "HPO_06_pareto_trajectory")

# ============================================================================
# Plot 2 — HPO 4-corner utilisation timeline at N=7
# ============================================================================
def plot_hpo_util_4corners_n7():
    d = json.loads(HPO_JSON.read_text())
    corner_keys = [
        ("STAT EDF",  "N7_static_edf",   HPO_LABEL["STAT EDF"]),
        ("STAT FCFS", "N7_static_fcfs",  HPO_LABEL["STAT FCFS"]),
        ("MAL EDF",   "N7_moldable_edf", HPO_LABEL["MAL EDF"]),
        ("MAL FCFS",  "N7_moldable_fcfs",HPO_LABEL["MAL FCFS"]),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharex=True, sharey=True)
    overall_xmax = 0.0
    for ax, (key, json_key, label) in zip(axes.flatten(), corner_keys):
        cell = d.get(json_key)
        if cell is None or not cell["per_run"]:
            ax.text(0.5, 0.5, f"No data for {label}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=14, color="#888")
            continue
        run = cell["per_run"][0]   # representative run
        ts = np.array(run["util_timeline_t"]) / 60.0   # minutes
        tiers = run["util_timeline_tiers"]
        slurm = np.array(tiers["slurm"])
        res   = np.array(tiers["reserved_cloud"])
        od    = np.array(tiers["on_demand"])
        ax.plot(ts, slurm, label="On-premise",      color="#10B981", linewidth=2.2)
        ax.plot(ts, res,   label="Reserved cloud",  color="#3B82F6", linewidth=2.2)
        ax.plot(ts, od,    label="On-demand cloud", color="#EF4444", linewidth=2.2)
        ax.fill_between(ts, 0, slurm, color="#10B981", alpha=0.15)
        ax.fill_between(ts, 0, res,   color="#3B82F6", alpha=0.15)
        ax.fill_between(ts, 0, od,    color="#EF4444", alpha=0.15)
        cost = run.get("cost_total", float("nan"))
        ax.set_title(f"{label}    (total cost: {cost:,.2f} USD)",
                     fontsize=TITLE_FS - 2, loc="left")
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
        # active range
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
    # single legend
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=3,
               loc="lower center", bbox_to_anchor=(0.5, 0.0),
               framealpha=0.92)
    fig.suptitle("Fleet utilisation over time — four corners at N = 7\n"
                 "(one representative run per corner)",
                 fontsize=TITLE_FS, y=1.00)
    fig.tight_layout(rect=[0, 0.07, 1, 0.96])
    _save(fig, "HPO_11_util_4corners_n7")

# ============================================================================
# Plot 3 — Cross-chapter Pareto comparison
# ============================================================================
def plot_cross_chapter_pareto():
    """Side-by-side Plain SeisSol N=400 and HPO N=7 Pareto frontiers."""
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    # ---- LEFT: Plain SeisSol N=400 ----
    d = json.loads(PLAIN_JSON.read_text())
    ok = [c for c in d.values() if c.get("_exit_code") == 0]
    from collections import defaultdict
    grp = defaultdict(list)
    for c in ok:
        grp[(c["_variant"], c["_N"])].append(c)
    EUR = 1.10
    VARS = ["fcfs_static_r","fcfs_static_c","fcfs_moldable_r","fcfs_moldable_c",
            "edf_static_r","edf_static_c","edf_moldable_r","edf_moldable_c",
            "heft_static","rank_moldable_5050","rank_moldable_2575"]
    LBL = {"fcfs_static_r":"FCFS-ST$_r$","fcfs_static_c":"FCFS-ST$_c$",
           "fcfs_moldable_r":"Elastic-FCFS$_r$","fcfs_moldable_c":"Elastic-FCFS$_c$",
           "edf_static_r":"EDF-ST$_r$","edf_static_c":"EDF-ST$_c$",
           "edf_moldable_r":"Elastic-EDF$_r$","edf_moldable_c":"Elastic-EDF$_c$",
           "heft_static":"HEFT-ST",
           "rank_moldable_5050":"Elastic-Rank[50,50]",
           "rank_moldable_2575":"Elastic-Rank[25,75]"}
    pts = {}
    for v in VARS:
        cells = grp[(v, 400)]
        cost = mean(c["total_cost_eur"] for c in cells) * EUR / 400
        miss = mean(c["overall_miss_rate"] for c in cells)
        pts[v] = (cost, miss)
    FRONTIER = ["edf_static_c", "edf_moldable_c", "rank_moldable_5050"]
    FCOL = {"edf_static_c":"#1D4ED8","edf_moldable_c":"#059669",
            "rank_moldable_5050":"#D97706"}
    ax = axes[0]
    for v, (c, m) in pts.items():
        if v in FRONTIER:
            ax.scatter([c], [m], s=260, color=FCOL[v], edgecolor="black",
                       linewidth=1.8, zorder=5)
            ax.annotate(LBL[v], (c, m), xytext=(0, 14),
                        textcoords="offset points",
                        ha="center", fontsize=11, fontweight="bold",
                        color=FCOL[v])
        else:
            ax.scatter([c], [m], s=70, color="#9CA3AF",
                       edgecolor="#4B5563", linewidth=0.8, alpha=0.8,
                       zorder=3)
    fx = sorted([pts[v][0] for v in FRONTIER])
    # match fx with corresponding miss
    f_sorted = sorted([(pts[v][0], pts[v][1]) for v in FRONTIER])
    ax.plot([p[0] for p in f_sorted], [p[1] for p in f_sorted],
            "k--", linewidth=1.8, alpha=0.7, zorder=2)
    ax.set_xlabel("Cost per workflow (USD)", fontsize=LABEL_FS)
    ax.set_ylabel("Overall miss-rate", fontsize=LABEL_FS)
    ax.set_title("Plain SeisSol @ N = 400\n(per-workflow basis)",
                 fontsize=TITLE_FS - 2)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)

    # ---- RIGHT: HPO N=7 ----
    ax = axes[1]
    HPO_FRONTIER = {"MAL FCFS"}    # frontier point at N=7
    # Hand-tuned offsets to avoid Elastic-FCFS / Elastic-EDF label overlap
    HPO_OFFSET = {
        "STAT EDF":  ( 10,   8),
        "STAT FCFS": ( 10,   8),
        "MAL EDF":   ( 10,  18),   # above
        "MAL FCFS":  ( 10, -22),   # below
    }
    for corner in ["STAT EDF", "STAT FCFS", "MAL EDF", "MAL FCFS"]:
        c, c_err = HPO[corner][7]["cost"]
        m, m_err = HPO[corner][7]["miss"]
        on_frontier = corner in HPO_FRONTIER
        ax.errorbar([c], [m], xerr=[c_err], yerr=[m_err], fmt="none",
                    ecolor=HPO_COL[corner], alpha=0.5, capsize=3, lw=1.0)
        ax.scatter([c], [m], s=260 if on_frontier else 110,
                   color=HPO_COL[corner],
                   marker=HPO_MARKER[corner],
                   edgecolor="black",
                   linewidth=1.8 if on_frontier else 1.0,
                   alpha=1.0 if on_frontier else 0.6,
                   zorder=5)
        dx, dy = HPO_OFFSET[corner]
        ax.annotate(HPO_LABEL[corner], (c, m),
                    xytext=(dx, dy), textcoords="offset points",
                    fontsize=11,
                    fontweight="bold" if on_frontier else "normal",
                    color=HPO_COL[corner])
    ax.set_xlabel("Total batch cost (USD)", fontsize=LABEL_FS)
    ax.set_ylabel("Deadline miss count", fontsize=LABEL_FS)
    ax.set_title("HPO @ N = 7\n(total-batch basis)", fontsize=TITLE_FS - 2)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)

    fig.suptitle(
        "Cross-chapter Pareto comparison: Elastic dominates at high load in both workloads",
        fontsize=TITLE_FS, y=1.00)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    _save(fig, "CROSS_pareto_comparison")

# ============================================================================
# Plot 4 — HPO intent satisfaction averaged across n=6 runs at N=7
# Reads the WE-intent counters added to total_cost_per_run.json by the
# instrumented run_corner. Mirrors plain SeisSol's plot 08 in structure:
# stacked bar per Elastic corner showing APPROVE / MODIFY / DENY split
# of WE-UP intents.
# ============================================================================
def plot_hpo_intent_satisfaction_n7():
    d = json.loads(HPO_JSON.read_text())
    moldable = [("MAL EDF", "N7_moldable_edf"), ("MAL FCFS", "N7_moldable_fcfs")]
    # Height matched to the 02g compute-nodes strip (5.5") so the two pair
    # cleanly side-by-side; include both with \includegraphics[height=...] in
    # LaTeX for an exact top/bottom alignment regardless of aspect ratio.
    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = np.arange(len(moldable))

    # Compute per-run share, then mean across 6 runs (consistent with how
    # plain SeisSol's plot 08 reports averaged percentages).
    apr_m, apr_s = [], []
    mod_m, mod_s = [], []
    den_m, den_s = [], []
    for key, json_key in moldable:
        runs = d[json_key]["per_run"]
        a_pct, m_pct, d_pct = [], [], []
        for r in runs:
            a = r["n_we_up_approve"]
            m = r["n_we_up_modify"]
            de = r["n_we_up_deny"]
            tot = a + m + de
            if tot > 0:
                a_pct.append(100 * a  / tot)
                m_pct.append(100 * m  / tot)
                d_pct.append(100 * de / tot)
        from statistics import stdev as _stdev
        apr_m.append(mean(a_pct) if a_pct else 0)
        apr_s.append(_stdev(a_pct) if len(a_pct) > 1 else 0)
        mod_m.append(mean(m_pct) if m_pct else 0)
        mod_s.append(_stdev(m_pct) if len(m_pct) > 1 else 0)
        den_m.append(mean(d_pct) if d_pct else 0)
        den_s.append(_stdev(d_pct) if len(d_pct) > 1 else 0)

    bottoms = np.zeros(len(moldable))
    segments = [
        (apr_m, apr_s, "APPROVE", "#10B981"),
        (mod_m, mod_s, "MODIFY",  "#3B82F6"),
        (den_m, den_s, "DENY",    "#EF4444"),
    ]
    for vals, stds, label, col in segments:
        vals = np.array(vals); stds = np.array(stds)
        ax.bar(x, vals, bottom=bottoms, label=label, color=col,
               edgecolor="white", linewidth=1.0)
        for i, v in enumerate(vals):
            if v >= 4:
                ax.text(i, bottoms[i] + v / 2,
                        f"{v:.0f}±{stds[i]:.0f}%",
                        ha="center", va="center", color="white",
                        fontsize=ANNOT_FS, fontweight="bold")
        bottoms += vals

    ax.set_xticks(x)
    ax.set_xticklabels([HPO_LABEL[k] for k, _ in moldable], fontsize=TICK_FS)
    ax.set_ylabel("Share of WE-UP intents (%)", fontsize=LABEL_FS)
    ax.set_title("Outcome of every Workflow-Engine UP intent at N = 7\n"
                 "(n = 6 averaged)", fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(0, 112)
    ax.legend(fontsize=LEG_FS, loc="upper center",
              bbox_to_anchor=(0.5, -0.18), ncol=3, framealpha=0.92)
    fig.tight_layout()
    _save(fig, "HPO_08_intent_satisfaction_n7")

# ----------------------------------------------------------------------------
# (Retained) per-event snapshot from the single actual run — different
# semantics (mechanism vs steady-state). Kept for completeness.
# ----------------------------------------------------------------------------
def plot_hpo_scaling_events_n7():
    import csv as _csv
    rep = HERE.parent / "r7_n7_edf_mold"
    merged = rep / "negotiation_merged.csv"
    sched  = rep / "negotiation_scheduler.csv"
    if not merged.exists() or not sched.exists():
        print("  skip — negotiation logs not found")
        return

    # WE-UP outcomes — derived from scheduler.csv by pairing each grow
    # observation with the matching reply for the same (wf_id, iter_idx):
    #   APPROVE              grow reply, granted_count > 0
    #   DENY                 grow reply, granted_count == 0
    #   MODIFY (scale down)  shrink reply for the same wf+iter
    #                         (scheduler overrode the engine's UP intent
    #                         with a DOWN action, identical mechanism to
    #                         plain SeisSol's processFreeRequest path)
    # Requests with no reply seen in the log are skipped (still in flight).
    grow_obs = set()
    grow_reply_count = {}    # (wf, iter) -> granted_count
    override_to_shrink = set()
    with open(sched) as f:
        for r in _csv.DictReader(f):
            wf, it = r["wf_id"], r["iter_idx"]
            gc_raw = r.get("granted_count", "")
            obs_t  = r.get("t_scheduler_request_observed", "")
            rep_t  = r.get("t_scheduler_reply_sent", "")
            if r["request_type"] == "grow" and obs_t and not rep_t:
                grow_obs.add((wf, it))
            elif r["request_type"] == "grow" and rep_t:
                try:
                    grow_reply_count[(wf, it)] = float(gc_raw or "nan")
                except ValueError:
                    pass
            elif r["request_type"] == "shrink" and rep_t:
                if (wf, it) in grow_obs:
                    override_to_shrink.add((wf, it))
    n_approve = sum(1 for k, g in grow_reply_count.items() if g and g > 0)
    n_deny    = sum(1 for k, g in grow_reply_count.items() if not g or g == 0)
    n_modify  = len(override_to_shrink)

    # WE-DOWN outcomes (all shrink rows in scheduler.csv)
    n_shrink_granted = 0
    with open(sched) as f:
        for r in _csv.DictReader(f):
            if r["request_type"] == "shrink" and r.get("granted_count"):
                n_shrink_granted += 1

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

    # LEFT: WE-UP intent outcomes
    cats = ["APPROVE", "DENY", "MODIFY (scale down)"]
    vals = [n_approve, n_deny, n_modify]
    cols = ["#10B981", "#EF4444", "#F59E0B"]
    ax = axes[0]
    bars = ax.bar(cats, vals, color=cols, edgecolor="black", linewidth=1.0)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width()/2, v + 0.05, str(v),
                ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_ylabel("Event count", fontsize=LABEL_FS)
    ax.set_title("WE-UP (grow) intent outcomes", fontsize=TITLE_FS - 2)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(0, max(vals + [1]) * 1.4)

    # RIGHT: WE-DOWN intent outcomes (all granted)
    ax = axes[1]
    ax.bar(["GRANTED"], [n_shrink_granted], color="#3B82F6",
           edgecolor="black", linewidth=1.0)
    ax.text(0, n_shrink_granted + 0.05, str(n_shrink_granted),
            ha="center", va="bottom", fontsize=ANNOT_FS, fontweight="bold")
    ax.set_ylabel("Event count", fontsize=LABEL_FS)
    ax.set_title("WE-DOWN (shrink) intent outcomes", fontsize=TITLE_FS - 2)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(0, max(n_shrink_granted, 1) * 1.4)

    fig.suptitle(
        "Workflow-Engine intent outcomes — Elastic-EDF$_c$ at N = 7\n"
        "(per-event trace from a single run)",
        fontsize=TITLE_FS, y=1.00)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    _save(fig, "HPO_08_intent_events_n7")

# ----------------------------------------------------------- save helper
def _save(fig, name):
    pdf = OUT_DIR / f"{name}.pdf"
    png = OUT_DIR / f"{name}.png"
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {pdf.name} + {png.name}")

# ----------------------------------------------------------- main
def main():
    print(f"Reading HPO data from {HPO_JSON.name}")
    print(f"Reading Plain SeisSol data from {PLAIN_JSON.name}")
    print(f"Output dir: {OUT_DIR}")
    print()
    for fn in (plot_hpo_pareto_trajectory,
               plot_hpo_util_4corners_n7,
               plot_cross_chapter_pareto,
               plot_hpo_intent_satisfaction_n7,
               plot_hpo_scaling_events_n7):
        print(f"[{fn.__name__}]")
        fn()
    print(f"\nDone — 3 figures written to {OUT_DIR}")

if __name__ == "__main__":
    main()
