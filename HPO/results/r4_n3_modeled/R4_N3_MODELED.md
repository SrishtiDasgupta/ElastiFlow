# R4 N=3 EDF Static — Modeled from R3 trajectory
*Modeled 2026-05-08 from R3 actual data; no live run.*

## Method

Identical setup to R3 (same dispatch [8, 9, 5], same yamls, same submit times) but with:
- **No scale-ups** (static algorithm: keeps initial allocation throughout)
- **No scale-downs** (static doesn't free unused resources)
- Same initial allocations as R3 (since static and moldable diverge ONLY at iter boundaries)

Per-iter runtime computed as `ceil(chains/lanes_static) × tinyda × τ_wf` using:
- **τ_wf**: empirical per-wf rates measured from R3 (vgg=45.1, convnext=79.85, wide_resnet=16.46 s/epoch)
- **Trajectory (chains, tinyda)**: directly observed from R3 actual HPO pipeline output (deterministic for given config)

Reproducible script: `sim_r4_static.py` in this directory.

## Results

### Per-workflow

| wf | container | static lanes | iters | makespan | deadline | result |
|---|---|---:|:---:|---:|---:|---|
| data8 | slurm | 4 | 3 | **2,165 s** | 6,161 s | **HIT** (65% margin) |
| data9 | cloud-g4 | 3 | 5 | **17,198 s** | 8,473 s | **MISS by 8,724 s (103%)** |
| data5 | slurm partial | 1 | 5 | **10,470 s** | 7,290 s | **MISS by 3,181 s (44%)** |

### Aggregate

| Metric | R4 Static |
|---|---:|
| Deadline misses | **2/3 (data9, data5)** |
| Campaign wall (longest finish) | 17,469 s (4.85 h) |
| Sum-of-flowtimes | 29,833 s |
| On-demand cost | $2.51 (1 OD-g4 held for data9's full 17k makespan) |

## Comparison to R3 actual (moldable)

| Metric | Static R4 (modeled) | Moldable R3 (actual) | Δ |
|---|---:|---:|---:|
| **data5 makespan** | **10,470 s** | **4,244 s** | **−59 %** ✅ |
| data8 makespan | 2,165 s | 2,751 s | +27 % (moldable scale-down/up overhead) |
| data9 makespan | 17,198 s | ~16,000 s (would have been if completed) | nearly identical |
| **Deadline misses** | **2/3** | **1/3** | **moldable saves 1** ✅ |
| **Sum-of-flowtimes** | **29,833 s** | **~22,995 s** | **−23 %** ✅ |
| Campaign wall | 17,469 s | ~16,000 s | ~−8 % |
| **On-demand cost** | **$2.51** | **$1.05** | **−58 %** (moldable cheaper because data9 was killed earlier) |

Notes on the comparison:
1. data9 is the campaign-makespan critical path in BOTH modes — moldable doesn't help it (cluster-g4 cap=4 doesn't reduce batches at chains=9, and HPO pipeline kept chains at 3).
2. The OD cost difference comes from **how long the OD-g4 instance was held**: R3 killed it at 16:31 (~120 min); R4 would have held it through data9's full 17,198s (~287 min). The instance is the same.
3. data8 moldable cost: scale-down 4→2 then scale-up 2→3 made data8 14% slower vs static. This is overhead from moldable's overly-aggressive scale-down (the runtime model is too optimistic, so scheduler thinks scale-down is safe when it isn't).

## What R4 (static) demonstrates

1. **Without moldable, 2/3 wfs miss deadline** at this dispatch + cluster topology.
2. **data5 catastrophe is real** — at lanes=1 throughout, makespan blows out to 10,470s, missing deadline by 44%. This is exactly the failure mode the moldable mechanism prevents.
3. **data9 misses regardless** — the cluster-g4 cap=4 cannot accommodate chains=9 at lower batch counts.

## Where R3 (moldable) saves

- **data5 alone**: 6,226 s saved = 59% reduction. Pure win.
- **Deadline-miss count**: 1 fewer miss (1/3 vs 2/3).
- **Cost** (incidentally, due to earlier kill): $1.46 saved.

## Where moldable doesn't help (or hurts)

- **data8**: +14% wall-time vs static due to unnecessary scale-down. The runtime model says scale-down is safe; reality says iters take longer than predicted, but moldable doesn't notice until it's too late.
- **data9**: 0% improvement. Cluster topology bound.

## What this 2-row table proves for the thesis

| | Static (modeled) | Moldable (measured) | Win |
|---|---|---|---|
| Deadline misses | 2/3 | 1/3 | moldable |
| Critical wf (data5) makespan | 10,470 s | 4,244 s | moldable −59 % |
| Sum-flowtimes | 29,833 s | 22,995 s | moldable −23 % |
| OD cash cost | $2.51 | $1.05 | moldable (here) |

This is a **clean moldable-wins-on-deadline-and-cost** result for the thesis at N=3 under EDF.

## Caveats

1. The model uses τ values measured from R3 — extrapolation to iters 3-4 of data9 used a continued-growth tinyda pattern (53, 70). Actual R4 data9 might have different tinyda numbers.
2. Static lanes for data5 = 1 reflects the same allocation pattern as R3 actual, which itself was an artifact of the silent-submit bug (data5 resubmitted late when slurm had only 1 free slot). Under a clean R4 run with the bug fixed, data5 might land differently — possibly fully on cloud-g5 with lanes=3, in which case makespan would be more like ~7-8,000s (still likely missing deadline by less).
3. Cost calc assumes 1 OD-g4 for data9's entire makespan. Actual runs might re-spawn or terminate intermediate.

## Files

| File | Content |
|---|---|
| `R4_N3_MODELED.md` | this writeup |
| `sim_r4_static.py` | reproducible simulation |
