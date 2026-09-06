# Differences between the dissertation and the code

Collected on 2026-09-06 while aligning the documentation with the submitted
sources (tag `thesis-submitted-2026-09-04`). Nothing here was changed in the
code or in the datasets: the tag is the reference, and the dissertation is
submitted. Each entry gives what the dissertation says and where, what the
code does and where, what the difference amounts to, and what reconciling it
would involve. Section and table numbers are not used because they can only
be read from a build; the section titles are.

## A. Numbers that differ

### A1. TinyDA--UM-Bridge coupling overhead

* Dissertation, Chapter 7 (Empirical Overhead Injection), overhead table:
  injected value 5.6 s (mesh 1000), 10.4 s (750), 27.2 s (500), described in
  the text as the measured means 5.6 ± 1.3, 10.4 ± 2.8, 27.2 ± 2.0 s.
* Code: `elastiflow/scripts/speedup.py:3`,
  `tinyDaOverhead = {1000: 4.713, 750: 8.378, 500: 25.799}`, added to every
  per-evaluation runtime by `getRuntime`, which both the Scheduler's estimates
  and the simulated backend use.
* Difference: 0.9, 2.0 and 1.4 s per evaluation.
* Reconciling: the table's "injected value" column would read 4.7, 8.4 and
  25.8 s (the measured means stay as they are), or the constants would change
  and every simulated number would move.

### A2. Heterogeneity threshold

* Dissertation, Chapter 8 (Scheduler Parameter Instantiation) and its
  parameter table: delta = 1.4 for SeisSol--TinyDA and the licence-constrained
  class, "a candidate instance type is admitted into a heterogeneous bundle
  only when its single-stream runtime lies within 1.4x the bundle's reference
  runtime"; Chapter 6 (The Heterogeneity Threshold) defines it as a ratio of
  the single-stream runtime estimators.
* Code: `elastiflow/config/constants.py:62`, `CLOSENESS_TOLERANCE = 0.15`;
  `Scheduler.checkCloseness` (`scheduler/scheduler.py:323`) admits a candidate
  when `math.isclose(runtime, x, rel_tol=0.15)` for any runtime `x` in the
  workflow's current fleet, and adds `COLD_START_TIME` to the candidate's
  runtime first when it is on-demand.
* Difference: the test is a symmetric relative tolerance of 15 % (a ratio of
  at most about 1.18), not a ratio bound of 1.4, and the cold start enters the
  comparison, which the dissertation's formula does not include.
* Reconciling: either the dissertation's description of the test and its
  value, or the constant and the comparison.

### A3. HPO deadline pacing

* Dissertation, Chapter 8 (Urgency weight schedules): one schedule for HPO,
  alpha = 0.70, 0.85, 1.00 at renegotiations 1 to 3, entering both the
  remaining budget and the remaining time.
* Code: `elastiflow/config/constants_HPO.py:218-219`,
  `OPTIM_FCFS_BFACTOR = {0: 0.5, 1: 0.7, 2: 0.85, 3: 1.0, 4: 1.0, 5: 1.0}` (budget,
  matches) and `OPTIM_FCFS_DFACTOR = {0: 0.1, 1: 0.3, 2: 0.4, 3: 0.6, 4: 0.7, 5: 0.8}`
  (deadline, does not).
* Scope: the HPO results of Chapter 9 come from the live R7 run and from the
  calibrated model `use_cases/hpo/results/r7_n7_actual_vs_modeled/sim_4corners_calibrated.py`,
  which has no pacing constants; the live schedulers used these factors.

### A4. HPO population bounds

* Dissertation, Chapter 8 (HPO Workflows): the trial population is bounded
  within [p_min, p_max] = [2, 10], p_min = 2 being the minimum viable GPU
  parallelism of the on-premise pool.
* Code: `use_cases/hpo/application/hpo_pipeline_verbose.py:22`,
  `MIN_TRIALS = 3`; the contraction is `max(MIN_TRIALS, int(num_samples * 0.5))`,
  the expansion `min(10, int(num_samples * 1.5))` (lines 836 and 838).
* Difference: the floor is 3, not 2.

### A5. HPO maximum trial length

* Dissertation, Chapter 8 (Fixed driver parameters): T_max = 20 epochs.
* Code: `TunePipeline.__init__` defaults to `AsyncHyperBandScheduler(max_t=20)`
  (`hpo_pipeline_verbose.py:452`), but the scheduler built for each phase is
  `AsyncHyperBandScheduler(max_t=200, ...)` (line 749). A trial's epoch count is
  in any case bounded by the YAML's `epochs`.
