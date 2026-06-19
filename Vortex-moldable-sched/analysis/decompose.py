"""
Post-processor for HPO campaign runs.

Inputs:
  --results <CSV>       per-workflow results (e.g. EDF_Moldable_HPO_5wf_hybrid_results.csv)
  --cold-log <CSV>      cold_start_log.csv produced by scripts/cold_start_log.py
  --label   <str>       run label for plot titles (e.g. "Moldable EDF N=5")
  --outdir  <DIR>       where to write PNGs

Outputs (in --outdir):
  gantt.png              workflow timelines, on-demand cold-start segments highlighted
  decompose.png          stacked bar: cold-start vs compute time per workflow
  gpu_area.png           GPU-time area: always-on vs on-demand utilization
  sensitivity.png        cost vs cold-start magnitude (analytical sweep)

The same script is run twice (moldable + static); compare side-by-side externally.
"""
import argparse
import ast
import csv
import math
import os
import re
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt


ON_DEMAND_PATTERN = re.compile(r"CloudOnDemandInstance")
RESERVED_PATTERN = re.compile(r"CloudReservedInstance")
ON_PREM_PATTERN = re.compile(r"OnPremInstance")


def _classify_pool(instance_repr: str) -> str:
    if ON_DEMAND_PATTERN.search(instance_repr):
        return "on_demand"
    if RESERVED_PATTERN.search(instance_repr):
        return "reserved"
    if ON_PREM_PATTERN.search(instance_repr):
        return "on_prem"
    return "unknown"


def _parse_instances_field(raw: str):
    """The CSV embeds repr() of {InstanceObject: [(count, start, end), ...]}.
    Return list of (pool, count, start, end). Keys are object reprs; we classify them by string match."""
    spans = []
    if not raw or raw == "{}":
        return spans
    pool_chunks = re.findall(
        r"<resource_manager\.instance\.\w+ object at 0x[0-9a-f]+>: (\[[^\]]*\])", raw
    )
    pool_keys = re.findall(r"<resource_manager\.instance\.(\w+) object at 0x[0-9a-f]+>", raw)
    for pool_cls, segs_repr in zip(pool_keys, pool_chunks):
        pool = {
            "OnPremInstance": "on_prem",
            "CloudReservedInstance": "reserved",
            "CloudOnDemandInstance": "on_demand",
        }.get(pool_cls, "unknown")
        try:
            segs = ast.literal_eval(segs_repr)
        except (SyntaxError, ValueError):
            continue
        for seg in segs:
            if len(seg) != 3:
                continue
            count, start, end = seg
            spans.append((pool, count, start, end))
    return spans


