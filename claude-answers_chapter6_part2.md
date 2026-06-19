# Chapter 6 — Engine / Driver / Runtime Q&A (Part 2)

## Q1. Driver EXECUTE-phase sequence: subprocess invocation → result return

The Driver does **not** start Waitress endpoints itself, and the Ray cluster used by TinyDA is **not** the executor's Ray; it is a transient, in-process Ray instance bootstrapped by the Driver subprocess for the duration of one iteration.

**Sequence per iteration (on-prem, mirrored on cloud):**

1. **Engine spawn (`steep_actions.py:98`)** — the per-workflow Engine instance does
   `subprocess.run([sys.executable, self.service, str(args)])`. `args` carries `wf_id`, `hosts`, `chains`, `tinyda_iterations`, `mesh`, `port`, `cohesion` (and for `PLAIN_ADAPTIVE` the dict carrying `next_cohesion_mean/var`, `next_links`, `next_chains`). The Engine then blocks on the pipe; it has no further awareness of the Driver internals.

2. **SLURM submission of the SeisSol/Waitress server (`plcl_runner.py:185–193`)** — for each chain the Driver renders an `sbatch` script from `SLURM_TEMPLATE` and submits it. SLURM allocates the requested compute nodes from the on-prem partition. The Engine is not involved in this; resources were already pre-allocated by the Scheduler and only the *placement layout* (which IPs serve which chain) is decided here by the Driver.

3. **Per-job startup on the compute node (the `sbatch` body at `plcl_runner.py:14–68`)** —
   - The job lands on the assigned compute node(s) and `cd /fsx/Seis-Bridge/tpv13`.
   - It assembles a machine file of node IPs.
   - The first node of each job pins `waitress-serve` to core 0 and exposes the WSGI app `tpv13server_wsgi_safe:app` on `$PORT`; non-first nodes `sleep infinity` so they remain reservable for MPI ranks invoked by the WSGI handler.
   - **So Waitress is launched here, by SLURM on the compute node, not by the Driver and certainly not by the executor.** It is per-iteration: a fresh server comes up for each EXECUTE call and is `scancel`-ed in cleanup (`plcl_runner.py:128–141`).

4. **IP discovery (`plcl_runner.py:84–102`)** — the Driver polls `srun --jobid <jid> cat /tmp/hosts` for up to ~120 s until each chain's first node reports its IP. These `(jobid, ip)` pairs are the UM-Bridge endpoints TinyDA will sample against.

5. **TinyDA sampling — the *adaptive* path (`tinyda_client_adaptive.py`)**:
   - `ray.init(ignore_reinit_error=True)` (line 252) is called inside the Driver subprocess. This is a **local Ray runtime** spun up in that process; it is unrelated to any Ray cluster the executor or compute nodes might run. Its sole role is to fan out one `@ray.remote run_sampling_remote` task per UM-Bridge server endpoint.
   - Each remote task instantiates `umbridge.HTTPModel` against its assigned `server_url`, builds a `tinyDA.GaussianLogLike` + `GaussianRandomWalk` + `Posterior`, and calls `tda.sample(..., iterations=links_now, n_chains=chains_per_server)`.
   - **So the parallelism across chains is provided by Ray, sharded by server, with TinyDA itself running `n_chains` chains per server inside one remote task.** When `n_servers ≥ n_chains_now`, each Ray task drives one chain; otherwise a single task drives several chains sequentially against the same server.
   - `ray.get(futures)` (line 269) blocks until all chains finish.

6. **Post-processing in the Driver (still inside the same subprocess)** — extract per-chain traces, append to `state_samples` on `/fsx/tinyda_state/{wf_id}.pkl`, compute rank-normalised split-R̂ and bulk ESS over post-burn-in pooled samples, run `decide_next` to set `next_links`/`next_chains`, compute posterior `mean/var` for the next iteration's prior+proposal.

7. **Cleanup (`plcl_runner.py:128–147`)** — `scancel` every SLURM job spawned this iteration, remove the per-iteration `.sh`, `.out`, `.err` files, and clear `job_ips/`. The Waitress endpoints disappear with the SLURM jobs.

8. **Result return** — the Driver prints a single result-dict line to stdout. The Engine reads it, parses via `eval`, and treats it as the next-iteration input via `yieldToInput` (`steep_actions.py:135–149`).

For the legacy plain/LA path (`run_seissol.py` → `run_client_script_mult.sh`) Ray does not appear — TinyDA runs in-process with `n_chains=chains` against the multi-server endpoint list, and only the log-likelihood line is parsed. The Waitress + SLURM + cleanup half is identical.

## Q2. Where is the Singularity/Apptainer container actually invoked?

It is invoked **on the compute node, by the SLURM job, inside the Waitress request handler — not by `PlclRunner` or `CloudRunner`.**

Concretely: the WSGI app `tpv13server_wsgi_safe:app` (started at `plcl_runner.py:64`) wraps the SeisSol forward-model service. When TinyDA POSTs a parameter sample to that endpoint, the handler shells out to a command of the form

```
mpirun ... apptainer exec ../seissol.sif SeisSol_Release_sskx_<order>_elastic <run_id>/parameters.par
```

(`Seis-Bridge/server.py:23` and `Seis-Bridge/server_cluster.py:23`). So one `apptainer exec` is launched per UM-Bridge call, i.e. once per MCMC link, *inside* the SLURM-allocated compute node, using the MPI ranks that node already owns.

