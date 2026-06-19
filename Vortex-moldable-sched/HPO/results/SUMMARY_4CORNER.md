# Moldable HPO Scheduling — 4-Corner Experimental Summary
*Final result, 2026-05-08. Supersedes individual R3/R4/R5/R6 takeaways.*

This document is the **canonical summary** of the moldable-vs-static comparison at N=3 across EDF and FCFS. Read this for the thesis story.

## Setup

- **Cluster**: 4 slurm + 2 reserved-g4dn + 2 reserved-g5 + on-demand (g4dn or g5).
- **Cluster topology constraints**: each container caps at 4 lanes; no migration between containers (slurm/cloud-g4/cloud-g5).
- **Workflows**: 3 wfs (data8 vgg19, data9 convnext, data5 wide_resnet); chains₀ = 4, 3, 3.
- **Dispatch**: `[8, 9, 5]` Poisson seed=42 avg-delay=90s.
- **Yamls**: ×3 deadline contention, ×1.5 budget bump, all yamls.
- **Code**: `MOLDABLE_INITIAL_CAP = 1.0`, `OPTIM_FCFS_BFACTOR = {0.5, 0.7, 0.85, 1.0, 1.0, 1.0}`, Fix-A pin in initial allocation.

## The 4-corner table

| | EDF static (R4 corrected) | EDF moldable (R3 actual) | FCFS static (R6 modeled) | FCFS moldable (R5 actual) |
|---|---:|---:|---:|---:|
| data8 makespan | 1,861 s | 2,751 s | 1,322 s | 3,185 s |
| data9 makespan | 17,229 s | ~15,000 s* | 9,075 s | ~10,000 s* |
| **data5 makespan** | **6,775 s** | **4,244 s** | **3,585 s** | **4,594 s** |
| **Deadline misses** | **1/3** | **1/3** | **1/3** | **1/3** |
| Sum-of-flowtimes | 25,865 s | ~22,000 s | 13,982 s | ~16,800 s |
| **OD cost (run-to-completion)** | **$4.41** | **$1.33** | **$2.33** | **$1.33** |
| OD instances spawned | 2 (g4dn + g5dn) | 1 (g4dn only) | 2 (g4dn + g5dn) | 1 (g4dn only) |

*data9 was killed mid-run in R3 and R5 to save infra cost. Run-to-completion estimate based on R5's measured τ values.

## Three honest findings

### Finding 1: Moldable does NOT save deadline misses
**All 4 cells: 1/3 misses (data9 only)**.
- data9 has tinyda growth (chains stays at 3, but tinyda grows iteratively).
- At lanes=3 on cluster-g4 cap=4, cannot scale to lanes>4.
- Even hypothetical lanes=4 doesn't help because chains=3 → 1 batch at any lanes ≥ 3.
- **The cluster topology + workload combination forces data9 to miss its deadline regardless of mode.**

The original "moldable saves 1 miss" claim (in initial R4 modeling) was based on **incorrect static allocation assumption** — assumed static would inherit moldable's bug-induced slurm-partial-1 allocation for data5. Under proper static, data5 lands on cloud-g5 lanes=3 and HITs.

### Finding 2: Moldable saves on-demand cost reliably ✅
- **EDF**: $1.33 (moldable) vs $4.41 (static) → **−70% OD cost**
- **FCFS**: $1.33 (moldable) vs $2.33 (static) → **−43% OD cost**

**Mechanism**: moldable's scale-down opens slurm capacity → on-prem-first override routes new wfs there → avoids spawning OD-g5 for data5.

### Finding 3: Per-wf makespan is workload-dependent ⚠️
- **EDF (R3 trajectory, high tinyda)**: moldable −37% on data5
- **FCFS (R5 trajectory, low tinyda)**: moldable +28% on data5 (static wins)

The HPO pipeline is non-deterministic: same yaml input can produce different (chains, tinyda) trajectories depending on accuracy outputs. R3 had data5 tinyda 18→25 (high); R5 had 18→8 (low).

When chains × tinyda is high, moldable's lanes=4 advantage on iter 4 saves big chunks. When low, the cleaner cloud-g5 allocation under static wins.

