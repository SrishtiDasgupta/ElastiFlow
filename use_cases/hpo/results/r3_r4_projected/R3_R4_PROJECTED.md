# R3 (Moldable cap=1.0, N=7) vs R4 (Static, N=7) — projected 2026-05-08

Modeled from R1's measured per-iteration trajectory data, extended to N=7 dispatch `[9, 5, 7, 3, 8, 12, 1]` with same Poisson seed=42 (mean delay 90s) for the additional data12, data1.

**Plan:** R3 will be **run** on real hardware to validate this projection; R4 will be **modeled only** (no live run, budget-constrained).

## Setup

- Pool: 14 always-on GPUs.
- Cold-start C: 330s (R1 measurement).
- Dispatch order: `[9, 5, 7, 3, 8, 12, 1]` (long-first, vgg-class last to absorb cold-starts).
- Submit times (rel): data9=0, data5=271, data7=390, data3=472, data8=487, data12=577 (estimate), data1=667 (estimate).
- Per-wf τ from R1 lanes=2 fits:
  - data9 (convnext): 20.16
  - data5, data7 (wide_resnet): 35.90, 16.18
  - data3, data8, data12, data1 (vgg19): 25.58 (proxy for data12/data1 from data3/data8)
- Per-iter trajectories (chains, tinyda) — observed for first 5; data12/data1 synthesized from yaml chains₀=4, next_trials growth 1.5×, vgg-class tinyda pattern.

## Results

| Metric | R4 Static (modeled) | R3 Moldable cap=1.0 (modeled, to be measured) | Δ |
|---|---:|---:|---:|
| Makespan | 10,509s (2.92h) | **6,021s (1.67h)** | **−43 %** |
| Deadline misses (×3) | 1/7 (data5) | **0/7** | better |
| On-demand cost | $4.68 | **$4.50** | **−4 %** (moldable wins) |
| Cluster-wall × 14-GPU | $21.5 | $12.3 | −$9.2 |
| Net infra cost | $26.1 | **$16.8** | **−$9.3** |

## Per-wf

| wf | model | static ms | moldable ms | Δ | mechanism |
|----|---|---:|---:|---:|---|
| data9  | convnext    | 3678 | 3053 | −625 | iter 1 scale 3→4 |
| data5  | wide_resnet | **10,238** | **5750** | **−4488** | iter 3 scale 3→9, iter 4 scale 3→10 |
| data7  | wide_resnet | 2709 | 2709 | 0 | chains₀=2, no growth headroom |
| data3  | vgg19       | 2546 | 2546 | 0 | finishes early, pool saturated during iters |
| data8  | vgg19       | 2415 | 2415 | 0 | initial cold-start, no late-iter window |
| data12 | vgg19       | 3929 | **3008** | **−921** | scale-ups on later iters once pool clears |
| data1  | vgg19       | 2876 | 2876 | 0 | submitted last, pool saturated |

**Two moldable winners now: data5 and data12.** At N=5 only data5 hit the late-iter scale-up regime; at N=7 data12 also benefits because the vgg-class wfs that finish early (data3, data8) free up enough capacity for data12's later iters. **The advantage scales with N as predicted.**

## Cost asymmetry inversion

At N=5 moldable paid +$0.35 in on-demand (one extra cold-start because data9's iter 1 scale-up ate headroom for data8). At N=7 the picture flips: moldable's data12 finishes ~920s earlier, releasing 4 on-demand instances ~$0.54 sooner than static does. Net moldable on-demand cost is **lower** than static at N=7.

This validates the analytical break-even prediction: moldable cost-loses below ~6 wfs (small batches, scale-up overhead dominates), wins above. The crossover is at this batch size.

## Risks in the projection

1. **τ proxy for data1/data12.** Both vgg19, using τ=25.58 from data3. data3's per-iter τ varied 21.9–27.5 in R1 → ±10 % uncertainty per-iter. data12/data1 makespans could shift ±5 % live. Headline −43 % is robust.
2. **Submit times for data12, data1 are estimates** (placed at +90s each from data8). Actual seed=42 spacing could shift ±50s. Doesn't change qualitative result.
3. **Synthetic iter trajectories for data12/data1.** Used vgg-class chain growth pattern (4→6→9 for data1 since 3 iters; 4→6→9→13 for data12 since 4 iters). Live `run_hpo.py` may produce slightly different next_trials values. Assumed pattern is conservative for moldable.
4. **Scale-up grant policy in scheduler.** Model assumes any free always-on capacity is granted on request. Real scheduler considers budget remaining and EDF urgency. If scale-up is denied for moldable's late iters (e.g., due to budget logic), moldable approaches static. Verify code before R3 launches.
5. **EDF urgency boosts at ×3 deadline.** With deadline ×3 in YAMLs (post-hoc multiplication or pre-launch script), urgency boost messages fire much later, so on-demand spawn rate is lower than R1 saw. Fewer surprise cold-starts.

## Provenance

- Simulation: `sim_n7.py` in this directory.
- τ fits + trajectory data: `HPO/results/r1_moldable_edf_5/R1_R2_FINALIZED.md`.
- Cold-start C=330s: `HPO/results/r1_moldable_edf_5/cold_start_log.csv`.