* Difference: the ASHA rung ceiling in the phase that runs is 200, not 20.

### A6. Evaluations per chain in the runtime model

* Dissertation, Chapter 8 (Constraint Generation): a chain of six links
  performs seven evaluations, one initial evaluation plus one per link; the
  population constant is e = 7.
* Code: the constraint constant agrees (`AVG_TINYDA_ITERATIONS = 7`), but the
  simulated iteration runtime multiplies the slowest chain's evaluation time by
  `tinyda_iterations + 2` (`elastiflow/scripts/tinyda_runtime.py:76`).
* Difference: an iteration with L links is simulated as L + 2 evaluations
  where the dissertation's count is L + 1: 50 % longer at L = 1, 10 % longer
  at L = 9.

### A7. Licence cost rates

No difference. `LicenseManager.license_cost_by_owner` uses 1 000, 2 500,
14 000 and 1 700 EUR per year over 365.25 days, in EUR; the dissertation's
per-second USD rates are these times 1.10.

## B. Points internal to the dissertation

### B1. Licence batch sizes

Chapter 8 (Licence-Constrained Scientific Workflows) lists
N in {200, 300, 400, 500, 600, 700}; Chapter 8 (Licence-aware thresholds)
speaks of "six of the seven batch sizes", Chapter 9 reports N = 150, and the
dataset `use_cases/licence/results/canonical_results.json` has seven sizes,
150 to 700.

### B2. HSM's expansion

Chapter 5 defines HSM as the Hybrid Static-Malleable strategy; Chapter 10
(Summary of Contributions) calls it the Hybrid Schedule Manager.

### B3. The fitted runtime model against the strong-scaling table

Chapter 8 (On-Premise Tier Configuration) reports measured strong-scaling
speedups for SeisSol on both platforms: about 2x at 2 nodes and 2.97 to 3.56
at 4 nodes for mesh 500, and 2.55 against 3.37 at 4 nodes for mesh 1000.
Chapter 8 (Speedup Curves and Runtime Models) then adopts one exponential
form per instance type, `a * exp(b * nodes / N0 + c * mesh / 1000) + d`, with
R² at or above 0.97 over nine points. The code's fits (`speedup.py`), which
the Scheduler and the simulator use, give these per-evaluation runtimes
(seconds, coupling overhead excluded; on-premise uses measured values at 1
and 2 nodes and the fit from 3 nodes on):

| instance | mesh | 1 node | 2 | 3 | 4 | 8 | speedup at 2 | speedup at 4 |
|---|---|---|---|---|---|---|---|---|
| on-prem | 1000 | 109 | 64 | 72 | 68 | 60 | 1.70 | 1.60 |
| on-prem | 500 | 469 | 246 | 351 | 272 | 118 | 1.91 | 1.73 |
| hpc7a.24xlarge | 1000 | 138 | 137 | 136 | 136 | 135 | 1.01 | 1.02 |
| hpc7a.24xlarge | 500 | 684 | 469 | 339 | 259 | 152 | 1.46 | 2.64 |
| hpc7a.12xlarge | 1000 | 182 | 155 | 139 | 130 | 117 | 1.17 | 1.40 |
| hpc7a.12xlarge | 500 | 1250 | 799 | 528 | 364 | 148 | 1.56 | 3.43 |
| c7i.24xlarge | 1000 | 100 | 92 | 87 | 83 | 77 | 1.09 | 1.20 |
| c7i.24xlarge | 500 | 1393 | 964 | 674 | 479 | 159 | 1.45 | 2.91 |
| c7i.12xlarge | 1000 | 213 | 198 | 186 | 176 | 154 | 1.08 | 1.21 |
| c7i.12xlarge | 500 | 2261 | 1807 | 1451 | 1170 | 534 | 1.25 | 1.93 |
| c6i.32xlarge | 1000 | 103 | 95 | 89 | 86 | 80 | 1.09 | 1.20 |
| c6i.32xlarge | 500 | 1285 | 879 | 610 | 432 | 147 | 1.46 | 2.98 |
| c6i.16xlarge | 1000 | 130 | 119 | 112 | 107 | 98 | 1.09 | 1.22 |
| c6i.16xlarge | 500 | 1836 | 1284 | 907 | 650 | 216 | 1.43 | 2.83 |

Two things follow. The additive exponent makes the cloud fits nearly flat in
node count at mesh 1000 (hpc7a.24xlarge 138 to 136 s from 1 to 4 nodes),
where the table reports 4-node speedups of 2.55 to 3.37. On-premise the fit
at 3 and 4 nodes is slower than the measured 2-node value, so the model's
4-node speedup at mesh 500 is 1.7 against the table's 2.97 to 3.56. The
speedup threshold sigma = 1.4 is evaluated on these model values, so they
decide how many nodes a chain may receive.

