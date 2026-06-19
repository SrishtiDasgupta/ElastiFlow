"""
Adaptive SeisSol/TinyDA driver — convergence-driven termination + adaptive resize.

Real-infra only; not invoked under simulus.

Per-call contract (read from sys.argv[1] as eval'd dict, stdout first line = result dict):
  Input:
    wf_id            : str
    cohesion         : dict {cohesion, next_links, next_chains,
                             next_cohesion_mean, next_cohesion_var}
    hosts            : {ip: count}        — UM-Bridge servers from the scheduler
    chains           : int                — current iteration's chain count
    tinyda_iterations: int                — current iteration's per-chain links
    mesh             : int
    port             : int
    state_dir        : str (optional, default /fsx/tinyda_state)
    cumulative_links_cap : int (optional) — absolute cap on cumulative links

  Output (stdout, line 0):
    {cohesion, likelihood, runtime,
     next_links, next_chains,
     next_cohesion_mean, next_cohesion_var,
     rhat, ess_bulk, converged,
     terminate_reason}      # 'converged' | 'cap' | 'continue'

State is persisted at {state_dir}/{wf_id}.pkl across invocations and deleted on
either termination path. The "prior" passed to TinyDA on iter ≥ 1 is the previous
iteration's pooled posterior mean/variance — adaptive-MCMC framing, not a true
sequential Bayesian update.
"""

import os
import sys
import time
import pickle
import numpy as np
from scipy.stats import multivariate_normal

import ray
import umbridge
import tinyDA as tda
import arviz as az


DEFAULT_STATE_DIR = "/fsx/tinyda_state"

LINKS_MIN, LINKS_MAX = 2, 10
CHAINS_MIN, CHAINS_MAX = 2, 6
RHAT_CONVERGE = 1.05
RHAT_EXPAND = 1.20
ESS_BULK_TARGET = 400
BURNIN_FRAC = 0.25
DIAGNOSTIC_MIN_SAMPLES = 50

DIFFUSE_PRIOR_VAR = 1.36e12
LIKELIHOOD_SIGMA = 0.1
N_RECEIVERS = 20

REFERENCE_DATA = np.array([
    0.16548211102121133, 0.10009936999735425, 0.3063361710114957,
    0.09183226689188462, 0.06786203933175347, 0.14964602482686096,
    0.09760305905160768, 0.070179070290844,   0.07502393292274322,
    0.05862669585853282, 0.09774360840157227, 0.10530374636225937,
    0.09503648926458205, 0.09708766433530303, 0.07170645408620686,
    0.11424754327742728, 0.11732727332405052, 0.1343788893754726,
    0.09462409894887182, 0.09853320811296683,
])


def _state_path(state_dir, wf_id):
    return os.path.join(state_dir, f"{wf_id}.pkl")


