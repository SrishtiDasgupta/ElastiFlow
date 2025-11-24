run_hpo.py
from __future__ import annotations
import sys
import ast
import json
import argparse
from typing import Dict, Any

# Runners
from plcl_runner_HPO import PlclRunnerHPO
# Make sure your Cloud runner inherits RunnerBase too:
# from runner_base import RunnerBase
# and keep a matching signature: CloudRunnerHPO(request: Dict[str, Any])
from cloud_runner_HPO import CloudRunnerHPO # adjust import path if needed

"""
def build_request(ns: argparse.Namespace) -> Dict[str, Any]:
print("Convert CLI args to the request dict consumed by runners.")
req = vars(ns).copy()

# Keep keys consistent with plcl_runner_HPO._build_args() and dispatcher
# Hyphens become underscores via argparse; runners expect underscores.
return req

def main() -> None:
parser = argparse.ArgumentParser(
description="Unified entrypoint for HPO runs (cloud or PLCL/Slurm).",
allow_abbrev=False,
)

parser.add_argument("--mode", choices=["plcl", "cloud"], default="plcl",
help="Where to run: 'plcl' (ParallelCluster via Slurm) or 'cloud'.")


# --- Keep these aligned with generate-slurm-dispatcher.py ---
parser.add_argument("--nodes", type=int, default=2)
parser.add_argument("--cpus-per-task", type=int, default=4)
parser.add_argument("--gpus-per-node", type=int, default=1)
parser.add_argument("--time", type=str, default="00:30:00")
parser.add_argument("--job-name", type=str, default="hpo-job")
parser.add_argument("--script", type=str, default="hpo_pipeline.py")


parser.add_argument("--learning-rate", type=float, default=None)
parser.add_argument("--momentum", type=float, default=None)
parser.add_argument("--next-trials", type=int, default=None)
parser.add_argument("--epochs", type=int, default=None)
parser.add_argument("--batch-size", type=int, default=None)
parser.add_argument("--image-size", type=int, default=None)
parser.add_argument("--hidden", type=int, default=None)

args = parser.parse_args()
request = build_request(args)

# Pick runner
mode = request.get("mode", "plcl")
if mode == "plcl":
runner = PlclRunnerHPO(request)
elif mode == "cloud":
runner = CloudRunnerHPO(request)
else:
raise ValueError(f"Unknown mode: {mode!r}")

# Execute and print JSON result
result = runner.run()
print(json.dumps(result, indent=2))

"""

# === Factory =====

def get_runner(mode, args):
    if mode == "cloud":
        return CloudRunnerHPO(args)
    elif mode == "on-prem":
        return PlclRunnerHPO(args)
    else:
        raise ValueError(f"Invalid mode: {mode}")


if __name__ == "__main__":
    val = sys.argv[1:] # ["{'cohesion': 3, 'hosts': {'hpc6id.32xlarge': []}, 'chains': 3, 'tinyda_iterations': 12}"] , 'c6i.16xlarge': ['10.19.224.55', '10.19.204.6']
    request = eval(val[0])
    hosts = request['hosts']
    host_type = next(iter(hosts))

    if 'on-prem' in host_type:
        runner = get_runner("on-prem", request)
    else:
        runner = get_runner("cloud", request)

    runner.run()



    # request = {'cohesion': 3e10, 'hosts': {'c7i.24xlarge': ['10.3.14.42', '10.3.14.43']}, 'chains': 2, 'tinyda_iterations': 1, 'mesh': 1000
    # used_hosts = ['10.3.14.38']
    #runClient(request, used_hosts)
    #fetchOutput()

    pass

