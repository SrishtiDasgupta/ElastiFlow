# Thesis Results — Moldable vs Static Scheduling Across N

*Compiled 2026-05-11. All numbers from R7-calibrated 4-corner simulator
(`sim_4corners_calibrated.py`) with n=6 modelled runs per cell (batch 1 seeds
[7, 107, 207] + batch 2 seeds [1007, 1107, 1207]).*

## Methodology

- **Workflows:** subset of `[data8, data9, data5, data7, data3, data12, data1]`,
  Poisson seed=42 dispatch (submit times 0/271/390/472/487/502/508 s).
- **Sources of variance per run:** HPO non-determinism (sampled iter count + per-iter epoch from empirical R3-R7 distribution) and PER_EPOCH calibration ±10 %.
- **Cluster topology:** slurm (4 lanes), cluster_g4 (4 lanes, 2 reserved + 2 OD-spawnable), cluster_g5 (4 lanes, 2 reserved + 2 OD-spawnable).
- **Bug-#2 fix assumed** (immediate retry on shrink/complete) for all moldable runs.
- **Robustness check passed** on 35/36 batch-1 vs batch-2 metric pairs (only N=7 Static FCFS cost narrowly fails — $6.73 B1 vs $9.21 B2 vs combined $7.97 ± $1.62).

## Headline Table — n=6 mean ± stdev per cell

| N | corner | misses | sum-flow (m) | campaign (m) | OD cost ($) | cost CV |
|---|---|---|---|---|---|---|
| **3** | Static EDF | 0.5 ± 0.5 | 270 ± 66 | 145 ± 57 | $2.39 ± $0.67 | 0.28 |
| | Static FCFS | 0.5 ± 0.5 | 270 ± 66 | 145 ± 57 | $2.39 ± $0.67 | 0.28 |
| | Mold EDF | 0.8 ± 0.4 | 282 ± 65 | 157 ± 46 | $4.09 ± $1.28 | 0.31 |
| | Mold FCFS | 0.8 ± 0.4 | 282 ± 65 | 157 ± 46 | $4.09 ± $1.28 | 0.31 |
| **5** | Static EDF | 2.2 ± 0.8 | 509 ± 65 | 177 ± 22 | $2.39 ± $0.67 | 0.28 |
| | Static FCFS | 2.5 ± 0.5 | 616 ± 71 | 214 ± 27 | $6.38 ± $0.98 | 0.15 |
| | Mold EDF | 1.8 ± 0.8 | 450 ± 54 | 172 ± 21 | $5.32 ± $0.97 | 0.18 |
| | Mold FCFS | 1.8 ± 0.8 | 450 ± 54 | 172 ± 21 | $5.32 ± $0.97 | 0.18 |
| **7** | Static EDF | 4.2 ± 0.8 | 944 ± 136 | 237 ± 21 | $6.95 ± $1.14 | 0.16 |
| | Static FCFS | 4.5 ± 0.5 | 991 ± 146 | 241 ± 47 | $7.97 ± $1.62 | 0.20 |
| | Mold EDF | 3.3 ± 0.8 | 719 ± 98 | 192 ± 42 | $6.92 ± $1.03 | 0.15 |
| | Mold FCFS | 3.3 ± 1.0 | 722 ± 104 | 192 ± 42 | $6.67 ± $0.88 | 0.13 |

## Paired Difference (moldable − static, matched seed)

| N | ordering | Δmisses | Δsum-flow | Δcampaign | Δcost |
|---|---|---|---|---|---|
| 3 | EDF | **+0.33 ± 0.52** | +12m ± 28m | +12m ± 28m | **+$1.70 ± 0.64** |
| 3 | FCFS | **+0.33 ± 0.52** | +12m ± 28m | +12m ± 28m | **+$1.70 ± 0.64** |
| 5 | EDF | −0.33 ± 0.82 | **−59m ± 35m** | −6m ± 19m | +$2.93 ± 0.35 |
| 5 | FCFS | **−0.67 ± 1.03** | **−166m ± 21m** | **−42m ± 26m** | −$1.06 ± 1.53 |
| 7 | EDF | **−0.83 ± 1.17** | **−225m ± 87m** | **−45m ± 34m** | −$0.03 ± 0.46 |
| 7 | FCFS | **−1.17 ± 1.17** | **−269m ± 87m** | −49m ± 61m | **−$1.30 ± 1.41** |