def load_state(state_dir, wf_id):
    path = _state_path(state_dir, wf_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def save_state(state_dir, wf_id, state):
    os.makedirs(state_dir, exist_ok=True)
    path = _state_path(state_dir, wf_id)
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(state, f)
    os.replace(tmp_path, path)


def delete_state(state_dir, wf_id):
    path = _state_path(state_dir, wf_id)
    if os.path.exists(path):
        os.remove(path)


@ray.remote
def run_sampling_remote(server_url, prior_mean, prior_cov, proposal_cov,
                        data, cov_likelihood, iterations, n_chains):
    """Run TinyDA against one SeisSol UM-Bridge endpoint."""
    import umbridge
    import tinyDA as tda
    from scipy.stats import multivariate_normal
    import numpy as np

    E = lambda x: np.array(x)
    model = umbridge.HTTPModel(f"{server_url}", "forward")
    model = tda.UmBridgeModel(model, pre=E)

    n_dim = len(prior_mean)
    prior = multivariate_normal(np.array(prior_mean),
                                np.array(prior_cov),
                                allow_singular=True)
    loglike = tda.GaussianLogLike(np.array(data), np.array(cov_likelihood))
    proposal = tda.GaussianRandomWalk(C=np.array(proposal_cov))
    posterior = tda.Posterior(prior, loglike, model)

    return tda.sample(posterior, proposal, iterations=iterations, n_chains=n_chains)


def extract_chain_traces(results, n_params):
    """Flatten Ray-returned per-server results into a list of per-chain numpy arrays."""
    chain_traces = []
    for res in results:
        for i in range(res["n_chains"]):
            key = f"chain_{i}"
            if key not in res or not res[key]:
                continue
            samples = np.array([np.atleast_1d(link.parameters)[:n_params]
                                for link in res[key]])
            chain_traces.append(samples)
    return chain_traces


def append_to_state_samples(state_samples, new_chain_traces):
    """Append new chain traces into per-chain-id sample dict.

    Chain id i in the new batch maps to chain id i in state_samples.
    Chains beyond the current batch retain their existing history;
    fresh chain ids past prev count start with the new trace as their first samples.
    """
    for i, trace in enumerate(new_chain_traces):
        if i in state_samples:
            state_samples[i] = np.concatenate([state_samples[i], trace], axis=0)
        else:
            state_samples[i] = trace
    return state_samples


def diagnostic(state_samples):
    """Compute (max rhat, min ess_bulk) across params over post-burn-in pooled chains.

    Returns (rhat, ess_bulk, ready_flag). ready_flag=False if not enough samples yet.
    """
    if len(state_samples) < 2:
        return None, None, False

    min_len = min(arr.shape[0] for arr in state_samples.values())
    burnin = int(BURNIN_FRAC * min_len)
    post = min_len - burnin
    if post < DIAGNOSTIC_MIN_SAMPLES:
        return None, None, False

    chain_ids = sorted(state_samples.keys())
    n_chains = len(chain_ids)
    n_params = state_samples[chain_ids[0]].shape[1]
    arr = np.empty((n_chains, post, n_params))
    for ci, cid in enumerate(chain_ids):
        arr[ci, :, :] = state_samples[cid][burnin:burnin + post, :]

    posterior = {f"theta_{p}": arr[:, :, p] for p in range(n_params)}
    idata = az.from_dict(posterior=posterior)
    rhat_ds = az.rhat(idata, method="rank")
    ess_ds = az.ess(idata, method="bulk")

    rhat_max = float(max(float(rhat_ds[v].values) for v in rhat_ds.data_vars))
    ess_min = float(min(float(ess_ds[v].values) for v in ess_ds.data_vars))
    return rhat_max, ess_min, True


def decide_next(rhat, ess_bulk, current_links, current_chains):
    """Apply the four-branch resize rule. Returns (next_links, next_chains, converged)."""
    if rhat is None:
        return current_links, current_chains, False
    if rhat < RHAT_CONVERGE and ess_bulk >= ESS_BULK_TARGET:
        return 0, 0, True
    if rhat >= RHAT_EXPAND:
        nl = min(current_links + 2, LINKS_MAX)
        nc = min(current_chains + 1, CHAINS_MAX)
        return nl, nc, False
    if rhat >= RHAT_CONVERGE:
        return current_links, current_chains, False
    nl = max(current_links - 1, LINKS_MIN)
    nc = max(current_chains - 1, CHAINS_MIN)
    return nl, nc, False


def posterior_moments(state_samples):
    """Pool post-burn-in samples across chains; return per-param mean and variance."""
    min_len = min(arr.shape[0] for arr in state_samples.values())
    burnin = int(BURNIN_FRAC * min_len)
    pooled = np.concatenate(
        [arr[burnin:min_len] for arr in state_samples.values()], axis=0
    )
    return pooled.mean(axis=0), pooled.var(axis=0, ddof=1)


def main():
    args = eval(sys.argv[1])

    wf_id = args["wf_id"]
    hosts = args["hosts"]
    n_chains_now = int(args["chains"])
    links_now = int(args["tinyda_iterations"])
    state_dir = args.get("state_dir", DEFAULT_STATE_DIR)
    cumulative_cap = int(args.get("cumulative_links_cap", 10**9))

    inner = args["cohesion"]
    if not isinstance(inner, dict):
        inner = {"cohesion": float(inner)}

    server_addrs = []
    for cluster_hosts in hosts.values() if isinstance(hosts, dict) else []:
        if isinstance(cluster_hosts, list):
            server_addrs.extend(cluster_hosts)

    if not server_addrs:
        server_addrs = list(hosts.keys()) if isinstance(hosts, dict) else list(hosts)

    state = load_state(state_dir, wf_id)
    n_params = 1

    if state is None:
        prior_mean = np.array([float(inner.get("cohesion", 0.0))] * n_params)
        prior_cov = np.eye(n_params) * DIFFUSE_PRIOR_VAR
        proposal_cov = np.eye(n_params)
        cumulative_links = 0
        state_samples = {}
    else:
        pm = inner.get("next_cohesion_mean", state["last_post_mean"])
        pv = inner.get("next_cohesion_var", state["last_post_var"])
        prior_mean = np.atleast_1d(np.array(pm, dtype=float))
        prior_var = np.atleast_1d(np.array(pv, dtype=float))
        prior_cov = np.diag(prior_var)
        proposal_cov = np.diag(prior_var)
        cumulative_links = int(state["cumulative_links"])
        state_samples = state["samples"]
        n_params = prior_mean.shape[0]

    cov_likelihood = (LIKELIHOOD_SIGMA ** 2) * np.eye(REFERENCE_DATA.shape[0])

    ray.init(ignore_reinit_error=True)

    start = time.time()
    n_servers = max(1, len(server_addrs))
    chains_per_server = n_chains_now // n_servers
    extra = n_chains_now % n_servers

    futures = []
    for i, addr in enumerate(server_addrs):
        c = chains_per_server + (1 if i < extra else 0)
        if c <= 0:
            continue
        url = f"http://{addr}" if not str(addr).startswith("http") else str(addr)
        futures.append(run_sampling_remote.remote(
            url, prior_mean.tolist(), prior_cov.tolist(), proposal_cov.tolist(),
            REFERENCE_DATA.tolist(), cov_likelihood.tolist(), links_now, c,
        ))
    results = ray.get(futures)

    new_traces = extract_chain_traces(results, n_params)
    state_samples = append_to_state_samples(state_samples, new_traces)
    cumulative_links += links_now

    min_lik = float("inf")
    best_cohesion = float(inner.get("cohesion", 0.0))
    for res in results:
        for i in range(res.get("n_chains", 0)):
            key = f"chain_{i}"
            if key in res and res[key]:
                last = res[key][-1]
                if last.likelihood < min_lik:
                    min_lik = float(last.likelihood)
                    best_cohesion = float(np.atleast_1d(last.parameters)[0])

    rhat, ess_bulk, ready = diagnostic(state_samples)
    next_links, next_chains, converged = decide_next(
        rhat, ess_bulk, links_now, n_chains_now
    )

    cap_hit = cumulative_links >= cumulative_cap
    if cap_hit and not converged:
        next_links, next_chains = 0, 0
        terminate_reason = "cap"
    elif converged:
        terminate_reason = "converged"
    else:
        terminate_reason = "continue"

    if state_samples:
        post_mean, post_var = posterior_moments(state_samples)
    else:
        post_mean = prior_mean
        post_var = np.diag(prior_cov)

    if terminate_reason in ("converged", "cap"):
        delete_state(state_dir, wf_id)
    else:
        save_state(state_dir, wf_id, {
            "samples": state_samples,
            "cumulative_links": cumulative_links,
            "prev_chains": n_chains_now,
            "prev_links": links_now,
            "last_post_mean": post_mean.tolist(),
            "last_post_var": post_var.tolist(),
        })

    runtime = time.time() - start

    output = {
        "cohesion": best_cohesion,
        "likelihood": min_lik if min_lik != float("inf") else None,
        "runtime": runtime,
        "next_links": int(next_links),
        "next_chains": int(next_chains),
        "next_cohesion_mean": post_mean.tolist(),
        "next_cohesion_var": post_var.tolist(),
        "rhat": rhat,
        "ess_bulk": ess_bulk,
        "converged": bool(converged),
        "terminate_reason": terminate_reason,
        "cumulative_links": cumulative_links,
        "diagnostic_ready": bool(ready),
    }
    print(output)


if __name__ == "__main__":
    main()
