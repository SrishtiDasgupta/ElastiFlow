"""
Parse the 27 BMW sim-vs-infra validation logs into a unified dataset.

Inputs:  /Users/srishtidasgupta/PhD/PhD/BMW/loose docs/useful _data /
Outputs: ./out/{events.csv, workflows.csv, runs.csv}
         ./raw_txt/<run_id>.txt  (RTF files stripped to plain text)

Run-pairing (established by inspecting the reserved-pool used in each log):
  infra_v1            : infra_test.txt              <-> sim_v1   : out_sim2.txt.rtf
  infra_v1_updated    : updated_infra_test.txt      <-> sim_v2   : sim_testV2.rtf
  infra_v2            : infra_testV2.txt            <-> sim_v2
  infra_v3            : infra_testV3.txt            <-> sim_v2
  sim_v1b             : sim_run.rtf   (unpaired, mid-evolution)
  sim_moldability_n3  : sim_run_moldability_logs_latest.rtf  (N=3 moldability test)
  sim_moldability_n3b : sim_run_moldability_logs_latest-1.rtf (replicate)

The remaining RTFs (c6i*/hpc7a*/onPrem*/runtimes_onprem/gantttime, infra_test.rtf
etc.) are either RTF mirrors of the .txt sources or per-instance fragment logs.
They are converted to .txt under raw_txt/ for inspection but not parsed into the
event table here -- they don't contain whole-run scheduling timelines.
"""
import csv
import os
import re
import ast
from pathlib import Path

SRC = Path("/Users/srishtidasgupta/PhD/PhD/BMW/loose docs/useful _data ")
HERE = Path(__file__).parent
RAW = HERE / "raw_txt"
OUT = HERE / "out"
RAW.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

# -- file -> (run_id, env, paired_with) ---------------------------------------
#
# Naming convention (defensible for dissertation):
#   Case A = scheduler with sort_key="cost_per_iteration" (cost-prioritising)
#            -> picks smaller cloud instances first
#   Case B = scheduler with sort_key="runtime_per_iteration" (runtime-prioritising)
#            -> picks larger cloud instances first
#   Both cases use the same fixed infrastructure pool: 1 Slurm head + ~4 compute,
#   5 reserved cloud instances (c6i.16xl, c6i.32xl, c7i.12xl, hpc7a.12xl,
#   hpc7a.24xl) and 3 on-demand cloud instances (c6i.16xl, c6i.32xl, hpc7a.24xl).
#
# r1, r2, r3 = real-infra replicates of Case B. The simulator is deterministic
# so a single sim run per case is canonical (no sim replicates needed).
#
# Files that are not used in the primary analysis (uncalibrated/intermediate
# simulator versions, separate moldability experiment) are parsed for the
# inventory but flagged 'supplementary'.
RUN_MAP = {
    # -- Case A: cost-prioritising scheduler --
    "infra_test.txt":             ("infra_A",     "infra", "sim_A"),
    "out_sim2.txt.rtf":           ("sim_A",       "sim",   "infra_A"),

    # -- Case B: runtime-prioritising scheduler --
    "updated_infra_test.txt":     ("infra_B_r1",  "infra", "sim_B"),
    "infra_testV2.txt":           ("infra_B_r2",  "infra", "sim_B"),
    "infra_testV3.txt":           ("infra_B_r3",  "infra", "sim_B"),
    "sim_testV2 -1-1.rtf":        ("sim_B",       "sim",   "infra_B_r2"),

    # -- Supplementary (parsed but not used in primary deviation analysis) --
    # sim_B_uncalib: an earlier Case B sim run before the resource runtime
    # model was calibrated to infra observations. Same scheduler, same
    # decisions, but cloud durations diverge by 30-55%. Retained for the
    # calibration-sensitivity footnote, not used as the canonical Case B sim.
    "sim_testV2.rtf":             ("sim_B_uncalib", "sim", None),
    # sim_A_intermediate: mid-evolution sim, mixes both pools, doesn't pair.
    "sim_run.rtf":                ("sim_A_intermediate", "sim", None),
    # Separate moldability experiment (N=3, per-iteration moldable expansion).
    "sim_run_moldability_logs_latest.rtf":   ("sim_moldable_r1", "sim", None),
    "sim_run_moldability_logs_latest-1.rtf": ("sim_moldable_r2", "sim", None),
}

# -- RTF stripping ------------------------------------------------------------
_rtf_ctrl = re.compile(r"\\[a-zA-Z]+-?\d*\s?|\\[*]|\\\'[0-9a-fA-F]{2}")
_rtf_groups = re.compile(r"[{}]")

