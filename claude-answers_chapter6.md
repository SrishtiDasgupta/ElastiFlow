# Chapter 6 — Implementation Answers

All citations are relative to `Vortex-moldable-sched/src/main/` unless qualified.

---

## Q1. SeisSol Driver — Execution Details

### (1) Node-to-chain mapping: Driver receives, does not request

The Driver does not consult the load balancer at runtime. The chain count is fixed at admission time from the workflow specification and passed to the Driver as part of the dispatched request:

- For *plain* SeisSol, `getClientInputs_Plain()` reads `workflowConfig[ind]['chains']` from the YAML (`utils/exec_sched.py:103–133`, chain count at line 115).
- For *license-aware* SeisSol, `getClientInputs_LA()` reads the same field for moldable workflows or falls back to `constraints['chains']` for static ones (`utils/exec_sched.py:153–159`).

The mapping of *physical nodes* to those chains is performed inside the Driver itself, by `CloudRunner.parallelAllocation()` (`service/cloud_runner.py:40–83`), not by the Resource Manager or load balancer. The algorithm is described under Q3.3.

### (2) EXECUTE phase sequence

1. **Endpoint provisioning.** The Driver issues a SLURM batch via the on-prem runner template (`service/plcl_runner.py:14–68`). On the head node of each allocation it executes `taskset -c $WAITRESS_CORE waitress-serve --host=$THIS_NODE --port=$PORT --threads=1 tpv13server_wsgi_safe:app` (`service/plcl_runner.py:64`). The remaining nodes within the SLURM allocation are kept alive but idle (`sleep infinity`, line 66) so that MPI can later target them.
2. **Chain dispatch.** The TinyDA client distributes `chains_per_server` work units across the live HTTP endpoints (`Seis-Bridge/tinyda_client.py:130–150`) and launches them as Ray remote tasks; the call `ray.get(ray_futures)` (line 152) blocks until every chain returns.
3. **Result aggregation.** Per-server results are reduced to the minimum-likelihood sample and the corresponding cohesion parameter is retained (`Seis-Bridge/tinyda_client.py:158–174`).
4. **Termination.** When the iteration loop exhausts, the Executor wraps the result and posts it to the scheduler's completion mailbox: `sim.sync().send(sim, 'completed_jobs_mb', str(request))` (`executor_LA.py:79–95`, send at line 93).

### (3) Convergence assessment — *the system has none*

This was the user's suspicion, and it is correct. **TinyDA termination in the current implementation is governed exclusively by a fixed iteration count.** The sampling call is

```python
tda.sample(posterior, proposal, iterations=args.iterations, n_chains=args.chains)
```

(`Seis-Bridge/tinyda.py:56`, mirrored in the Ray-distributed variant at `Seis-Bridge/tinyda_client.py:43`). No instance of an R-hat / Gelman–Rubin diagnostic, an effective-sample-size check, a Geweke statistic, a Heidelberger–Welch stationarity test, or a posterior-variance threshold appears in `Seis-Bridge/tinyda*.py`, `fsx/chain.py`, or `utils/exec_sched.py`.

The *only* termination signal the Driver emits to the Engine is the natural exhaustion of the iteration budget; the workflow then traverses the standard executor → completion-mailbox path documented in (2).

#### What the Driver actually returns and what the inverse problem is

For completeness — since the convergence-criterion gap can only be designed against a clear understanding of what is being inferred — the SeisSol/TinyDA driver in the current implementation operates as follows:

**Inverse problem.** The benchmark is **SCEC TPV13** — a 60° dipping normal fault with Drucker–Prager plastic yielding (`Seis-Bridge/tpv13server_wsgi_safe.py:14–22`). The single inferred parameter is the **plastic cohesion** of the off-fault medium:

**Convergence diagnostic:** Rank-normalised split-R^\hat{R}
R^ (Vehtari et al., 2021) and bulk effective sample size (ESSbulk\mathrm{ESS}_{\mathrm{bulk}}
ESSbulk​), computed via ArviZ against the cumulative per-chain cohesion samples pooled across all completed moldable iterations of the workflow. A 25% burn-in discard is applied before each diagnostic evaluation. No diagnostic is applied until at least 50 cumulative samples per chain exist. Convergence is declared when R^<1.05\hat{R} < 1.05
R^<1.05 and ESSbulk≥400\mathrm{ESS}_{\mathrm{bulk}} \geq 400
ESSbulk​≥400.
Adaptive link and chain count — both are outputs of the diagnostic at each iteration boundary, strictly within bounds [2,10][2,10]
[2,10] and [2,6][2,6]
[2,6] respectively:

