# plcl_runner_HPO.py
from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path
import json

from runner_base import RunnerBase


class PlclRunnerHPO(RunnerBase):
    """
    Run HPO on AWS ParallelCluster via Slurm.
    Mirrors generate-slurm-dispatcher.py:
      - start Ray head/workers
      - export RAY_ADDRESS on head
      - pipe JSON (learning_rate, momentum, next_trials) to the script via stdin
      - pass only --hosts <nodes> as CLI arg
    Logs are always written to /fsx/slurm_res.
    """

    def __init__(self, request: dict | None = None):
        super().__init__(request)
        self.slurm_script: str | None = None
        self.output_file: str | None = None
        self.job_id: str | None = None

    def _json_payload(self) -> str:
        """Build the JSON payload exactly like the dispatcher expects."""
        payload: dict[str, object] = {}
        for k in ("learning_rate", "momentum", "next_trials"):
            if k in self.request and self.request[k] is not None:
                payload[k] = self.request[k]
        # If nothing provided, keep it an empty JSON object
        return json.dumps(payload)

    def _generate_slurm_script(self) -> str:
        """
        nodes = int(self.request.get("nodes", 2))
        cpus = int(self.request.get("cpus_per_task", 4))
        gpus = int(self.request.get("gpus_per_node", 1))
        time_limit = str(self.request.get("time", "00:30:00"))
        #job_name = str(self.request.get("job_name", "hpo-job"))
        # Default to your original script path; override via --script in request if needed
        script_to_run = str(
            self.request.get(
                "script",
                "/home/ubuntu/Vortex/pytorch-test-scripts/hpo_pipeline_verbose.py",
            )
        )
        
        port = int(self.request.get("port", 6379))  # matches export RAY_ADDRESS usage
        learning_rate = float(self.request.get("learning_rate", 0.01))
        momentum = float(self.request.get("momentum", 0.9))
        next_trials = int(self.request.get("next_trials", 2))

        )# Hardcoded shared log directory
        log_dir = "/fsx/slurm_res"
        os.makedirs(log_dir, exist_ok=True

        # Slurm expands %j to JOBID; resolve after sbatch.
        self.output_file = os.path.join(log_dir, f"{job_name}-%j.out")
        self.slurm_script = os.path.join(log_dir, "dispatcher.slurm")

        # Build the inline JSON (no shell expansion needed thanks to single-quoted HEREDOC)
        #json_payload = self._json_payload()
        """

        self.wf_id = self.request['wf_id']
        self.learning_rate = self.request['cohesion']['learning_rate']
        self.momentum = self.request['cohesion']['momentum']
        self.threads = self.request['cohesion']['threads']
        self.epoch = self.request['cohesion']['epoch']
        self.next_trials = self.request['cohesion']['next_trials']
        self.nodes = len(self.request['hosts']['on-prem'])

        # variables required for further function calls
        self.cpus_per_task = int(4) ## HARDCODED BECAUSE THE NODES ARE HOMOGENEOUS !!!
        self.gpus_per_node = int(1)
        self.time = str("00:60:00")

        # construct job name from workflow-id
        self.job_name = self.wf_id
        # Hardcoded shared log directory
        log_dir = "/fsx/slurm_res"
        os.makedirs(log_dir, exist_ok=True)
        # Slurm expands %j to JOBID; resolve after sbatch.
        self.output_file = os.path.join(log_dir, f"{self.job_name}-%j.out")
        self.error_file = os.path.join(log_dir, f"{self.job_name}-%j.err")
        self.slurm_script = os.path.join(log_dir, f"{self.job_name}_dispatcher.slurm")
        self.port = int(self.request.get('port', 6380))  # From port pool; default 6380 to avoid Redis (6379)

        slurm_script = f"""#!/bin/bash
#SBATCH -J {self.job_name}
#SBATCH -N {self.nodes}
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task={self.cpus_per_task}
#SBATCH --gres=gpu:{self.gpus_per_node}
#SBATCH --time={self.time}
#SBATCH --output={self.output_file}
#SBATCH --error={self.error_file}
#SBATCH -D {log_dir}

set -euxo pipefail

# Environment (adapt to your site)
source ~/rayenv/bin/activate || true
module load libfabric-aws/1.22.0amzn5.0 || true

# Get node info
nodes=$(scontrol show hostnames "$SLURM_NODELIST")
head_node=$(echo "$nodes" | head -n1)
head_ip=$(srun -N1 -n1 -w "$head_node" hostname -I | awk '{{print $1}}')
port={self.port}

echo "[INFO] Cluster nodes: $nodes"
echo "[INFO] Head Node: $head_node ($head_ip)"
echo "[INFO] Using RAY_ADDRESS=$head_ip:$port"

cleanup() {{
  echo "[INFO] Cleaning up Ray on all nodes..."
  for node in $nodes; do
    (srun --overlap -N1 -n1 -w "$node" ray stop || true)
  done
}}
trap cleanup EXIT

# Stop any old Ray (best-effort)
for node in $nodes; do
  (srun --overlap -N1 -n1 -w "$node" ray stop || true)
done

# Start Ray head (block in background)
srun --overlap -N1 -n1 -w "$head_node" ray start --head --node-ip-address="$head_ip" --port="$port" --block &

# Start Ray workers (block in background)
for node in $(echo "$nodes" | tail -n +2); do
  srun --overlap -N1 -n1 -w "$node" ray start --address="$head_ip:$port" --block &
done

# ---- Start HPO job on the Ray Head Node
echo "[INFO] Running HPO pipeline on head ...."
srun --overlap -N1 -n1 -w "$head_node" bash -lc "
export RAY_ADDRESS='$head_ip:$port'
echo '{{\\"learning_rate\\": {self.learning_rate}, \\"momentum\\": {self.momentum}, \\"next_trials\\": {self.next_trials}}}' \\
      | python3 /fsx/Vortex-mid/Vortex-moldable-sched/fsx/hyperparameter_test/hpo_pipeline_verbose.py --hosts {self.nodes} "

echo '[INFO] Shutting down Ray Cluster ..........'
srun --label bash -c 'ray stop' || true
echo '[INFO] HPO Completed!'
"""

        with open(self.slurm_script, "w", encoding="utf-8") as f:
            f.write(slurm_script)

        return self.slurm_script

    def _submit_slurm(self) -> str:
        if not self.slurm_script:
            raise RuntimeError("Slurm script not generated.")
        res = subprocess.run(
            ["sbatch", self.slurm_script],
            check=True,
            capture_output=True,
            text=True,
        )
        # "Submitted batch job <JOBID>"
        self.job_id = res.stdout.strip().split()[-1]
        # Resolve %j -> job_id in our intended path
        if self.output_file:
            self.output_file = self.output_file.replace("%j", self.job_id)
        # Also query Slurm for the real StdOut (in case scheduler rewrote it)
        self._resolve_output_from_scontrol()
        return self.job_id

    def _resolve_output_from_scontrol(self) -> None:
        """Best-effort: ask Slurm for the actual StdOut path."""
        if not self.job_id:
            return
        try:
            res = subprocess.run(
                ["scontrol", "show", "job", self.job_id],
                capture_output=True,
                text=True,
                check=True,
            )
            out = res.stdout
            # Tokens like: StdOut=/fsx/slurm_res/hpo-job-123456.out
            for token in out.split():
                if token.startswith("StdOut="):
                    real = token.split("=", 1)[1]
                    if real:
                        self.output_file = real
                        break
        except Exception:
            pass  # non-fatal

    def _wait_for_completion(self, poll_seconds: int = 8) -> None:
        if not self.job_id:
            raise RuntimeError("Job not submitted.")
        while True:
            res = subprocess.run(
                ["squeue", "--job", self.job_id],
                capture_output=True,
                text=True,
            )
            if self.job_id not in res.stdout:
                break
            time.sleep(poll_seconds)

    def _parse_results(self) -> dict:
        """
        Cloud parity + multi-line tolerant:
        - Find the last JSON object in the Slurm .out that contains "config".
        - If it has array(...), replace with np.array(...) and eval with restricted env.
        - Return {"config": ...} like cloud_runner_hpo.fetchOutput().
        """
        import re, json

        out = {"output_file": self.output_file, "job_id": self.job_id}
        if not self.output_file or not Path(self.output_file).exists():
            return out

        # Helpers
        output = {}
        client_output_filepath = self.output_file
        with open(client_output_filepath, 'r') as client_output:
            for line in client_output:
                if re.search('{"config":', line):
                    line = line.replace("array", "np.array")
                    line = eval(line)
                    output['config'] = line['config']
                    print(output)
                    break

        return output

    def run(self) -> dict:
        # Arguments from the workflow

        #self._generate_slurm_script(self.nodes, self.cpus_per_task, self.gpus_per_task, self.time, self.learning_rate, self.momentum, self.threads, self.epoch, self.next_trials, self.wf_id)
        self._generate_slurm_script()
        self._submit_slurm()
        self._wait_for_completion()
        return self._parse_results()


"""
# ---------------- Standalone test mode ----------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Standalone PLCL HPO Runner")
    # Keep arg names aligned with generate-slurm-dispatcher.py
    parser.add_argument("--nodes", type=int, default=2)
    parser.add_argument("--cpus-per-task", type=int, default=4)
    parser.add_argument("--gpus-per-node", type=int, default=1)
    parser.add_argument("--time", type=str, default="00:30:00")
    parser.add_argument("--job-name", type=str, default="hpo-job")
    parser.add_argument("--script", type=str,
                        default="/home/ubuntu/Vortex/pytorch-test-scripts/hpo_pipeline_verbose.py")
    parser.add_argument("--port", type=int, default=6379)

    # These become the JSON payload piped to the script:
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--momentum", type=float, default=0.9)
    parser.add_argument("--next-trials", type=int, default=2)

    args = parser.parse_args()
    request = vars(args)  # Namespace → dict

    out = PlclRunnerHPO(request).run()
    print(json.dumps(out, indent=2))
"""
