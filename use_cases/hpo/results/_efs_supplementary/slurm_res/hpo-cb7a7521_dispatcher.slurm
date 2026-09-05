#!/bin/bash
#SBATCH -J hpo-cb7a7521
#SBATCH -N 2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --time=00:60:00
#SBATCH --output=/fsx/slurm_res/hpo-cb7a7521-%j.out
#SBATCH --error=/fsx/slurm_res/hpo-cb7a7521-%j.err
#SBATCH -D /fsx/slurm_res

set -euxo pipefail

# Environment (adapt to your site)
source ~/rayenv/bin/activate || true
module load libfabric-aws/1.22.0amzn5.0 || true

# Get node info
nodes=$(scontrol show hostnames "$SLURM_NODELIST")
head_node=$(echo "$nodes" | head -n1)
head_ip=$(srun -N1 -n1 -w "$head_node" hostname -I | awk '{print $1}')
port=7171

echo "[INFO] Cluster nodes: $nodes"
echo "[INFO] Head Node: $head_node ($head_ip)"
echo "[INFO] Using RAY_ADDRESS=$head_ip:$port"

cleanup() {
  echo "[INFO] Cleaning up Ray on all nodes..."
  for node in $nodes; do
    (srun --overlap -N1 -n1 -w "$node" bash -c "source ~/rayenv/bin/activate && ray stop" || true)
  done
}
trap cleanup EXIT

# Stop any old Ray (best-effort)
for node in $nodes; do
  (srun --overlap -N1 -n1 -w "$node" bash -c "source ~/rayenv/bin/activate && ray stop" || true)
done

# Start Ray head (block in background) — explicit venv activation
srun --overlap -N1 -n1 -w "$head_node" bash -c "source ~/rayenv/bin/activate && ray start --head --node-ip-address=$head_ip --port=$port --block" &

# Wait for Ray head GCS port to be ready (up to 60s)
echo "[INFO] Waiting for Ray head GCS on $head_ip:$port ..."
for attempt in $(seq 1 12); do
  if python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('$head_ip',$port)); s.close(); print('READY')" 2>/dev/null | grep -q READY; then
    echo "[INFO] Ray head GCS ready after $((attempt*5))s"
    break
  fi
  if [ "$attempt" -eq 12 ]; then
    echo "[ERROR] Ray head GCS not ready after 60s — aborting"
    exit 1
  fi
  echo "[INFO] GCS not ready yet (attempt $attempt/12), waiting 5s..."
  sleep 5
done

# Start Ray workers (block in background) — explicit venv activation
for node in $(echo "$nodes" | tail -n +2); do
  srun --overlap -N1 -n1 -w "$node" bash -c "source ~/rayenv/bin/activate && ray start --address=$head_ip:$port --block" &
done

# Wait for workers to register
echo "[INFO] Waiting 15s for Ray workers to join ..."
sleep 15

# ---- Start HPO job on the Ray Head Node
echo "[INFO] Running HPO pipeline on head ...."
srun --overlap -N1 -n1 -w "$head_node" bash -lc "
export RAY_ADDRESS='$head_ip:$port'
echo '{\"learning_rate\": 0.018417711995656477, \"momentum\": 0.804415256682956, \"next_trials\": 6, \"epochs\": 8}' \
      | python3 /fsx/Vortex-mid/Vortex-moldable-sched/fsx/hyperparameter_test/hpo_pipeline_verbose.py --hosts 2 "

echo '[INFO] Shutting down Ray Cluster ..........'
srun --label bash -c 'ray stop' || true
echo '[INFO] HPO Completed!'
