# Elastic vs Static — cross-workload metric comparison

Percentage improvement of the **best elastic policy over its corresponding
static baseline**, for four SLA/efficiency metrics, across the three Vortex
workloads. Companion figure: `plots/XWORKLOAD_elastic_vs_static.{pdf,png}`.

## Method

- **Pairing.** Elastic policies are compared to the static baseline of the same
  ordering class: ELASTIC-FCFS vs STATIC-FCFS, ELASTIC-EDF vs STATIC-EDF (and,
  for LA, ELASTIC-HSM vs STATIC-EDF). For each metric we report the *best*
  elastic policy and name it.
- **Sign convention.** All metrics are lower-is-better, so
  `% improvement = (static − elastic) / static × 100`; **positive = elastic
  better.** A negative number means the best elastic policy still loses.
- **CPR** (cost-performance ratio) = per-workflow cost (USD) ÷ (1 − overall miss
  rate) = USD per constraint-satisfying workflow. Currency USD = EUR × 1.10.
- **Data sources.**
  - Plain SeisSol — `plain_results/plain_results_per_run.json`, N=400, 6 seeds,
    `_c` (cohesion) sort-key variants.
  - LA / license-constrained CAE — `license_results/canonical_results.json`,
    N=300, 6 seeds, baseline 33/33/34 solver deck.
  - HPO — `HPO/results/SUMMARY_4CORNER.md`, N=3 real-cluster 4-corner
    (deadline misses tie at 1/3; budget bumped ×1.5 so no budget misses; CPR
    follows OD cost because miss rates are equal across corners).

## 1. Plain SeisSol-TinyDA (focus workload)

| Metric | Static baseline | Best elastic | Best elastic policy | % improvement |
|---|---:|---:|---|---:|
| Deadline miss-rate | 0.256 | 0.260 | ELASTIC-FCFS | **−1.3%** |
| Budget miss-rate | 0.0217 | 0.0054 | either elastic | **+75.0%** |
| CPR (USD/success) | 44.5 | 26.2 | ELASTIC-FCFS | **+41.1%** |
| Avg turnaround (s) | 27,730 | 26,850 | ELASTIC-FCFS | **+3.1%** |
| Avg queue wait (s) | 11,020 | 6,585 | ELASTIC-EDF | **+40.2%** |

No license tier → hardware-dominated. Elastic wins decisively on cost/CPR,
wait and budget misses. The honest cost is **deadline adherence**: ELASTIC-EDF
worsens deadline miss-rate 0.053 → 0.134 (−156%); even the best elastic only
ties static on that axis.

## 2. HPO / ML hyperparameter tuning (trend check)

| Metric | Static baseline | Best elastic | Best elastic policy | % improvement |
|---|---:|---:|---|---:|
| Deadline miss-rate | 1/3 | 1/3 | tie | **0%** |
| OD cost → CPR (misses equal) | $4.41 | $1.33 | ELASTIC-EDF | **+69.8%** |
| Avg turnaround (Σ flowtime, s) | 25,865 | ~22,000 | ELASTIC-EDF | **+14.9%** |
| Budget miss-rate | — | — | n/a (×1.5 headroom) | n/a |

Most hardware-dominated workload → the **largest** elastic cost win (−70%),
deadlines tied (cluster topology forces data9 to miss in every corner). Same
direction as Plain, amplified. (ELASTIC-FCFS turnaround is instead −20%.)

## 3. License-constrained CAE / LA (trend check)

| Metric | Static baseline | Best elastic | Best elastic policy | % improvement |
|---|---:|---:|---|---:|
| Deadline miss-rate | 0.268 | 0.262 | ELASTIC-FCFS | **+2.3%** |
| Budget miss-rate | 0.0356 | 0.0283 | ELASTIC-HSM | **+20.3%** |
| CPR (USD/success) | 216.2 | 203.9 | ELASTIC-FCFS | **+5.7%** |
| Avg turnaround (s) | 26,060 | 28,080 | ELASTIC-FCFS | **−7.7%** |
| Avg queue wait (s) | 9,978 | 8,731 | ELASTIC-FCFS | **+12.5%** |

License-dominated → the elastic edge collapses. Only ELASTIC-FCFS (LAMF) stays
mildly positive (CPR +5.7%, wait +12.5%); every elastic-EDF metric except
budget-miss regresses (deadline −30%, CPR −3%, turnaround −20%), and **static
EDF remains the strongest policy**.

## Cross-workload synthesis

Best elastic vs matching static, % improvement (positive = elastic better):

| Metric | HPO (hardware) | Plain (hardware, deadline-tight) | LA (license) |
|---|---:|---:|---:|
| CPR / cost | +70% | +41% | +6% |
| Queue wait | n/a | +40% | +13% |
| Budget miss | n/a | +75% | +20% |
| Deadline miss | tie | −1% (EDF −156%) | +2% (EDF −31%) |
| Turnaround | +15% | +3% | −8% |

**The elastic advantage tracks the binding constraint and decays monotonically
as that constraint shifts from hardware to licenses.** Elasticity wins big where
compute is the bottleneck (HPO, Plain) and the win evaporates where licenses
bind (LA), where static EDF dominates. The consistent elastic weak spot across
every workload is **deadline adherence**: molding trades schedule predictability
for cost and throughput.
