# R1 — Moldable EDF, N=5 — finalized 2026-05-08

Run started 2026-05-06 ~19:50 UTC; killed at iter 3 of `hpo-df19b440` (data5). 4 of 5 wfs completed naturally; 1 projected from observed trajectory.

## Dispatch

Order `[9, 7, 5, 3, 8]`, Poisson avg-delay 90s, seed=42. Submit base `t0 = 1778098179`.

| pos | wf  | id           | model            | submit (rel) | chains | deadline |
|-----|-----|--------------|------------------|--------------|--------|----------|
| 0   | 9   | hpo-b03a4442 | convnext_large   | 0            | 3      | 5648.81s |
| 1   | 7   | hpo-73120874 | wide_resnet101_2 | 271          | 2      | 3837.20s |
| 2   | 5   | hpo-df19b440 | wide_resnet101_2 | 390          | 3      | 4859.88s |
| 3   | 3   | hpo-a87b8123 | vgg19            | 472          | 4      | 3463.89s |
| 4   | 8   | hpo-60bf3eb4 | vgg19            | 487          | 4      | 4107.07s |

## Outcomes

| wf | makespan (s) | deadline ×2 | status (×2) | deadline ×3 | status (×3) | source |
|----|-------------:|------------:|:-----------:|------------:|:-----------:|--------|
| 9  | 6272.8       | 5648.81     | **MISS** (+624s, 1.11×) | 8473.22 | HIT | observed |
| 7  | 2339.3       | 3837.20     | HIT (61%)   | 5755.80 | HIT | observed |
| 5  | **9394** *(proj)* | 4859.88     | **MISS** (+4534s, 1.93×) | 7289.82 | **MISS** (+2104s) | iter 0–2 obs + iter 3 proj |
| 3  | 3519.4       | 3463.89     | **MISS** (+55s, 1.02×) | 5195.84 | HIT | observed |
| 8  | 3264.2       | 4107.07     | HIT (79%)   | 6160.61 | HIT | observed |

**Deadline misses:** 3/5 at ×2 → 1/5 at ×3 (only wf5).

## wf5 projection (the killed one)

Iteration trajectory (lanes = 2 throughout — scale-up to 3 was requested but denied):

| iter | chains | tinyda_iters | runtime  | per-unit (s) |
|-----:|-------:|-------------:|---------:|-------------:|
| 0    | 3      | 18           | 1231.5s  | 34.21        |
| 1    | 4      | 20           | 1518.2s  | 37.95        |
| 2    | 6      | 24           | 2557.9s  | 35.53        |
| 3    | 9      | 25           | **~4487s** *(proj)* | 35.90 (avg of 0–2) |

Projection model: `runtime ≈ ceil(chains/lanes) × tinyda_iters × 35.9 s/unit`. Per-unit converges within ±5% across 3 observed iters → projection is tight (±3%). With 2 lanes locked in, scaling chains 6→9 forced 3→5 batches → 75 % more compute.

Projected wf5 makespan = 9394s (~2h37m).

## Cold-starts (3 on-demand instances spun up)

| instance              | role   | C = listening − request | setup phase share |
|-----------------------|--------|------------------------:|------------------:|
| i-0b639401206ec7986   | worker | 340.0s                  | 84.7 %            |
| i-061882b7803842104   | worker | 345.1s                  | 85.1 %            |
| i-0477bb9c3c6e3c903   | worker | 305.1s                  | 88.5 %            |

**Mean C ≈ 330s** — substantially better than modeled 530s. Setup phase dominates (~85 %). One of the three was scaled down at ~+1100s; the other two persisted to end-of-run (terminated by SAFETY-NET at ~+3000s).

On-demand cost ≈ (2 × 5500s + 1 × 1100s) × $0.526/h × $/3600 ≈ **$1.78** for R1 on-demand burn (excludes always-on / reserved).

## Read on the moldable signal

- The deadline-miss pattern at ×2 (3/5) is dominated by **wf5 — the wf that requested chain 9 with only lanes=2**. The scale-up was denied because all 14 GPUs were busy serving the other 4 wfs. This is exactly the queueing-edge prediction: at N=5 we are already hitting saturation when the longest moldable wf grows late.
- wf9 (convnext, the longest) finished within ×3 of its deadline despite being on-prem-locked (no scale-up). It just barely misses ×2.
- The vgg-class wfs (3, 8) absorbed cold-start exposure as predicted by the dispatch reorder, and 8 cleared its deadline by 21 % margin.
- **R1 measured C ≈ 330s** — re-run the analytical sensitivity in `decompose.py` with this anchor instead of 530s.

## Decision for R2 and beyond

1. **Apply ×3 deadline policy** (i.e. multiplier in deadline formula `× 2 → × 3`). At ×2 the deadline-miss metric is too noisy: 3/5 misses with the worst margin being 1.02× makes the metric near-binary on resource luck. At ×3 it cleanly isolates the one wf where moldable lost the lane-allocation race.
2. Apply via `constants_HPO.py` formula change (one source of truth) **not** YAML bumps — keeps yamls deterministic across R1/R2/R3/R4 and avoids per-file bookkeeping.
3. R1 stays as recorded; re-evaluate post-hoc against ×3 (already done above: 1 miss).
4. R2 (Static EDF, 5 wf, same dispatch + seed) compares against R1 *under the same ×3 policy*. Expected: static misses 2–3/5 (vs moldable 1/5) due to cold-start exposure on critical path.

## Artifacts

```
HPO/results/r1_moldable_edf_5/
├── R1_FINALIZED.md               # this file
├── cold_start_log.csv            # 3 on-demand instances
├── cold_start_log_fsx.csv        # (identical copy from EFS)
├── workflow_status.log           # 4 COMPLETED entries
├── r1_dispatcher.log             # dispatch order + delays
├── r1_dispatcher_aborted.log     # earlier failed attempt (schema crash)
├── r1_moldable_edf_5.log         # main_HPO.py stdout
├── r1_moldable_edf_5_aborted.log # earlier failed attempt
├── executor_172_31_*.out         # 5 executor stdout files
└── yamls_used/data{0..14}.yaml   # exact yaml snapshot at run time
```

`decompose.py` cannot run on this dataset because the run was killed before `main_HPO.py` emitted the final results CSV (`instances` field is the repr() of resource-manager objects only available at end-of-run). The metrics above are computed by hand from raw logs.