R^≥1.2\hat{R} \geq 1.2
R^≥1.2 (poor mixing): next_links=min⁡(current_links+2, 10)\text{next\_links} = \min(\text{current\_links} + 2,\ 10)
next_links=min(current_links+2, 10), next_chains=min⁡(current_chains+1, 6)\text{next\_chains} = \min(\text{current\_chains} + 1,\ 6)
next_chains=min(current_chains+1, 6)
1.05≤R^<1.21.05 \leq \hat{R} < 1.2
1.05≤R^<1.2 (monitoring): no change to either
R^<1.05\hat{R} < 1.05
R^<1.05 and ESSbulk<400\mathrm{ESS}_{\mathrm{bulk}} < 400
ESSbulk​<400 (converging): next_links=max⁡(current_links−1, 2)\text{next\_links} = \max(\text{current\_links} - 1,\ 2)
next_links=max(current_links−1, 2), next_chains=max⁡(current_chains−1, 2)\text{next\_chains} = \max(\text{current\_chains} - 1,\ 2)
next_chains=max(current_chains−1, 2)
R^<1.05\hat{R} < 1.05
R^<1.05 and ESSbulk≥400\mathrm{ESS}_{\mathrm{bulk}} \geq 400
ESSbulk​≥400 (converged): next_links=0\text{next\_links} = 0
next_links=0, next_chains=0\text{next\_chains} = 0
next_chains=0 — termination signal

Termination signal: pi+1w=0p^w_{i+1} = 0
pi+1w​=0 maps directly to the yieldToInput variable producing no output (§4.3.1), which is the existing Engine termination path. No protocol or scheduler changes are required.
Adaptive Metropolis warm-start (proposal update): At each iteration boundary, over the post-burn-in cumulative samples pooled across all chains, the Driver computes the posterior mean μi+1=mean({θj})\mu_{i+1} = \mathrm{mean}(\{\theta_j\})
μi+1​=mean({θj​}) and posterior variance σi+12=var({θj})\sigma^2_{i+1} = \mathrm{var}(\{\theta_j\})
σi+12​=var({θj​}) of the cohesion parameter. These are passed to the next iteration's TinyDA initialisation as the proposal centre and proposal covariance respectively — a Haario-style adaptive Metropolis move (Haario et al., 2001). This is not a Bayesian prior update: the cumulative chain trace is the single inferential object, and the proposal update accelerates mixing by focusing exploration on the region of high posterior mass. The "prior" passed to TinyDA functions as a starting distribution for the next batch of links, not as an informative prior in the inferential sense.
Return payload extension: The Driver return dict is extended to {likelihood, cohesion, runtime, next_links, next_chains, next_cohesion_mean, next_cohesion_var, rhat, ess_bulk, converged}. The converged boolean distinguishes statistical termination from hard-cap exhaustion, permitting post-hoc auditing.
Key cross-references for writing §6.3.2:

The yieldToInput termination path → §4.3.1
The negotiation request carrying pi+1wp^w_{i+1}
pi+1w​ and updated link count → §5.4.1
Budget and deadline as cost ceiling → §5.4.3.2 (Equations 5.24–5.26)
Gelman–Rubin as motivation for 4-chain structure → CCGrid paper §V-F (cite [36])
### (4) Singularity / cold-start overhead

There is no Singularity image, container runtime, or `.def`-based image build invoked by the Driver path. On-prem execution loads SeisSol via SLURM module loading directly (`service/plcl_runner.py:27`, `module load openmpi`); cloud execution uses SSH-based direct invocation. The `seissol.def`, `cuda.def`, `mpi.def`, `server.def` files in `Vortex-moldable-sched/fsx/Seis-Bridge/` exist as build recipes but are not referenced by any runtime code path under `src/main/`.

The relevant cold-start figure is the *cloud on-demand provisioning latency*, not a container start: `COLD_START_TIME = 384.136 + 16.4` seconds (`config/constants.py:9`), i.e.\ ≈ 400 s end-to-end from EC2 launch to executor-registered availability.

### (5) Chain count: assigned, not derived

The Driver does not infer chain count from any property of the TinyDA output. The count enters the Driver from one of three upstream sources, in order of precedence:

