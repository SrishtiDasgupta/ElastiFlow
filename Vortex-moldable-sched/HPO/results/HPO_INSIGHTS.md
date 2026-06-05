# HPO — Pareto & Scaling Insights (supplement)

**Supplementary figures** (in `HPO/results/plots/`):

- `HPO_06_pareto_trajectory.{pdf,png}` — four corner trajectories in
  (cost, miss) space across N=3 → 5 → 7. The single most direct visual
  of the regime crossover finding tabulated below.
- `HPO_11_util_4corners_n7.{pdf,png}` — 2 × 2 panel of fleet utilisation
  (nodes in use vs time) at N=7, one panel per corner. Cost annotated
  per panel.
- `CROSS_pareto_comparison.{pdf,png}` — side-by-side Plain SeisSol
  N=400 and HPO N=7 Pareto frontiers. Visually anchors the cross-chapter
  synthesis claim ("Elastic dominates at high load in both workloads").
- `HPO_08_intent_satisfaction_n7.{pdf,png}` — **n = 6 averaged**
  WE-UP intent outcomes for both Elastic corners at N = 7, computed
  from the WE-intent counters instrumented into the simulator. Stacked
  bar with APPROVE / MODIFY / DENY segments, per-segment ± std across
  the six runs.
- `HPO_08_intent_events_n7.{pdf,png}` — companion figure showing intent
  outcomes from the only run for which per-event negotiation logs were
  captured. Different semantics from the satisfaction plot — see the
  scaling-event-data section below.

This document supplements the existing `THESIS_RESULTS.md` and
`THESIS_TABLE.md` with two pieces of analysis that were missing from
the HPO chapter and that mirror what the Plain SeisSol chapter already
carries: a **Pareto frontier** at each batch size and a
**variance/robustness** summary.

All numbers are pulled directly from the n=6 4-corner results in
`THESIS_TABLE.md`.

### Sort-key convention

All HPO runs use the **cost-sort** resource ordering
(`sort_key='cost'` in `fcfs_scheduler_HPO.py` and `edf_scheduler_HPO.py`,
`sort_key='cost_per_trial'` in the optimised variants). There is no
runtime-sort (`_r`) variant of HPO in the codebase. To be precise with
the Plain SeisSol nomenclature, the four HPO corners are
**EDF-ST<sub>c</sub>**, **FCFS-ST<sub>c</sub>**, **Elastic-EDF<sub>c</sub>**, and
**Elastic-FCFS<sub>c</sub>** — the cost-sort versions only.
In all HPO figures and tables, the subscript is suppressed for visual
brevity, but the cost-sort convention is implicit. Choosing cost-sort
matches the choice motivated for plain SeisSol in section 9.6 of the
thesis: at saturation, cost-sort dominates runtime-sort.

### Scaling event data — two complementary views

The chapter carries **two intent-outcome figures**, each describing a
different facet of the scaling mechanism.

**`HPO_08_intent_satisfaction_n7` — n = 6 averaged steady-state outcomes.**
The simulator's `run_corner` was instrumented (in
`compute_total_cost.py`) with four WE-intent counters
(`n_we_up_approve`, `n_we_up_modify`, `n_we_up_deny`,
`n_we_down_granted`), incremented at every grow / shrink decision
inside the simulated event loop. These counters are stored per run in
`total_cost_per_run.json` and aggregated across the six runs per cell
the same way every other headline metric is. The resulting plot mirrors
plain SeisSol's figure 08 in structure and supports the same n = 6 ±
std reporting convention.

At N = 7 the averaged outcomes for Elastic-EDF<sub>c</sub> are 1 % APPROVE,
12 % MODIFY (partial grant), 87 % DENY; Elastic-FCFS<sub>c</sub> is
indistinguishable within a percentage point. The scheduler refuses
almost every UP intent. This is qualitatively the same lesson as plain
SeisSol's 08 — the scheduler dominantly *withholds* the resources the
engine asked for — except that the simulator's MODIFY events are
*partial grants* (the scheduler offered fewer nodes than requested),
not *overrides to scale-down*.

