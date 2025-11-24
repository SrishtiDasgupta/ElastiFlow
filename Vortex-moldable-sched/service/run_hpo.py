from __future__ import annotations
import sys
import ast
import json
from typing import Dict, Any

# Runners
from plcl_runner_HPO import PlclRunnerHPO
from cloud_runner_HPO_new import CloudRunnerHPO

def get_runner(mode: str, args: Dict[str, Any]):
    if mode == "cloud":
        return CloudRunnerHPO(args)
    elif mode == "on-prem":
        return PlclRunnerHPO(args)
    else:
        raise ValueError(f"Invalid mode: {mode}")

def parse_request_from_argv() -> Dict[str, Any]:
    """
    Join all argv[1:] into a single string and try to parse as JSON first,
    then as a Python literal. This makes it robust against shell splitting.
    """
    if len(sys.argv) < 2:
        raise SystemExit("No request payload provided on the command line.")

    raw = " ".join(sys.argv[1:]).strip()

    # Try JSON first (preferred)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Fallback: Python literal (safe version)
    try:
        return ast.literal_eval(raw)
    except Exception as e:
        raise SystemExit(f"Failed to parse request payload: {e}\nRaw: {raw}")

def decide_mode(request: Dict[str, Any]) -> str:
    """
    Decide mode by inspecting 'hosts'. If the top-level keys are the cloud
    instance types (e.g., 'g4dn.2xlarge'), assume cloud. If you pass the
    tiered form {'on-prem': {...}, 'reserved': {...}, 'on-demand': {...}},
    prefer 'on-prem' when present.
    """
    hosts = request.get("hosts", {})
    if not isinstance(hosts, dict) or not hosts:
        return "cloud"

    # Tiered shape?
    if any(k in hosts for k in ("on-prem", "reserved", "on-demand")):
        if hosts.get("on-prem"):
            return "on-prem"
        return "cloud"

    # Flat shape (instance_type -> [ips]) looks like cloud.
    return "cloud"

if __name__ == "__main__":
    request = parse_request_from_argv()
    mode = decide_mode(request)
    runner = get_runner(mode, request)

    # 1) Always print a first line so downstream `output_lines[0]` exists.
    wf_id = request.get("wf_id", "unknown-workflow")
    print(wf_id, flush=True)

    # 2) Run and print a structured JSON result for subsequent lines.
    try:
        result = runner.run()
    except Exception as e:
        # Emit a structured error so caller can detect and log nicely.
        err = {"status": "error", "wf_id": wf_id, "mode": mode, "error": str(e)}
        print(json.dumps(err), flush=True)
        sys.exit(1)

    # Success path
    if not isinstance(result, dict):
        result = {"status": "ok", "wf_id": wf_id, "mode": mode, "result": str(result)}
    else:
        result.setdefault("status", "ok")
        result.setdefault("wf_id", wf_id)
        result.setdefault("mode", mode)

    print(json.dumps(result), flush=True)