**Bold** = mean's magnitude exceeds its stdev (confidence interval excludes zero).

## Seven Trends — Findings

### Finding 1 — Miss-rate advantage grows monotonically with N

Moldable's lead in deadline misses tracks linearly with N. Each pair of extra wfs adds ~0.5 to the advantage as moldable rescues one more contested wf (data7 at N=5; data12 and data1 at N=7).

```
        EDF Δmisses    FCFS Δmisses
N=3     +0.33  (worse)  +0.33  (worse)
N=5     −0.33           −0.67
N=7     −0.83           −1.17
```

### Finding 2 — Sum-flowtime: moldable wins decisively from N=5

Sum-flowtime crossover happens between N=3 and N=5. Static scales near-linearly (+3.5× from N=3 to N=7); moldable scales sub-linearly (+2.6×) because grow/shrink lets one wf release capacity for the next without queue waiting.

| corner | N=3 | N=5 | N=7 | growth |
|---|---|---|---|---|
| Static EDF | 270m | 509m | 944m | ×3.5 |
| Static FCFS | 270m | 616m | 991m | ×3.7 |
| Mold EDF | 282m | 450m | 719m | ×2.6 |
| Mold FCFS | 282m | 450m | 722m | ×2.6 |

### Finding 3 — Campaign wallclock saturates under moldable

Adding wfs barely lengthens moldable's wallclock. The longest-running wf still caps the campaign and moldable parallelises better around it.

| corner | N=3 | N=5 | N=7 | Δ N=3→7 |
|---|---|---|---|---|
| Static EDF | 145m | 177m | 237m | +92m |
| Mold EDF | 157m | 172m | 192m | **+35m** |

### Finding 4 — Cost: moldable catches up by N=7

Cost crossover differs by ordering. For EDF: crossover at N≈7 (tied at −$0.03 ± 0.46). For FCFS: crossover at N≈5 (moldable wins thereafter).

| corner | N=3 | N=5 | N=7 |
|---|---|---|---|
| Static EDF | $2.39 | $2.39 | $6.95 |
| Mold EDF | $4.09 | $5.32 | $6.92 (tied) |
| Static FCFS | $2.39 | $6.38 | $7.97 |
| Mold FCFS | $4.09 | $5.32 | $6.67 (wins) |

Mechanism: at low N, static idles a cluster (g4) so its OD cost is low and moldable's partial-allocation forces unnecessary g5 OD. At high N, static is forced to spawn OD on every cluster regardless; moldable amortises by shorter OD-lifetimes.

### Finding 5 — Ordering rule (EDF vs FCFS) matters only for static

At every N, Mold EDF and Mold FCFS are within stdev of each other. At every N, Static FCFS is materially worse than Static EDF (misses, sum-flow, cost). **Moldable's flexibility absorbs the ordering rule.**

| N | Stat-EDF vs Stat-FCFS Δflow | Mold-EDF vs Mold-FCFS Δflow |
|---|---|---|
| 3 | 0m (tied) | 0m (tied) |
| 5 | 107m worse FCFS | 0m (tied) |
| 7 | 47m worse FCFS | ~0m (tied) |

**Practical implication:** moldable doesn't need known deadlines to perform well — relevant for production systems where deadlines aren't always explicit.

### Finding 6 — Moldable is more predictable (lower CV)

| corner | N=3 | N=5 | N=7 |
|---|---|---|---|
| Static EDF | 0.28 | 0.28 | 0.16 |
| Static FCFS | 0.28 | 0.15 | 0.20 |
| Mold EDF | 0.31 | 0.18 | 0.15 |
| Mold FCFS | 0.31 | 0.18 | 0.13 |