**Image location.** The image is **pre-pulled on the shared parallel filesystem**, not pulled per-job. `seissol.sif` lives next to `/fsx/Seis-Bridge/...` and is referenced relatively as `../seissol.sif` from `tpv13/`. The image is built once by `fsx/Seis-Bridge/seissol.def` (`From: mpi.sif`) and read directly from `/fsx` by every compute node — that is the whole point of having `/fsx` as a shared mount. No per-iteration pull, no registry traffic, no cold-start cost.

**Startup overhead.** Apptainer's `exec` over a SIF on a shared NFS/Lustre mount is essentially a `mmap` of the image followed by namespace setup; on the order of 100–300 ms per invocation on this stack. SeisSol forward solves on TPV13 at `mesh=750` take seconds-to-tens-of-seconds per evaluation, and one MCMC link requires one forward solve. Container startup is therefore well under 1 % of per-evaluation runtime and is *not* a cost the cost model needs to account for. The dominant per-evaluation cost is the SeisSol kernel itself; the per-iteration overheads worth modelling are SLURM start-up (one-shot per chain per iteration), not container exec (per-link).

## Q3. Convergence mechanism — exact specification

Yes — the implementation matches the described scheme exactly. Source: `Seis-Bridge/tinyda_client_adaptive.py`.

- **Statistic.** Rank-normalised split-R̂ via `arviz.rhat(idata, method="rank")` and bulk ESS via `arviz.ess(idata, method="bulk")` (`tinyda_client_adaptive.py:173–174`).
- **Window.** Over **cumulative pooled chain samples** persisted across iterations in `/fsx/tinyda_state/{wf_id}.pkl::samples` (chain id → ndarray). Each iteration's traces are appended via `append_to_state_samples` (lines 142–147), so R̂/ESS are over all sampling done so far for the workflow, not just the current iteration.
- **Burn-in.** `BURNIN_FRAC = 0.25` (line 52), trimmed off the front of every chain *after* truncating to the shortest chain length (`min_len`) so all chains contribute equal post-burn-in samples (lines 158–169).
- **Diagnostic-readiness gate.** `DIAGNOSTIC_MIN_SAMPLES = 50` post-burn-in samples per chain (lines 53, 161–162); below this, R̂/ESS are not even computed and the Driver returns `diagnostic_ready=False`.
- **Thresholds.** `RHAT_CONVERGE = 1.05`, `RHAT_EXPAND = 1.20`, `ESS_BULK_TARGET = 400` (lines 49–51).
- **Four-case adaptive table** (`decide_next`, lines 181–195):

  | Condition                              | next_links            | next_chains            | converged |
  |----------------------------------------|-----------------------|------------------------|-----------|
  | R̂ < 1.05 **and** ESS_bulk ≥ 400        | 0                     | 0                      | True      |
  | R̂ ≥ 1.20                               | min(cur+2, LINKS_MAX=10) | min(cur+1, CHAINS_MAX=6) | False  |
  | 1.05 ≤ R̂ < 1.20                        | unchanged             | unchanged              | False     |
  | R̂ < 1.05 **and** ESS_bulk < 400        | max(cur−1, LINKS_MIN=2) | max(cur−1, CHAINS_MIN=2) | False    |

  The "converged" branch sets `next_links=next_chains=0`; the Driver also sets `terminate_reason='converged'`, which the Engine reads (`steep_actions.py:127–135`) to short-circuit the moldable iteration cap and call `setWorkflowComplete(True)`. So this *is* the value that determines the chain count for the next iteration — it is returned to the Engine inside the result dict, and the Engine surfaces it as the `yieldToInput` payload that feeds `getClientInputs_PlainAdaptive` on the next iteration. The cumulative-link cap (Layer 2) and moldable-iteration cap (Layer 3) only fire if Layer 1's R̂/ESS check has not yet declared convergence.

## Q4. Parallel filesystem — shared or per-workflow?

A **single shared parallel-filesystem instance per tier**, not one per workflow.

- **On-prem tier.** ParallelCluster mounts EFS at `/fsx` on every node — head, all compute nodes — with mount targets in eu-north-1a/b/c. Filesystem ID `fs-0c7ed8d283368b734`. There is exactly one EFS for the entire on-prem tier; concurrent workflows share it.
- **Cloud tier (reserved + on-demand).** Same EFS, mounted at the same path `/fsx` on every reserved instance and on every on-demand instance brought up by `on_demand_setup_HPO_*.sh` and `reserved_instance_setup.sh`. The shared mount is what makes the `seissol.sif` image, the `tpv13/` mesh assets, the per-workflow TinyDA state file (`/fsx/tinyda_state/{wf_id}.pkl`), and the cluster-wide config files visible everywhere without copying.
- **Per-workflow isolation** is by *namespacing* on this shared mount, not by separate filesystems: each workflow has its own `wf_id`-prefixed state file, its own SLURM job ids, and its own port assignment. Concurrent workflows therefore share I/O bandwidth on the same EFS but never touch each other's state.
- **Why this is fine.** The dominant load on `/fsx` is read traffic on `seissol.sif` (mmap, cached) and small pickle writes per iteration; throughput is not the bottleneck. Provisioning a filesystem per workflow would multiply cost (FSx Lustre was ~$600/month per filesystem before the EFS migration) without any isolation benefit, since nothing in the workload contends on filesystem-level metadata.
