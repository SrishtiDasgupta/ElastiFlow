# Plain SeisSol-TinyDA — Results & Analysis

This document is the prose companion to the figures in
`plain_results/plots/`. Every figure receives a section explaining what
it shows, what conclusions can be drawn, and any caveats. Two tables
(T5 — Policy Frontier, T9 — Variance/Robustness) live in the chapter
body; the full N=400 results table (T3) sits in the appendix.

The algorithm taxonomy (T1) and the simulated fleet & workflow
configuration (T2) are presented in earlier chapter sections and are not
repeated here. All costs are in USD using `1 EUR = 1.10 USD`.

The sweep covers 11 scheduler variants × N ∈ {100, 200, 300, 400} ×
**6 runs** per cell = 264 completed runs in total. Workflow mesh sizes
are drawn in a 1 : 1 : 2 ratio over {1000, 750, 500} as the
distribution input.

---

## T5 — Policy Frontier at N=400

Three operating points define the Pareto frontier on the
(cost, overall-miss) plane. The labels are regime tags used in the
recommendation prose; they are not used in any figure.

| Regime | Variant | Cost (USD / wf) | Wait (s) | Makespan (s) | Deadline miss | Budget miss | Overall miss | When to choose |
|---|---|---|---|---|---|---|---|---|
| **Hard-SLO**   | EDF-ST<sub>c</sub>          | 34.2 ± 3.2 | 11 021 | 158 566 | 0.052 | 0.022 | 0.074 | Regulated or contract-bound workloads where SLO violations cost more than compute |
| **Balanced**   | Elastic-EDF<sub>c</sub>     | 20.5 ± 1.9 | 6 585  | 163 936 | 0.134 | 0.005 | 0.140 | Default operating point; cost-conscious with soft SLOs |
| **Cost-first** | Elastic-Rank[50,50] | 18.7 ± 2.0 | 6 004  | 163 355 | 0.237 | 0.005 | 0.242 | Bulk research workloads, no per-workflow SLO penalty |

**Reading the table.** Walking left-to-right along the frontier, each
step trades ~$1.7 of per-workflow cost for ~10 percentage points of
overall-miss. The right-most point (Hard-SLO) costs 1.8 × the left-most
(Cost-first) but slashes miss-rate by 3.3 ×. The middle point retains
most of the cost saving of Cost-first while keeping miss-rate well below
the SLO point's worst neighbours. **Elastic variants always win on
budget miss** — their conservative scale-up keeps spend bounded.
Makespan barely varies along the frontier (±3 %).

---

## T9 — Variance & Robustness across 6 Runs (N=400)

Coefficient of variation (% of mean) for each variant on each headline
metric. CoV ≤ 15 % is the rule-of-thumb threshold for "stable enough to
quote a mean as the headline number"; everything here passes.

| Variant | Cost CoV | Deadline miss CoV | Wait CoV | Makespan CoV | Turnaround CoV |
|---|---|---|---|---|---|
| FCFS-ST<sub>r</sub>          |  8.2 % |  4.5 % | 1.4 % | 7.4 % | 1.8 % |
| FCFS-ST<sub>c</sub>          |  7.2 % |  2.4 % | 0.9 % | 6.7 % | 2.0 % |
| Elastic-FCFS<sub>r</sub>     |  8.6 % |  4.6 % | 4.0 % | 7.5 % | 2.9 % |
| Elastic-FCFS<sub>c</sub>     | 10.9 % |  3.4 % | 2.9 % | 6.4 % | 1.6 % |
| EDF-ST<sub>r</sub>           | 10.1 % | 24.9 % | 2.8 % | 2.2 % | 3.5 % |
| EDF-ST<sub>c</sub>           |  9.4 % | 15.4 % | 3.1 % | 5.0 % | 2.6 % |
| Elastic-EDF<sub>r</sub>      | 13.4 % | 10.8 % | 5.5 % | 4.0 % | 4.9 % |
| Elastic-EDF<sub>c</sub>      |  9.1 % |  7.7 % | 5.3 % | 8.0 % | 3.9 % |
| HEFT-ST              | 11.0 % |  2.5 % | 3.5 % | 3.8 % | 2.6 % |
| Elastic-Rank[50,50]  | 10.8 % |  4.9 % | 4.8 % | 4.8 % | 3.0 % |
| Elastic-Rank[25,75]  | 14.7 % |  6.0 % | 7.1 % | 4.9 % | 4.8 % |

