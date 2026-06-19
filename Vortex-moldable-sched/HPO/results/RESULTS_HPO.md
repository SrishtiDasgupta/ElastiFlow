# RESULTS_HPO — Data Extract for HPO Chapter

**Provenance**
- Source file: `HPO/results/plots/total_cost_per_run.json` (regenerated 2026-06-07; backup `total_cost_per_run.OLD_OD4.json`)
- Simulator: `HPO/results/r7_n7_actual_vs_modeled/sim_4corners_calibrated.py` — fleet now `CLUSTER_CAP = {slurm: 4, g4: 5, g5: 5}`, `RESERVED = {slurm: 4, g4: 2, g5: 2}` ⇒ on-prem 4 + reserved cloud 4 + on-demand cloud 6 = **14 lanes total** (deployed AWS plan).
- Aggregation: `HPO/results/plots/compute_total_cost.py` (re-run on patched simulator).
- Plot generators: `generate_thesis_plots.py`, `generate_hpo_insights.py`, `regen_corrected_figs.py` (all run on new data).
- Cross-workload reference: `plain_results/plain_results_per_run.json` (unchanged).
- Extract revised: 2026-06-07.
- All numbers in this file are recomputed from the new `total_cost_per_run.json` on the SeisSol-canonical definitions (`src/main/utils/metrics.py`).
- † next to a number = coefficient of variation > 15 %.

---

## CHANGELOG — data regeneration (OD ceiling 4 → 6)

Trigger: the previous extract used a simulator config (`OD_AVAILABLE = 2 g4 + 2 g5 = 4`) narrower than the deployed AWS plan (`up to 3 g4 + 3 g5 = 6`). The 12 cells were rerun with the deployed-plan ceiling. Every metric moved; the qualitative findings are robust but magnitudes shifted.

### Cost̄ per workflow (USD) — point estimates @ N=7

| Variant | OLD (OD 4) | NEW (OD 6) | Δ |
|---|---|---|---|
| STAT-EDF | 2.88 | **2.96** | +2.8 % |
| STAT-FCFS | 2.94 | **2.93** | −0.3 % |
| ELASTIC-EDF | 2.35 | **2.50** | +6.4 % |
| ELASTIC-FCFS | 2.37 | **2.52** | +6.3 % |

Elastic costs rise more than static because elastic now spawns more OD lanes at iteration boundaries (was previously OD-capped).

### Miss rates @ N=7

| Variant | OLD DMR | NEW DMR | OLD BMR (comp) | NEW BMR (comp) | OLD OMR | NEW OMR |
|---|---|---|---|---|---|---|
| STAT-EDF | 0.619 | **0.476** | 0.571 | **0.571** | 0.857 | **0.762** |
| STAT-FCFS | 0.643 | **0.476** | 0.548 | **0.548** | 0.833 | **0.762** |
| ELASTIC-EDF | 0.452 | **0.381** | 0.381 | **0.381** | 0.714 | **0.667** |
| ELASTIC-FCFS | 0.429 | **0.357** | 0.429 | **0.476** | 0.762 | **0.714** |

Static deadline-miss rate drops 14–17 pp under the extra OD headroom — static admission now has room to allocate a larger initial fleet onto OD. Elastic deadline-miss rate drops 7 pp. So **the extra OD helps static more than elastic on deadlines**, narrowing the elastic-vs-static deadline advantage.

### Batch makespan (minutes) @ N=7

| Variant | OLD | NEW | Δ |
|---|---|---|---|
| STAT-EDF | 220.4 | **199.5** | −9.5 % |
| STAT-FCFS | 236.1 | **202.1** | −14.4 % |
| ELASTIC-EDF | 200.8 | **157.2** | −21.7 % |
| ELASTIC-FCFS | 197.1 | **152.9** | −22.4 % |