## C. Descriptions that differ from the code

### C1. Where the Load Balancer lives

Chapter 6 places the Load Balancer inside each application driver. The
SeisSol chain-to-node assignment runs in the policy classes and
`elastiflow/utils/exec_sched.py`; the HPO phase decomposition runs in the
driver (`hpo_pipeline_verbose.py`). Stated in `docs/ARCHITECTURE.md`.

### C2. Driver script names

Chapter 4 names `drivers/seissol_tinyda.py`, `drivers/raytune_hpo.py` and
`drivers/seissol_tinyda_licensed.py`. The scripts are
`scripts/simulate-tinyda-seissol.py` (simulated), `deploy/runners/run_seissol.py`
over `use_cases/seissol/driver/` (live), and `deploy/runners/run_hpo.py` over
`use_cases/hpo/application/hpo_pipeline_verbose.py`. There is no separate
licensed driver; Chapter 6 (Licence coordination) says the driver receives
compute identifiers only, which is what the code does.

### C3. Monetary unit

The datasets and YAMLs carry cost fields the code names EUR
(`total_cost_eur`, `budget`), the on-premise rate in `config/resources.yaml`
is 0.0004056 per second (1.46 per hour); the dissertation reports USD at
1 EUR = 1.10 USD through the figure generators and states the on-premise rate
as 1.47 USD per node-hour (a 0.7 % drift accepted on 2026-08-30).

### C4. HSM's gate outside the campaign driver

Chapter 8 applies P_thresh = 0.70 uniformly. `edf_hsm_LA.py:87-91` defaults
the uniform value to 0.70 but the per-pool values to 0.60, 0.60 and 0.95
(`LA_HSM_POOL_RHO_ANSYS/ABAQUS/LSDYNA`); `canonical_sweep.py` sets
`LA_HSM_POOL_RHO*=0.70`. A run of `simulate_main_LA.py --scheduler EDF-HSM`
without that environment is not the dissertation's HSM.

### C5. Constants that describe an earlier design

`constants_LA.py:173-185` defines `SCALE_UP_URGENCY_CRITICAL = 0.4`,
`SCALE_UP_URGENCY_WARNING = 0.8`, `URGENCY_BOOST_CRITICAL = 2.5`,
`URGENCY_BOOST_WARNING = 1.8`. The trigger the elastic licence policies apply
is the one in Chapter 5's trigger-mode table (upsilon < 0.30 with boost 2.0,
then 1.5, 1.2, 1.2, 1.0), coded inline in `edf_optimized_LA.py:400-416` and
`576-585`. The constants are not what runs.

### C6. HPO improvement threshold and patience

Chapter 8 states delta_imp = 0.01. The pipeline class defaults to `1e-3`
(`hpo_pipeline_verbose.py:453`); the path `run_hpo.py` takes passes `1e-2`
(line 1085), so the campaign used the dissertation's value. The same path
sets `patience=2` (stop after two rounds without improvement), which the
dissertation does not mention; it states the iteration cap as the operative
termination criterion.

### C7. Load-balancer efficiency

The dissertation uses a pooled epsilon = 0.92 (`scaling_efficiency`).
`use_cases/hpo/WORKLOAD_DESCRIPTION.md` also reports the measured per-worker
efficiencies of 97.6 % at 2 GPUs and 96.0 % at 4 from the profiling grid.
Both are the author's numbers; the dissertation reports only the pooled one.

## D. Cited policies that differ in more than their ordering

Found in Phase B7 (`docs/PHASE_B7_SCHEDULER_MERGE.md`, "differences kept
behind hooks") when the scheduler forks were merged; the dissertation
describes each pair as differing in admission order or allocation regime
alone. They are kept behind named hooks and reproduce the tagged numbers.

1. SeisSol elastic fallback: `FCFS_Optimized.checkNewResourcesMoldable` skips
   a candidate node (`else: continue`) where the base method does not, so
   Elastic-FCFS and Elastic-EDF differ in more than ordering.
2. FCFS-LAMF and EDF-LAMF evaluate different feasibility chains in
   `checkNewResourcesWithLicenses`.
3. The HPO on-premise runtime model is `getRuntime_g5` in one elastic policy
   and `getRuntime_g4` in the other (`_runtimeFunctionFor`).
4. HSM holds `setResourcesAvailable(False)` where EDF-LAMF does not, besides
   its phase gate, and writes its metrics under its own prefix.
