#!/bin/bash
#SBATCH --job-name=seis-server
#SBATCH --nodes=2
#SBATCH --ntasks=2 # one task per node
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=24
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=server_%j.out
#SBATCH --error=server_%j.err
#SBATCH --export=ALL

# Move into your application directory
cd /fsx/Seis-Bridge/tpv13

# Load necessary modules
module load openmpi

#echo "=== DEBUG: PWD = $(pwd), contents:"
#ls -l .

# Get all node hostnames from SLURM
NODELIST=$(scontrol show hostname "$SLURM_JOB_NODELIST")
MACHINE_FILE=/tmp/hosts
> "$MACHINE_FILE"

echo "=== Gathering node IPs ==="
for NODE in $NODELIST; do
IP=$(ssh "$NODE" hostname -I | awk '{print $1}')
echo "$IP" >> "$MACHINE_FILE"
done

echo "=== MACHINE_FILE contents ==="
cat "$MACHINE_FILE"
export MACHINE_FILE=$MACHINE_FILE

# Get IP of the current node
THIS_NODE=$(hostname -I | awk '{print $1}')
FIRST_NODE=$(head -n1 "$MACHINE_FILE")

# Detect physical cores
PHYS_TOTAL=$(lscpu | awk '
/^Socket\(s\):/ { s = $2 }
/^Core\(s\) per socket:/ { c = $4 }
END { print s * c }')

WAITRESS_CORE=0
OVERHEAD_CORE=$((PHYS_TOTAL-1))
WORKER_RANGE="1-$((PHYS_TOTAL-2))"
NUM_WORKERS=$((PHYS_TOTAL-2))

export PORT=4242
export RANKS=2
export WORKER_RANGE
export NUM_WORKERS

echo "=== Environment Setup ==="
echo "THIS_NODE=$THIS_NODE"
echo "FIRST_NODE=$FIRST_NODE"
echo "NUM_WORKERS=$NUM_WORKERS"
echo "MACHINE_FILE=$MACHINE_FILE"
echo "WORKER_RANGE=$WORKER_RANGE"


# Start server only on the first node
if [[ "$THIS_NODE" == "$FIRST_NODE" ]]; then
echo "=== Launching waitress-serve on $THIS_NODE ==="
# Pin waitress to core 0 to avoid interfering with MPI cores
taskset -c $WAITRESS_CORE waitress-serve --host=$THIS_NODE --port=$PORT --threads=1 tpv13server_wsgi_safe:app 
else
echo "=== Secondary node ($THIS_NODE) waiting for MPI task ==="
sleep infinity
fi

#cd /fsx/Seis-Bridge/tpv13

#export OMP_NUM_THREADS=22
#export OMP_PLACES=cores

#srun --mpi=pmix -x OMPI_MCA_gds=^shmem2  --ntasks=1 --cpus-per-task=47 --cpu-bind=cores --threads-per-core=1 apptainer exec ../seissol.sif SeisSol_Release_sskx_4_elastic simulation_faae4d5b123895b96fa02e46fd2