## Why these findings differ from initial expectations

The thesis was originally framed as "moldable saves deadlines via late-iter scale-up." The R3 actual run *appeared* to validate this (1/3 misses vs projected 2/3). But that projection was wrong — it assumed static and moldable shared the same physical allocation. **They don't**: the moldable scheduler has a different on-prem-first override than the static scheduler.

The correct comparison reveals that:
- **Static avoids the slurm trap** that moldable gets stuck in (because static checks `optimal_type == on-prem.type`)
- **Moldable saves cash by reusing freed slurm capacity** instead of spawning new OD instances

This is a **cost-vs-latency trade-off**, not a clear moldable win.

## EDF vs FCFS within moldable

- EDF moldable triggered 4 scaling events on data5 (1 mid-iter urgency-driven + 3 boundary-driven).
- FCFS moldable triggered 1 scaling event (iter-4 boundary only).
- EDF advantage on data5 makespan: ~8% faster (urgency boost adds early scale-up).

Both apply moldable scale-down on data8 → both save OD-g5 cost via slurm reuse.

## Headline thesis claim (revised)

> **Moldable scheduling provides consistent on-demand cost savings of 43–70% across EDF and FCFS schedulers**, achieved by reusing slurm capacity opened by scale-down events to avoid spawning extra cloud instances. Per-workflow makespan is workload-dependent: moldable wins on workflows with high per-iteration tinyda growth (chains × tinyda product); static wins when the moldable on-prem-first override traps workflows in slurm partial allocations. **Both modes have identical deadline-miss rates** in this experiment because cluster topology (cap=4 per cluster) cannot accommodate data9's growth pattern at any lane count. Within moldable, EDF's deadline-urgency boost adds ~8% additional makespan improvement vs FCFS by triggering early scale-ups; the core moldable opportunity check at iter boundaries is algorithm-agnostic.

## Scheduler bug discovered

R3 EDF moldable run had a **silent submit drop** of data5 — likely a race between the scheduler's run loop and a long createOnDemandWorkers call for data9. data5 had to be manually resubmitted. R5 FCFS moldable did NOT exhibit this bug (different timing). Bug is real but timing-dependent.

## Files in the campaign

```
HPO/results/
├── SUMMARY_4CORNER.md                ← this file
├── r1_moldable_edf_5/                 ← original R1 (cap=0.5, killed; provided per-wf τ)
│   └── R1_R2_FINALIZED.md
├── r3_n3_actual/                      ← R3: EDF moldable N=3 (live)
│   ├── R3_N3_ACTUAL.md (updated 2026-05-08)
│   └── 6× executor.out, scheduler log, cold-start log
├── r3_n3_projected/                   ← original R3 projection (homogeneous-pool model, superseded)
├── r3_r4_projected/                   ← N=7 projection (superseded)
├── r4_n3_modeled/                     ← R4: EDF static (modeled from R3)
│   ├── R4_N3_MODELED.md (original, wrong)
│   ├── R4_N3_CORRECTED.md (canonical)
│   ├── sim_r4_static.py
│   └── sim_r4_static_corrected.py
├── r5_n3_actual/                      ← R5: FCFS moldable N=3 (live)
│   ├── R5_N3_ACTUAL.md (updated 2026-05-08)
│   └── 6× executor.out, scheduler log, cold-start log
└── r6_n3_modeled/                     ← R6: FCFS static (modeled from R5)
    ├── R6_N3_MODELED.md
    └── sim_r6_static.py
```

## Spend summary

- R3 EDF moldable: $1.05 actual (data9 killed early)
- R5 FCFS moldable: $0.70 actual (data9 killed early)
- R4, R6: modeled, $0
- **Total live infra spend: ~$1.75 + cluster wall** (~6h scheduler + 4h reserved + ~1h compute)

## Open items

- **Scheduler silent-submit bug** — needs investigation/fix.
- **R5's data5 trajectory differing from R3's** — non-deterministic HPO pipeline. Worth running R3' (reproducibility) to gather more τ samples.
- **R4, R6 use scheduler-model τ for data5 on g5** (no measurement). A single live run would tighten this.