def load_results(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r.get("instances"):
                continue
            r["spans"] = _parse_instances_field(r["instances"])
            r["budget"] = float(r["budget"]) if r.get("budget") else 0.0
            r["deadline"] = float(r["deadline"]) if r.get("deadline") else 0.0
            r["cost"] = float(r["cost"]) if r.get("cost") else 0.0
            for k in ("submit_time", "sched_start_time", "exec_start_time", "finish_time"):
                r[k] = float(r[k]) if r.get(k) else 0.0
            rows.append(r)
    return rows


def load_cold_log(path):
    if not path or not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            for k in ("t_request", "t_running", "t_ssh", "t_setup_done", "t_executor_listening"):
                r[k] = float(r[k]) if r.get(k) else None
            rows.append(r)
    return rows


def plot_gantt(rows, cold_log, label, outpath):
    rows_sorted = sorted(rows, key=lambda r: r["submit_time"])
    t0 = min(r["submit_time"] for r in rows_sorted)
    fig, ax = plt.subplots(figsize=(12, max(3, 0.5 * len(rows_sorted) + 2)))
    pool_color = {"on_prem": "#4C72B0", "reserved": "#55A868", "on_demand": "#C44E52"}
    for i, r in enumerate(rows_sorted):
        ax.barh(i, r["finish_time"] - r["exec_start_time"],
                left=r["exec_start_time"] - t0, color="#cccccc", alpha=0.4, height=0.8)
        for pool, count, start, end in r["spans"]:
            if start is None or end is None:
                continue
            ax.barh(i, end - start, left=start - t0, color=pool_color.get(pool, "#888"),
                    height=0.5, edgecolor="white", linewidth=0.3)
    for entry in cold_log:
        if entry.get("t_request") and entry.get("t_executor_listening"):
            ax.axvspan(entry["t_request"] - t0, entry["t_executor_listening"] - t0,
                       color="#C44E52", alpha=0.08)
    ax.set_yticks(range(len(rows_sorted)))
    ax.set_yticklabels([r.get("model", "") + " " + r.get("", "")[:8]
                        if False else r["model"] for r in rows_sorted])
    ax.set_xlabel("Time since first submit (s)")
    ax.set_title(f"Gantt — {label}")
    handles = [mpatches.Patch(color=c, label=p) for p, c in pool_color.items()]
    ax.legend(handles=handles, loc="lower right")
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def plot_decompose(rows, cold_log, label, outpath):
    """Stacked bar per workflow: cold-start exposure vs compute."""
    cs_by_request = sorted(
        [c for c in cold_log if c.get("t_request") and c.get("t_executor_listening")],
        key=lambda c: c["t_request"],
    )
    rows_sorted = sorted(rows, key=lambda r: r["submit_time"])
    cold_per_wf = []
    compute_per_wf = []
    labels = []
    used = [False] * len(cs_by_request)
    for r in rows_sorted:
        cold_exposure = 0.0
        for j, c in enumerate(cs_by_request):
            if used[j]:
                continue
            if r["exec_start_time"] - 60 <= c["t_executor_listening"] <= r["finish_time"]:
                cold_exposure += c["t_executor_listening"] - c["t_request"]
                used[j] = True
        compute = max(0.0, r["finish_time"] - r["exec_start_time"])
        cold_per_wf.append(cold_exposure)
        compute_per_wf.append(compute)
        labels.append(f"{r['model'][:8]}")

    fig, ax = plt.subplots(figsize=(10, 5))
    idx = range(len(rows_sorted))
    ax.bar(idx, compute_per_wf, color="#4C72B0", label="compute")
    ax.bar(idx, cold_per_wf, bottom=compute_per_wf, color="#C44E52", label="cold-start exposure")
    ax.set_xticks(list(idx))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Seconds")
    ax.set_title(f"Time decomposition — {label}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def plot_gpu_area(rows, label, outpath):
    """GPU-time area: per-pool utilization over wall-clock."""
    pool_events = defaultdict(list)  # pool -> [(t, delta_gpus)]
    t0 = min(r["submit_time"] for r in rows)
    t_end = max(r["finish_time"] for r in rows)
    for r in rows:
        for pool, count, start, end in r["spans"]:
            if start is None or end is None:
                continue
            pool_events[pool].append((start - t0, count))
            pool_events[pool].append((end - t0, -count))
    fig, ax = plt.subplots(figsize=(12, 5))
    pool_color = {"on_prem": "#4C72B0", "reserved": "#55A868", "on_demand": "#C44E52"}
    duration = t_end - t0
    grid = [i for i in range(0, int(duration) + 30, 30)]
    bottom = [0.0] * len(grid)
    for pool in ("on_prem", "reserved", "on_demand"):
        events = sorted(pool_events.get(pool, []))
        usage = []
        running = 0.0
        ei = 0
        for t in grid:
            while ei < len(events) and events[ei][0] <= t:
                running += events[ei][1]
                ei += 1
            usage.append(max(0.0, running))
        ax.fill_between(grid, bottom, [b + u for b, u in zip(bottom, usage)],
                        color=pool_color[pool], alpha=0.7, label=pool)
        bottom = [b + u for b, u in zip(bottom, usage)]
    ax.set_xlabel("Time since first submit (s)")
    ax.set_ylabel("GPUs in use")
    ax.set_title(f"GPU-time area — {label}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def plot_sensitivity(rows, cold_log, label, outpath):
    """ΔCost(C) where C is cold-start magnitude. Sweep 200..800s."""
    K = sum(1 for c in cold_log if c.get("t_request") and c.get("t_executor_listening"))
    measured_C = 0.0
    if K:
        durations = [
            c["t_executor_listening"] - c["t_request"]
            for c in cold_log
            if c.get("t_request") and c.get("t_executor_listening")
        ]
        measured_C = sum(durations) / len(durations)
    p_bar = sum(r["cost"] for r in rows) / max(1.0, sum(r["finish_time"] - r["exec_start_time"]
                                                        for r in rows))
    Cs = list(range(200, 820, 20))
    cost_curve = [K * C * p_bar for C in Cs]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(Cs, cost_curve, color="#C44E52", label=f"K={K} cold-starts × p̄={p_bar:.4f} €/GPU·s")
    if measured_C:
        ax.axvline(measured_C, color="black", linestyle="--",
                   label=f"measured C={measured_C:.0f}s")
    ax.set_xlabel("Cold-start duration C (s)")
    ax.set_ylabel("On-demand cold-start cost (€)")
    ax.set_title(f"Sensitivity — {label}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--cold-log", default=None)
    ap.add_argument("--label", default="run")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    rows = load_results(args.results)
    cold_log = load_cold_log(args.cold_log)
    if not rows:
        print(f"No rows in {args.results}")
        return

    plot_gantt(rows, cold_log, args.label, os.path.join(args.outdir, "gantt.png"))
    plot_decompose(rows, cold_log, args.label, os.path.join(args.outdir, "decompose.png"))
    plot_gpu_area(rows, args.label, os.path.join(args.outdir, "gpu_area.png"))
    plot_sensitivity(rows, cold_log, args.label, os.path.join(args.outdir, "sensitivity.png"))
    print(f"Wrote 4 plots to {args.outdir}")


if __name__ == "__main__":
    main()
