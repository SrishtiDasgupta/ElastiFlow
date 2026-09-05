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
        return f"mpirun --report-bindings -x OMP_NUM_THREADS={threads} --map-by ppr:1:node:pe={cores} --mca plm_rsh_agent 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' -machinefile $MACHINE_FILE apptainer exec ../seissol.sif SeisSol_Release_sskx_{order}_elastic {run_id}/parameters.par"


class SeisSolServer(umbridge.Model):
    def __init__(self, ranks, cores, mesh):
        self.name = "SeisSol"
        self.ranks = ranks
        self.cores = cores-1
        self.threads = cores - 1
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
        my_env["OMP_NUM_THREADS"] = threads_config
        my_env["OMP_PLACES"] = cores_config
        return my_env

    def __call__(self, parameters, config):

        config["order"] = config.get("order", 4)
        run_id = self.prepare_filesystem(parameters, config, self.mesh)
        
        print(self.cores, self.threads, self.ranks)
        command = seissol_command(self.cores, self.threads, run_id, self.ranks, config["order"])
        print(command)
        my_env = self.prepare_env()
        sys.stdout.flush()
        subprocess.run("cat $MACHINE_FILE", shell=True)
        try:
                result = subprocess.run(command, shell=True, env=my_env)
                result.check_returncode()

                m = [misfits.misfit(run_id, self.reference_dir, self.prefix, i) for i in range(1, self.number_of_receivers+1)]

                output = [m]
        except Exception as e:
                output = [np.zeros(self.number_of_receivers)]
        output = [np.nan_to_num(o, nan=-1000).tolist() for o in output]
        return output

    def supports_evaluate(self):
        return True
