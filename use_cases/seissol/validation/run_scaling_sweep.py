"""
Sim-only scaling sweep harness for the SeisSol-Plain Vortex simulator.

For each target N (workload size), patches `elastiflow/config/constants.py` to
set TOTAL_WORKFLOWS=N, runs `elastiflow/simulate_main.py`, captures the
end-of-run metric line from stdout, and writes a tidy CSV.

Requires Redis to be running locally (the simulator uses Redis as the
inter-process queue even in pure-sim mode).

Usage:
    python use_cases/seissol/validation/run_scaling_sweep.py \
        --N 10,25,50,100 \
        --replicates 1 \
        --out use_cases/seissol/validation/out/sweep_results.csv

Replicates: the simulator's stochasticity comes from cloud cold-start
delay sampling. With replicates>1 you get identical results unless the
simulator is also reseeded -- this harness does not modify random seeds,
so set replicates=1 unless you've added seed control upstream.
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CONSTANTS = REPO / "elastiflow/config/constants.py"
SIM_MAIN  = REPO / "elastiflow/simulate_main.py"
WORK_DIR  = REPO / "elastiflow"

METRIC_RX = {
    "Total workflows":         re.compile(r"Total workflows\s*=\s*([0-9.]+)"),
    "Executed workflows":      re.compile(r"Executed workflows\s*=\s*([0-9.]+)"),
    "Average Makespan":        re.compile(r"Average Makespan\s*=\s*([0-9.]+)"),
    "Average Cost":            re.compile(r"Average Cost\s*=\s*([0-9.]+)"),
    "Average Wait Time":       re.compile(r"Average Wait Time\s*=\s*([0-9.]+)"),
    "Average resource utilization": re.compile(r"Average resource utilization\s*=\s*([0-9.]+)"),
    "Deadline miss rate":      re.compile(r"Deadline miss rate\s*=\s*([0-9.]+)"),
    "Budget miss rate":        re.compile(r"Budget miss rate\s*=\s*([0-9.]+)"),
    "Overall miss rate":       re.compile(r"Overall miss rate\s*=\s*([0-9.]+)"),
}

ALLOC_RX = re.compile(r"allocated.*?'(on-prem|reserved|on-demand)':\s*\{'?([a-zA-Z0-9._-]*)")

def patch_constants(n: int):
    text = CONSTANTS.read_text()
    new = re.sub(r"^TOTAL_WORKFLOWS\s*=\s*\d+",
                 f"TOTAL_WORKFLOWS = {n}", text, count=1, flags=re.M)
    if new == text:
        raise RuntimeError("Failed to find TOTAL_WORKFLOWS line in constants.py")
    CONSTANTS.write_text(new)

def parse_output(out: str):
    metrics = {}
    for k, rx in METRIC_RX.items():
        m = rx.search(out)
        metrics[k] = float(m.group(1)) if m else None

    # Allocation counts by family
    family_counts = {"on-prem": 0, "hpc7a": 0, "c6i": 0, "c7i": 0, "other_cloud": 0}
    for line in out.splitlines():
        if "allocated at" not in line:
            continue
        m = ALLOC_RX.search(line)
        if not m: continue
        tier, inst = m.group(1), m.group(2)
        if tier == "on-prem":
            family_counts["on-prem"] += 1
        else:
            fam = inst.split(".")[0] if "." in inst else "other_cloud"
            if fam not in family_counts:
                family_counts["other_cloud"] += 1
            else:
                family_counts[fam] += 1
    metrics.update({f"alloc_{k}": v for k, v in family_counts.items()})
    return metrics

def run_one(n: int, replicate: int, log_dir: Path, timeout: int):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"sim_N{n}_r{replicate}.log"
    print(f"[N={n} r={replicate}] starting (logs -> {log_path})", flush=True)
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "simulate_main.py"],
        cwd=str(WORK_DIR),
        capture_output=True, text=True, timeout=timeout,
    )
    elapsed = time.time() - t0
    out = proc.stdout + "\n" + proc.stderr
    log_path.write_text(out)
    metrics = parse_output(out)
    metrics["N"] = n
    metrics["replicate"] = replicate
    metrics["wall_clock_s"] = round(elapsed, 1)
    metrics["returncode"] = proc.returncode
    print(f"[N={n} r={replicate}] done in {elapsed:.1f}s  "
          f"makespan={metrics.get('Average Makespan')} "
          f"miss={metrics.get('Overall miss rate')}", flush=True)
    return metrics

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", default="10,25,50,100",
                    help="comma-separated workload sizes")
    ap.add_argument("--replicates", type=int, default=1)
    ap.add_argument("--out", default=str(REPO / "use_cases/seissol/validation/out/sweep_results.csv"))
    ap.add_argument("--log-dir", default=str(REPO / "use_cases/seissol/validation/out/sweep_logs"))
    ap.add_argument("--timeout", type=int, default=1800,
                    help="per-run timeout in seconds")
    args = ap.parse_args()

    N_list = [int(x) for x in args.N.split(",")]
    log_dir = Path(args.log_dir)
    out_path = Path(args.out)

    # Snapshot constants.py so we can restore it no matter what
    backup = CONSTANTS.read_text()

    rows = []
    try:
        for n in N_list:
            patch_constants(n)
            for r in range(args.replicates):
                rows.append(run_one(n, r, log_dir, args.timeout))
    finally:
        CONSTANTS.write_text(backup)
        print("Restored constants.py")

    # Tidy CSV
    if not rows:
        print("No rows collected.", file=sys.stderr)
        return
    cols = ["N", "replicate", "wall_clock_s", "returncode"] + \
           list(METRIC_RX) + \
           [f"alloc_{k}" for k in ("on-prem","hpc7a","c6i","c7i","other_cloud")]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {out_path}")

if __name__ == "__main__":
    main()