1. **Moldable per-iteration assignment** by the scheduler: `workflowConfig[iteration_index]['chains']` (`utils/exec_sched.py:151–154`). Different chain counts at successive iterations trigger explicit allocate/free renegotiations with the Resource Manager.
2. **Static admission constraint** from the YAML: `constraints['chains']` (`utils/exec_sched.py:157–159`).
3. **HPO dynamic feedback** (HPO use case only): `input[0]['next_trials']` written back by the previous iteration's ASHA pipeline (`utils/exec_sched.py:199, 203`).

In all three cases the count is an *input* to the Driver; the Driver never originates it.

---

## Q2. HPO Driver — Ray Tune Integration

### (1) Ray cluster launch

The Driver launches Ray under SLURM via `plcl_runner_HPO.py:93–150`:

- Head node: `ray start --head --node-ip-address=$head_ip --port=$port --block`, executed with `srun --overlap -N1 -n1` on the first node of the allocation (`service/plcl_runner_HPO.py:134`). The default port is `6380`, configurable per request (line 91).
- Worker nodes: `ray start --address=$head_ip:$port` (line 150 onward), one `srun` per worker.
- Readiness: the Driver polls the GCS socket for up to 60 s before submitting trials (lines 138–149).

Trial-relevant parameters are pulled from the request payload: `learning_rate` (line 71), `momentum` (line 72), `epochs` (line 73), `next_trials` (line 74).

### (2) ASHA configuration

`fsx/hyperparameter_test/hpo_pipeline_verbose_instrumented.py:147–148`:

```python
scheduler = ASHAScheduler(time_attr="training_iteration",
                          max_t=10 if args.smoke_test else 50,
                          grace_period=1, reduction_factor=2)
```

- `reduction_factor = 2` ⇒ **keep-floor fraction = 1/2 = 50 %** at every rung.
- `grace_period = 1` epoch before any trial can be paused.
- `max_t = 50` epochs maximum per trial.
- `max_concurrent_trials = 1` (line 171) — i.e.\ the Tune-side concurrency cap is 1; *parallelism across trials is achieved by the moldable scheduler issuing multiple workflows*, not by Tune itself.

The chain count (= trials promoted into the next iteration) is recovered upstream of Tune via `exec_sched.py:199`:

```python
chains = input[0].get('next_trials', constraints.get('chains', 1))
```

i.e.\ `next_trials` is written by ASHA's per-iteration output and consumed by the scheduler as the chain count for the *next* allocation request.

### (3) Convergence signal — *correction*

The earlier claim that "ASHA's pruning is itself the convergence mechanism" was wrong: ASHA only governs *within-iteration* trial promotion. Cross-iteration convergence — i.e.\ when the moldable workflow as a whole stops requesting further iterations — is implemented in the HPO pipeline driver itself (`fsx/hyperparameter_test/hpo_pipeline_verbose.py`, the script wired into `service/plcl_runner_HPO.py:165`, `service/run_client_HPO.py:4`, and `service/cloud_runner_HPO_new.py:325`).

The live pipeline combines two distinct mechanisms — one acting *within* an iteration, the other *across* iterations.

**(a) Intra-iteration early-stop on target metric.** A target accuracy of `0.99` is configured (`hpo_pipeline_verbose.py:449`). Within a single iteration, the pipeline executes Tune in successive phase batches; if at the end of any phase `best_score >= self.target`, the remaining phases are skipped:

```python
if best_score >= self.target:
    logger.info(f"TARGET REACHED ({best_score:.4f} >= {self.target}) - Skipping remaining phases")
    early_stopped = True
    break
```

(lines 804–808). This saves compute within one workflow iteration but does not terminate the workflow.

**(b) Cross-iteration trial count via a resize-only heuristic.** The `next_trials` value returned to the workflow Engine is computed unconditionally from the success rate, with no target test:

```python
if success_rate > 0.5:
    next_trials = max(MIN_TRIALS, int(self.num_samples * 0.5))
else:
    next_trials = min(10, int(self.num_samples * 1.5))
```

(`hpo_pipeline_verbose.py:835–838`). There is no `next_trials = 0` branch — even after the target is achieved within an iteration, a positive trial count is emitted and the Engine, reading `chains = input[0].get('next_trials', …)` (`utils/exec_sched.py:199, 203`), requests another iteration.

**Implication for the AWS experiments.** The workflow as a whole does not terminate on target-hit. Cross-iteration termination is governed by the YAML-declared `workflowIterations` budget — a hard cap, not a convergence criterion. The intra-iteration early-stop accelerates individual iterations but the workflow runs out its full iteration count regardless. The HPO termination story is therefore "ASHA pruning + intra-iteration target check + iteration cap", not "target-driven convergence". This layer of cross-iteration logic nevertheless *exists* for HPO and *does not* exist for SeisSol/TinyDA, which is the asymmetry that motivates the gap analysis below.

