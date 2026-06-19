"""Shared helpers and styling for thesis figures (Fig 1, 2, 4).

Each thesis figure has its own script (make_fig1_gantt.py, make_fig2_per_wf_bias.py,
make_fig4_cost.py) that imports from here.
"""
import csv
import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).parent
SRC  = HERE / "modelled"
FIG  = SRC / "figures" / "thesis"
FIG.mkdir(parents=True, exist_ok=True)


def apply_style():
    """Common matplotlib rcParams. Call from each figure script."""
    plt.rcParams.update({
        "font.size": 10,
        "axes.titlesize": 12,
        "figure.dpi": 140,
        "savefig.bbox": "tight",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


# ---- Colour palette ---------------------------------------------------------
FAM_COLOR = {
    "on-prem":        "#2e7d32",   # green
    "hpc7a.24xlarge": "#1565c0",   # blue
    "hpc7a.12xlarge": "#1565c0",
    "c6i.16xlarge":   "#ef6c00",   # orange
    "c6i.32xlarge":   "#c62828",   # red
    "c7i.12xlarge":   "#6a1b9a",
}

# bucket colours (Fig 2)
CLEAN_GREEN  = "#43a047"
CLOUD_ORANGE = "#fb8c00"
SWAP_GREY    = "#9e9e9e"

# Fig 4 palette
INFRA_BLUE       = "#1565c0"
INFRA_BLUE_LIGHT = "#5d99c6"
SIM_RED          = "#c62828"
CORR_GREEN       = "#2e7d32"


# ---- Decision parsing -------------------------------------------------------
def family_of(decision: str) -> str:
    if not decision: return "?"
    if "on-prem:" in decision and "reserved" not in decision and "on-demand" not in decision:
        return "on-prem"
    m = re.search(r"(?:reserved|on-demand):(.+?)x\d+(?=\||$)", decision)
    return m.group(1) if m else "?"


# ---- Data loaders -----------------------------------------------------------
def load_workflows_raw():
    """Load out/workflows.csv. Returns dict[run_id] -> dict[wfid] -> row.
    Used by Fig 1 (Gantt timestamps) and Fig 2 (per-replicate durations)."""
    by_run = defaultdict(dict)
    src = HERE / "out" / "workflows.csv"
    for r in csv.DictReader(open(src)):
        for k in ("alloc_t", "complete_t", "free_t", "duration_s"):
            r[k] = float(r[k]) if r[k] else None
        by_run[r["run_id"]][r["workflow_id"]] = r
    return by_run


def load_modelled():
    """Per-(run, wf) modelled durations and costs. Used by Fig 4."""
    return list(csv.DictReader(open(SRC / "modelled_workflows.csv")))


def load_summary():
    """All-workflows summary table (per-case bias data). Used by all figures
    for wf ordering and bias values."""
    return list(csv.DictReader(open(SRC / "all_workflows_summary.csv")))


# ---- Workflow labelling -----------------------------------------------------
def stable_order(summary):
    """Canonical wf0..wf9 labelling: same-resource workflows sorted by infra
    duration ascending, then the 2 swap workflows last. Returns the ordered
    workflow-ID list and a {wfid: 'wfN'} mapping."""
    caseB = [r for r in summary if r["case"] == "B"]
    same  = sorted([r for r in caseB if r["is_swap"] == "0"],
                   key=lambda r: float(r["infra_duration_s"]))
    swap  = sorted([r for r in caseB if r["is_swap"] == "1"],
                   key=lambda r: r["workflow_id"])
    ordered = [r["workflow_id"] for r in same] + [r["workflow_id"] for r in swap]
    return ordered, {wf: f"wf{i}" for i, wf in enumerate(ordered)}


def write_label_mapping(order, summary):
    """Dump the wf-label → workflow-ID mapping CSV for traceability."""
    sumB = {r["workflow_id"]: r for r in summary if r["case"] == "B"}
    map_csv = FIG / "wf_label_mapping.csv"
    with open(map_csv, "w") as f:
        f.write("wf_label,workflow_id_full,is_swap,infra_duration_s_caseB\n")
        for i, w in enumerate(order):
            r = sumB[w]
            f.write(f"wf{i},{w},{r['is_swap']},{r['infra_duration_s']}\n")
    return map_csv
