# this is the file which should be run on the head node to communicate with the external scheduler 
from flask import Flask, request, jsonify
import subprocess, os, uuid

app = Flask(__name__)
active = {} # workflow_id → slurm_job_id

SLURM_TEMPLATE = """#!/bin/bash
#SBATCH --job-name=wf_{wid}
#SBATCH --nodes={total}
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=02:00:00
#SBATCH --output=client_{wid}.out

source ~/venv/bin/activate

ALL=($(scontrol show hostnames $SLURM_JOB_NODELIST))
# head = first node, workers = next N
WORKERS=("${{ALL[@]:1:{nworkers}}}")
printf "%s\n" "${{WORKERS[@]}}" > workers_{wid}.txt

# start server on each worker
for node in "${{WORKERS[@]}}"; do
srun --nodes=1 --ntasks=1 -w $node \
python /fsx/Seis-Bridge/tpv13server_wsgi_safe.py --port 4242 --ranks 4 --cores 8 --mesh 1000 &
done
sleep 30

# start the client
python /fsx/tinyda_client.py \
--workflow_id {wid} \
--scheduler_url {sched} \
--workers_file workers_{wid}.txt \
--chains {chains} \
--iterations {iters} \
--cohesion {cohesion}
"""

@app.route('/start_workflow', methods=['POST'])
def start_workflow():
    data = request.get_json()
    # Required fields from external scheduler:
    wid = data.get("workflow_id", str(uuid.uuid4()))
    sched = data["scheduler_url"]
    nworkers = data["num_workers"]
    chains = data["n_chains"]
    iters = data["iterations"]
    cohesion = data["cohesion"]

    total_nodes = nworkers + 1
    script = SLURM_TEMPLATE.format(
        wid=wid,
        total=total_nodes,
        nworkers=nworkers,
        sched=sched,
        chains=chains,
        iters=iters,
        cohesion=cohesion
    )
    path = f"launch_{wid}.sh"
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, 0o755)

    out = subprocess.check_output(f"sbatch {path}", shell=True).decode().strip()
    slurm_id = out.split()[-1]
    active[wid] = slurm_id
    return jsonify({"workflow_id": wid, "slurm_job_id": slurm_id})

@app.route('/status/<wid>', methods=['GET'])
def status(wid):
    if wid not in active:
        return jsonify({"error": "unknown workflow"}), 404
    return jsonify({"workflow_id": wid, "slurm_job_id": active[wid]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

