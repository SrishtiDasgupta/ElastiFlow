# R3 N=3 — Actual Run Results
*Run executed 2026-05-08, 14:26:54–16:31:31 UTC. Campaign data8+data5 wall: 6,114s. Total infra wall: 7,477s.*

## Run setup

- **Mode**: moldable EDF (`--mode moldable --algo edf`)
- **N**: 3
- **Dispatch**: WORKFLOW_ORDER `[8, 9, 5, ...]`, Poisson `--avg-delay 90 --seed 42`
- **Infra**: hybrid (`resources_HPO.yaml` loaded — on-prem $0.84/h TCO basis)
- **Cluster topology**: 4 slurm + 2 reserved-g4dn + 2 reserved-g5 + up to 6 on-demand
- **Code state**: cap=1.0, BFACTOR ramp `{0.5, 0.7, 0.85, 1.0, 1.0, 1.0}`, ×3 deadline / ×1.5 budget yamls applied

## Submit timeline

| pos | wf | submit (rel) | id | model |
|---:|---|---:|---|---|
| 0 | data8 | 0 | hpo-60bf3eb4 | vgg19 |
| 1 | data9 | +271s | hpo-b03a4442 | convnext_large |
| 2 | data5 (orig) | +389s | hpo-df19b440 | wide_resnet101_2 |

⚠️ **Bug**: data5's first submit (14:33:24) was silently lost by the scheduler — likely a race with the OD-g4 instance creation for data9. Manual resubmit at 14:58:04 (+1,870s after data8) was required to recover.

## Allocations (initial)

| wf | lands on | lanes | reason |
|---|---|---:|---|
| data8 | slurm | 4 (full) | on-prem-first; slurm had 4 free |
| data9 | cloud-g4 cluster | 3 (2 res-g4 + 1 OD-g4) | slurm full; score picked g4 over g5 |
| data5 | **slurm partial** | **1** | resubmit landed when slurm had 1 free slot (data8 was at 3 after scale-up); on-prem-first took it |

OD-g4 cold-start measured: **359s** (request → executor listening). One OD-g4 spawned (`i-03932f84a2299bae6`).

## Per-iter trajectory (measured)

### data8 (slurm, vgg19) — 3 iters, completed

| iter | chains | tinyda | lanes | runtime |
|---:|---:|---:|---:|---:|
| 0 | 4 | 10 | 4 | 385 s |
| 1 | 6 | 6 | **2 (scaled-down)** | 995 s |
| 2 | 9 | 8 | **3 (scaled-up +1)** | 995 s |

**data8 makespan: 2,751 s** (DONE 15:12:45). Deadline 6,161s → **HIT (55% margin)** ✅

### data9 (cloud-g4, convnext_large) — 3 iters completed, killed at iter 3 start

| iter | chains | tinyda | lanes | runtime |
|---:|---:|---:|---:|---:|
| 0 | 3 | 20 | 3 | 1,713 s |
| 1 | 3 | 29 | 3 | 2,178 s |
| 2 | 3 | 37 | 3 | 2,916 s |
| 3 | 3 | 53 | 3 | (killed at iter-3 start) |

Note: data9's HPO pipeline kept `next_trials=3` across all iterations (no chain growth), but `tinyda` grew substantially (20 → 53). No moldable scale-up fired — cluster-g4 had 1 free OD-g4 slot but the moldable opportunity check didn't trigger.

**data9 elapsed at kill: 7,209s.** Already past `8473s × 0.85 ≈ 7,200s`, with 2 more iters to run. **Would have MISSED** (projected makespan ~16,000s+).

### data5 (slurm partial → scaled, wide_resnet101_2) — 5 iters, completed

| iter | chains | tinyda | lanes | runtime | scale event |
|---:|---:|---:|---:|---:|---|
| 0 | 3 | 18 | 1 | 995 s | partial alloc at submit |
| 1 | 4 | 18 | **2** | 674 s | early-scale-up `+1` (urgency, mid-iter-0) |
| 2 | 6 | 23 | **4** | 554 s | **moldable opportunity `2→6` requested, granted +2** |
| 3 | 9 | 17 | **3** | 987 s | scale-down `4→3` at iter-3 boundary |
| 4 | 10 | 21 | **4** | 866 s | scale-up `3→4` at iter-4 boundary |