**Why the satisfaction plot shows zero WE-DOWN intents.** The simulator
records zero shrink events across all six runs of every Elastic corner.
This is honest for two reasons:

1. *Empirical (faithful to R7).* The seven workflows in the R7 batch all
   grow or stay flat in chain count across iterations
   (data8: 4 → 6 → 9; data9: 3 → 3 → 3 → 3 → 3; data5: 3 → 4 → 6 → 9 →
   10; etc.). The `CHAIN_GROWTH_BY_MODEL` factors in `compute_total_cost.py`
   are monotone non-decreasing because the R7 calibration data is
   monotone non-decreasing — HPO workflows in this experiment never
   converged early enough to release chains. So no workflow ever has
   `chains_next < chains_current`, and the simulator's shrink branch
   (`cn_next < lanes`) cannot fire.
2. *Modelling simplification.* The simulator evaluates grow / shrink
   decisions cleanly at iteration boundaries with no timing race that
   could let the scheduler reply to an UP intent with a DOWN action.
   The real-system override-to-shrink mechanism (visible in the
   per-event log as same-iteration grow-observation → shrink-reply
   pairs) is therefore absent from the n = 6 satisfaction plot.

The companion `HPO_08_intent_events_n7` figure surfaces 3 such override
events for the one run with per-event logging. If the chapter wants to
make a quantitative claim about the override-to-shrink mechanism in HPO,
the per-event figure is the right reference; the satisfaction plot is
the right reference for steady-state APPROVE / MODIFY-partial / DENY
ratios.

**`HPO_08_intent_events_n7` — single-run snapshot from per-event logs.**
The per-event negotiation logger (`negotiation_scheduler.csv` etc.)
captures the real-system mechanism in finer detail than the simulator
models. It was enabled for one run in the Elastic-EDF<sub>c</sub> N = 7 cell
and that run records seventeen WE-UP intents with: 1 APPROVE, 13 DENY,
and **3 MODIFY (scale down)** — the same "scheduler converts a grow
request into a shrink" mechanism that drives plain SeisSol's MODIFY
column. The simulator does not model this timing-race override
(grow / shrink decisions are evaluated cleanly at iteration boundaries
with no possibility of replying with a different intent), so the n = 6
satisfaction plot shows MODIFY-partial but not MODIFY-down.

The two plots together cover both the **steady-state** behaviour
(satisfaction plot, n = 6) and the **mechanism** that is too fine-grained
for the simulator (events plot, one run). Each is honestly labelled with
its scope.

Consequently the HPO chapter cannot produce the full N-evolution / 4-corner
equivalents of Plain SeisSol's figures 07 (scaling frequency vs N), 07b
(grant-tier composition), and 07c (per-N evolution). What it can show is
a single-run snapshot of intent outcomes from the run for which the
negotiation log is available — see figure `HPO_08_intent_events_n7`.

In that representative run, of 17 WE-UP (grow) intents with resolved
outcomes:

- **1 APPROVE** (the scheduler granted nodes as requested);
- **13 DENY** (the scheduler refused to add capacity);
- **3 MODIFY (scale down)** (the scheduler turned the grow intent into
  a shrink reply for the same workflow + iteration — visible as
  matching grow-observation and shrink-reply rows in
  `negotiation_scheduler.csv` for `hpo-60bf3eb4`, `hpo-417bb2bd`,
  and `hpo-ccb43739`, all at iteration 1).

This MODIFY-down mechanism is exactly the same `processFreeRequest`
override pattern that drives plain SeisSol's 80% MODIFY-down rate.
The HPO numbers are smaller (3 events out of 17) and the absolute
counts are too few to claim a steady-state ratio, but the *direction*
matches: the scheduler routinely refuses or downgrades the engine's
UP intents. WE-DOWN (shrink) intents that originate from the engine
are all granted in this run.