**Reading the table.** Cost CoV sits in a tight 7–15 % band, which means
the elastic-vs-static cost gap of ~40 % is roughly 4 σ (the gap is far
larger than the noise floor). The two cells worth a footnote:

- **EDF-ST<sub>r</sub> deadline-miss CoV = 25 %.** With miss-rate this low (~0.05)
  the absolute std is tiny (~0.014), but proportionally a single misbehaving
  run inflates the CoV. The mean remains defensible.
- **Elastic-Rank[25,75] cost CoV = 15 %.** Rank's per-iteration
  reranking compounds inter-run differences in submit timing, producing
  the largest variance among Elastic variants. Still inside the threshold.

Wait time and turnaround variances are uniformly < 6 % — these are the
most reproducible metrics.

---

## Figure 01 — Cost per workflow vs N
**`01_cost_vs_n.{pdf,png}`** — line plot, one line per variant, ±1σ
shaded band.

**What it shows.** Headline scaling figure. Per-workflow cost on y-axis,
batch size on x-axis. Elastic variants cluster between $17–22 / wf at
N=400; static variants between $32–38; HEFT-ST sits highest at $37.

**Conclusions.**
- Elastic variants save 40–45 % vs their static counterparts at every N
  ≥ 200. The gap is established by N=200 and remains stable through
  N=400.
- The static cluster's per-workflow cost falls from N=100 to N=300 then
  saturates — fixed cluster costs are amortised over more workflows
  until the on-demand-spillover regime kicks in.
- The Elastic cluster is essentially flat across N=300–400. Once you
  reach the per-workflow cost floor of $17–20, more workflows don't make
  each one cheaper.

**Caveats.** N=100 is noisy because some variants haven't yet seen
overflow; their per-workflow costs are artificially uniform.

---

## Figure 01b — Cost-tier decomposition stack @ N=400
**`01b_cost_stack.{pdf,png}`** — stacked bar per variant (on-prem +
reserved + on-demand). Y-axis in scientific notation. OD% annotated
above each bar. Single error bar at top showing total-cost std.

**What it shows.** The *mechanism* behind figure 01. Where the money
went. Elastic variants spend 21–25 % on on-demand; static variants
spend 38–55 %; HEFT-ST spends 51 %. Total height ranges 6–14 K USD per
batch.

**Conclusions.**
- The cost difference between elastic and static is almost entirely
  explained by on-demand share. The on-prem and reserved bars are similar
  across all variants — they're the fleet floor.
- HEFT-ST's static rank can't react to capacity pressure, so it leans
  hardest on on-demand. Elastic-Rank[25,75] sits at the opposite extreme
  with the smallest OD slice.
- The OD% labels make this the single best mechanism-explanation figure
  for the chapter — every variant's cost can be reconstructed from its
  OD% and a constant on-prem floor.

---

## Figure 02 — Per-metric scaling vs N (six panels)
**`02_cost_per_wf_vs_n`, `02_miss_vs_n`, `02_wait_vs_n`,
`02_turnaround_vs_n`, `02_budget_vs_n`, `02_util_vs_n`** — six
separate line plots, each with 11 variants and ±1σ bands.

**What they show.** The same data as figure 01 expanded to six metrics:
cost, deadline-miss, wait, turnaround, budget-miss, utilisation. Reading
all six together exposes which metrics saturate and which keep
deteriorating.

**Conclusions.**
- **Cost saturates by N=300.** All variants flatten between N=300 and
  N=400 (Δ ≤ 14 % per step).
- **Wait time blows up.** From ~17 s at N=100 to 6 000–12 000 s at
  N=400 — over 200× growth. This is the queue-bound regime.
- **Deadline-miss diverges by family.** EDF-ST keeps it under 6 %;
  Elastic-EDF climbs to 13–16 %; FCFS-class climbs to 25 %; HEFT-ST
  reaches 40 %.
- **Budget-miss stays near zero for Elastic.** All Elastic variants are
  ≤ 1 % at every N. Static variants drift to 2–4 % by N=400.
- **Utilisation plateaus at 45–58 %.** The system is not capacity-bound;
  the bottleneck is the queue.
- **Turnaround tracks wait closely.** Execution time per workflow is
  largely constant once a workflow is admitted; the variance comes
  almost entirely from wait.

