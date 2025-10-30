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

@ray.remote
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

    # Return Sampling
    #return tda.sample(posterior, proposal, iterations=iterations, n_chains=n_chains)

    # Use a ThreadPoolExecutor to call the evaluation in a separate thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: tda.sample(posterior, proposal, iterations=iterations, n_chains=n_chains))
        """
        try:
            # Wait for a short timeout
            result = future.result(timeout=600)
            print("RESULT AFTER RUN:")
            print(result)
            return result
        except concurrent.futures.TimeoutError:
            # If the simulation does not finish within the timeout, return an immediate response
            return {"job_id": "submitted", "status": "running"}
        """

        result = future.result()
        print("RESULT AFTER RUN:")
        print(result)
        return result

if __name__ == "__main__":

    print("Full sys.argv:", sys.argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("servers", nargs='+', help="List of servers with ports (ip:port)")
    parser.add_argument("--chains", type=int, required=True, help="Total number of chains")
    parser.add_argument("--iterations", type=int, required=True, help="Iterations per chain")
    parser.add_argument("--cohesion", type=float, required=True, help="Cohesion parameter")

    #print("sys.argv:", sys.argv)

    args = parser.parse_args()
    print("parsed args:", args)

    ray.init(ignore_reinit_error = True)
    
    # Attempt connection to all servers
    server_models = []
    for address in args.servers:
        connected = False
        while not connected:
            try:
                base_url = f"http://{address}"
                print(f"DEBUG using base URL: {base_url} with model name 'forward'")
                model = umbridge.HTTPModel(base_url, "forward")
                #expected_info_url = f"{base_url}/forward/Info"
                #print(f"DEBUG Expected Info URL: {expected_info_url}")
                
                
                sizes = model.get_input_sizes({})
                print(f"health check response from {address}: {sizes}")
                server_models.append(model)
                print(f"Connected to model server at {address}")
                connected = True
            except Exception as e:
                print(f"Server at {address} not available. Error:{e} ... Retrying...")
                time.sleep(10)

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

    # Distribute workload across servers
    num_servers = len(server_models)
    chains_per_server = args.chains // num_servers
    extra_chains = args.chains % num_servers
    
    ray_futures = []
    for i, model in enumerate(server_models):
        chains = chains_per_server + (1 if i < extra_chains else 0)
        url = model.url
        print(f"Dispatching {chains} chains to {url}")
        ray_futures.append(
            run_sampling_remote.remote(
                url, 
                None, #Place, E is defined remotely
                mean_prior.tolist(),
                data.tolist(),
                cov_likelihood.tolist(),
                np.eye(n_dim).tolist(),
                args.iterations,
                chains
            )
        )
    results = ray.get(ray_futures)

    # Print final results
    min_likelihood = []
    for i, res in enumerate(results):
        print(f"\nResults from Server {server_models[i].url}:")
        print(res)
        chain = f"chain_{0}"
        # the chian[0] is to be corrected ... this must be looped over res
        min_likelihood.append(res["chain_0"][0].likelihood)

    print("Total Execution Time:", time.time() - start_time)