def strip_rtf(text: str) -> str:
    # General approach: remove control words and structural braces.
    # RTF uses { } as group delimiters but escapes literal content braces as \{ \},
    # so protect those before stripping structural braces or the dict literals
    # in sim log lines (e.g. {'on-prem': ...}) get destroyed.
    t = text.replace("\\\n", "\n").replace("\\\r\n", "\n")
    t = t.replace("\\{", "\x01").replace("\\}", "\x02")
    # control words like \rtf1, \cocoartf2822, \f0, etc.
    t = _rtf_ctrl.sub("", t)
    # font/color tables and other groups -- crude removal of structural braces
    t = _rtf_groups.sub("", t)
    t = t.replace("\x01", "{").replace("\x02", "}")
    # collapse multiple blank lines
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t

def read_log(p: Path) -> str:
    raw = p.read_text(errors="replace")
    if p.suffix.lower() == ".rtf":
        return strip_rtf(raw)
    return raw

# -- log parsing --------------------------------------------------------------
RX_RECV   = re.compile(r"Workflow received at ([0-9.]+)")
RX_SEND   = re.compile(r"Sending wf(\d+) at ([0-9.]+)")
RX_ALLOC  = re.compile(r"(test-[0-9a-f-]+) allocated at ([0-9.]+):\s*(\{.*\})")
RX_FREE   = re.compile(r"(test-[0-9a-f-]+) workflow freed at ([0-9.]+)")
RX_COMPL  = re.compile(r"Workflow (test-[0-9a-f-]+) complete at ([0-9.]+)")
RX_NORES  = re.compile(r"No resources to allocate, waiting")
RX_METRIC = re.compile(r"^(Total workflows|Executed workflows|Average Makespan|Average Cost|"
                       r"Average Wait Time|Average resource utilization|Deadline miss rate|"
                       r"Budget miss rate|Overall miss rate)\s*=\s*([0-9.]+)", re.M)

def classify_decision(d: dict) -> str:
    """Return a short resource-class label for an allocation dict."""
    if not d:
        return "NONE"
    parts = []
    for tier in ("on-prem", "reserved", "on-demand"):
        sub = d.get(tier, {})
        if not sub:
            continue
        for inst, val in sub.items():
            count = val[0] if isinstance(val, (list, tuple)) else "?"
            parts.append(f"{tier}:{inst}x{count}")
    return "|".join(parts) if parts else "NONE"

def parse_run(text: str, run_id: str, env: str, paired_with):
    events = []
    workflows = {}  # wfid -> dict
    recv_queue = []
    send_map = {}  # wf_idx -> send_t (sim only)
    metrics = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = RX_RECV.search(line)
        if m:
            ts = float(m.group(1))
            recv_queue.append(ts)
            events.append((run_id, env, "", "recv", ts, "", ""))
            continue

        m = RX_SEND.search(line)
        if m:
            wf_idx, ts = int(m.group(1)), float(m.group(2))
            send_map[wf_idx] = ts
            events.append((run_id, env, f"wf{wf_idx}", "send", ts, "", ""))
            continue

        m = RX_ALLOC.search(line)
        if m:
            wfid, ts, dstr = m.group(1), float(m.group(2)), m.group(3)
            try:
                d = ast.literal_eval(dstr)
            except Exception:
                d = {}
            cls = classify_decision(d)
            ip = ""
            for tier in d.values():
                for v in tier.values():
                    if isinstance(v, (list, tuple)) and len(v) > 1 and v[1]:
                        ip = v[1][0] if isinstance(v[1], list) else str(v[1])
                        break
                if ip: break
            wf = workflows.setdefault(wfid, {"wfid": wfid, "alloc_attempts": 0})
            wf["alloc_attempts"] += 1
            # Record the *successful* allocation (non-empty decision); keep first-attempt time too
            if cls != "NONE":
                wf["alloc_t"] = ts
                wf["alloc_decision"] = cls
                wf["alloc_resource_ip"] = ip
                if "first_alloc_attempt_t" not in wf:
                    wf["first_alloc_attempt_t"] = ts
            else:
                wf.setdefault("first_alloc_attempt_t", ts)
                wf["had_no_res_wait"] = True
            events.append((run_id, env, wfid, "alloc", ts, cls, ip))
            continue

        m = RX_COMPL.match(line)
        if m:
            wfid, ts = m.group(1), float(m.group(2))
            wf = workflows.setdefault(wfid, {"wfid": wfid})
            wf["complete_t"] = ts
            events.append((run_id, env, wfid, "complete", ts, "", ""))
            continue

        m = RX_FREE.search(line)
        if m:
            wfid, ts = m.group(1), float(m.group(2))
            wf = workflows.setdefault(wfid, {"wfid": wfid})
            wf["free_t"] = ts
            events.append((run_id, env, wfid, "free", ts, "", ""))
            continue

        if RX_NORES.search(line):
            events.append((run_id, env, "", "no_res", 0.0, "", ""))

    for m in RX_METRIC.finditer(text):
        metrics[m.group(1)] = float(m.group(2))

    # Build workflow-level rows
    wf_rows = []
    for wfid, wf in workflows.items():
        alloc = wf.get("alloc_t")
        free = wf.get("free_t")
        complete = wf.get("complete_t")
        # Use complete_t when present (sim has both; infra logs only have 'free'),
        # else fall back to free_t.
        end_t = complete if complete is not None else free
        duration = (end_t - alloc) if (alloc is not None and end_t is not None) else None
        wait = None
        if alloc is not None and wf.get("first_alloc_attempt_t") is not None:
            wait = alloc - wf["first_alloc_attempt_t"]
        wf_rows.append({
            "run_id": run_id,
            "env": env,
            "paired_with": paired_with or "",
            "workflow_id": wfid,
            "alloc_t": alloc,
            "complete_t": complete,
            "free_t": free,
            "duration_s": duration,
            "alloc_decision": wf.get("alloc_decision", ""),
            "alloc_resource_ip": wf.get("alloc_resource_ip", ""),
            "alloc_attempts": wf.get("alloc_attempts", 0),
            "queueing_event": wf.get("had_no_res_wait", False),
        })

    run_row = {
        "run_id": run_id,
        "env": env,
        "paired_with": paired_with or "",
        "n_workflows_executed": int(metrics.get("Executed workflows", len(wf_rows))),
        "avg_makespan": metrics.get("Average Makespan"),
        "avg_cost": metrics.get("Average Cost"),
        "avg_wait": metrics.get("Average Wait Time"),
        "avg_util": metrics.get("Average resource utilization"),
        "deadline_miss_rate": metrics.get("Deadline miss rate"),
        "budget_miss_rate": metrics.get("Budget miss rate"),
        "overall_miss_rate": metrics.get("Overall miss rate"),
    }
    return events, wf_rows, run_row