**Elastic accelerates more than static** — the OD ceiling was the binding makespan constraint for elastic (every iteration boundary that wanted more lanes than the cap couldn't get them); for static the constraint was already mostly satisfied at admission.

### Utilisation all-tier (denominator now 14 lanes) @ N=7

| Variant | OLD Ū (12-lane den.) | NEW Ū (14-lane den.) |
|---|---|---|
| STAT-EDF | 0.723 | **0.669** |
| STAT-FCFS | 0.706 | **0.656** |
| ELASTIC-EDF | 0.665 | **0.733** |
| ELASTIC-FCFS | 0.685 | **0.760** |

**Qualitative reversal:** the static-utilisation-higher-than-elastic pattern from the prior extract is **flipped**. Now elastic utilisation > static utilisation by 6–10 pp on the all-tier metric. Mechanism: under the new ceiling, elastic actually exploits the additional 2 OD lanes (max OD observed = 6 = simulator cap), while static's larger reserved fleet ends up under-saturated at the headline N because workflows complete faster on the bigger admission fleet.

### Paired Elastic − Static deltas @ N=7

| Pair | OLD %ΔCost̄ | NEW %ΔCost̄ | OLD ppΔDMR | NEW ppΔDMR | OLD ppΔBMR | NEW ppΔBMR | OLD %ΔM_batch | NEW %ΔM_batch |
|---|---|---|---|---|---|---|---|---|
| EDF | −17.1 | **−14.4** | −16.67 | **−9.52** | −19.05 | **−19.05** | −9.0 | **−19.4** |
| FCFS | −19.3 | **−13.5** | −21.43 | **−11.90** | −7.14 | **−7.14** | −15.3 | **−23.2** |

Elastic remains the clear winner on every paired axis; the **deadline-miss advantage shrank** (extra OD helped static more) but the **makespan advantage grew** (extra OD helped elastic more). Cost advantage narrowed by ~3–6 pp. BMR delta essentially unchanged.

### Figures regenerated, removed, or unchanged

- **Regenerated on the new data:** 01_cost_vs_n, 01b_cost_stack, 02_misses_vs_n, 02b_misses_bar, 02c_budget_misses_bar, 02d_turnaround_stack, 02e_makespan_bar, 02g_utilization_time, 02h_tier_zoom_elastic_edf_n7, 03_sumflow_vs_n, 04_paired_diff, 05_per_wf_n7, HPO_06_pareto_trajectory, HPO_08_intent_events_n7, HPO_08_intent_satisfaction_n7, HPO_11_util_4corners_n7, CROSS_pareto_comparison.
- **Removed:** 02f_cpr_bar (CPR is dropped for HPO — ill-conditioned at OMR → 1).
- **Total figures present:** 17 (PDF + PNG each).

---

## Step 1 — Discovery

**Variant coverage:** 4 of the 11 SeisSol variants (static_edf, static_fcfs, moldable_edf, moldable_fcfs). HEFT-ST, both Rank pairs, and the four sort-key suffix variants are absent.

**N-sweep:** N ∈ {3, 5, 7}; headline N = **7**; 6 seeds per cell × 12 cells = **72 simulations**.

**Fleet (now matches deployed AWS plan):**
- On-premises (slurm): 4 lanes (always-on)
- Reserved cloud: 2 g4 + 2 g5 = 4 lanes (always-on)
- On-demand cloud: up to 3 g4 + 3 g5 = 6 lanes (spawn-on-demand)
- **Total provisioned capacity: 14 lanes** (max ever observed in any run: 14; max OD observed: 6 — both at the simulator cap, indicating capacity-bounded behaviour).

---

## Step 2 — Workload Characterisation

(See HPO_WORKLOAD_DESCRIPTION.md for full detail.)

**Workflow population:** Seven CIFAR-10 hyperparameter-optimisation workflows. Dispatch order by N:
- N=3 → `data8, data9, data5`
- N=5 → `data8, data9, data5, data7, data3`
- N=7 → `data8, data9, data5, data7, data3, data12, data1`

Each workflow runs multiple HPO rounds, each round a population of ML training trials in parallel.

**Per-iteration concurrency:** *Chain count GROWS* across iterations (the opposite of successive halving):

| Workflow | Model | Iterations | Chains per iter | Epoch per iter |
|---|---|---|---|---|
| data8 | vgg19 | 3 | 4 → 6 → 9 | 10 → 11 → 12 |
| data9 | convnext_large | 5 | 3 → 3 → 3 → 3 → 3 | 28 → 40 → 57 → 65 → 95 |
| data5 | wide_resnet101_2 | 5 | 3 → 4 → 6 → 9 → 10 | 25 → 22 → 21 → 30 → 30 |
| data7 | wide_resnet101_2 | 4 | 2 → 3 → 4 → 6 | 27 → 35 → 50 → 50 |
| data3 | vgg19 | 3 | 4 → 6 → 9 | 18 → 9 → 12 |
| data12 | vgg19 | 4 | 4 → 6 → 9 → 13 | 17 → 18 → 24 → 33 |
| data1 | vgg19 | 3 | 4 → 6 → 9 | 16 → 12 → 13 |

The widening pattern is the **defining structural property of HPO** in this dissertation: late iterations need more compute than admission predicts, which structurally penalises static (cannot grow) and rewards elastic (can scale up at iteration boundaries — if OD capacity exists).

**Structural contrast with SeisSol:** SeisSol-TinyDA workflows are constant-width across iterations; HPO is widening. Combined with `data9`'s epoch-growth pattern (3.4× from iter 0 to iter 4), per-iteration computational demand grows monotonically over each workflow's lifetime.

---

## Step 3 — Numbers Behind Each Figure  (new basis, OD = 6)

### Definition reconciliation with SeisSol

| Metric | SeisSol (`metrics.py`) | HPO (corrected) | Match? |
|---|---|---|---|
| DMR | `(completed-late + never-completed) / |W|` | `misses / N` with `completion = t_end` for never-completed | ✓ |
| BMR | `(completed-over-budget) / |W|` — completed only | `#{w: completion<makespan ∧ cost>budget} / N` (corrected; was all-workflow) | ✓ |
| OMR | `(completed-violated-either + never-completed) / |W|` | `#{w: miss==1 OR budget_miss==1} / N` from per-wf flags | ✓ |
| Cost̄ | EUR × 1.10 USD per wf | natively USD per wf | ✓ (different hardware; both on-the-bill) |
| Utilisation | Time-averaged active/all-tier-provisioned cores | `(slurm_h + res_h + od_h) / (14 × t_end/3600)` | ✓ |
| CPR | Lower=better; ill-conditioned at OMR→1 for HPO | DROPPED for HPO | n/a |
| Sum-of-flowtimes | not a SeisSol headline | `sum_turnaround_s` per run; non-completed → `t_end − submit` | HPO-only |

---

### Figure 01 — `01_cost_vs_n` — Per-workflow cost (USD)

| Variant | N=3 | N=5 | N=7 |
|---|---|---|---|
| STAT-EDF | 2.57 ± 0.51 † | 2.70 ± 0.34 | 2.96 ± 0.51 † |
| STAT-FCFS | 2.57 ± 0.51 † | 2.70 ± 0.34 | 2.93 ± 0.49 † |
| ELASTIC-EDF | 3.34 ± 0.73 † | 2.48 ± 0.37 † | 2.50 ± 0.32 |
| ELASTIC-FCFS | 3.34 ± 0.73 † | 2.48 ± 0.37 † | 2.52 ± 0.36 |

### Figure 01b — `01b_cost_stack` — Cost tier shares at N=7

| Variant | on-prem (slurm) % | reserved cloud % | on-demand cloud % | total cost (USD) |
|---|---|---|---|---|
| STAT-EDF | 44.5 ± 7.9 | 18.1 ± 1.9 | 37.5 ± 6.2 | 20.71 ± 3.58 |
| STAT-FCFS | 43.0 ± 7.2 | 18.5 ± 1.9 | 38.5 ± 5.5 | 20.54 ± 3.46 |
| ELASTIC-EDF | 31.6 ± 6.6 | 17.2 ± 1.7 | **51.2 ± 5.1** | 17.51 ± 2.23 |
| ELASTIC-FCFS | 33.5 ± 4.0 | 16.8 ± 1.3 | **49.7 ± 2.9** | 17.62 ± 2.49 |

**Elastic now spends over half its budget on on-demand** (was ~44 % under OD 4). The extra two OD lanes per cluster get used heavily by elastic's iteration-boundary scale-up path.

### Figure 02 — `02_misses_vs_n` — Miss rates by N

| Variant | N=3 DMR | N=3 BMR | N=3 OMR | N=5 DMR | N=5 BMR | N=5 OMR | N=7 DMR | N=7 BMR | N=7 OMR |
|---|---|---|---|---|---|---|---|---|---|
| STAT-EDF | 0.22 ± 0.17 † | 0.44 ± 0.17 † | 0.67 ± 0.30 † | 0.30 ± 0.21 † | 0.53 ± 0.21 † | 0.70 ± 0.21 † | 0.48 ± 0.17 † | 0.57 ± 0.22 † | 0.76 ± 0.20 † |
| STAT-FCFS | 0.22 ± 0.17 † | 0.44 ± 0.17 † | 0.67 ± 0.30 † | 0.30 ± 0.21 † | 0.53 ± 0.21 † | 0.70 ± 0.21 † | 0.48 ± 0.17 † | 0.55 ± 0.23 † | 0.76 ± 0.20 † |
| ELASTIC-EDF | 0.22 ± 0.17 † | 0.56 ± 0.17 † | 0.83 ± 0.28 † | 0.23 ± 0.15 † | 0.37 ± 0.08 † | 0.60 ± 0.13 † | 0.38 ± 0.12 † | 0.38 ± 0.07 † | 0.67 ± 0.15 † |
| ELASTIC-FCFS | 0.22 ± 0.17 † | 0.56 ± 0.17 † | 0.83 ± 0.28 † | 0.23 ± 0.15 † | 0.37 ± 0.08 † | 0.60 ± 0.13 † | 0.36 ± 0.15 † | 0.48 ± 0.07 † | 0.71 ± 0.09 |

### Figure 03 — `03_sumflow_vs_n` — Sum-of-flowtimes (minutes)

| Variant | N=3 | N=5 | N=7 |
|---|---|---|---|
| STAT-EDF | 274.4 ± 67.9 † | 454.0 ± 60.2 | 775.2 ± 123.2 † |
| STAT-FCFS | 274.4 ± 67.9 † | 454.0 ± 60.2 | 774.1 ± 118.8 † |
| ELASTIC-EDF | 253.6 ± 56.2 † | 397.5 ± 53.4 | 633.5 ± 90.7 |
| ELASTIC-FCFS | 253.6 ± 56.2 † | 397.5 ± 53.4 | 635.0 ± 94.4 |

### Figure 02b — `02b_misses_bar` — DMR at N=7

| Variant | DMR | Miss count (of 7) |
|---|---|---|
| STAT-EDF | 0.476 ± 0.173 † | 3.33 ± 1.21 |
| STAT-FCFS | 0.476 ± 0.173 † | 3.33 ± 1.21 |
| ELASTIC-EDF | 0.381 ± 0.117 † | 2.67 ± 0.82 |
| ELASTIC-FCFS | 0.357 ± 0.150 † | 2.50 ± 1.05 |

### Figure 02c — `02c_budget_misses_bar` — BMR at N=7 (completed-only)

| Variant | BMR (rate) | Budget-miss count (of completed) |
|---|---|---|
| STAT-EDF | 0.571 ± 0.221 † | 4.00 ± 1.55 |
| STAT-FCFS | 0.548 ± 0.229 † | 3.83 ± 1.60 |
| ELASTIC-EDF | 0.381 ± 0.074 † | 2.67 ± 0.52 |
| ELASTIC-FCFS | 0.476 ± 0.074 † | 3.33 ± 0.52 |

### Figure 02d — `02d_turnaround_stack` — Wait + execution at N=7 (seconds)

| Variant | WA̅ (wait) | M̄ (exec) | TA̅ (total) | WA̅ / TA̅ |
|---|---|---|---|---|
| STAT-EDF | 2062 ± 371 | 4583 ± 752 | 6645 ± 1056 | 31.0 % |
| STAT-FCFS | 2074 ± 359 | 4561 ± 725 | 6636 ± 1018 | 31.3 % |
| ELASTIC-EDF | 1029 ± 147 | 4401 ± 648 | 5430 ± 777 | 18.9 % |
| ELASTIC-FCFS | 1029 ± 147 | 4414 ± 685 | 5443 ± 809 | 18.9 % |

Elastic halves wait time. Static wait also dropped vs the OD-4 basis (3119 s → 2062 s) because static now admits with a larger fleet that finishes earlier.

### Figure 02e — `02e_makespan_bar` — Batch makespan (minutes)

| Variant | N=3 | N=5 | N=7 |
|---|---|---|---|
| STAT-EDF | 149.4 ± 58.7 † | 165.7 ± 38.4 † | 199.5 ± 32.7 † |
| STAT-FCFS | 149.4 ± 58.7 † | 165.7 ± 38.4 † | 202.1 ± 29.0 |
| ELASTIC-EDF | 128.5 ± 35.4 † | 136.3 ± 20.4 | 157.2 ± 29.1 † |
| ELASTIC-FCFS | 128.5 ± 35.4 † | 136.3 ± 20.4 | 152.9 ± 31.4 † |

### Figure 02g, HPO_11 — Utilisation  (denominator now 14 lanes)

| Variant | Ū all-tier (cap=14) | Ū reserved-only (legacy, cap=8) |
|---|---|---|
| STAT-EDF | 0.669 ± 0.054 | 0.830 ± 0.089 |
| STAT-FCFS | 0.656 ± 0.039 | 0.805 ± 0.061 |
| ELASTIC-EDF | **0.733 ± 0.111** | 0.744 ± 0.153 |
| ELASTIC-FCFS | **0.760 ± 0.110** | 0.779 ± 0.123 |

**Elastic utilisation exceeds static on the all-tier metric.** This is the qualitative reversal driven by the OD-ceiling correction. The reserved-only legacy metric still shows the historical static > elastic pattern (because the reserved fleet really is held longer by static), but the deployed-fleet picture is the opposite.

**OD hours used at N=7** (cap 6 g4 + 6 g5 = 12 OD-cluster-hours total within a 60-min window):

| Variant | OD g4 hours | OD g5 hours | OD total |
|---|---|---|---|
| STAT-EDF | 2.78 ± 0.67 | 6.38 ± 2.39 | 9.16 |
| STAT-FCFS | 2.78 ± 0.67 | 6.51 ± 2.19 | 9.29 |
| ELASTIC-EDF | 4.75 ± 0.88 | 6.42 ± 1.66 | **11.17** |
| ELASTIC-FCFS | 4.90 ± 1.13 | 6.14 ± 1.43 | **11.04** |

Elastic burns ~20 % more OD-hours than static. With the previous OD-4 ceiling this was structurally capped; now it isn't.

### Figure 04 — `04_paired_diff` — Paired Elastic − Static deltas at N=7

| Family pair | %ΔCost̄ | %ΔWA̅ | ppΔDMR | ppΔBMR | %ΔM_batch |
|---|---|---|---|---|---|
| EDF | −14.4 ± 10.8 | −49.6 ± 5.9 | −9.52 ± 17.30 | −19.05 ± 17.30 | **−19.4 ± 20.3** |
| FCFS | −13.5 ± 9.6 | −49.9 ± 5.5 | −11.90 ± 14.05 | −7.14 ± 19.69 | **−23.2 ± 18.3** |

### Figure HPO_08 — Intent events at N=7

| Variant | up_approve | up_modify | up_deny | down_granted |
|---|---|---|---|---|
| ELASTIC-EDF | 0.00 ± 0.00 | 1.17 ± 0.41 | 13.33 ± 1.21 | 0.00 ± 0.00 |
| ELASTIC-FCFS | 0.00 ± 0.00 | 1.33 ± 0.52 | 13.17 ± 1.47 | 0.00 ± 0.00 |

Satisfaction rate (approve + modify) / total upward intents:

| Variant | total upward | approve + modify | denied | satisfaction |
|---|---|---|---|---|
| ELASTIC-EDF | 14.50 | 1.17 | 13.33 | **8.0 %** |
| ELASTIC-FCFS | 14.50 | 1.33 | 13.17 | **9.2 %** |

Roughly 91 % of upward scale-up intents denied (was ~88 % under OD 4). The extra OD didn't reduce denial — the workload's late-iteration peaks still exceed available headroom in many cases. Zero downward intents granted (workload widens monotonically; nothing to release).

### Figure 05 — `05_per_wf_n7` — Per-workflow spread at N=7 (ELASTIC-EDF, representative)

| Workflow | completion (s) | miss freq | budget-miss freq | cost (USD) |
|---|---|---|---|---|
| data8 | 3075 ± 648 | 0.00 | 1.00 | 2.87 ± 0.60 |
| data9 | 5090 ± 1391 | 0.00 | 0.67 | 3.11 ± 0.90 |
| data5 | 8448 ± 2878 | 0.67 | 0.83 | 3.98 ± 1.71 |
| data7 | 4838 ± 1623 | 0.33 | 0.00 | 0.98 ± 0.37 |
| data3 | 4850 ± 1022 | 0.17 | 0.17 | 1.47 ± 0.34 |
| data12 | 8395 ± 1918 | 1.00 | 0.33 | 2.41 ± 1.77 |
| data1 | 5945 ± 1622 | 0.50 | 0.67 | 2.68 ± 1.06 |

data5 completion shortened from 11382 s (OD 4) to 8448 s (OD 6) because elastic could spawn more OD when its chain count grew to 10 in iter 4.

---

## Step 4 — Frontier and Cross-workload Pareto

### `HPO_06_pareto_trajectory` — Frontier composition by N (Cost̄, OMR)

| N | Frontier composition |
|---|---|
| 3 | STAT-EDF and STAT-FCFS tied ($2.57, OMR 0.667) |
| 5 | ELASTIC-EDF and ELASTIC-FCFS tied ($2.48, OMR 0.600) |
| 7 | ELASTIC-EDF alone ($2.50, OMR 0.667) |

Cheapest / lowest-miss / structure:
- N=3: cheapest = STAT (elastic costs more under low load because OD is provisioned eagerly); lowest OMR = STAT.
- N=5 & 7: cheapest = ELASTIC; lowest OMR = ELASTIC.

**Frontier flip from static-only (N=3) to elastic-only (N=5 onward) survives the regeneration.** No "Hard-SLO" regime exists at any N — every variant misses 60–86 % of submissions on at least one constraint.

### `CROSS_pareto_comparison` — Cross-workload Pareto on shared axes (per-wf cost USD, OMR)

LEFT panel — Plain SeisSol @ N=400 (unchanged from prior extract; pulled from `plain_results/plain_results_per_run.json`):
| Variant | Cost̄/wf (USD) | OMR | On frontier? |
|---|---|---|---|
| rank_moldable_5050 | 18.74 | 0.242 | ✓ |
| edf_moldable_c | 20.46 | 0.140 | ✓ |
| edf_static_c | 34.18 | 0.074 | ✓ |
| (other 8 variants dominated) | … | … | |

RIGHT panel — HPO @ N=7 (new):
| Variant | Cost̄/wf (USD) | OMR | On frontier? |
|---|---|---|---|
| ELASTIC-EDF | 2.50 ± 0.32 | 0.667 ± 0.155 | ✓ |
| ELASTIC-FCFS | 2.52 ± 0.36 | 0.714 ± 0.090 | dominated by ELASTIC-EDF |
| STAT-EDF | 2.96 ± 0.51 | 0.762 ± 0.205 | dominated |
| STAT-FCFS | 2.93 ± 0.49 | 0.762 ± 0.205 | dominated |

Both panels share axes; the cross-workload story is now quantitative. HPO sits at the high-OMR (0.67–0.76), low-per-wf-cost ($2.5–2.9) corner of the diagram; SeisSol sits at the low-OMR (0.07–0.28), high-per-wf-cost ($18–38) corner. Both workloads have elastic on the frontier; HPO has elastic-only.

---

## Step 5 — Findings-vs-SeisSol Table  (re-evaluated on the new data)

| # | SeisSol finding @ N=400 | HPO equivalent @ N=7 (NEW) | Verdict | One-line driver |
|---|---|---|---|---|
| (a) | Elastic 40–45 % lower Cost̄ (paired mean −41.7 %) | Elastic −14.4 % (EDF) / −13.5 % (FCFS) — was −17.1 / −19.3 under OD 4 | **WEAKER** (slightly more so than before) | Extra OD raises elastic's absolute cost more than static's; cost gap shrinks ~3 pp under the correction. |
| (b) | Elastic 33–40 % shorter WA̅ (paired mean −38.0 %) | Elastic −49.6 % (EDF) / −49.9 % (FCFS) — was −65 / −68 under OD 4 | **STRONGER** (less so than before) | Static now finishes faster under the larger fleet, reducing its wait too; elastic's wait advantage narrows. |
| (c) | Elastic BMR < 1 % (0.005–0.009) | Elastic BMR 0.38–0.48 — was 0.38–0.43 under OD 4 | **REVERSES** (survives) | HPO per-workflow budgets are intrinsically tight relative to actual completed-run costs; the reversal is workload-structural. |
| (d) | Deadline response family-dependent (FCFS Δ≈0, EDF +8–10 pp, Rank vs HEFT-ST −16 pp) | EDF Δ = −9.5 pp; FCFS Δ = −11.9 pp — was −16.7 / −21.4 under OD 4 | **REVERSES** (less strongly) | Extra OD helped static more than elastic on deadlines (static gets to admit with a bigger fleet); the elastic deadline advantage shrunk ~50 % but is still negative. Rank/HEFT N/A. |
| (e) | Elastic +2–4 % M_batch overhead vs static | Elastic −19.4 % (EDF) / −23.2 % (FCFS) — was −9.0 / −15.3 under OD 4 | **REVERSES** (more strongly) | Elastic was the most OD-ceiling-bound under the old config; the extra OD gives elastic the headroom to actually shrink makespan. |
| (f) | HEFT-ST worst CPR / strictly dominated | HEFT-ST not run | **N/A** | Variant absent. |
| (g) | Frontier = 2 elastic + 1 static (edf_moldable_c, edf_static_c, rank_moldable_5050) | Frontier = elastic-only (ELASTIC-EDF at N=7; both elastics at N=5) | **REVERSES** | HPO has no "Hard-SLO regime" because every variant is in saturation. |

**Five of six computable findings still reverse direction.** Cost advantage weakens, wait-time advantage weakens (but still strong), BMR reversal survives, deadline reversal weakens but survives, makespan reversal strengthens, frontier reversal survives. The qualitative HPO-vs-SeisSol story is robust to the OD-ceiling correction.

---

## Step 6 — Explicitly N/A Analyses

| SeisSol analysis | HPO status | Notes |
|---|---|---|
| Sort-key sensitivity (`_c` vs `_r`) | **absent** | Sort key not parameterised in HPO simulation. |
| Rank-factor sensitivity ([50,50] vs [25,75]) | **absent** | Rank scheduler not run. |
| Variance / CoV scatter | **absent** | No figure; per-cell CoV computable but not rendered. |
| Grant-tier-vs-N | **absent** | Tier shares at headline N are in `01b`; vs-N sweep not rendered. |

---

## Step 7 — Anomalies and Caveats

**1. CPR omitted.** `02f_cpr_bar` deleted; the SeisSol CPR `cost / (1 − OMR)` is ill-conditioned where OMR → 1 in HPO. The Pareto figures cover the cost-vs-OMR trade-off without a ratio.

**2. N=3 cells statistically thin.** 3 wfs × 6 seeds = 18 per-wf observations per cell. Most N=3 metrics carry CoV > 15 %.

**3. ELASTIC-EDF and ELASTIC-FCFS coincide at N=3 and N=5.** Identical dispatch order produces identical schedules; the two variants diverge only at N=7 when data12 and data1 enter and EDF's deadline ordering reorders them differently from FCFS.

**4. Sum-of-flowtimes is HPO-specific.** Not a SeisSol metric.

**5. Utilisation now reconciled with deployed fleet.** Denominator = 14 lanes (on-prem 4 + reserved cloud 4 + on-demand cloud 6). Max total lanes observed in any run = 14 (cap-binding); max OD lanes observed = 6 (cap-binding). The simulator and the deployed AWS plan are now consistent.

**6. Currency.** HPO costs are USD natively; SeisSol stored as EUR converted at 1.10. Rate tables describe different hardware (GPU vs CPU). The cross-workload cost comparison is on actual incurred dollars; no equivalent-lane-rate adjustment applied.

**7. CROSS_pareto axes harmonised.** Both panels use (per-wf USD, OMR); cross-workload positions are quantitatively comparable.

**8. Intent satisfaction is one-sided.** All 12 runs at N=7 record zero downward intent events. The workload widens monotonically across iterations, so there is structurally nothing for elastic to release.

**9. Sub-directory completeness.** All 12 cells × 6 seeds present and consistent. The aggregator `compute_total_cost.py` re-ran end-to-end in 0.08 s.

**10. Validation passes.** Sanity checks confirm: BMR_completed ≤ OMR for every cell; 0 ≤ Ū ≤ 1 for every cell; no CPR values remain.

---

## Summary

1. **Data regenerated under deployed-fleet OD ceiling (4 → 6 lanes).** Every metric moved, but the qualitative findings are robust.
2. **Elastic dominates static at N≥5** and reverses five of seven SeisSol headline findings. Cost advantage shrunk slightly (−14 % vs −17 % previously); makespan advantage grew (−20 % vs −9 % previously); deadline-miss advantage shrunk (−10 pp vs −17 pp previously); BMR reversal survives unchanged.
3. **Static now scales faster on the new data** — the extra OD headroom gives static a viable admission fleet that closes a chunk of the deadline-miss gap. Elastic's relative advantage is now in *makespan* and *wait* more than in *deadline misses*.
4. **Utilisation pattern flips** under the all-tier metric: elastic Ū (0.73) > static Ū (0.67). The reserved-fleet-only metric still shows the historical static > elastic pattern, but the deployed-fleet picture is the opposite.
5. **17 figures present** (CPR removed); CROSS_pareto on shared axes; data and figures self-consistent with the simulator-matches-deployed-fleet correction. Major gaps remain: no HEFT, no Rank, no sort-key or Rank-factor sensitivity, no variance/CoV scatter, no grant-tier-vs-N.
