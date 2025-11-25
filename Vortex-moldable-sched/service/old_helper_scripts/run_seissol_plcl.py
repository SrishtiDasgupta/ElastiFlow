import sys
import subprocess
import time
from pathlib import Path
import os
import json

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

echo "=== MACHINE_FILE contents ==="
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

export PORT=4242
export RANKS={nodes}
export WORKER_RANGE
export NUM_WORKERS
export NUM_NODES

if [[ "$THIS_NODE" == "$FIRST_NODE" ]]; then
echo "=== Launching waitress-serve on $THIS_NODE ==="
taskset -c $WAITRESS_CORE waitress-serve --host=$THIS_NODE --port=$PORT --threads=1 tpv13server_wsgi_safe:app
else
echo "=== Secondary node ($THIS_NODE) waiting for MPI task ==="
sleep infinity
fi
"""

def write_slurm_script(nodes, chain_id, filename_prefix="seis_server_job"):
    filename = f"{filename_prefix}_{chain_id}.sh"
    script_content = SLURM_TEMPLATE.format(nodes=nodes)
    with open(filename, "w") as f:
        f.write(script_content)
        print(f"[Chain {chain_id}] SLURM script written to {filename}")
    return filename

def submit_job(script_filename):
    result = subprocess.run(["sbatch", script_filename], capture_output=True, text=True)
    if result.returncode != 0:
        print("Job submission failed:", result.stderr)
        sys.exit(1)
    job_id = result.stdout.strip().split()[-1]
    print(f"Submitted job with ID {job_id}")
    return job_id

def fetch_hosts_from_first_node(job_id, job_output_path="job_ips"):
    os.makedirs(job_output_path, exist_ok=True)
    out_file = Path(f"{job_output_path}/{job_id}.hosts")

    print(f"Waiting for /tmp/hosts from job {job_id}...")

    for _ in range(60): # try for 2 minutes
        try:
            result = subprocess.run(
                ["srun", "--jobid", job_id, "--ntasks=1", "cat", "/tmp/hosts"],
                capture_output=True, text=True
            )   
            if result.returncode == 0 and result.stdout.strip():
                ips = [line.strip() for line in result.stdout.splitlines()]
                with open(out_file, "w") as f:
                    f.write("\n".join(ips))
                update_index(job_id, ips, job_output_path)
                print(f"Saved IPs for job {job_id} to {out_file}")
                return ips
        except Exception as e:
            print(f"Error trying to fetch IPs: {e}")

        time.sleep(2)

    print(f"Failed to retrieve IPs for job {job_id}")
    return []

def run_client_script(first_ips, num_chains, iterations, cohesion):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "run_client_script_mult.sh")
    cmd = [ script_path, f"{num_chains}", f"{iterations}", f"{cohesion}"] #f"{ip}:{port}"]
    ips_only = [ip for _, ip in first_ips]
    for ip in ips_only:
        cmd.append(f"{ip}:4242")
    print("\n==== cmd generated ====")
    print(cmd)
    print(f"\n=== Running client script: {' '.join(cmd)} ===")

    try:        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("=== Client script output ===")
        print(result.stdout)
        if result.stderr:
            print("=== Client script errors ===")
            print(result.stderr)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print("=== Client script FAILED ===")
        print(e.stdout)
        print(e.stderr)
        return f"FAILED: {e.stderr}"

def update_index(job_id, ips, path):
    index_path = Path(path) / "index.json"
    index = {}
    if index_path.exists():
        with open(index_path) as f:
            index = json.load(f)
    index[str(job_id)] = ips
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Updated index at {index_path}")

if __name__ == "__main__":
    print("I am inside plcl main")
    if len(sys.argv) != 5:
        print("Usage: python run_seissol_plcl.py <number_of_nodes> <chains> <iterations> <cohesion>")
        sys.exit(1)

    num_nodes = int(sys.argv[1])
    num_chains = int(sys.argv[2])
    iterations = int(sys.argv[3])
    cohesion = sys.argv[4]
    first_ips = []
    job_ids = []

    for chain_id in range(1, num_chains + 1):
        print(f"\n=== Submitting Chain {chain_id}/{num_chains} ===")
        script_path = write_slurm_script(num_nodes, chain_id)
        job_id = submit_job(script_path)
        job_ids.append(job_id)
        ips = fetch_hosts_from_first_node(job_id)

        if num_nodes >= 1 and ips:
            first_ip = ips[0]
            first_ips.append((job_id, first_ip))
            print(f"[Chain {chain_id}] waitress-serve on {first_ip}")
        else:
            print(f"[Chain {chain_id}] Skipped storing waitress IP (only 1 node or failed fetch)")

    print("\n=== Summary of waitress-serve IPs per chain ===")
    for job_id, ip in first_ips:
        print(f"Job {job_id}: waitress-serve on {ip}")


    print("\n ==== Start the Client and Dispatch Chains ======")
    output = run_client_script(first_ips, num_chains, iterations, cohesion)

    print("\n ==== Cleanup Script =====")
    if output :
        # scancel waitress servers on 4242
        for jid in job_ids:
            subprocess.run(["scancel", jid]) 

        # remove output and error files
        for jid in job_ids:
            for suffix in [".out", ".err"]:
                path = f"server_{jid}{suffix}"
                try:
                    os.remove(path)  
                    print(f"Deleted {path}")
                except FileNotFoundError:
                    pass

        # remove slurm submission scripts
        for chain_id in range(1, num_chains + 1):
            script_path = f"seis_server_job_{chain_id}.sh"
            try:
                os.remove(script_path)
                print(f"Deleted {script_path}")
            except FileNotFoundError:
                pass

        # Remove IP folder
        ip_dir = Path("job_ips")
        if ip_dir.exists() and ip_dir.is_dir():
            for file in ip_dir.glob("*"):
                file.unlink()
            ip_dir.rmdir()
            print("Deleted job_ips directory")