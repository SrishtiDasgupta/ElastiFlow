import argparse
import itertools
import numpy as np
import os
import time
import umbridge
import tinyDA as tda
from scipy.stats import multivariate_normal
import json
import ray
import sys
import concurrent.futures
import importlib
importlib.reload(tda)

print(tda.__file__)
print(sys.path)


def parse_args():
    p = argparse.ArgumentParser(
        description="tinyDA client running under Slurm, coordinating multiple UM-Bridge servers"
    )
    p.add_argument("--workers_file", required=True, help="List of servers with ports (ip:port)")
    p.add_argument("--workflow_id", required=True, help="workflow ID assigned by external scheduler/orchestrator")
    p.add_argument("--orchestrator_url", required=True, help="Base URL of orchestrator,ie, the HEAD node")
    p.add_argument("--chains", type=int, required=True, help="Total number of chains")
    p.add_argument("--iterations", type=int, required=True, help="Iterations per chain")
    p.add_argument("--cohesion", type=float, required=True, help="Cohesion parameter")
    return p.parse_args()

def load_workers(workers_file_path):
    workers = []
    with open(workers_file_path, 'r') as f:
        for line in f:
            worker = line.strip()
            if worker:
                workers.append(worker)
    return workers

@ray.remote(num_cpus=0)
def run_sampling_remote(server_url, E_func, prior_params, data, cov_likelihood, proposal_cfg, iterations, n_chains):
    import umbridge
    import tinyDA as tda
    from scipy.stats import multivariate_normal
    import numpy as np
    import concurrent.futures

    # Set up transfromation and model
    E = lambda x: np.array(x)
    model = umbridge.HTTPModel(f"{server_url}", "forward")
    model = tda.UmBridgeModel(model, pre=E)

    # Set up prior and likelihood
    n_dim = len(prior_params)
    prior = multivariate_normal(prior_params, np.eye(n_dim) * 1.36e12, allow_singular=True)
    loglike = tda.GaussianLogLike(np.array(data), np.array(cov_likelihood))

    proposal = tda.GaussianRandomWalk(C=np.array(proposal_cfg))
    posterior = tda.Posterior(prior, loglike, model)

        # Use a ThreadPoolExecutor to call the evaluation in a separate thread.
    chains = tda.sample(posterior, proposal, iterations=iterations, n_chains=n_chains)
    return chains

def main():
    args = parse_args()

    workers = load_workers(args.workers_file)
    print(f"Loaded workers: {workers}")

    print("Connecting to Ray......")
    ray.init(ignore_reinit_error = True)
    
    # Attempt connection to all servers
    server_models = []
    for address in workers:
        url = f"http://{address}:4242"
        connected = False
        while not connected:
            try:
                model = umbridge.HTTPModel(url, "forward")
                print(f"[Healthcheck] {url} ->", model.get_input_sizes({}))                
                sizes = model.get_input_sizes({})
                print(f"health check response from {url}: {sizes}")
                server_models.append(model)
                print(f"Connected to model server at {url}")
                connected = True
            except Exception as e:
                print(f"Server at {url} not available. Error:{e} ... Retrying...")
                time.sleep(5)

    if not server_models:
        raise RuntimeError("No model servers available. Exiting.")

    # Define transformation function
    E = lambda x: np.array(x)

    # Initialize models for each server
    tda_models = [tda.UmBridgeModel(server, pre=E) for server in server_models]

    # Define prior
    n_dim = server_models[0].get_input_sizes()[0]  # Assume all servers have same input size
    mean_prior = np.zeros(n_dim) + args.cohesion
    cov_prior = np.eye(n_dim) * 1.36e12
    prior = multivariate_normal(mean_prior, cov_prior, allow_singular=True)

    # Generate data
    log_E_true = prior.rvs()
    params = np.array([log_E_true])
    data = tda_models[0](params)  # Get data from any model (assumed identical)
    print(data)
    # Define likelihood
    sigma_like = 0.1
    cov_likelihood = sigma_like**2 * np.eye(data.shape[0])
    loglike = tda.GaussianLogLike(data, cov_likelihood)

    # Proposal distribution
    proposal = tda.GaussianRandomWalk(C=np.eye(n_dim))

    start_time = time.time()

    #extra_chains = args.chains % num_servers
    
    round_no = 1
    while True:
        print(f"\n>>> Round {round_no}: dispatching {args.chains} chains ....")
        #Split chains evenly
        num_servers = len(server_models)
        base, extra = divmod(args.chains, num_servers)
        ray_futures = []
        for i, m in enumerate(server_models):
            nch = base + (1 if i < extra else 0)
            print(f" . {nch} chains -> {m.url}")
            ray_futures.append(
                run_sampling_remote.remote(
                    m.url, 
                    None, #Place, E is defined remotely
                    mean_prior.tolist(),
                    data.tolist(),
                    cov_likelihood.tolist(),
                    np.eye(n_dim).tolist(),
                    args.iterations,
                    nch
                )  
            )
        results = ray.get(ray_futures)

        output = {"likelihood": 100, "input_parameters": 0} 
        # iterator = 0
        print(results)
        for i, res in enumerate(results):
            print(f"\nResults from Server {server_models[i].url}:")
            print(res)
            for i in range(res["n_chains"]):
                chain = f"chain_{i}"
                if chain in res and res[chain]:
                    print(f"INPUT PARAMETERS for {chain} : {res[chain][-1].parameters}" )
                    print("Likelihood: ", res[chain][-1].likelihood)
                    if res[chain][-1].likelihood < output["likelihood"]:
                        output["likelihood"] = res[chain][-1].likelihood
                        output["input_parameters"] = res[chain][-1].parameters

                else:
                    print(f"Chain {chain} not found in results from server {server_models[i].url}.")
            
            
        print(output)
            # the chian[0] is to be corrected ... this must be looped over res
            # min_likelihood.append(res["chain_0"][0].likelihood)

        print("Total Execution Time:", time.time() - start_time)

        """
        print(f"[Client] Collected {len(flat)} likelihoods; sending orchestrator ....")
        payload = {
            "workflow_id": args.workflow_id,
            "rounds": round_no,
            "results": flat
        }

        resp = requests.post(f"{args.orchestrator_url}/lient_results", json=payload).json()
        print("[Scheduler] replied: ", resp)

        if not resp.get("continue", False):
            print("[Client] Scheduler said STOP. Exiting")
            break

        # optionally handle dynamic scaling
        new_n = resp["num_workers"]
        if new_n != len(server_models):
            print(f"[Client] Scheduler wants {new_n} workers")

        round_no +=1

        """

    print("[Client] Workflow complete, shutting down")
    ray.shutdown()

if __name__ == "__main__":
    main()
