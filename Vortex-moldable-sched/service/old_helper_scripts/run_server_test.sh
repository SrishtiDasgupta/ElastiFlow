#!/bin/bash
#SBATCH --job-name=seis-server
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=server_%j.out
#SBATCH --error=server_%j.err
#SBATCH --export=ALL # export all our env vars into the job

# 1) Move into your app dir (must exist on the compute node)
cd /fsx/Seis-Bridge/tpv13


module load openmpi

# 2) DEBUG: show where we are and what files exist
echo "=== DEBUG: pwd=$(pwd), ls: ==="
ls -l .

# 3) Gather node‑local information (this runs on the compute node!)
NODE=$(hostname)
IP=$(hostname -I | awk '{print $1}')
echo "=== DEBUG: Running on node $NODE with IP $IP ==="

# 4) Write the MPI machine file
HOSTFILE=/tmp/hosts
echo "$IP" > "$HOSTFILE"
export MACHINE_FILE=$HOSTFILE
echo "=== DEBUG: MACHINE_FILE contents ==="
cat "$HOSTFILE"

# 5) Append to shared list (with locking)
SHARED_IP_FILE=$HOME/all_server_ips.txt
{
flock 200
echo "$IP" >> "$SHARED_IP_FILE"
} 200>>"${SHARED_IP_FILE}.lock"
echo "=== DEBUG: Shared IP list now: ==="
tail -n5 "$SHARED_IP_FILE" || echo "(file had fewer than 5 lines)"

# 6) Detect *physical* cores on *this* node
PHYS_TOTAL=$(lscpu | awk '
/^Socket\(s\):/ { s = $2 }
/^Core\(s\) per socket:/ { c = $4 }
END { print s * c }')
echo "=== DEBUG: physical cores (sockets×cores): $PHYS_TOTAL ==="

# 7) Carve out core assignments
WAITRESS_CORE=0
OVERHEAD_CORE=$((PHYS_TOTAL-1))
WORKER_RANGE="1-$((PHYS_TOTAL-2))"
NUM_WORKERS=$((PHYS_TOTAL-2))

echo "=== DEBUG: WAITRESS on core $WAITRESS_CORE"
echo "=== DEBUG: MPI overhead reserved on core $OVERHEAD_CORE"
echo "=== DEBUG: WORKER cores = $WORKER_RANGE (count: $NUM_WORKERS) ==="

# 8) Export the vars your Python/MPI code will need
export PORT=4242
export RANKS=1
export WORKER_RANGE
export NUM_WORKERS

# 9) Final sanity check of env
echo "=== DEBUG: env dump of relevant vars ==="
echo "PORT=$PORT"
echo "MACHINE_FILE=$MACHINE_FILE"
echo "WORKER_RANGE=$WORKER_RANGE"
echo "NUM_WORKERS=$NUM_WORKERS"
echo "PATH=$PATH"
which waitress-serve || echo ">> waitress-serve not found in PATH!"

# 10) Launch Waitress pinned to core 0 in the foreground
echo "=== DEBUG: exec taskset -c $WAITRESS_CORE waitress-serve --host=$IP --port=$PORT --threads=1 tpv13server_wsgi_safe:app ==="
#exec taskset -c $WAITRESS_CORE /home/ubuntu/.local/bin/waitress-serve \
#--host=$IP \
#--port=4242 \
#--threads=1 \
#tpv13server_wsgi_safe:app


module load openmpi

waitress-serve --host=$IP --port=4242 --threads=1 tpv13server_wsgi_safe:app


#srun --ntasks=1 --cpus-per-task=1 --exclusive  waitress-serve --host=$IP --port=4242 --threads=1 tpv13server_wsgi_safe:app


#export OMPI_MCA_btl_vader_single_copy_mechanism=none 
#export PMIX_MCA_gds=hash
#export OMP_NUM_THREADS=30

#srun --verbose --ntasks=1 --cpus-per-task=30  --cpu-bind=cores  --mpi=pmix --exclusive  apptainer exec ../seissol.sif SeisSol_Release_sskx_4_elastic simulation_faf67ec3fe0d60fb62551d50df35c7fb/parameters.par

#srun --verbose --ntasks=1 --cpus-per-task=30  --cpu-bind=cores --mpi=pmix --exclusive hostname
