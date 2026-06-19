# R4 N=3 EDF Static — CORRECTED Model
*Modeled 2026-05-08, supersedes original `R4_N3_MODELED.md`.*

## Why this correction

Original R4 modeling assumed data5 had the same allocation as in R3 (slurm partial lanes=1). That was **wrong**: it copied a moldable-induced bug pattern into the static model.

Under proper static allocation (no scale-downs of data8), slurm stays full, so data5 hits the cloud path. Score function picks **cloud-g5 lanes=3** for chains=3 wide_resnet (cheaper score than g4 partial=1).

## Corrected R4 results

| wf | container | lanes | makespan | deadline | result |
|---|---|---:|---:|---:|---|
| data8 | slurm | 4 | 1,861 s | 6,161 s | HIT (70%) |
| data9 | cloud-g4 | 3 | 17,229 s | 8,473 s | MISS (by 103%) |
| **data5** | **cloud-g5** | **3** | **6,775 s** | 7,290 s | **HIT (7% margin)** |

| Aggregate | Value |
|---|---:|
| Deadline misses | **1/3** (data9) |
| Campaign wall | 17,500 s |
| Sum-of-flowtimes | 25,865 s |
| OD-g4 cost (data9) | $2.52 |
| OD-g5 cost (data5) | $1.89 |
| **Total OD cost** | **$4.41** |

## R3 (moldable) vs R4 (static, corrected)

| Metric | R3 moldable | R4 static | Δ |
|---|---:|---:|---|
| **data5 makespan** | **4,244 s** | **6,775 s** | **moldable −37 %** |
| data8 makespan | 2,751 s | 1,861 s | moldable +48 % (scale-down overhead) |
| data9 makespan (run-to-completion estimate) | ~15,000 s | 17,229 s | moldable −13 % |
| Deadline misses | 1/3 | 1/3 | tied |
| OD cost (run-to-completion) | $1.33 | $4.41 | **moldable −70 %** |

## What changed from original R4

| Metric | Original R4 (wrong) | Corrected R4 |
|---|---:|---:|
| data5 allocation | slurm partial 1 | cloud-g5 lanes=3 |
| data5 makespan | 10,470 s | 6,775 s |
| Deadline misses | 2/3 | 1/3 |
| OD cost | $2.51 | $4.41 |

The **2/3 misses → 1/3 misses** correction means **moldable does NOT save deadline misses** — both modes have 1/3 (data9 alone). The moldable advantage shifts to:
1. **OD cost savings** (consistent ~$3 saved per batch).
2. **Per-wf makespan** on high-tinyda workflows (workload-dependent).

## Caveats

- **τ for cloud-g5** is from scheduler model (22.30 s/epoch) — no measurement. Could be different in real R4 run.
- **data5 trajectory uses R3's HPO pipeline output** (chains 3→4→6→9→10, tinyda 18→20→24→25→25). HPO is non-deterministic so a real R4 might produce different tinyda values.
- **data9 iters 3–4 tinyda** extrapolated (53, 70). Actual R4 might differ.

## Files

- `R4_N3_CORRECTED.md` — this writeup (supersedes `R4_N3_MODELED.md`)
- `sim_r4_static_corrected.py` — reproducible simulation
- `R4_N3_MODELED.md` — original (wrong) writeup, kept for audit trail
- `sim_r4_static.py` — original (wrong) sim, kept for audit