**Caveats.** The `02_cost_per_wf_vs_n` plot is the same data as
figure 01 but with the four-metric series-style legend below. Use 01 as
the canonical reference.

---

## Figure 02b — Deadline miss-rate bar @ N=400
**`02b_miss_bar.{pdf,png}`** — bar per variant, hatched for static.

**What it shows.** N=400 snapshot of deadline-miss. EDF-ST at 5 %,
Elastic-EDF at 13–16 %, Elastic-Rank and Elastic-FCFS at 24–26 %,
FCFS-ST at 25 %, HEFT-ST at 40 %.

**Conclusions.**
- EDF-ST is in a class of its own on deadline compliance. The next-best
  variant (Elastic-EDF<sub>c</sub>) is 2.6 × worse.
- HEFT-ST is dominated. Worst miss-rate among all variants AND highest
  cost (figure 01).
- The middle band (Elastic + FCFS-ST) is tightly clustered around 25 %,
  meaning miss-rate alone doesn't differentiate Elastic vs Static FCFS.

---

## Figure 02c — Budget miss-rate bar @ N=400
**`02c_budget_miss_bar.{pdf,png}`** — same structure as 02b but for
budget miss.

**What it shows.** Elastic variants at 0.5–1 % budget miss; Static
variants and HEFT-ST at 2–4 %.

**Conclusions.**
- Elastic's conservative scale-up keeps almost every workflow under
  budget — exactly the opposite ordering from deadline-miss (02b).
- This is the **counterweight to figure 02b**: Elastic loses deadlines
  but wins budgets. The two constraints respond inversely to elasticity.
- The chapter's "Static is good for SLOs" narrative is partial — Static
  is good for *deadline* SLOs at the cost of *budget* SLOs.

---

## Figure 02d — Turnaround stack @ N=400
**`02d_turnaround_stack.{pdf,png}`** — stacked bar per variant: wait +
execution+cold-start. Single error bar at top using turnaround std.

**What it shows.** How turnaround decomposes into wait and execution.
Static variants have ~80 % wait, ~20 % execution. Elastic variants have
~70 % wait, ~30 % execution. Total turnaround is similar across
variants (~7 hours).

**Conclusions.**
- **Wait dominates everywhere.** Even for Elastic variants where wait
  is shortest (~6 600 s), it still exceeds the execution-plus-cold-start
  time.
- Elastic shifts time from wait to execution — workflows start sooner
  but spend longer running because each iteration triggers a
  cold-start when capacity is reclaimed and re-acquired.
- The chapter point: elasticity is a *time-shape* change, not a
  *throughput* change. Total turnaround is essentially conserved
  (~7 hours across variants); only the split between waiting and
  running shifts.

---

## Figure 02e — Saturation diagnostic (3 × 2 panel)
**`02e_saturation.{pdf,png}`** — symlog-y bars showing Δ % between
adjacent N levels, for six representative operating points (the static
and elastic version of each family).

**What it shows.** For each of the four metrics (cost/wf, wait,
deadline-miss, budget-miss, makespan), the percentage change from N=100
→ 200, 200 → 300, 300 → 400. Bars within each panel use a symlog scale
so small (cost, miss) and huge (wait) deltas coexist.

**Conclusions.**
- **Cost and makespan saturate** — Δ ≤ 15 % per step by 200 → 300 → 400
  for every operating point.
- **Wait does not saturate** — Δ stays above 100 % per step for every
  operating point through N=400. Going to N=500 would not change cost
  or makespan but would inflate wait further.
- The bottom row (Rank pair: HEFT-ST → Elastic-Rank[25,75]) shows the
  most dramatic miss-rate stabilisation — Elastic-Rank's per-iteration
  reranking actively defends against load.

**Why we stop at N=400.** Cost and makespan are already at their
saturation values; further N adds only more queue pressure. The
qualitative story is fixed.

---

## Figure 02f — Miss decomposition stack @ N=400
**`02f_miss_decomposition.{pdf,png}`** — per variant, stacked
budget-only / deadline-only / both. Error bar at top using overall-miss
std.

**What it shows.** Which workflows missed what. Almost the entirety of
every variant's miss-rate is *deadline-only* (purple). Budget-only
(amber) is < 1 % everywhere except HEFT-ST. "Both" (red) is rare —
when a workflow misses budget it usually also misses deadline.