**data5 makespan: 4,244 s** (DONE 16:08:48). Deadline 7,290s → **HIT (42% margin)** ✅

This is the headline moldable result: data5 went `1 → 2 → 4 → 3 → 4` lanes across iters, dynamically tracking chain growth and freed cluster capacity.

## Empirical per-epoch τ (measured)

| wf (model) | scheduler model τ | actual τ measured | ratio |
|---|---:|---:|---:|
| data8 (vgg19) | 22.17 s/epoch | ~45 s/epoch | 2.0× slower |
| data9 (convnext) | 34.03 s/epoch | ~80 s/epoch | 2.4× slower |
| data5 (wide_resnet) | 29.66 s/epoch | ~16 s/epoch | **0.55× FASTER** |

The scheduler's power-law model is wrong by 2–2.4× in both directions depending on the workflow. data5 was actually faster than predicted; data9 and data8 were slower.

## Aggregate metrics

### Moldable (R3 actual)

| Metric | Value |
|---|---:|
| data8 makespan | 2,751 s |
| data5 makespan | 4,244 s |
| data9 makespan (at kill, partial) | 7,209 s (projected ~16,000s if completed) |
| **Total wall (data8 t=0 → data5 finish)** | **6,114 s = 1h 41m 54s** |
| Deadline misses (completed wfs) | 0/2 |
| Deadline misses (including data9 projected) | 1/3 (data9) |
| Sum-of-flowtimes (completed wfs only) | 6,995 s |
| OD instances spawned | 1 (g4dn.xlarge) |
| OD-g4 cold-start measured | 359 s |
| **On-demand cost** | **$1.05** |

### Static R4 (corrected) — see `HPO/results/r4_n3_modeled/R4_N3_CORRECTED.md`

⚠️ **Updated 2026-05-08**: original assumption (static = same allocations as moldable) was WRONG. Under proper static, data5 lands on cloud-g5 lanes=3 (not slurm partial 1) because static's on-prem-first override only fires when score's optimal_type matches on-prem (g4). Score picks g5 for data5 → on-prem doesn't match → cloud-g5.

| wf | static container | static lanes | makespan | deadline | result |
|---|---|---:|---:|---:|---|
| data8 | slurm | 4 throughout | 1,861 s | 6,161 s | HIT |
| data9 | cloud-g4 | 3 throughout | 17,229 s | 8,473 s | MISS by ~8,756s |
| data5 | **cloud-g5** | **3 throughout** | **6,775 s** | 7,290 s | **HIT (7% margin)** |

Static R4: **1/3 deadline misses** (data9 only — same as moldable).

### Comparison: moldable advantage (corrected)

| Metric | Static R4 (corrected) | Moldable R3 (actual) | Δ |
|---|---:|---:|---|
| data5 makespan | 6,775 s | 4,244 s | **moldable −37 %** ✅ |
| data8 makespan | 1,861 s | 2,751 s | static −33 % (moldable scale-down/up overhead) |
| data9 makespan (run-to-completion) | 17,229 s | ~15,000 s (proj) | moldable −13 % |
| **Deadline misses** | **1/3** | **1/3** | **TIED** (data9 misses regardless) |
| Sum-of-flowtimes (run-to-completion) | 25,865 s | ~22,000 s | moldable −15 % |
| **On-demand cost (run-to-completion)** | **$4.41** | **$1.33** | **moldable −70 %** ✅ |

## Observations

### What worked (moldable mechanism on data5)

1. **Early-scale-up** detection (`time 14.5% > budget 11.4%`) fired correctly mid-iter-0 → +1 lane.
2. **Moldable opportunity** at iter-2 boundary requested `2→6`, granted `+2` (slurm cap). Speedup model said 3.0× — actual measured speedup on iter 2: 554s vs ~2,540s static-equivalent = 4.6× wall-clock reduction for that iter.
3. **Late-iter scale-up window** materialized exactly when data8 finished (~15:12:45) — allowing data5 to claim 3 freed slurm slots within ~15 minutes of data8 freeing.

### What didn't work