def main():
    all_events = []
    all_wfs = []
    all_runs = []
    inventory = []

    # First, dump every file in SRC to raw_txt/ for inspection (RTFs stripped).
    for p in sorted(SRC.iterdir()):
        if p.suffix.lower() not in (".rtf", ".txt", ".csv", ".py"):
            continue
        body = read_log(p) if p.suffix.lower() in (".rtf", ".txt") else p.read_text(errors="replace")
        outp = RAW / (p.stem + ".txt")
        outp.write_text(body)
        inventory.append((p.name, p.stat().st_size, outp.name))

    for fname, (run_id, env, paired) in RUN_MAP.items():
        p = SRC / fname
        if not p.exists():
            print(f"[skip missing] {fname}")
            continue
        text = read_log(p)
        ev, wfs, run = parse_run(text, run_id, env, paired)
        all_events.extend(ev)
        all_wfs.extend(wfs)
        all_runs.append(run)
        print(f"[{run_id:22s}] {env:5s} from {fname}: {len(wfs)} workflows, "
              f"{len(ev)} events, makespan={run['avg_makespan']}")

    # Write events.csv
    with (OUT / "events.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "env", "workflow_id", "event", "ts", "decision_class", "resource_ip"])
        w.writerows(all_events)

    # Write workflows.csv
    with (OUT / "workflows.csv").open("w", newline="") as f:
        cols = ["run_id", "env", "paired_with", "workflow_id", "alloc_t",
                "complete_t", "free_t", "duration_s", "alloc_decision",
                "alloc_resource_ip", "alloc_attempts", "queueing_event"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_wfs)

    # Write runs.csv
    with (OUT / "runs.csv").open("w", newline="") as f:
        cols = ["run_id", "env", "paired_with", "n_workflows_executed",
                "avg_makespan", "avg_cost", "avg_wait", "avg_util",
                "deadline_miss_rate", "budget_miss_rate", "overall_miss_rate"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_runs)

    # Inventory of all source files
    with (OUT / "file_inventory.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_file", "bytes", "stripped_to", "role_in_dataset"])
        roles = {fname: (run_id, env) for fname, (run_id, env, _) in RUN_MAP.items()}
        for name, sz, stripped in inventory:
            r = roles.get(name)
            role = f"{r[1]}:{r[0]}" if r else "supplementary"
            w.writerow([name, sz, stripped, role])

    print(f"\nWrote: {OUT}/events.csv, workflows.csv, runs.csv, file_inventory.csv")
    print(f"Stripped {len(inventory)} source files into {RAW}/")

if __name__ == "__main__":
    main()
