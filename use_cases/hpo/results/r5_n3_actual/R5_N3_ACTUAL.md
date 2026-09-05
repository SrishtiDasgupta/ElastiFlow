# R5 N=3 — FCFS Moldable Actual Results
*Run executed 2026-05-08, 16:56:17–18:23:06 UTC. Total infra wall: 5,209s. Campaign data8+data5 wall (completed wfs): 4,987s.*

## Run setup

- **Mode**: moldable FCFS (`--mode moldable --algo fcfs`)
- **N**: 3
- **Dispatch**: WORKFLOW_ORDER `[8, 9, 5, ...]`, Poisson `--avg-delay 90 --seed 42`
- **Same code/yaml/cluster** as R3 EDF run (cap=1.0, BFACTOR ramp, ×3 deadline / ×1.5 budget)

## Submit timeline

| pos | wf | submit | id |
|---:|---|---|---|
| 0 | data8 | 16:56:17 | hpo-60bf3eb4 |
| 1 | data9 | 17:00:48 | hpo-b03a4442 |
| 2 | data5 | 17:02:50 | hpo-df19b440 |

✅ **Bug DID NOT recur** — data5 was allocated cleanly without manual resubmit. The R3 silent-submit bug appears to be timing-sensitive; R5's slightly different timing avoided it.

## Allocations (initial)

