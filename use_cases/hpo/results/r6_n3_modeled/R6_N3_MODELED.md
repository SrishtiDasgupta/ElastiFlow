# R6 N=3 FCFS Static — Modeled from R5 trajectory
*Modeled 2026-05-08 from R5 actual data; no live run.*

## Method

Same dispatch + submit times + yamls as R5. Static algorithm: no scale-ups, no scale-downs. **Critical change**: data5's allocation is different from R5 moldable.

Per-iter runtime: `ceil(chains / lanes_static) × tinyda × τ_wf`, using:
- **τ values**: empirical from R5 measurements where available (data8 vgg slurm, data9 convnext g4); scheduler model for data5 g5 (no measurement).
- **Trajectory (chains, tinyda)**: from R5 actual HPO pipeline output (deterministic for these wfs).

Reproducible script: `sim_r6_static.py`.

## ⚠️ Critical insight — different allocation than R5 moldable

**R5 moldable (actual)**: data5 → slurm partial 2 lanes
- WHY: data8 had scaled DOWN to 2 lanes (moldable behavior), opening 2 slurm slots. Moldable's on-prem-first override (line 209 in `edf_optimized_HPO.py`) takes ANY available slurm slots regardless of optimal_type → data5 hijacked to slurm.

**R6 static (modeled)**: data5 → cloud-g5 lanes=3
- WHY: data8 stays at 4 lanes throughout (static doesn't scale down). Slurm full when data5 submits. Static's on-prem-first check (line 110 in `fcfs_scheduler_HPO.py`) requires `optimal_type` to match on-prem (g4). Score picks g5 for data5 (cheaper for chains=3 wide_resnet) → on-prem (g4) doesn't match → cloud path → cloud-g5 lanes=3.

**This is a genuine algorithmic difference**: moldable has aggressive on-prem-first that hijacks new wfs to opened slurm slots; static checks type compatibility first.

## Results

### Per-workflow

| wf | container | static lanes | iters | makespan | deadline | result |
|---|---|---:|:-:|---:|---:|---|
| data8 | slurm | 4 | 3 | **1,322 s** | 6,161 s | **HIT (79% margin)** |
| data9 | cloud-g4 | 3 | 5 | **9,075 s** | 8,473 s | **MISS by 602 s (7%)** |
| **data5** | **cloud-g5** | **3** | 5 | **3,585 s** | 7,290 s | **HIT (51% margin)** ✅ |

### Aggregate

| Metric | R6 Static |
|---|---:|
| Deadline misses | **1/3** (data9) |
| Campaign wall | 9,346 s (2.60 h) |
| Sum-of-flowtimes | 13,982 s |
| OD-g4 cost (data9) | $1.33 |
| OD-g5 cost (data5) | $1.00 |
| **Total OD cost** | **$2.33** |

## R5 (FCFS moldable) vs R6 (FCFS static) — corrected comparison

For fair comparison, normalize R5 to "data9 ran to completion" (current R5 was killed at iter 3 start). Estimated R5 data9 completion makespan ≈ 9,089 s (using same τ values).

| Metric | R5 moldable | R6 static | Winner |
|---|---:|---:|---|
| **data5 makespan** | **4,594 s** | **3,585 s** | **static** (−22 %) |
| data8 makespan | 3,185 s | 1,322 s | static (−59 %) |
| data9 makespan | ~9,089 s | 9,075 s | tied |
| Deadline misses | 1/3 | 1/3 | tied |
| Sum-of-flowtimes | ~16,868 s | 13,982 s | static (−17 %) |
| OD-g4 cost (data9) | $1.33 (if to completion) | $1.33 | tied |
| OD-g5 cost (data5) | $0 (data5 on slurm) | $1.00 | **moldable** (saves $1.00) |
| **Total OD cost** | **$1.33** | **$2.33** | **moldable** (−43 %) |

**Real trade-off**: static is faster per-wf (better data5 placement) but spawns an extra OD-g5; moldable saves cash by routing data5 to slurm but pays in wall-time.

## What this reveals about the moldable mechanism

The R5 moldable behavior was a **side-effect chain**:

1. **data8 scale-down** at iter-0 boundary (4 → 2 lanes) — moldable's "we're ahead of schedule" optimization.
2. **2 slurm slots open** as a result.
3. **data5 submits next**, on-prem-first override hijacks data5 to slurm partial.
4. data5 stuck at lanes=2 until data8 finishes; gets 1 scale-up to lanes=4 only at iter-4 boundary.

vs static:
1. data8 stays at 4 (no scale-down).
2. Slurm full when data5 submits.
3. data5 gets clean cloud-g5 lanes=3 allocation; runs at full speed throughout.

**The moldable scale-down + on-prem-first interaction is COSTING data5 wall-time** but **saving on-demand cash**. This is a genuine performance vs cost trade-off — not a clear win for either side.

## The 4-corner table (final)

| | EDF static (R4 modeled) | EDF moldable (R3 actual) | FCFS static (R6 modeled) | FCFS moldable (R5 actual) |
|---|---:|---:|---:|---:|
| data8 makespan | 2,165 s | 2,751 s | **1,322 s** | 3,185 s |
| data9 makespan | 17,198 s | ~7,209 (killed) | 9,075 s | ~4,724 (killed) |
| **data5 makespan** | 10,470 s* | 4,244 s | **3,585 s** | 4,594 s |
| **Deadline misses** | 2/3* | 1/3 | **1/3** | **1/3** |
| Sum-of-flowtimes (completed) | ~14,886 s | 6,995 s | 13,982 s | 7,779 s |
| OD cost (run-to-completion) | $3.51 | $1.33 | $2.33 | $1.33 |

*R4 modeling used R3's actual (bug-induced) allocation pattern. With CORRECT static allocation (data5 → cloud-g5 lanes=3 as in R6), R4 actual would be much closer to R6's numbers — data5 makespan ~3,585 s, misses 1/3 (only data9). **My original R4 modeling was overly pessimistic.**

## Honest thesis story (revised)

The 4-corner table shows two distinct findings:

### Finding 1: Moldable mechanism prevents data9-style misses? **NO, not really.**
- All 4 cells have data9 missing (its tinyda growth pattern at lanes=3 just doesn't fit deadline).
- The "1/3 vs 2/3" advantage I claimed earlier was based on R3's bug-induced bad data5 allocation. Under PROPER static, data5 gets a clean cloud-g5 allocation and HITs deadline.

### Finding 2: Moldable saves on-demand cost.
- Moldable's scale-down opens slurm capacity → new wfs go to slurm instead of spawning OD.
- $1.00 saved per batch (vs static spawning extra OD-g5).
- 43% reduction in OD spend.

### Finding 3: Static is faster per-wf when allocations align well.
- Static avoids the slurm-trap by routing data5 to cloud-g5.
- 22% faster on data5 makespan.

### The real moldable advantage
**Moldable trades wall-time for cash**: it saves on-demand cost by reusing slurm capacity that scale-downs free up, at the cost of slower per-wf makespan when wfs land on slurm partial allocations.

This is a more nuanced and honest result than the original "moldable saves deadlines" claim.

## Caveats

1. **R5 data5 on slurm + cloud-g5 in static** is a comparison of DIFFERENT physical allocations. The "same workload" test isn't perfectly clean.
2. **τ for data5 on cloud-g5** is from scheduler model (not measured). Might be different in real runs (could be faster due to g5 hardware).
3. **R4 modeling (EDF static) was wrong** — it assumed R3's bug-induced allocation. Should redo with cloud-g5 allocation.

## Files

| File | Content |
|---|---|
| `R6_N3_MODELED.md` | this writeup |
| `sim_r6_static.py` | reproducible simulation |