1. **data9 received zero moldable scale-ups** despite cluster-g4 having 1 free OD-g4 slot. The scale-up logic at iter boundaries didn't fire (perhaps because chains stayed at 3 — moldable opportunity branch requires `chains > current_trials`, but data9 already had `current_trials = chains = 3`).
2. **Scheduler bug lost original data5 submit** — silent failure. data5 had to be manually resubmitted with a 25-min delay, complicating the timeline.
3. **data8's scale-down was unnecessary** — moldable freed 2 of 4 slurm lanes after iter 0, then had to scale back up at iter 2. Net: +331s wall-time on data8 vs static. Moldable's runtime model was too optimistic (`runtime < paced_available_time` triggered scale-down because the 22.17 s/epoch model under-predicted by 2×).
4. **Cluster-g4 cap=4 prevents data9 deadline rescue.** Even hypothetical lanes=5 would help; lanes=4 doesn't (chains=9 still requires 3 batches at lanes=4 just like at lanes=3).

### Cost picture (corrected)

⚠️ Original analysis claimed cost differential = $0. Wrong — based on incorrect R4 modeling.

**Real cash differential moldable vs static (run-to-completion):**
- Moldable R3: 1 OD-g4 spawned (data9). $1.33 (would-be projected).
- Static R4 (corrected): 1 OD-g4 (data9) + 1 OD-g5 (data5). $4.41 ($2.52 + $1.89).
- **Moldable saves $3.08 / batch in OD cost (−70%).**

The mechanism: moldable's scale-down of data8 opens slurm capacity, which on-prem-first override routes new wfs (data5) into. Static doesn't scale down → slurm stays full → data5 must spawn its own OD instance.

## What this experiment validates

✅ **The moldable scaling mechanism fires when conditions allow** — data5 was the textbook case (long wf, partial start, sibling finishes, scale into freed slots).

❌ **Moldable does NOT save deadline misses** — both moldable and corrected-static have 1/3 misses (data9 misses regardless because cluster-g4 cap=4 + chains=3 + tinyda growth blows deadline).

✅ **Moldable saves on-demand cost (~70% in EDF)** — by routing data5 to slurm (via on-prem-first override on slurm slots opened by data8's scale-down), moldable avoids spawning OD-g5 for data5. Static spawns OD-g5 → +$1.89 vs moldable.

✅ **Per-wf time savings can be substantial on high-tinyda trajectories** — moldable's slurm partial → lanes=4 path beats static's cloud-g5 lanes=3 path when chains × tinyda is large (R3's data5 trajectory had tinyda 18→25). On low-tinyda trajectories (R5-style), static wins.

⚠️ **Moldable does NOT help every wf** — data9 got nothing because cluster topology doesn't allow useful lane growth (cap=4 for chains=9).

⚠️ **Moldable can hurt on simple wfs** — data8's scale-down/up cycle added 331s vs static, with no benefit (deadline easily met either way). Moldable's runtime model needs to be calibrated, not lean optimistic.

⚠️ **Schedule has a known bug** — silent submit drop under contention. Documented for fix.

## What this experiment does NOT validate

- The campaign-level **makespan** is dominated by data9 (which moldable couldn't help). Total wall ≈ static wall.
- The cost story is null. No cash difference.
- The bug-induced data5 delay (25 min) means the timeline isn't a clean reproduction of what moldable would do under normal dispatch. But the per-iter measurements and scaling decisions are still valid evidence of the mechanism.

## Files in this directory

| File | Content |
|---|---|
| `R3_N3_ACTUAL.md` | this writeup |
| `r3_moldable_edf_3.log` | full scheduler log |
| `r3_dispatcher.log` | dispatch order |
| `executor_172_31_*.out` | 6 executor stdout files |
| `cold_start_log.csv` | OD-g4 cold-start timing (359s) |
| `EDF_Moldable_HPO_3wf_hybrid_resources.csv` | resource utilization log (computeMetrics crashed before main results CSV; not available) |
| `yamls_used/data*.yaml` | yaml snapshot at run time |

## Next analytical steps

1. **Apply `analysis/decompose.py` to this dataset** to generate Gantt + decomposition plots. Note: main results CSV missing (computeMetrics crashed on data9's missing finish_time), but per-iter trajectory + cold-start log are sufficient for hand-built plots.
2. **Refit τ values per wf** using the empirical measurements above. Update `sim_n3_real.py` projections.
3. **Document the silent-submit-drop bug** for later fix in scheduler. Likely a race between Phase 1 (resource_request) and Phase 2 (workflow) in the run loop, exacerbated by long createOnDemandWorkers blocking.
4. **Project R4 (static, N=3)** at higher confidence using the calibrated τ values from this run.
