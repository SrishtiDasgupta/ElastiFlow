#!/bin/bash
#SBATCH --job-name=seis-server
#SBATCH --output=server_%A.out
#SBATCH --error=server_%A.err
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --time=04:00:00
#SBATCH --exclusive

cd $HOME

# Prepare host list
mapfile -t NODES < <(scontrol show hostnames $SLURM_NODELIST)
HOST_FILE=$HOME/server_hosts_${SLURM_JOB_ID}.txt
: > "$HOST_FILE"
for N in "${NODES[@]}"; do
getent hosts "$N" | awk '{print $1}' >> "$HOST_FILE"
done

export PORT=4242
export MACHINE_FILE="$HOST_FILE"
export CORES=$SLURM_CPUS_PER_TASK
export RANKS=2

# Log file for waitress
SERVER_LOG=$HOME/server_${SLURM_JOB_ID}_waitress.log

# Multi-prog conf file
CONF_FILE=$HOME/server_multi_${SLURM_JOB_ID}.conf
cat > "$CONF_FILE" <<EOF
0 bash -c 'cd /fsx/Seis-Bridge/tpv13 && source ~/.bashrc && waitress-serve --host=0.0.0.0 --port=\$PORT --threads=\$CORES tpv13server_wsgi_safe:app >> $SERVER_LOG 2>&1'
1 bash -c 'sleep infinity'
EOF

echo "[$(date)] Launching waitress server..."
srun --multi-prog "$CONF_FILE"