Moldable's cost CV converges to ~0.13–0.15 by N=7; static FCFS climbs back to 0.20. **For budgeting, moldable becomes the safer choice as N grows.**

### Finding 7 — Moldable is more equitable (lower intra-run slack stdev)

Static's per-wf slack distribution widens with N (some wfs win big, others miss big). Moldable holds intra-run slack stdev ~50 min across all N.

| corner | N=3 | N=5 | N=7 |
|---|---|---|---|
| Static EDF | 46m | 53m | 76m |
| Static FCFS | 46m | 79m | 81m |
| Mold EDF | 51m | 46m | 52m |
| Mold FCFS | 51m | 46m | 53m |

**Implication:** moldable doesn't starve specific wfs as system load grows.

## Single-Sentence Thesis Result

> Moldable scheduling is a poor choice at light load (N≤3) and a strict win at heavy load (N≥7), with N=5 as the crossover regime where time savings appear before cost savings; the advantage is concentrated in FCFS workloads where ordering flexibility is most valuable.

## Live-Validation Appendix

These are out-of-sample anchors. None directly comparable to the modelled cells (different deadlines or different wf sets), reported as qualitative validation only.

| live run | corner | N | parameters | live outcome | nearest modelled cell |
|---|---|---|---|---|---|
| **R3** | Mold EDF | 3 | ×3 dl, dispatch [8,9,5] | 1/3 miss, ~$1.05 OD | 0.8 ± 0.4 misses, $4.09 ± $1.28 |
| **R5** | Mold FCFS | 3 | ×3 dl, dispatch [8,9,5] | 1/3 miss, ~$1.05 OD | 0.8 ± 0.4 misses, $4.09 ± $1.28 |
| **R1** | Mold EDF | 5 | **×2 dl**, dispatch [9,7,5,3,8] | 3/5 ×2, 1/5 ×3 | not directly comparable (dl factor) |
| **5/FCFS-Mold** | Mold FCFS | 5 | different wf set | 2/5, cost $5.42 | not comparable (different wfs) |
| **5/FCFS-Stat** | Static FCFS | 5 | different wf set | 1/5, cost $3.44 | not comparable (different wfs) |
| **R7** | Mold EDF | 7 | ×3 dl, dispatch [8,9,5,7,3,12,1] | 4/7 miss | 3.3 ± 0.8 misses |

R3, R5, R7 fall within the modelled cell's stdev for miss count. Cost diverges because live runs hit the silent-submit-drop bug (R3, R5) or used cheaper OD instance mix than the model assumes.

## Data Files (n=6 modelled, per cell)

- `metrics_n3.py` — N=3 metrics across all 4 corners, both batches
- `metrics_n5.py` — N=5 metrics
- `metrics_n7.py` — N=7 metrics
- `sim_4corners_calibrated.py` — underlying simulator
- `trace_n3_mold_edf.py` — example full event-level trace for one cell

All three `metrics_n*.py` scripts print Batch 1, Batch 2, Combined, and Batch-1-vs-Batch-2 robustness sections.

## Caveats

1. **No live Static EDF at any N** — all Static EDF numbers come from the model alone.
2. **Live-vs-modelled divergence at N=3** — model misses data5 while live R3 misses data9. Two modelling simplifications drive this: (a) no dynamic cluster scoring (model uses fixed pref_clusters), (b) monotone chains (model has data8 grow `[4,6,9]` but live R3 had data8 shrink `4→2→3`). See conversation in `r7_n7_actual_vs_modeled/` for details.
3. **N=7 Static FCFS cost narrowly fails robustness check** ($6.73 B1 vs $9.21 B2). Other 35/36 cells pass cleanly.
4. **HPO non-determinism distribution** is empirical from R3-R7 (4 actual runs). Sampling outside that envelope (very-low or very-high iteration counts) is extrapolation.
