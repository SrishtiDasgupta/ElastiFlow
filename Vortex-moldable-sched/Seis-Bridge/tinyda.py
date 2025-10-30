import argparse
import itertools
import multiprocessing as mp
import numpy as np
import os
import time
import umbridge
import tinyDA as tda
from scipy.stats import multivariate_normal
import json

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="port", type=int)
    parser.add_argument("chains", help="chains", type=int)
    parser.add_argument("iterations", help="iterations", type=int)
    parser.add_argument("cohesion", help="cohesion", type=float)
    args = parser.parse_args()
    address = f"http://localhost:{args.port}"
    server_available = False
    while not server_available:
        try:
            model = umbridge.HTTPModel(address, "forward")
            print("Server available")
            server_available = True

        except:
            print("Server not available")
            time.sleep(10)

    start_time = time.time()
    E = lambda x: np.array(x)
    tda_model = tda.UmBridgeModel(model, pre = E)
    n_dim = model.get_input_sizes()[0] # 1
    # Set prior
    mean_prior = np.zeros(n_dim) + args.cohesion # TODO: randomize between [5e4 5e8] in wf
    cov_prior = np.eye(n_dim) * 1.36e12 # TODO: Calculate new cov for [5e4 5e8]
    prior = multivariate_normal(mean_prior, cov_prior, allow_singular=True)
    # Get data (can add noise if needed)
    log_E_true = prior.rvs()
    params = np.array([log_E_true])
    data = tda_model(params)
    print(data)
    # TODO: Add noise
    # data = np.array([0.16548211102121133, 0.10009936999735425, 0.3063361710114957, 0.09183226689188462, 0.06786203933175347, 0.14964602482686096, 0.09760305905160768, 0.070179070290844, 0.07502393292274322, 0.05862669585853282, 0.09774360840157227, 0.10530374636225937, 0.09503648926458205, 0.09708766433530303, 0.07170645408620686, 0.11424754327742728, 0.11732727332405052, 0.1343788893754726, 0.09462409894887182, 0.09853320811296683])
    # Set liklihood
    sigma_like = 0.1 # what value?
    cov_likelihood = sigma_like**2*np.eye(data.shape[0])
    loglike = tda.GaussianLogLike(data, cov_likelihood)
    # Finally initialize the posterior
    posterior = tda.Posterior(prior, loglike, tda_model) 
    # Initialize a proposal
    proposal = tda.GaussianRandomWalk(C=np.eye(n_dim))

    # Sample - this creates n chains, each of which has m iterations
    chains = tda.sample(posterior, proposal, iterations=args.iterations, n_chains=args.chains) #DEBUG
    # print(f"chains: {chains}")
    # Extract parameters
    # for chain in chains:
    #     for link in chains[chain]:
    #         print("INPUT PARAMETERS: ", chains[chain][link].parameters)
    #         print("PRIOR: ", chains[chain][link].prior)
    #         print("LIKLIHOOD; ", chains[chain][link].likelihood)
    #         print("------------------------------")

    end_time = time.time()
    print(end_time - start_time)