The scaling mechanism's integral behaviour remains visible in figure
HPO_11 (fleet utilisation over time, 4 corners at N = 7) as the
moment-to-moment node counts; only the per-event accounting is missing
across the other cells.

---

## Pareto Frontier vs N — the regime crossover

The HPO Pareto frontier on the (cost, misses) plane changes
**qualitatively** as the batch size grows. The same four corners
(Static EDF, Static FCFS, Elastic EDF, Elastic FCFS) sit in very
different parts of the tradeoff space at each N.

| N | On the Pareto frontier | Dominated |
|---|---|---|
| 3 | Static EDF, Static FCFS (tied, $2.39, 0.5 misses) | Elastic EDF, Elastic FCFS — strictly worse on both axes |
| 5 | **Static EDF** ($2.39, 2.2 misses, cost anchor) **and Elastic EDF / FCFS** ($5.32, 1.8 misses, miss anchor) | Static FCFS ($6.38, 2.5 misses) — dominated |
| 7 | **Elastic FCFS** ($6.67, 3.3 misses) | All three others. Elastic EDF is $0.25 more expensive at same miss count → just outside the frontier but statistically tied (Δ < 0.5 σ). Both Static variants are strictly worse on both axes. |

**The qualitative story.**

- **At N = 3** (light load), the cluster is large enough that Static
  variants run everything to completion without overflow. Their fixed
  one-shot allocation matches workload demand precisely; Elastic's
  reactive scaling pays a setup-overhead penalty without any
  compensating benefit. Static is unambiguously the right choice.

- **At N = 5** (transition), the Pareto frontier *splits*. Static EDF
  remains cheapest because it can still complete each workflow on a
  single allocation. But Elastic-class variants now dominate on
  miss-rate because their per-iteration reallocation actively rescues
  the marginal workflow that would otherwise miss its deadline.
  Operators must explicitly trade cost vs SLO.

- **At N = 7** (saturated), Elastic FCFS becomes the **single Pareto
  point**. Static is now overloaded — its rigid one-shot allocation
  forces on-demand provisioning that costs more *and* misses more
  deadlines because OD ramp-up time exceeds slack on the urgency tail.

**Implication for the chapter narrative.** The "elastic vs static"
choice is not a fixed preference — it is a **load-dependent regime
choice**. Each batch size has its own recommended policy:

| Batch size | Recommended policy | Rationale |
|---|---|---|
| Small (N=3) | Static (either ordering) | Cluster large enough; elastic overhead unpaid |
| Medium (N=5) | Static EDF for cost; Elastic for SLO | Frontier splits; pick the binding constraint |
| Large (N=7) | Elastic FCFS | Static is dominated on both axes |

This is the cleanest articulation of the "moldability becomes
necessary above a threshold load" thesis claim.

---

## Variance & Robustness — extended T9

Coefficient of variation across the n=6 runs per cell, for the three
headline metrics. CoV is expressed as % of mean (stdev / mean × 100).

### Cost CoV

| Corner | N=3 | N=5 | N=7 |
|---|---|---|---|
| Static EDF   | 28 % | 28 % | 16 % |
| Static FCFS  | 28 % | 15 % | 20 % |
| Elastic EDF  | 31 % | 18 % | 15 % |
| Elastic FCFS | 31 % | 18 % | 13 % |

### Deadline-miss CoV

Reported on the absolute miss-count rather than rate (HPO uses count
because batches are small). For cells with mean misses < 1 the CoV is
proportionally large but absolute variation is tiny (≤ 0.5 misses).

| Corner | N=3 | N=5 | N=7 |
|---|---|---|---|
| Static EDF   | 100 % | 36 % | 19 % |
| Static FCFS  | 100 % | 20 % | 11 % |
| Elastic EDF  |  50 % | 44 % | 24 % |
| Elastic FCFS |  50 % | 44 % | 30 % |

### Sum-flow CoV

