# plcl_runner.py
import sys
import subprocess
import time
from pathlib import Path
import os
import json
import re
import numpy as np
from seissol_runner_base import SeisSolRunner

class PlclRunner(SeisSolRunner):
    #print("Inside plcl runner ... ")
    SLURM_TEMPLATE = """#!/bin/bash
#SBATCH --job-name=seis-server
#SBATCH --nodes={nodes}
#SBATCH --ntasks={nodes}
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=server_%j.out
#SBATCH --error=server_%j.err
#SBATCH --export=ALL

cd /fsx/Seis-Bridge/tpv13
module load openmpi

NODELIST=$(scontrol show hostname "$SLURM_JOB_NODELIST")
MACHINE_FILE=/tmp/hosts
> "$MACHINE_FILE"

echo "=== Gathering node IPs ==="
for NODE in $NODELIST; do
IP=$(ssh "$NODE" hostname -I | awk '{{print $1}}')
echo "$IP" >> "$MACHINE_FILE"
done

cat "$MACHINE_FILE"
export MACHINE_FILE=$MACHINE_FILE

THIS_NODE=$(hostname -I | awk '{{print $1}}')
FIRST_NODE=$(head -n1 "$MACHINE_FILE")

PHYS_TOTAL=$(lscpu | awk '
/^Socket\\(s\\):/ {{ s = $2 }}
/^Core\\(s\\) per socket:/ {{ c = $4 }}
END {{ print s * c }}')

WAITRESS_CORE=0
OVERHEAD_CORE=$((PHYS_TOTAL-1))
WORKER_RANGE="1-$((PHYS_TOTAL-2))"
NUM_WORKERS=$((PHYS_TOTAL-2))
NUM_NODES={nodes}

export PORT={port}
export RANKS={nodes}
export WORKER_RANGE
export NUM_WORKERS
export NUM_NODES
export MESH={mesh}

if [[ "$THIS_NODE" == "$FIRST_NODE" ]]; then
taskset -c $WAITRESS_CORE waitress-serve --host=$THIS_NODE --port=$PORT --threads=1 tpv13server_wsgi_safe:app
else
sleep infinity
fi
"""

    def write_slurm_script(self, nodes, mesh, chain_id, port, wf_id, filename_prefix="seis_server_job"):
        filename = f"{filename_prefix}_{wf_id}_{port}_{chain_id}.sh"
        script_content = self.SLURM_TEMPLATE.format(nodes=nodes, mesh=mesh, port=port)
        with open(filename, "w") as f:
            f.write(script_content)
        return filename

    def submit_job(self, script_filename):
        result = subprocess.run(["sbatch", script_filename], capture_output=True, text=True)
        if result.returncode != 0:
            print("Job submission failed:", result.stderr)
            sys.exit(1)
        return result.stdout.strip().split()[-1]

    def fetch_hosts_from_first_node(self, job_id, job_output_path="job_ips"):
        os.makedirs(job_output_path, exist_ok=True)
        out_file = Path(f"{job_output_path}/{job_id}.hosts")

        for _ in range(60):
            result = subprocess.run(
            ["srun", "--jobid", job_id, "--ntasks=1", "cat", "/tmp/hosts"],
            capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                ips = [line.strip() for line in result.stdout.splitlines()]
                with open(out_file, "w") as f:
                    f.write("\n".join(ips))
                self.update_index(job_id, ips, job_output_path)
                return ips
            time.sleep(2)

        print(f"Failed to retrieve IPs for job {job_id}")
        return []

    def update_index(self, job_id, ips, path):
        index_path = Path(path) / "index.json"
        index = {}
        if index_path.exists():
            with open(index_path) as f:
                index = json.load(f)
        index[str(job_id)] = ips
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

    def run_client_script(self, first_ips, num_chains, iterations, cohesion, port):
        script_path = Path(__file__).parent / "run_client_script_mult.sh"
        cmd = [str(script_path), f"{num_chains}", f"{iterations}", f"{cohesion}"]
        cmd.extend([f"{ip}:{port}" for _, ip in first_ips])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            #print(result.stdout)
            return result.stdout
        except subprocess.CalledProcessError as e:
            #print(e.stdout)
            print(e.stderr)
            return None

    def cleanup(self, job_ids, num_chains, wf_id, port):
        for jid in job_ids:
            subprocess.run(["scancel", jid])
            for suffix in [".out", ".err"]:
                try:
                    os.remove(f"server_{jid}{suffix}")
                except FileNotFoundError:
                    pass

        for chain_id in range(1, num_chains + 1):
            try:
                os.remove(f"seis_server_job_{wf_id}_{port}_{chain_id}.sh")
            except FileNotFoundError:
                pass

        ip_dir = Path("job_ips")
        if ip_dir.exists():
            for file in ip_dir.glob("*"):
                file.unlink()
            ip_dir.rmdir()

    def map_chains_to_nodes(self, nodes, chains):
        mapping = {i: [] for i in range(chains)}

        if nodes <= chains:
            # More chains than nodes: distribute chains across nodes, nodes will repeat
            for i in range(chains):
                mapping[i].append(i % nodes)
        else:
            # More nodes than chains: distribute nodes across chains
            base = nodes // chains
            remainder = nodes % chains
            node_id = 0
            for i in range(chains):
                extra = 1 if i < remainder else 0
                mapping[i] = list(range(node_id, node_id + base + extra))
                node_id += base + extra

        return mapping

    def run(self):
        #print("Inside run function of plcl ... ")
        args = self.args
        wf_id = args['wf_id']
        cohesion = int(args['cohesion'])
        num_chains = int(args['chains'])
        num_nodes = int(len(args['hosts']['on-prem']))  #### THIS MIGHT BE WRONG !!!!!!!
        iterations = int(args['tinyda_iterations'])
        mesh = int(args['mesh'])
        port = int(args['port'])

        chains_to_nodes = self.map_chains_to_nodes(num_nodes, num_chains)

        first_ips = []
        job_ids = []


        for chain_id in range(1, num_chains + 1):
            nodes_for_chain = len(chains_to_nodes[chain_id - 1]) 
            script_path = self.write_slurm_script(nodes_for_chain, mesh, chain_id, port, wf_id)
            #script_path = self.write_slurm_script(nodes_per_chain, mesh, chain_id, port, wf_id)
            job_id = self.submit_job(script_path)
            job_ids.append(job_id)
            ips = self.fetch_hosts_from_first_node(job_id)
            if ips:
                first_ips.append((job_id, ips[0]))

        output = self.run_client_script(first_ips, num_chains, iterations, cohesion, port)
        output_object = dict()
        

        if output:
            lines = output.split('\n')
            for line in lines:
                if re.search("likelihood", line):
                    line = line.replace("array", "np.array")
                    #print(line)
                    output_object = eval(line)
                     

        self.cleanup(job_ids, num_chains, wf_id, port)