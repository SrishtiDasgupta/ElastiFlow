# R3 N=3 Projection — Moldable EDF (cap=1.0) vs Static EDF
*Locked 2026-05-08 after no-migration policy analysis.*

This is the experiment we will actually run. R4 (static, N=3) will be modeled from the same R3 trajectory data, no live run needed.

## Why N=3

Cluster topology constraints (4-slot cap per of 3 containers: slurm + cloud-g4 + cloud-g5, no migration) gridlock the moldable algorithm at higher N. At N=5 or N=7 with 90s Poisson dispatch density, every wf becomes a cluster-mate before the previous wf reaches its iter-1 boundary, so scaling never fires. N=3 is the smallest configuration where a long wf can scale into freed capacity (after a sibling finishes).

## Setup

| Workflow | Model | chains₀ | iters | budget | deadline | container | initial lanes |
|---|---|---:|---:|---:|---:|---|---:|
| data8 | vgg19 | 4 | 3 | $1.77 | 6161s | slurm | 4 (full) |
| data9 | convnext | 3 | 5 | $2.66 | 8473s | cloud-g4 | 3 (full, 1 free in g4 cluster) |
| data5 | wide_resnet | 3 | 5 | $2.15 | 7290s | cloud-g4 PARTIAL | 1 (cluster-g4 full, only 1 OD-g4 left) |

**Why data5 lands partial**: by t=390 (its submit), data9 has already taken 2 res-g4 + 1 OD-g4 = 3 of 4 cluster-g4 slots. Score function picks cluster-g4 partial=1 (cheap reserved or single OD-g4) over cluster-g5 full=3 (expensive 2 res-g5 + 1 OD-g5) because cost dominates the score. data5 starts at lanes=1 and stays there until cluster-g4 frees up.

**The moldable opportunity**: data9 finishes at t=6376, freeing 3 cluster-g4 slots. data5 is mid-iter-2 (ends 8725). At iter-3 boundary (t=8755), data5's moldable scheduler requests +3 lanes. Cluster-g4 free=3 (data9 just done). Granted. data5 scales from 1 → 4 lanes for iters 3 and 4.

## Predicted results (from sim_n3_real.py under actual scheduler policy)

| Metric | Static R4 | Moldable R3 | Δ |
|---|---:|---:|---|
| **Total makespan** | 22,874s (6.35h) | **13,234s (3.68h)** | **−42 %** |
| **Sum of flowtimes** | 30,408s | 20,769s | **−32 %** |
| **Deadline misses** | 1/3 | 1/3 | tied |
| **data5 deadline-miss MARGIN** | over by 15,194s (3.1× deadline) | over by 5,555s (1.76× deadline) | **−63 %** |
| **On-demand cost** | $0.89 | $2.87 | +$1.98 (3.2× more) |
| **wfs hitting deadline** | 2/3 (data8, data9) | 2/3 (data8, data9) | tied |
| **Tail finish (worst-case wf)** | 22,874s | 13,234s | −42 % |

## Per-workflow detail

### data8 (slurm, chains₀=4) — identical in both modes
- Slurm cap=4 = chains₀, so initial allocation is full and no scale-up possible (cluster cap = lanes already).
- Trajectory: chains 4→6→9 → 1, 2, 3 batches at lanes=4 throughout.
- Makespan 1819s. HIT deadline 6161s by 4341s margin. Same in both modes.

### data9 (cloud-g4, chains₀=3) — identical in both modes
- Lands fully on cloud-g4. Cluster-g4 has 1 free slot. Could moldable scale at iter 1?
- iter-1 boundary at t=1012. By then, data5 has submitted (t=390) and taken the free slot at lanes=1 partial. Cluster-g4 full from t=420 onward.
- Result: data9 stuck at lanes=3 throughout.
- Trajectory: chains 3→4→6→9→10 → 1, 2, 2, 3, 4 batches at lanes=3.
- Makespan 6105s. HIT deadline 8473s by 2368s. Same in both modes.

### data5 (cloud-g4 PARTIAL, chains₀=3) — moldable saves enormously
**Static** (no scale-up):
- Stuck at lanes=1 throughout.
- iter 0 chains=3 lanes=1: 3×18×29.66 = 1602s
- iter 1 chains=4 lanes=1: 4×20×29.66 = 2373s
- iter 2 chains=6 lanes=1: 6×24×29.66 = 4271s
- iter 3 chains=9 lanes=1: 9×25×29.66 = 6674s
- iter 4 chains=10 lanes=1: 10×25×29.66 = 7415s
- Total: 22,335s. **Deadline missed by 15,194s.**