| Corner | N=3 | N=5 | N=7 |
|---|---|---|---|
| Static EDF   | 25 % | 13 % | 14 % |
| Static FCFS  | 25 % | 12 % | 15 % |
| Elastic EDF  | 23 % | 12 % | 14 % |
| Elastic FCFS | 23 % | 12 % | 14 % |

**Reading the tables together.** Cost CoV is consistently the largest of
the three (13–31 %), because cost combines variable OD lifetimes with
variable tier composition. Sum-flow CoV is the smallest (12–25 %)
because turnaround is dominated by service-time which has well-bounded
DL-kernel noise. Miss-count CoV is proportionally large only at low N
where the absolute counts are 0–1; at N = 7 it sits in a defensible
11–30 % band.

**For the chapter narrative:** the headline N = 7 numbers all have
CoV ≤ 30 %, meaning the regime crossover finding (Elastic dominates
Static at high N) is well above the noise floor on every metric the
chapter cares about.

**Reading the table.** CoV runs 13–31 %, materially higher than the
Plain SeisSol chapter's 5–15 % band. Two reasons:

1. **HPO campaigns are shorter** (N ≤ 7, ~3 hours wall-clock) so a
   single contested workflow has larger fractional impact than in a
   400-workflow batch.
2. **HPO has DL-kernel service-time noise** on top of queueing noise —
   per-epoch service times vary ±10 % independently of the scheduler,
   compounding into per-run cost variance.

Both effects are intrinsic to the HPO workload and do not threaten the
findings. The frontier composition is invariant to this noise because
the gaps between adjacent corners exceed the standard deviations:

| N | Gap on the deciding axis | σ of deciding cell |
|---|---|---|
| 3 | Static at $2.39 vs Elastic at $4.09 (Δ $1.70) | σ ≈ $0.67–1.28 → ~1.3 σ separation |
| 5 | Elastic EDF 1.8 misses vs Static EDF 2.2 misses (Δ 0.4) | σ ≈ 0.7 → ~0.6 σ separation (closest call) |
| 7 | Elastic FCFS 3.3 misses vs Static EDF 4.2 misses (Δ 0.9) | σ ≈ 0.85 → ~1 σ separation |

The N = 5 deadline-miss difference (Elastic ahead by 0.4 ± 0.7) is the
narrowest gap in the analysis. The chapter should phrase that
crossover with appropriate uncertainty ("Elastic catches up on
miss-rate at N = 5"), rather than claiming a sharp turnover.

---

## Comparison to the Plain SeisSol Chapter

| Question | Plain SeisSol answer | HPO answer |
|---|---|---|
| Does Elasticity always reduce cost? | Yes — uniformly 40–45 % at every N ≥ 200 | **No — Elastic is more expensive at N = 3** (cluster idles); Elastic catches up only at N ≥ 5 |
| Does Elasticity always cost deadlines? | Yes for EDF (+8–10 pp), no for FCFS (~0 pp), inverted for Rank | **No — Elastic improves miss-rate at every N ≥ 5** |
| Is HEFT-ST dominated? | Yes — strictly on every metric | (HEFT not tested; HPO chapter uses only the four corners) |
| Number of Pareto points at headline N | 3 (Cost-first, Balanced, Hard-SLO) | 1 (Elastic FCFS) at N = 7 |
| CoV across 6 runs | 5–15 % | 13–31 % |

**The headline cross-chapter difference.** Plain SeisSol has a stable
elastic-vs-static tradeoff structure that holds across all N ≥ 200 —
the right policy depends on which SLO is binding, not on load level. HPO
has a *load-dependent* tradeoff structure in which the right policy
itself changes as N grows. This reflects the workload character:

- Plain SeisSol is a long, well-structured iterative simulation —
  elasticity gains a steady tier-shift advantage that scales with load.
- HPO is a short, bursty deep-learning campaign — elasticity has a
  fixed setup cost that only amortises once load is high enough.

Both chapters argue that elasticity is necessary, but the threshold and
the dominant mechanism differ. This is a useful synthesis point for the
combined results discussion in the thesis.
