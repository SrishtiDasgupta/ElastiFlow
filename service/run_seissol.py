import sys
import ast
from plcl_runner import PlclRunner
from cloud_runner import CloudRunner
from seissol_runner_base import SeisSolRunner


# === Factory =====
def get_runner(mode, args):
    if mode == "cloud":
        return CloudRunner(args)
    elif mode == "on-prem":
        return PlclRunner(args)
    else:
        raise ValueError(f"Invalid mode: {mode}")
    

# ==== Main Dispatcher =====
if __name__ == "__main__":
    val = sys.argv[1:] # ["{'cohesion': 3, 'hosts': {'hpc6id.32xlarge': []}, 'chains': 3, 'tinyda_iterations': 12}"] , 'c6i.16xlarge': ['10.19.224.55', '10.19.204.6']
    request = eval(val[0])
    hosts = request['hosts']
    host_type = next(iter(hosts))

    if 'on-prem' in host_type:
        runner = get_runner("on-prem", request)
    else:
        runner = get_runner("cloud", request)

    runner.run()

    # request = {'cohesion': 3e10, 'hosts': {'c7i.24xlarge': ['10.3.14.42', '10.3.14.43']}, 'chains': 2, 'tinyda_iterations': 1, 'mesh': 1000
    # used_hosts = ['10.3.14.38']
    #runClient(request, used_hosts)
    #fetchOutput()

    pass