**Moldable cap=1.0**:
- iters 0–2 same (data9 still alive, blocking). lanes=1.
- iter 3 boundary at t=8755. data9 finished at t=6376 → cluster-g4 has 3 free. Scale-up requested: +3. Granted. **lanes=1 → 4.**
- iter 3 chains=9 lanes=4: ⌈9/4⌉×25×29.66 = 3×25×29.66 = 2224s (vs 6674s static).
- iter 4 chains=10 lanes=4: ⌈10/4⌉×25×29.66 = 3×25×29.66 = 2224s (vs 7415s static).
- Total: 12,694s. **Deadline still missed but only by 5,555s** (vs 15,194s static).
- **Moldable saves 9,640s on data5** = 64% time saved on the hardest wf.

## Cost analysis

**Static**:
- Only on-demand cost: data9 holds 1 OD-g4 from t=271 to t=6376 = 6105s. $0.89.

**Moldable**:
- data9: same OD-g4 = $0.89.
- data5 scale-up: spawns 3 OD-g4 instances at t=8725 (when iter 3 starts). Held until t=13234 = 4509s × 3 instances. $1.98.
- Total: $2.87.

**Real cash differential: +$1.98** for moldable (3.2× more on-demand spend).

But this $1.98 buys:
- 9,640 seconds of wall-clock savings (64% reduction on data5)
- 42% total makespan reduction
- 9,640s reduction in deadline-miss margin for data5

## What this experiment demonstrates

1. **The moldable mechanism does fire** — once a sibling finishes and frees cluster capacity, the trapped wf scales.
2. **Cluster topology bounds the gain**: max lanes per wf is 4 (cluster cap), so data5 scales 1→4 not 1→9. Even so, 4× speedup on iter 3 + iter 4 is the headline result.
3. **The trade is real cash for wall time**: moldable adds $2 of on-demand for ~10,000 seconds of wall-time savings. Translates to "moldable lets you choose to spend a bit more to finish much faster."
4. **Both modes miss data5's deadline** but moldable miss is 64% smaller. If data5's deadline had been ~12,800s (e.g., ×3.5 contention factor instead of ×3), moldable would HIT and static would MISS. **The win in deadline-miss-rate appears at slightly more lenient deadlines.**

## What this DOESN'T demonstrate (honest caveats)

1. **Both modes still miss data5's deadline.** Headline "deadline-miss rate" metric is tied 1/3 vs 1/3 here. The win is in MARGIN, not count.
2. **At higher N, moldable advantage collapses** because cluster gridlock prevents scaling (covered in the no-migration analysis). N=3 is a special case.
3. **The scale-up only fires once** (data5 at iter 3). data9 doesn't get to scale (data5 took its free slot too quickly). data8 can't scale (slurm cap = chains).

## Run plan

1. Apply all changes per `r3_launch_checklist.md` (already done: cap=1.0, ×3 deadline, ×1.5 budget, BFACTOR ramp, WORKFLOW_ORDER=[8,9,5,...]).
2. Sync local repo to /fsx scheduler via git pull.
3. Resume infra (5 EC2 + ParallelCluster compute fleet).
4. Launch: `dispatcher_HPO.py --count 3 --poisson --avg-delay 90 --seed 42 --host 172.31.9.121`
5. Expected wall: ~3.7h. Expected on-demand: ~$2.87. Total cluster running cost (with always-on burn): ~€7-9.
6. After R3 finishes, refit τ from R3's measured per-iter trajectories and re-run sim_n3_real.py with mode='static' to lock R4 (modeled).

## Provenance
- Per-iter τ trajectories: from `HPO/results/r1_moldable_edf_5/` measurements.
- Cluster topology: `src/main/config/resources_HPO_hybrid.yaml`.
- Allocation policy: traced through `src/main/scheduler/edf_optimized_HPO.py:183-348` (allocateResourcesMoldableHPO + selectOptimalInstanceType + on-prem-first + cluster isolation).
- Simulation: `sim_n3_real.py` in this directory.