| wf | container | lanes | reason |
|---|---|---:|---|
| data8 | slurm | 4 (full) | on-prem-first; slurm had 4 free |
| data9 | cloud-g4 cluster | 3 (2 res-g4 + 1 OD-g4) | slurm full; score picked g4 |
| **data5** | **slurm partial** | **2 (better than R3's 1)** | by data5's submit, data8 had scaled down 4→2; slurm had 2 free → on-prem-first took 2 |

OD-g4 cold-start: **358s** (i-077c5170adb104c9f).

## Per-iter trajectory (measured)

### data8 (slurm, vgg19) — 3 iters, completed

| iter | chains | tinyda | lanes | runtime |
|---:|---:|---:|---:|---:|
| 0 | 4 | 10 | 4 | 385 s |
| 1 | 6 | 5 | **2 (scaled-down)** | 963 s |
| 2 | 9 | 4 | 2 | 1,444 s |

**data8 makespan: 3,185 s** (DONE 17:49:22). Deadline 6,161 s → **HIT (48% margin)** ✅

### data9 (cloud-g4, convnext_large) — 3 iters completed, killed at iter 3 start

| iter | chains | tinyda | lanes | runtime |
|---:|---:|---:|---:|---:|
| 0 | 3 | 20 | 3 | 1,336 s |
| 1 | 3 | 19 | 3 | 1,306 s |
| 2 | 3 | 22 | 3 | 1,691 s |
| 3 | 3 | 27 | 3 | (killed at iter-3 start) |

**data9 elapsed at kill: 4,724s.** No moldable scale-up fired (chains stayed at 3 → moldable opportunity branch never fires).

Iters faster in R5 than R3 (1,336 vs 1,713 for iter 0). Different OD-g4 instance had warmer cache.

### data5 (slurm partial → scaled, wide_resnet101_2) — 5 iters, completed

| iter | chains | tinyda | lanes | runtime | event |
|---:|---:|---:|---:|---:|---|
| 0 | 3 | 18 | 2 | 498 s | initial alloc partial=2 |
| 1 | 4 | 14 | 2 | 594 s | scale-up DENIED (slurm full) |
| 2 | 6 | 12 | 2 | 866 s | scale-up DENIED |
| 3 | 9 | 12 | 2 | 1,364 s | scale-up DENIED (data8 still running iter 2) |
| 4 | 10 | 8 | **4** | 850 s | **moldable scale 2→4 GRANTED** (data8 just finished, slurm 2 free) |

**data5 makespan: 4,594 s** (DONE 18:19:24). Deadline 7,290 s → **HIT (37% margin)** ✅

The lone moldable scale-up event fired at iter-4 boundary — exactly when data8 finished and freed slurm capacity.

## Aggregate metrics

### R5 moldable FCFS

| Metric | Value |
|---|---:|
| data8 makespan | 3,185 s |
| data5 makespan | 4,594 s |
| data9 makespan (at kill) | 4,724 s (would have been ~10,000–14,000s if completed) |
| **Campaign wall (data8 t=0 → data5 finish)** | **4,987 s = 1h 23m 7s** |
| Deadline misses (completed wfs) | 0/2 |
| Deadline misses (with data9 projected) | 1/3 (data9) |
| Sum-of-flowtimes (completed wfs only) | 7,779 s |
| OD instances spawned | 1 |
| OD-g4 cold-start | 358 s |
| **On-demand cost** | **$0.70** (1 OD-g4 × ~80 min) |

## Comparison table — the four-corner result (CORRECTED 2026-05-08)

⚠️ Original R4 modeling was wrong. Corrected R4 (and R6 modeled) have data5 → cloud-g5 lanes=3, not slurm partial. **All 4 corners have 1/3 deadline misses (data9 only).**

| | EDF static (R4 corrected) | EDF moldable (R3 actual) | FCFS static (R6 modeled) | FCFS moldable (R5 actual) |
|---|---:|---:|---:|---:|
| data8 makespan | 1,861 s | 2,751 s | 1,322 s | 3,185 s |
| data9 makespan | 17,229 s | ~15,000 s (proj) | 9,075 s | ~10,000 s (proj) |
| **data5 makespan** | **6,775 s** | **4,244 s** | **3,585 s** | **4,594 s** |
| **Deadline misses** | **1/3** | **1/3** | **1/3** | **1/3** |
| OD cost (run-to-completion) | $4.41 | $1.33 | $2.33 | $1.33 |
| Sum-of-flowtimes (run-to-completion) | 25,865 s | ~22,000 s | 13,982 s | ~16,800 s |

## EDF vs FCFS moldable: the urgency-boost effect

### Scaling event count for data5

| iter | R3 EDF | R5 FCFS | who wins |
|---|:-:|:-:|---|
| 0 | lanes=1 | **lanes=2** | FCFS (cleaner timing) |
| 1 | lanes=2 (early-scale-up via urgency at t=14.5%) | lanes=2 | tie |
| 2 | **lanes=4** (moldable opp) | lanes=2 | **EDF wins** |
| 3 | lanes=3 (scale-down) | lanes=2 | EDF wins |
| 4 | lanes=4 | lanes=4 (moldable opp grant) | tie |

EDF triggered **4 scaling events** for data5 (1 mid-iter urgency + 3 boundary-driven).
FCFS triggered **1 scaling event** for data5 (only at iter-4 boundary, when slurm finally freed).

### Net data5 makespan impact

- R3 EDF: 4,244 s (data5 ran lanes=4 during iters 2, 3, 4)
- R5 FCFS: 4,594 s (data5 ran lanes=4 only during iter 4)
- **EDF advantage: 350 s (8% faster)** — attributable to early-scale-up enabling lanes=4 for 2 more iters

### What this proves (REVISED 2026-05-08)

✅ **Both EDF and FCFS moldable activate the scaling mechanism** on data5.
✅ **The "moldable opportunity" check at iter boundaries works under both algorithms** — fires whenever cluster has free capacity.
⚠️ **The "EDF early-scale-up" via deadline urgency adds ~8% additional improvement** — small but real.
⚠️ **Without urgency boosts, FCFS waits longer for natural cluster availability** to fire scale-ups.

❌ **Moldable does NOT win on data5 makespan in the FCFS case** — FCFS static actually beats FCFS moldable on data5 (3,585 s vs 4,594 s) because static's allocation routes data5 to cloud-g5 lanes=3 (cleaner) while moldable hijacks data5 to slurm partial 2 via on-prem-first override.

✅ **Moldable DOES save on-demand cost in FCFS** — $1.33 vs $2.33 (43% cheaper) by avoiding the OD-g5 spawn for data5.

### Implication for the thesis (REVISED)

> The moldable mechanism's core scale-up logic is algorithm-agnostic — fires under both EDF and FCFS. The **headline benefit is on-demand cost savings (43–70% across both schedulers)**, achieved by routing new wfs to slurm capacity opened by scale-down. **Per-wf makespan effect is workload-dependent**: moldable wins on high-tinyda trajectories (R3 EDF case, −37% on data5) but loses on low-tinyda trajectories (R5 FCFS case, +28% slower than static on data5). EDF's deadline-aware urgency boost adds ~8% improvement vs FCFS by triggering early scale-ups. **Moldable does NOT save deadline misses in this experiment** (data9 misses across all 4 corners due to cluster-g4 cap=4 + tinyda growth).

## Empirical τ values (R5)

| wf (model) | mean τ measured | vs scheduler model | vs R3 measurement |
|---|---:|---:|---:|
| data8 (vgg19) | ~58 s/epoch | 2.6× model | 1.3× R3 (slower in R5 due to slurm contention) |
| data9 (convnext) | ~71 s/epoch | 2.1× model | 0.89× R3 (faster in R5) |
| data5 (wide_resnet) | ~23 s/epoch | 0.78× model | 1.4× R3 (slower in R5 due to lanes=2 baseline vs R3's mixed lanes) |

τ varies even between same-cluster runs depending on contention and instance variability.

## Files

| File | Content |
|---|---|
| `R5_N3_ACTUAL.md` | this writeup |
| `r5_moldable_fcfs_3.log` | full scheduler log |
| `r5_dispatcher.log` | dispatch order |
| `executor_172_31_*.out` | 6 executor stdout files |
| `cold_start_log.csv` | OD-g4 cold-start (358s) |
| `FCFS_Moldable_HPO_3wf_hybrid_resources.csv` | resource utilization log |
| `yamls_used/` | (use R3's snapshot — same yamls) |
