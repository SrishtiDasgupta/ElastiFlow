# Plot Generation Guide — Thesis Results Chapter

*Roadmap for the figures that go into the moldable-vs-static thesis chapter.
Each entry: what the plot shows, which file feeds it, what numbers to extract,
plot type, and a one-line caption draft.*

## Quick reference — where each datum lives

| data | source file | how to access |
|---|---|---|
| Modelled n=6 per cell (all metrics) | `metrics_n3.py`, `metrics_n5.py`, `metrics_n7.py` | run script → parse stdout, or import + call `run_all()` |
| Modelled per-event trace (single run) | `trace_n3_mold_edf.py` | run script → event log printed |
| Underlying simulator | `sim_4corners_calibrated.py` | exports `simulate(mode, ordering)` |
| Live R3 (N=3 Mold EDF) | `HPO/results/r3_n3_actual/R3_N3_ACTUAL.md` + executor.out files | makespans in MD, per-iter durations in executor logs |
| Live R5 (N=3 Mold FCFS) | per-conversation memory note `r5_n3_actual_2026-05-08.md` | summary numbers; logs not in repo at last check |
| Live R1 (N=5 Mold EDF) | `HPO/results/r1_moldable_edf_5/R1_FINALIZED.md` + dispatcher.log + executor.out | per-wf makespan table in MD, iter trajectories in logs |
| Live FCFS-pair (N=5, two corners) | `HPO/results/5/FCFS_Moldable_HPO_hybrid_results.csv` and `5/FCFS_Static_HPO_5wf_hybrid_results.csv` | CSV per-wf rows; `.out` files have aggregates |
| Live R7 (N=7 Mold EDF) | `HPO/results/r7_n7_edf_mold/` | scheduler.log, dispatcher.log, hpo_logs/*.jsonl, negotiation CSVs |
| 4-corner summary (canonical thesis result) | `HPO/results/SUMMARY_4CORNER.md` | text summary |

## Primary plots (must-have for the chapter)

### Plot 1 — Headline cost-vs-N with error bars

- **What:** OD cost ($) on Y, N on X (3, 5, 7), 4 lines (one per corner), error bars at ±1 stdev.
- **Source:** `metrics_n{3,5,7}.py` → `od_cost_usd` mean ± stdev per corner per N.
- **Type:** line plot with shaded error bands or vertical error bars.
- **Highlight:** annotate the cost-crossover (N≈5 for FCFS, N≈7 for EDF).
- **Caption draft:** *"On-demand cost scaling under each scheduling policy. Moldable starts more expensive at low N (idle clusters favour static's 'wait for full' rule) but converges to or beats static by N=7 as static is forced to spawn OD on every cluster. Bars: ±1 stdev across 6 simulated runs per cell."*

### Plot 2 — Misses-vs-N

- **What:** misses on Y (out of N), N on X, 4 lines + error bars.
- **Source:** `metrics_n{3,5,7}.py` → `misses` mean ± stdev.
- **Type:** line plot, possibly with miss-rate (%) as secondary Y.
- **Highlight:** monotonic divergence of mold vs static lines after N=3.
- **Caption draft:** *"Deadline misses across N. Moldable's lead grows by ~0.5 misses per pair of added workflows, reaching 0.83 (EDF) and 1.17 (FCFS) at N=7."*

### Plot 3 — Sum-flowtime growth

- **What:** sum-flow in minutes, log-y optional.
- **Source:** `sum_flow_s` from metrics scripts.
- **Type:** line plot. Add growth rate annotation (static ×3.5, mold ×2.6).
- **Caption:** *"Sum-flowtime scales near-linearly under static (×3.5 from N=3 to N=7) but sub-linearly under moldable (×2.6) — moldable releases capacity between iterations rather than holding it."*

### Plot 4 — Paired-difference bars (mold − static)

- **What:** Δmisses, Δsum-flow, Δcost as grouped bar chart, x-axis = N (3/5/7), grouped by EDF/FCFS, error bars.
- **Source:** paired-difference section of `metrics_n{3,5,7}.py` output.
- **Highlight:** bars cross zero between N=3 and N=5 for most metrics.
- **Caption:** *"Paired difference of moldable minus static for matched HPO seeds. The crossover from 'mold loses' to 'mold wins' is between N=3 and N=5 for time-based metrics, and at N=5–7 for cost. Paired matching tightens the confidence interval by controlling for HPO seed variance."*

## Secondary plots (recommended)

### Plot 5 — Per-wf P(miss) heatmap

- **What:** rows = wf (data8 ... data1), cols = (N, corner) flattened to 12, cell value = P(miss) over 6 runs.
- **Source:** per-wf `hit` field in each run's `per_wf` dict from metrics scripts.
- **Type:** matplotlib `imshow` with grayscale or RdYlGn colormap. Annotate cells with percentages.
- **Caption:** *"Per-workflow miss probability across 6 simulated runs. data5 is the persistent bottleneck across all configurations; moldable's wins concentrate in data7 (rescued at N=5+) and data1/data12 (rescued at N=7)."*

### Plot 6 — Slack distribution box plot

- **What:** y = per-wf slack in minutes, x = corner, faceted by N.
- **Source:** per-wf `slack_s` from metrics scripts (compute slack as `deadline - makespan`).
- **Type:** box-and-whisker or violin plot.
- **Caption:** *"Slack distribution per scheduling corner. Moldable produces narrower distributions across all N (lower intra-run stdev), suggesting more equitable resource allocation rather than 'winners and losers'."*

### Plot 7 — Cost CV-vs-N (stability)

- **What:** CV (= stdev / mean) of OD cost across 6 runs, vs N.
- **Source:** computed inline from metrics scripts.
- **Caption:** *"Coefficient of variation in OD cost across simulated runs. Moldable converges to CV ≈ 0.13–0.15 by N=7 while static FCFS climbs back to 0.20 — moldable is the more predictable choice for budgeting under load."*

### Plot 8 — Campaign-wallclock saturation

- **What:** campaign wall (minutes) vs N, 4 lines.
- **Source:** `campaign_s` field.
- **Caption:** *"Campaign wall-clock under moldable saturates (+35m from N=3 to N=7) while static keeps growing (+92m). The longest-running workflow caps moldable's wall; static must serialise queue-clearing on top of it."*

## Live-validation plots (appendix)

### Plot A1 — Live R7 actual Gantt vs modelled trace

- **What:** Gantt of 7 wfs on R7's 3 clusters (slurm, g4, g5) with iter boundaries marked.
- **Live source:** `HPO/results/r7_n7_edf_mold/hpo_logs/*.jsonl` for iter timestamps; `negotiation_*.csv` for allocation events.
- **Modelled source:** add event-log printing to `sim_4corners_calibrated.py` and run once with R7's seed equivalents.
- **Type:** broken-horizontal-bar plot per wf per lane.
- **Caption:** *"Live R7 (top) vs modelled R7 (bottom). Live placed data9 on cluster_g4; model places it on cluster_g5. Both finish 4/7 on time; allocation paths differ due to the model's fixed pref_clusters vs the live scheduler's dynamic scoring."*

### Plot A2 — Live R1 vs modelled N=5 dispatch comparison

- **What:** per-wf makespan, two bars per wf (live R1 vs modelled mean ± stdev).
- **Live source:** `HPO/results/r1_moldable_edf_5/R1_FINALIZED.md` outcomes table.
- **Modelled source:** `metrics_n5.py` per-wf makespan table.
- **Caveat to note in caption:** R1 used ×2 deadlines and dispatch `[9,7,5,3,8]` while modelled uses ×3 deadlines and `[8,9,5,7,3]` — bars compare *trajectories* not direct equivalents.

### Plot A3 — Live FCFS pair (N=5) bar comparison

- **What:** per-wf makespan + cost, two bars per wf (Mold FCFS live vs Static FCFS live).
- **Source:** `HPO/results/5/FCFS_Moldable_HPO_hybrid_results.csv` and `5/FCFS_Static_HPO_5wf_hybrid_results.csv`.
- **Highlight:** Static beats Moldable here (1/5 vs 2/5 misses, $3.44 vs $5.42 cost) — the *opposite* of the modelled prediction. Plot serves as honest counter-evidence to discuss in caveats.
- **Caption draft:** *"Live FCFS pair: static outperforms moldable on this workload set (different wfs from the modelled set; deadlines were ×2 in this campaign). Highlights the limits of generalising from a single workload mix."*

## Diagnostic plots (optional, only if reviewers ask)

### Plot D1 — Cluster-mix bar chart per corner per N

- **What:** stacked-bar of OD-hours by cluster (slurm/g4/g5).
- **Source:** `od_hours_by_cluster` dict from metrics scripts.
- **Use:** explains the cost-crossover mechanism (static uses g4 cheaply at low N; both saturate g4 + g5 at high N).

### Plot D2 — Cold-start tax vs N

- **What:** mean cold-start time (sum of 3.2× penalty) per corner per N.
- **Source:** `cold_start_s` field.
- **Use:** quantifies moldable's overhead cost.

### Plot D3 — Robustness scatter (batch 1 vs batch 2 means)

- **What:** scatter, each point = (corner, N, metric), x = batch 1 mean, y = batch 2 mean.
- **Source:** robustness check section of each metrics script.
- **Annotation:** y=x line and ±combined-stdev envelope.
- **Use:** visual confirmation that 35/36 fall on diagonal within ±σ.

## Plot generation workflow

1. **Source-of-truth pass:** for each plot, dump the underlying numbers into a small JSON file (e.g., `metrics_dump_n3.json`) so plots can be regenerated without re-running the sim.
2. **Style:** matplotlib, scienceplots/`style='science'` or seaborn-paper. 1-column width 3.5", 2-column width 7".
3. **Color convention:** Static = solid line, Mold = dashed; EDF = blue, FCFS = orange. Consistent across all figures.
4. **Error bars:** ±1 stdev (n=6 runs). Note in caption.
5. **All plots vector format (PDF or SVG)** for thesis embedding.

## Suggested figure order in the chapter

1. **Setup figure:** topology + dispatch timeline (no data; conceptual).
2. **Headline:** Plot 1 (cost), Plot 2 (misses) side-by-side.
3. **Mechanism:** Plot 4 (paired diff).
4. **Detail:** Plot 5 (per-wf heatmap).
5. **Predictability:** Plot 6 (slack) + Plot 7 (CV) side-by-side.
6. **Live validation:** Plot A1 (R7 Gantt) + Plot A3 (FCFS counter-evidence).
7. **Optional:** Plot 3 (sum-flow), Plot 8 (campaign saturation), Plot D1/D2/D3 if reviewer-requested.

## Numbers I should *not* invent

If a plot needs a number that isn't already in this directory's scripts:
- For live runs, the raw logs are in `HPO/results/r*/` or `HPO/results/5/` — extract, don't approximate.
- For modelled cells, re-run the relevant `metrics_n*.py` rather than guessing from this guide's quoted numbers.
- For per-event traces, regenerate with `trace_n3_mold_edf.py` (and write equivalents for other cells if needed).