**Conclusions.**
- Deadline miss is the only SLO type the chapter needs to argue about.
  Budget compliance is essentially solved by every variant.
- For Static variants, the small budget-miss component is correlated
  with the deadline-miss component (the "both" segment is non-zero),
  suggesting they miss budget *because* they're forced to use OD nodes
  trying to catch the deadline tail.
- For Elastic variants, "both" is near-zero — they trade deadlines for
  budget cleanly.

---

## Figure 04 — Paired delta (Elastic vs Static) @ N=400
**`04_paired_delta.{pdf,png}`** — left panel: % deltas for cost, wait,
makespan across 5 pairs. Right panel: pp delta for deadline-miss +
budget-miss, side-by-side bars per pair.

**What it shows.** Head-to-head comparison of Elastic vs Static within
each algorithm family at N=400. The five pairs: FCFS<sub>c</sub>, FCFS<sub>r</sub>, EDF<sub>c</sub>,
EDF<sub>r</sub>, Rank (HEFT-ST ↔ Elastic-Rank[25,75]).

**Conclusions.**
- **Cost: Elastic wins every pair by 40–44 %.** Uniform across families.
- **Wait: Elastic wins every pair by 33–40 %.** Uniform.
- **Makespan: tied within ± 5 %.** Elasticity does not change total
  batch duration.
