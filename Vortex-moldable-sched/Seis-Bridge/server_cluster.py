import hashlib
import jinja2
import misfits
import numpy as np
import os
import subprocess
import sys
import umbridge
import time


def gpu_available():
    # Hard coded for now, until I find a better way to automatically check,
    # whether a GPU is available.
    # return True
    return False


def seissol_command(cores, threads, run_id="", ranks=4, order=4):
    if gpu_available():
        return f"mpirun -n {ranks} -bind-to none seissol-launch SeisSol_Release_ssm_86_cuda_{order}_elastic parameters.par"
    else:
        return f"srun --verbose --ntasks=1 --cpus-per-task=30 --cpu-bind=cores --export=ALL,OMP_NUM_THREADS=30 --mpi=pmix  apptainer exec ../seissol.sif SeisSol_Release_sskx_{order}_elastic {run_id}/parameters.par"

class SeisSolServer(umbridge.Model):
    def __init__(self, ranks, cores, mesh):
        self.name = "SeisSol"
        self.ranks = ranks
        self.cores = cores-1
        # self.cores = cores
        self.threads = cores - 1
        # self.threads = cores*2
        self.mesh = mesh
        super().__init__("forward")

    def get_input_sizes(self, config):
        return [self.number_of_parameters]

    def get_output_sizes(self, config):
        return [self.number_of_receivers]

    def prepare_parameter_files(self, parameters, run_id, mesh):
        pass

    def prepare_filesystem(self, parameters, config, mesh):   
        submission_time = time.ctime(time.time())
        param_conf_string = str((parameters, config, submission_time)).encode("utf-8")
        print(param_conf_string) 

        m = hashlib.md5()
        m.update(param_conf_string)
        h = m.hexdigest()
        run_id = f"simulation_{h}"
        print(run_id)

        subprocess.run(["rm", "-rf", run_id])
        subprocess.run(["mkdir", run_id])
        self.prepare_parameter_files(parameters, run_id, mesh)

        return run_id
    
    def prepare_env(self):
        cores_config = "cores(" + str(self.cores) + ")"
        threads_config = str(self.threads)
        my_env = os.environ.copy()
        my_env["MV2_ENABLE_AFFINITY"] = "0"
        my_env["MV2_HOMOGENEOUS_CLUSTER"] = "1"
        my_env["MV2_SMP_USE_CMA"] = "0"
        my_env["MV2_USE_AFFINITY"] = "0"
        my_env["MV2_USE_ALIGNED_ALLOC"] = "1"
        my_env["TACC_AFFINITY_ENABLED"] = "1"
        my_env["OMPI_MCA_btl_vader_single_copy_mechanism"] = "none"
        my_env["PMIX_MCA_gds"] = "hash"
        my_env["OMP_NUM_THREADS"] = "23"
        my_env["OMP_PLACES"] = "cores"
        #my_env["OMP_PROC_BIND"] ="spread"
        my_env["OMP_DISPLAY_ENV"] = "TRUE"
        print("printing all env var")
        for key, value in os.environ.items():
            print(f"{key}={value}")
        return my_env

    def __call__(self, parameters, config):

        config["order"] = config.get("order", 4)
        run_id = self.prepare_filesystem(parameters, config, self.mesh)
        
        print("Inside server.py")
        print(self.cores, self.threads, self.ranks)
        #command = seissol_command(self.cores, self.threads, run_id, self.ranks, config["order"])
        #print(command)
        my_env = self.prepare_env()
        sys.stdout.flush()
        num_workers = int(os.environ.get("NUM_WORKERS", 15))
        subprocess.run("cat $MACHINE_FILE", shell=True)
        try:
                #command = [
                #        "srun", "--verbose", "--nodes=2", "--ntasks=2", "--ntasks-per-node=1",  f"--cpus-per-task={num_workers}", "--cpu-bind=cores", "--mpi=pmix", "--exclusive","--hint=nomultithread", "--threads-per-core=1", "--export=ALL, OMP_NUM_THREADS=34, OMP_PROC_BIND=spread, OMP_PLACES=cores",  "apptainer", "exec", "../seissol.sif", "SeisSol_Release_sskx_4_elastic", f"{run_id}/parameters.par"
                #         ]

                command = [
                        "srun", "--verbose", "--mpi=pmix", "--nodes=2", "--ntasks=2", "--ntasks-per-node=1", "--cpus-per-task=47", "--cpu-bind=cores", "--threads-per-core=1", "apptainer", "exec", "../seissol.sif", "SeisSol_Release_sskx_4_elastic", f"{run_id}/parameters.par"
                        ]
                result = subprocess.run(command, env=my_env,  capture_output=True, text=True)
                
                print("=== MPI STDOUT ===")
                print(result.stdout)

                print("=== MPI STDERR ===")
                print(result.stderr)

                print("=== MPI RETURN CODE ===")
                print(result.returncode)

                
                result.check_returncode()

                print("result:")
                print(result)
                print("What's happening .......")
                print(run_id, self.reference_dir, self.prefix)
                m = [misfits.misfit(run_id, self.reference_dir, self.prefix, i) for i in range(1, self.number_of_receivers+1)]
                print("after misfits:")
                print(m)

                output = [m]
                print("output:")
                print(output)
        except subprocess.CalledProcessError as e:
            print("Subprocess failed with non-zero exit:")
            print("Return code:", e.returncode)
            print("STDOUT:", e.stdout)
            print("STDERR:", e.stderr)
        except Exception as e:
                import traceback
                print("=== UNHANDLED EXECPTION ===")
                traceback.print_exc()
                output = [np.zeros(self.number_of_receivers)]
        output = [np.nan_to_num(o, nan=-1000).tolist() for o in output]
        print("Output before return.....")
        print(output)
        return output

    def supports_evaluate(self):
        return True