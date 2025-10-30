import argparse
import numpy as np
import time
import umbridge
import tinyDA as tda
from scipy.stats import multivariate_normal
import ray
import concurrent.futures
import boto3
import heapq
from speedup import getRuntime

# === Ray Task ===
@ray.remote
def run_sampling_remote(server_url, E_func, prior_params, data, cov_likelihood, proposal_cfg, iterations, n_chains):
    import umbridge
    import tinyDA as tda
    from scipy.stats import multivariate_normal
    import numpy as np
    import concurrent.futures

    E = lambda x: np.array(x)
    model = umbridge.HTTPModel(f"{server_url}", "forward")
    model = tda.UmBridgeModel(model, pre=E)

    n_dim = len(prior_params)
    prior = multivariate_normal(prior_params, np.eye(n_dim) * 1.36e12, allow_singular=True)
    loglike = tda.GaussianLogLike(np.array(data), np.array(cov_likelihood))

    proposal = tda.GaussianRandomWalk(C=np.array(proposal_cfg))
    posterior = tda.Posterior(prior, loglike, model)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: tda.sample(posterior, proposal, iterations=iterations, n_chains=n_chains, force_sequential=True))
        return future.result()

# === For Chains > Nodes ===
def fetch_instance_types(ips, region="eu-north-1"):
    ec2 = boto3.client("ec2", region_name=region)
    reservations = ec2.describe_instances(Filters=[
    {"Name": "private-ip-address", "Values": ips}
    ])["Reservations"]

    ip_to_type = {}
    for res in reservations:
        for inst in res["Instances"]:
            ip = inst["PrivateIpAddress"]
            typ = inst["InstanceType"]
            ip_to_type[ip] = typ
    return ip_to_type

def collectHostRuntimes(hosts, mesh):
    return {host: getRuntime(1, mesh, host) for host in hosts}

def sequentialChainAllocation(hosts, chains, mesh):
    runtimes = collectHostRuntimes(hosts, mesh)
    heap = []
    host_counts = {}

    for host in hosts:
        for ip in hosts[host]:
            host_counts[ip] = 1
            heapq.heappush(heap, (runtimes[host], host, ip))


    extraChains = chains - len(heap)
    while extraChains > 0:
        runtime, host, ip  = heapq.heappop(heap)
        heapq.heappush(heap, (runtime + runtimes[host], host, ip))
        host_counts[ip] += 1
        extraChains -= 1


    return host_counts

# === Main ===
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("servers", nargs='+', help="List of servers with ports (ip:port)")
    parser.add_argument("--chains", type=int, required=True)
    parser.add_argument("--iterations", type=int, required=True)
    parser.add_argument("--cohesion", type=float, required=True)
    args = parser.parse_args()

    ray.init(ignore_reinit_error=True)

    # Parse and map server IPs
    server_ips = [ip_port.split(":")[0] for ip_port in args.servers]
    num_nodes = len(server_ips)
    ip_to_type = fetch_instance_types(server_ips)
    hosts = {}
    for ip in server_ips:
        inst_type = ip_to_type[ip]
        hosts.setdefault(inst_type, []).append(ip)

    # Schedule chains based on load only if chains > nodes
    # TBD: CORNER CASE OF CHAINS == NODES (????) -> HANDLE IT 
    if args.chains > num_nodes:
        allocation = sequentialChainAllocation(hosts, args.chains, mesh=1000)
        print(f"allocation sequential: {allocation}")
    else:
        # Evenly distribute chains across IPs
        allocation = {}
        per_node = args.chains // num_nodes
        extras = args.chains % num_nodes
        for i, ip in enumerate(server_ips):
            #inst_type = ip_to_type[ip]
            allocation[ip] = allocation.get(ip, 0) + per_node + (1 if i < extras else 0)
        print(f"allocation_normal : {allocation}")
    # Setup Umbridge models
    server_models = []
    for address in args.servers:
        base_url = f"http://{address}"
        model = umbridge.HTTPModel(base_url, "forward")
        model.get_input_sizes({})
        server_models.append(model)

    E = lambda x: np.array(x)
    tda_model = tda.UmBridgeModel(server_models[0], pre = E)
    n_dim = server_models[0].get_input_sizes()[0]
    mean_prior = np.zeros(n_dim) + args.cohesion
    cov_prior = np.eye(n_dim) * 1.36e12
    prior = multivariate_normal(mean_prior, cov_prior, allow_singular=True)
    sigma_like = 0.1
    log_E_true = prior.rvs()
    params = np.array([log_E_true])
    start_time = time.time()
    data = tda_model(params)  # Get data from any model (assumed identical)
                  
    # data = np.array([
    # 0.16548211102121133, 0.10009936999735425, 0.3063361710114957, 0.09183226689188462,
    # 0.06786203933175347, 0.14964602482686096, 0.09760305905160768, 0.070179070290844,
    # 0.07502393292274322, 0.05862669585853282, 0.09774360840157227, 0.10530374636225937,
    # 0.09503648926458205, 0.09708766433530303, 0.07170645408620686, 0.11424754327742728,
    # 0.11732727332405052, 0.1343788893754726, 0.09462409894887182, 0.09853320811296683
    # ])

    cov_likelihood = sigma_like**2 * np.eye(data.shape[0])

    # Map instance types to models
    instance_type_to_models = {}
    for model in server_models:
        ip = model.url.replace("http://", "").split(":")[0]
        inst_type = ip_to_type[ip]
        instance_type_to_models.setdefault(ip, []).append(model)

    # Launch chains 
    ray_futures = []
    for ip, models in instance_type_to_models.items():
        total_chains = allocation.get(ip, 0)
        if total_chains == 0:
            continue
        per_model = total_chains // len(models)
        extras = total_chains % len(models)

        for i, model in enumerate(models):
            assigned = per_model + (1 if i < extras else 0)
            if assigned == 0:
                continue
            ray_futures.append(
                run_sampling_remote.remote(
                    model.url,
                    None,
                    mean_prior.tolist(),
                    data.tolist(),
                    cov_likelihood.tolist(),
                    np.eye(n_dim).tolist(),
                    args.iterations,
                    assigned
                )
            )

    # Aggregate and print output
    results = ray.get(ray_futures)
    output = {"likelihood": 100, "cohesion": 0}
    for res in results:
        for j in range(res["n_chains"]):
            chain = f"chain_{j}"
            if chain in res and res[chain]:
                if res[chain][-1].likelihood < output["likelihood"]:
                    output["likelihood"] = res[chain][-1].likelihood
                    output["cohesion"] = res[chain][-1].parameters[0]

    output["runtime"] = time.time() - start_time
    print(output)