### (4) Dataset pre-caching and file lock

The dataset directory is statically configured: `"data_dir": os.path.expanduser("~/cifar10")` (`hpo_pipeline_verbose_instrumented.py:138`). Caching is performed manually as part of the cluster bring-up (cf. `IaC_scripts/CLUSTER_SETUP_GUIDE.txt`); there is **no programmatic file-lock implementation** in the Python code path. Concurrent-write safety relies on the underlying filesystem (EFS POSIX semantics post the FSx → EFS migration, commit `11e9f0d`).

### (5) Core map to Ray

Resource information reaches Ray through two channels:

- *Per-trial declaration*: `tune.with_resources(train_fn, {"cpu": args.cpus_per_trial, "gpu": 0})` (`hpo_pipeline_verbose_instrumented.py:125`). Ray's scheduler enforces this against its detected slot inventory.
- *Environment passthrough* via `runtime_env`: `HOSTS`, `EPOCHS`, `MEASURE`, `MODEL_NAME` are forwarded into worker processes (line 140 onward). `HOSTS` is consumed inside the trainable as `int(os.environ.get("HOSTS", "1"))`.

The Driver does *not* communicate the moldable scheduler's per-instance core map to Ray; Ray autodetects via its node-discovery handshake on `ray start`.

### (6) Per-trial core count

Default `--cpus-per-trial = 2.0` (`hpo_pipeline_verbose_instrumented.py:92`); fixed for the lifetime of a trial; GPU allocation is hard-coded to 0 in the trainable resource spec.

---

## Q3. Coherence Threshold and Load Balancer

### (1) Coherence / closeness value

`scheduler/fcfs_optimized_kavitha.py:1`:

```python
closeness = lambda x: math.isclose(runtime, x, rel_tol=0.15)
```

i.e.\ **15 % relative tolerance** on predicted runtime when deciding whether two candidate instance selections are "coherent enough" to be treated as substitutes. The HPO use case inherits the same constant via shared imports from `config/constants.py`; no separate HPO-specific coherence value is defined.

### (2) Speedup threshold

Confirmed: **1.4×**.
- `config/constants.py:47`: `SPEEDUP_THRESHOLD = 1.4`
- `config/constants_HPO.py:192`: `SPEEDUP_THRESHOLD = 1.4  # Minimum speedup for instance type switching`

This is the minimum marginal speedup that justifies admitting a heterogeneous instance type into the allocation.

### (3) Greedy assignment

`service/cloud_runner.py:40–83`, `parallelAllocation()`. Confirmed as **least-loaded-bundle** assignment, implemented with a max-heap keyed on negative runtime:

1. Seed the heap with one node per chain (`cloud_runner.py:48–56`):
   ```python
   heapq.heappush(heap, (-host_runtimes[host], host, 1, [ip]))
   ```
2. For every remaining IP, pop the chain with the largest current runtime, append the IP, recompute the runtime as `getRuntime(nodes + 1, mesh, bottleneck_type)`, and push back (`cloud_runner.py:62–77`).

The heap key uses the *current bottleneck chain* (largest runtime), but since each chain's runtime is only ever decreased by adding capacity, the algorithm is mathematically equivalent to "always extend the chain that would otherwise be the bottleneck" — i.e.\ the standard makespan-minimising greedy bundle assignment.

---

## Q4. License-Aware Execution

### (1) Tools requiring tokens

Defined in `config/licenses.yaml:9–21`:

| Pool | Tokens (production cap) | software_id |
|---|---|---|
| ANSYS | 6 700 | 1 |
| ABAQUS | 2 200 | 2 |
| LSDYNA | 7 600 | 3 |

### (2) Where availability is checked

Internally, by the `LicenseManager` owned by the Resource Manager — there is **no separate License Server process** queried over the network. The scheduler accesses it through `self.license_manager = self.resource_manager.license_manager` (`scheduler/fcfs_optimized_LA.py:56`).

The check is two-phase committed (`config/licenses.yaml:29 — two_phase_commit: true`): `hold()` reserves tokens optimistically; `commit()` finalises them after compute allocation succeeds. This prevents races during moldable rebalancing.

The actual feasibility test:

```python
licenses_needed = self.license_manager.calculate_tokens(
    pool=license_pool, cores=total_cores, chains=constraints.get('chains', 1))
pool_status = self.license_manager.get_pool_status(license_pool)
if licenses_needed > pool_status['total']:
    rejected_workflows.add(wf_plan['id'])
```

