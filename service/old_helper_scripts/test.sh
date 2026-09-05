#!/bin/bash
#SBATCH --job-name=seissol_1000_N
#SBATCH --nodes=1 # Adjust to how many nodes you want
#SBATCH --ntasks=1 # 1 MPI task per node
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=23 # 23 OpenMP threads per MPI task (1 core reserved for communication)
#SBATCH --time=02:00:00 # Max walltime
#SBATCH --output=%x.out # STDOUT file
#SBATCH --error=%x.err # STDERR file
#SBATCH --exclusive # Allocate whole node exclusively
##SBATCH --hint=nomultithread # Bind to physical cores only (important)

# Load necessary modules (adjust to your system)
module load openmpi

# Set OpenMP environment variables
export OMP_NUM_THREADS=23
#export OMP_PROC_BIND=spread
export OMP_PLACES=cores

# Move to application directory
cd /fsx/Seis-Bridge/tpv13

# Collect hostnames
HOSTS=$(scontrol show hostnames $SLURM_JOB_NODELIST | paste -sd,)


# Run the application
#mpirun --bind-to none  -np 2 --host $HOSTS \
#       -x OMP_NUM_THREADS -x OMP_PROC_BIND -x OMP_PLACES \
#        apptainer exec ../seissol.sif SeisSol_Release_sskx_4_elastic simulation_faae4d5b123895b96fa02e46fd24b9bf/parameters.par

#mpirun --bind-to none ../SeisSol_Release_sskx_4_elastic simulation_faae4d5b123895b96fa02e46fd24b9bf/parameters.par


srun --mpi=pmix --ntasks=1 --cpus-per-task=47 --cpu-bind=cores --threads-per-core=1 apptainer exec ../seissol.sif SeisSol_Release_sskx_4_elastic simulation_faae4d5b123895b96fa02e46fd24b9bf/parameters.par


#srun --hint=nomultithread --cpu-bind=cores --ntasks=36  bash -c 'echo $SLURM_PROCID on $(hostname) bound to $(taskset -cp $$)'

#srun --hint=nomultithread --cpu-bind=cores --ntasks=1 bash -c 'lscpu | grep Thread'