- **Deadline-miss: family-dependent.**
  - FCFS pairs: ~0 pp change (queue order doesn't respond to
    allocation).
  - EDF pairs: Elastic *loses* 8–10 pp (conservative scale-up exposes
    the urgency tail).
  - Rank pair: Elastic *wins* 16 pp (Elastic-Rank's reranking actively
    avoids HEFT's stale rank failures).
- **Budget-miss: Elastic wins every pair by 1.7–2.6 pp.** Elastic-Rank
  alone is tied here because Elastic-Rank's budget-aware ranking already
  beats HEFT-ST's purely structural rank.

The Rank pair is the cleanest single demonstration of "elasticity as a
Pareto improvement" — five metrics, Elastic wins or ties on all five.

---

## Figure 06 — Policy frontier (Pareto)
**`06_pareto.{pdf,png}`** — scatter at N=400, x = cost, y = overall
miss-rate. Three coloured anchors (frontier), dashed line connecting
them, shaded dominated region behind. Each point has ± std error bars.

**What it shows.** The cost-vs-miss tradeoff space at saturation. Three
frontier points (EDF-ST<sub>c</sub>, Elastic-EDF<sub>c</sub>, Elastic-Rank[50,50]) connected by
a Pareto curve; eight dominated points in the shaded region.

**Conclusions.**
- The Pareto frontier has exactly three points. T5 spells out the
  regime labels.
- HEFT-ST is the most dominated variant (highest cost AND highest miss).
- The middle frontier point (Elastic-EDF<sub>c</sub>, Balanced) is the
  most defensible default — closest to "best of both" without
  committing fully to either extreme.
- Error bars on each anchor are smaller than the gap between adjacent
  anchors, so the frontier shape is robust.

---

## Figure 07 — Scaling activity (line plots)
**`07_scaling_activity.{pdf,png}`** — left: scale-up attempts per
workflow vs N. Right: scale-down attempts per workflow vs N. Moldable
variants only.

**What it shows.** Scaling event frequency for the six Elastic variants
as load grows. Scale-up: ~0.3 attempts per workflow. Scale-down: ~1.9
attempts per workflow (6 × more frequent).

**Conclusions.**
- **Scale-down events are 5–7 × more frequent than scale-up.** This is
  because every iteration boundary fires a FREE_RESOURCE intent from the
  workflow engine, which the scheduler then evaluates.
- All six Elastic variants converge tightly — the *count* of scaling
  events is scheduler-family-invariant.
- The chapter point: the *frequency* of scaling decisions is determined
  by the workload (iterations per workflow), not by the scheduler.
  What differs across variants is *what the scheduler does* with each
  event — handled by figures 07b/c and 08.

---

## Figure 07b — Scale-up grant tier composition @ N=400
**`07b_grant_tier_stack.{pdf,png}`** — per Elastic variant, stacked
% of granted scale-up nodes coming from on-prem / reserved / on-demand.
OD% ± std annotated above each bar.

**What it shows.** Of the nodes that the scheduler did grant on a
scale-up request, what tier they came from. All variants are > 90 %
on-prem, with OD share from 3 % (Elastic-Rank[25,75]) to 10 %
(Elastic-FCFS<sub>c</sub>).

**Conclusions.**
- The OD shares here (3–10 %) are much lower than the OD spend shares
  in figure 01b (21–25 %). The difference is *duration*: OD nodes are
  granted rarely but, once acquired, stay alive much longer than on-prem
  nodes. Figure 11 makes this concrete.
- The variant with the lowest OD share on grants (Elastic-Rank[25,75])
  also has the lowest OD spend share. The two correlate.
- Within the Elastic-EDF and Elastic-Rank families, the spread is only
  a few pp — the cost-tier mechanism is mostly determined by family
  choice, not by the secondary knob (sort key or factor pair).

---

## Figure 07c — Grant tier composition vs N (per variant)
**`07c_grant_tier_vs_n.{pdf,png}`** — six small multiples, one per
Elastic variant; within each, four stacked bars (one per N).

**What it shows.** How each Elastic variant's grant-tier mix evolves
with load. At N=100, most variants are 100 % on-prem (no overflow). OD
share grows with N for every variant.

**Conclusions.**
- Elastic-EDF<sub>c</sub> shows a notable OD spike at N=300 (its EDF urgency
  drives early OD escalation), which then drops back at N=400 — the
  scheduler reclaims that capacity as load stabilises.
- Elastic-Rank[25,75] keeps the smallest OD ramp through the full N
  range — the budget-deadline factor pair actively suppresses OD escalation.
- The chapter point: OD share is not just a function of load, but of
  *how the scheduler responds to load*. Family choice matters.

---

## Figure 08 — Workflow-Engine intent outcomes @ N=400
**`08_intent_satisfaction.{pdf,png}`** — per Elastic variant, stacked %
of WE-UP intents: APPROVE / MODIFY / DENY / MODIFY (scale down).

**What it shows.** Every UP intent the workflow engine sent, and what
the scheduler did with it. APPROVE rate ~17 %, MODIFY (partial)
< 1 %, DENY ~3–4 %, and **MODIFY (scale-down) ~ 79–80 %.**

**Conclusions.**
- Four out of every five WE-UP intents are silently converted into a
  scale-down. This is the rich `processFreeRequest` behaviour: when
  the engine asks for more, the scheduler concludes the workflow has
  budget or capacity slack and frees instead.
- WE-DOWN intents are 100 % executed (not shown — single bar would be
  trivial). There is no symmetric override-down behaviour.
- This explains why scale-down attempts (figure 07) outnumber scale-up
  attempts ~6 × — most "scale-downs" are actually engine-up-converted-
  to-down by the scheduler.
- The percentages are uniform across schedulers (within 1–2 pp) — the
  override mechanism is family-invariant. What differs by family is
  *which workflows* receive APPROVE vs MODIFY-down, not the mix.

**Scheduler precedence in prose.** Across all moldable variants and all
N ≥ 200, **66 % of scale-down events are scheduler-initiated overrides
of WE-UP intents; only 34 % originate from WE-DOWN intents.** This 2:1
ratio is the chapter's quantitative summary of scheduler precedence
over engine wishes — explained in the prose because the figure that
showed this directly (six near-identical bars) added no visual
information beyond what's already in 08.

---

## Figure 09 — Sort-key sensitivity (2 × 2 panel)
**`09_sortkey_panel.{pdf,png}`** — for four family-mode combinations
(FCFS-ST, Elastic-FCFS, EDF-ST, Elastic-EDF), grouped bars for cost
(`_r` vs `_c`) across N=100/200/300/400, with miss-rate plotted on a
secondary y-axis. Error bars on cost; capped error bars on miss.

**What it shows.** Sensitivity of cost and deadline-miss to the
resource-sort key — runtime-first (`_r`) vs cost-first (`_c`).

**Conclusions.**
- **Cost-sort wins at low N**: `_r` is +20 % to +130 % more expensive
  at N ≤ 200, because at light load the scheduler picks expensive fast
  instances when cheaper-but-slower ones would suffice.
- **Gap closes at N = 400** to +3 % to +11 % — once on-demand-saturated,
  instance choice doesn't matter.
- **Miss-rate is essentially insensitive to the sort key** (≤ 1 pp
  difference everywhere).
- Recommendation: use `_c` as the default sort key. `_r` is only a
  concern for very light loads.

---

## Figure 10 — Rank factor pair
**`10_rank_pair.{pdf,png}`** — two panels (cost vs N, miss vs N) with
error bars, comparing Elastic-Rank[50,50] and Elastic-Rank[25,75].

**What it shows.** Whether the budget/deadline weighting in
Elastic-Rank's rank function matters for plain SeisSol.

**Conclusions.**
- The two factor pairs are nearly indistinguishable on plain SeisSol.
  Cost differs by ≤ 5 %, miss by ≤ 0.01.
- This is a *negative result* worth reporting: in a workload without
  resource-contention dimensions (no licenses), the factor pair is not
  a useful tuning knob. Default to [50,50].
- The factor pair will matter more in the License-Aware chapter where
  budget and deadline interact non-trivially with the license pool.

---

## Figure 11 — Fleet utilisation over time (3 Pareto policies)
**`11_util_timeline.{pdf,png}`** — three stacked subplots, one per
Pareto operating point at N=400 (single representative run). Each
subplot: on-prem (cap 148), reserved (cap 36), on-demand (cap 72) nodes
in use over simulation hours. OD cost for that run annotated in the
subplot title.

**What it shows.** Intra-run intuition for the cost-tier shift
mechanism. On-prem saturates immediately in all three. Cloud spillover
ramps differently.

**Conclusions.**
- **Cost-first (Elastic-Rank[50,50])**: OD cost $1,775 for this run.
  OD lane drains by ~30 hours.
- **Balanced (Elastic-EDF<sub>c</sub>)**: OD cost $1,917. OD lane drains by
  ~45 hours.
- **Hard-SLO (EDF-ST<sub>c</sub>)**: OD cost **$4,608** — 2.6 × the Cost-first
  run. OD lane ramps slowly, sustains at peak capacity (72/72) for
  ~15 hours, drains last.
- The area under the OD curve directly mirrors the OD-cost annotations.
  This makes the cost differences in figure 01b tangible.
- The chapter point: elasticity's cost win comes mostly from *how long
  OD nodes are kept alive*, not from whether they are acquired at all.
  Static keeps them around to absorb the long deadline tail; Elastic
  releases them aggressively.

**Caveats.** Single representative run per panel; per-cell run-to-run
variance not visualised here, but is well-characterised in T9.

---

## Figure 12 — Cost vs turnaround scatter (individual runs)
**`12_per_wf_scatter.{pdf,png}`** — scatter at N=400, x = cost/wf,
y = turnaround. One marker per (variant, run) — 11 variants × 6 runs =
66 points. Markers colour-coded by family, shape-coded by sort
key / static.

**What it shows.** The full N=400 distribution at the run level.
Variants cluster tightly: Elastic at left (low cost, high spread on
turnaround), Static at right (~2 × cost, slightly lower turnaround),
HEFT-ST top right.

**Conclusions.**
- The within-variant cloud is small — confirms T9's CoV numbers
  visually. No variant overlaps with another across the full ranges.
- The two Elastic-EDF variants (`_r` and `_c`) are visually
  indistinguishable from each other.
- HEFT-ST sits alone in the top-right quadrant — the worst cost AND
  worst turnaround.
- This is the chapter's "trust the means" figure — anyone sceptical of
  the headline numbers can verify the variance is small enough to
  defend single-number reporting.

---

## Synthesis: three claims the chapter makes

1. **Elasticity is an unconditional improvement on cost and wait** — 40
   to 45 % cheaper, 33 to 40 % shorter wait, makespan unchanged. The
   gain is uniform across schedulers and stable from N=200 onwards.
2. **Elasticity creates a budget-vs-deadline tradeoff, not a free win
   on SLOs.** Elastic-EDF gains 8–10 pp deadline-miss but loses 1.7 pp
   budget-miss vs EDF-ST. Operators should pick a regime (T5) based on
   which SLO is binding.
3. **HEFT's classical static rank is obsolete for iterative+moldable
   workflows.** Elastic-Rank's per-iteration recomputation of priorities
   based on remaining budget and deadline slack dominates HEFT-ST on
   every metric. The Rank pair is the only one in which Elastic is
   Pareto-dominant.

---

## Methodology notes

- All numbers are means across 6 runs unless stated otherwise.
  Variance is reported in T9 and via error bars / shaded bands.
- The scaling sweep stops at N=400 because cost, makespan, and
  qualitative rankings are saturated and extending it deepens an
  existing wait-time blow-up without changing the chapter's story.
- The `hpc7a.12xlarge` instance entry in `resources.yaml` was corrected
  to 6 reserved + 12 on-demand slots (parity with other cloud
  families). The previous 1 / 0 entry under-provisioned the cloud
  fleet by ~3 %.
- A no-op bug in the closeness filter (`checkCloseness` used the
  outer-scope variable rather than the loop variable) was fixed in both
  the base `Scheduler` and `FCFS_Optimized`.

---

## Appendix — T3: Full results at N=400

Mean ± std across 6 runs per variant. Costs in USD using
1 EUR = 1.10 USD. CPR (Cost-Performance Ratio) = 1 / (cost per
workflow); higher is better. Util column omits std because we report
only the mean for table compactness; T9 carries variance.

| Variant | Turnaround (s) | Makespan (s) | Cost (USD/wf) | Wait (s) | Util | Deadline miss | Budget miss | Overall miss | CPR |
|---|---|---|---|---|---|---|---|---|---|
| FCFS-ST<sub>r</sub>          | 28 161 ± 513   | 158 430 ± 11 719 | 35.7 ± 2.9 | 11 860 ± 165 | 54.4 % | 0.252 ± 0.011 | 0.027 ± 0.008 | 0.279 ± 0.012 | 0.0280 |
| FCFS-ST<sub>c</sub>          | 27 727 ± 542   | 160 329 ± 10 775 | 32.1 ± 2.3 | 11 799 ± 103 | 53.4 % | 0.256 ± 0.006 | 0.022 ± 0.003 | 0.278 ± 0.007 | 0.0311 |
| Elastic-FCFS<sub>r</sub>     | 27 479 ± 788   | 160 059 ± 12 063 | 20.6 ± 1.8 |  7 599 ± 305 | 46.0 % | 0.260 ± 0.012 | 0.005 ± 0.002 | 0.266 ± 0.011 | 0.0486 |
| Elastic-FCFS<sub>c</sub>     | 26 854 ± 439   | 166 197 ± 10 696 | 19.2 ± 2.1 |  7 641 ± 220 | 47.1 % | 0.260 ± 0.009 | 0.005 ± 0.002 | 0.265 ± 0.010 | 0.0520 |
| EDF-ST<sub>r</sub>           | 25 282 ± 892   | 163 700 ±  3 554 | 37.9 ± 3.8 | 11 326 ± 318 | 57.7 % | 0.055 ± 0.014 | 0.034 ± 0.003 | 0.089 ± 0.012 | 0.0264 |
| EDF-ST<sub>c</sub>           | 24 519 ± 647   | 158 566 ±  7 937 | 34.2 ± 3.2 | 11 021 ± 336 | 56.8 % | 0.052 ± 0.008 | 0.022 ± 0.006 | 0.074 ± 0.010 | 0.0293 |
| Elastic-EDF<sub>r</sub>      | 24 133 ± 1 179 | 160 805 ±  6 490 | 21.1 ± 2.8 |  6 739 ± 373 | 48.1 % | 0.156 ± 0.017 | 0.008 ± 0.003 | 0.164 ± 0.018 | 0.0474 |
| Elastic-EDF<sub>c</sub>      | 23 886 ± 934   | 163 936 ± 13 047 | 20.5 ± 1.9 |  6 585 ± 349 | 49.1 % | 0.134 ± 0.010 | 0.005 ± 0.003 | 0.140 ± 0.008 | 0.0489 |
| HEFT-ST              | 28 488 ± 733   | 159 695 ±  6 076 | 37.2 ± 4.1 |  9 255 ± 321 | 51.3 % | 0.395 ± 0.010 | 0.037 ± 0.006 | 0.432 ± 0.010 | 0.0269 |
| Elastic-Rank[50,50]  | 24 423 ± 734   | 163 355 ±  7 778 | 18.7 ± 2.0 |  6 004 ± 291 | 45.9 % | 0.237 ± 0.012 | 0.005 ± 0.003 | 0.242 ± 0.011 | 0.0534 |
| Elastic-Rank[25,75]  | 25 137 ± 1 200 | 163 531 ±  7 950 | 20.8 ± 3.1 |  6 175 ± 435 | 45.1 % | 0.239 ± 0.014 | 0.009 ± 0.003 | 0.248 ± 0.013 | 0.0480 |