(`scheduler/fcfs_optimized_LA.py:194–206`).

### (3) YAML declaration

License demand is declared inside `constraints` (validated by `utils/validate_workflow.py`):

- `license_pool`: one of `ANSYS | ABAQUS | LSDYNA` (string).
- `software_id`: integer mapping `1 | 2 | 3`, mutually consistent with `license_pool`.
- `chains` (existing constraint) + the workflow's per-iteration `cores`: jointly determine the token count, computed by `LicenseManager.calculate_tokens(...)` per the per-pool strategy in `licenses.yaml:32–47`:
  - **LS-DYNA** — `linear` (1 token / core).
  - **ABAQUS** — `powerlaw` with $a = 5.0$, $b = 0.422$, `min_tokens = 5` ⇒ tokens $= \max(5, 5.0 \cdot \text{cores}^{0.422})$.
  - **ANSYS** — `ansys_workgroup` (MEBA-style stepped formula).

### (4) Cloud restriction

There is **no hard restriction** on cloud execution by software type. ANSYS / ABAQUS / LSDYNA workflows can in principle run on any pool. The contention is realised *implicitly* through reduced token caps: `config/constants_LA.py:75–84` configures pool capacities at 60 %, 87 %, and 55 % below baseline respectively, producing temporal scarcity rather than categorical exclusion. Workflows that require more tokens than the entire pool can ever provide are rejected at admission as *impossible* (distinct from *temporarily unavailable*) at `scheduler/fcfs_optimized_LA.py:201–204`.

### (5) Reservation and release timing

Tokens are **acquired and released atomically with compute resources**, not staggered:

- *Admission*: `allocateResourcesWithLicenses(constraints)` returns `(ips, alloc_resources, license_holds)` in one call (`scheduler/fcfs_optimized_LA.py:124`); both pools commit together or not at all.
- *Dispatch*: the executor receives the `license_holds` payload (`executor_LA.py:68–69`) but performs no further license bookkeeping — the reservation is already firm.
- *Moldable scale-down*: `processFreeRequestWithLicenses()` (`scheduler/fcfs_optimized_LA.py:224+`) releases tokens proportionally as compute slots are freed, again in one transaction.
- *Completion*: terminal release follows the same path as the compute return, on the completion-thread side.

There is no separate "license heartbeat" or grace-period release; the license lifecycle is a strict subset of the resource lifecycle.

### (6) Stage-1 feasibility extension

Plain SeisSol Stage 1 evaluates compute feasibility plus the implicit deadline / budget admission filters. The license-aware extension (`scheduler/fcfs_optimized_LA.py:119–206`) adds **one additional condition**:

> The required token count, computed via the per-pool strategy from `(cores, chains)`, must not exceed the *current free balance* of the corresponding license pool — and, separately, must not exceed the pool's *total capacity* (the second test distinguishes permanent rejection from transient queuing).

Implementation-wise the Stage-1 entry point is unchanged in shape; `allocateResourcesWithLicenses(...)` returns `None` if either compute *or* tokens are unavailable, and `calculate_tokens(...) > pool_status['total']` triggers the impossible-rejection branch at lines 201–204.

---

## Summary of gaps surfaced

| Use case | Termination criterion in current code | Adequate? |
|---|---|---|
| SeisSol / TinyDA | Fixed `iterations` count (`Seis-Bridge/tinyda.py:56`) | **No** — proposed: rank-normalised split-$\hat{R} < 1.05$ + ESS > 400, retaining the iteration count as a hard cap. See Q1.3. |
| HPO / Ray Tune | Intra-iteration: phase-loop early-stop when `best_score >= 0.99` (`hpo_pipeline_verbose.py:804–808`). Cross-iteration: resize-only heuristic on `success_rate` (`hpo_pipeline_verbose.py:835–838`); no target-driven zero-out, so workflow terminates only at the YAML `workflowIterations` cap. ASHA handles within-iteration trial pruning. | Weaker than the gap analysis suggests: the iteration cap is the de-facto cross-iteration stop. |
| License-aware | Inherits SeisSol termination | Same gap as SeisSol. |

The convergence-criterion gap for the SeisSol/TinyDA path is a single-file change (TinyDA driver), does not require any modification to the scheduler protocol, and would convert the iteration parameter from a termination rule into a cost ceiling — a substantively cleaner framing for the moldable-scheduling thesis, where budget and deadline already carry the cost-cap responsibility.
