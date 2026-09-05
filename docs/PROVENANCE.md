# Figure provenance

Every figure in the dissertation that comes from data maps to one committed
dataset, one generator script, and one committed output file. This table was
produced by comparing the submitted image tree (`images/<chapter>/...` of the
thesis sources, tag `thesis-submitted-2026-09-04`) with the generator output
committed in this repository, byte for byte and, where the bytes differ, by the
rendered text of the PDF (`gs -sDEVICE=txtwrite`). "identical text (PDF metadata
differs)" means the drawn content is the same and only the PDF creation
timestamp differs. Phase C of `docs/REORGANISATION.md` turns this table into a
manifest with a sync script.

Pairing: `9.chapter/1.naive-seissol` ↔ `use_cases/seissol/results/plots`,
`9.chapter/2.hpo` ↔ `use_cases/hpo/results/plots`, `9.chapter/3.license-aware` ↔
`use_cases/licence/results/plots`; Chapter 8 figures are named individually;
Chapter 7 figures are hand-renamed copies of `thesis_fig*.png` from
`use_cases/seissol/validation/`; the Chapter 5 urgency schedule is drawn by
`urgency_weights.py`. The remaining Chapter 5 figures and Fig. 7.1 are drawn by
hand (draw.io, TikZ) and have no generator.

## Summary

* Ch5 identical: 1
* Ch7 hand-renamed copy; generator output use_cases/seissol/validation/modelled/figures/thesis/thesis_fig3_mape_context.png: 1
* Ch7 identical: 2
* Ch8 identical: 6
* Ch9 CONTENT DIFFERS: 4
* Ch9 identical: 41
* Ch9 identical text (PDF metadata differs): 7

## Figures whose committed output differs in content from the thesis copy

All four are in the HPO campaign and all were understood before this
reorganisation began:

* `04_paired_diff.pdf`: the thesis copy is the six-metric paired
  elastic-versus-static chart that its caption describes, produced by
  `regen_paired_n7.py` from the committed `total_cost_per_run.json` (reproduced
  token for token). The committed file is a superseded N=3/5/7 panel written
  later by `generate_thesis_plots.py` (module level) and
  `regen_corrected_figs.py`, both of which still target the same filename.
  Three writers, one name; the thesis is right and the repository file is stale.
* `02g_utilization_time.pdf`: legend text only; the committed output reflects
  the on-demand utilisation accounting fix of 2026-09-04, which the thesis
  figure predates (documented in the commit that made the fix).
* `02b_misses_bar.pdf`, `03_sumflow_vs_n.pdf`: six differing tokens each, a
  policy-name subscript in the axis labels; the drawn data are the same.

`mape.png` (Chapter 7) differs from the committed
`thesis_fig3_mape_context.png`; the other two Chapter 7 copies are byte-identical
renames. The four HPO figures with more than one writer script are the Phase C
work item.

## Table

| Ch | thesis image | committed generator output | generator | reads | status |
|---|---|---|---|---|---|
| 9 | 1.naive-seissol/01_cost_vs_n.pdf | use_cases/seissol/results/plots/01_cost_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/01b_cost_stack.pdf | use_cases/seissol/results/plots/01b_cost_stack.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02_budget_vs_n.pdf | use_cases/seissol/results/plots/02_budget_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02_cost_per_wf_vs_n.pdf | use_cases/seissol/results/plots/02_cost_per_wf_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02_miss_vs_n.pdf | use_cases/seissol/results/plots/02_miss_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02_turnaround_vs_n.pdf | use_cases/seissol/results/plots/02_turnaround_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02_util_vs_n.pdf | use_cases/seissol/results/plots/02_util_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02_wait_vs_n.pdf | use_cases/seissol/results/plots/02_wait_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02b_miss_bar.pdf | use_cases/seissol/results/plots/02b_miss_bar.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02c_budget_miss_bar.pdf | use_cases/seissol/results/plots/02c_budget_miss_bar.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02d_turnaround_stack.pdf | use_cases/seissol/results/plots/02d_turnaround_stack.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02e_saturation.pdf | use_cases/seissol/results/plots/02e_saturation.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02f_miss_decomposition.pdf | use_cases/seissol/results/plots/02f_miss_decomposition.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/02g_cpr_bar.pdf | use_cases/seissol/results/plots/02g_cpr_bar.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/04_paired_delta.pdf | use_cases/seissol/results/plots/04_paired_delta.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/06_pareto.pdf | use_cases/seissol/results/plots/06_pareto.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/07_scaling_activity.pdf | use_cases/seissol/results/plots/07_scaling_activity.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/07b_grant_tier_stack.pdf | use_cases/seissol/results/plots/07b_grant_tier_stack.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/07c_grant_tier_vs_n.pdf | use_cases/seissol/results/plots/07c_grant_tier_vs_n.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/08_intent_satisfaction.pdf | use_cases/seissol/results/plots/08_intent_satisfaction.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/09_sortkey_panel.pdf | use_cases/seissol/results/plots/09_sortkey_panel.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/10_rank_pair.pdf | use_cases/seissol/results/plots/10_rank_pair.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/11_util_timeline.pdf | use_cases/seissol/results/plots/11_util_timeline.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 1.naive-seissol/12_per_wf_scatter.pdf | use_cases/seissol/results/plots/12_per_wf_scatter.pdf | generate_plain_plots.py | plain_results_per_run.json | identical |
| 9 | 2.hpo/01_cost_vs_n.pdf | use_cases/hpo/results/plots/01_cost_vs_n.pdf | generate_thesis_plots.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/01b_cost_stack.pdf | use_cases/hpo/results/plots/01b_cost_stack.pdf | generate_thesis_plots.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/02_misses_vs_n.pdf | use_cases/hpo/results/plots/02_misses_vs_n.pdf | generate_thesis_plots.py, regen_misses_n.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/02b_misses_bar.pdf | use_cases/hpo/results/plots/02b_misses_bar.pdf | generate_thesis_plots.py | total_cost_per_run.json | CONTENT DIFFERS |
| 9 | 2.hpo/02c_budget_misses_bar.pdf | use_cases/hpo/results/plots/02c_budget_misses_bar.pdf | generate_thesis_plots.py, regen_corrected_figs.py | plain_results_per_run.json, total_cost_per_run.json | identical |
| 9 | 2.hpo/02d_turnaround_stack.pdf | use_cases/hpo/results/plots/02d_turnaround_stack.pdf | generate_thesis_plots.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/02e_makespan_bar.pdf | use_cases/hpo/results/plots/02e_makespan_bar.pdf | generate_thesis_plots.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/02g_utilization_time.pdf | use_cases/hpo/results/plots/02g_utilization_time.pdf | generate_thesis_plots.py, regen_corrected_figs.py | plain_results_per_run.json, total_cost_per_run.json | CONTENT DIFFERS |
| 9 | 2.hpo/02h_tier_zoom_elastic_edf_n7.pdf | use_cases/hpo/results/plots/02h_tier_zoom_elastic_edf_n7.pdf | generate_thesis_plots.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/03_sumflow_vs_n.pdf | use_cases/hpo/results/plots/03_sumflow_vs_n.pdf | generate_thesis_plots.py | total_cost_per_run.json | CONTENT DIFFERS |
| 9 | 2.hpo/04_paired_diff.pdf | use_cases/hpo/results/plots/04_paired_diff.pdf | generate_thesis_plots.py, regen_corrected_figs.py, regen_paired_n7.py | plain_results_per_run.json, total_cost_per_run.json | CONTENT DIFFERS |
| 9 | 2.hpo/05_per_wf_n7.pdf | use_cases/hpo/results/plots/05_per_wf_n7.pdf | generate_thesis_plots.py | total_cost_per_run.json | identical text (PDF metadata differs) |
| 9 | 2.hpo/HPO_06_pareto_trajectory.pdf | use_cases/hpo/results/plots/HPO_06_pareto_trajectory.pdf | generate_hpo_insights.py | negotiation_merged.csv, negotiation_scheduler.csv, plain_results_per_run.json, total_cost_per_run.json | identical |
| 9 | 2.hpo/HPO_08_intent_satisfaction_n7.pdf | use_cases/hpo/results/plots/HPO_08_intent_satisfaction_n7.pdf | generate_hpo_insights.py | negotiation_merged.csv, negotiation_scheduler.csv, plain_results_per_run.json, total_cost_per_run.json | identical |
| 9 | 3.license-aware/HSM_PERPOOL_vs_LAMF_vsN.pdf | use_cases/licence/results/plots/HSM_PERPOOL_vs_LAMF_vsN.pdf | fig_hsm_vs_lamf_vsN.py | canonical_results.json, hsm_perpool_sweep.json, hsm_rho_sweep.json | identical |
| 9 | 3.license-aware/HSM_vs_LAMF_vsN.pdf | use_cases/licence/results/plots/HSM_vs_LAMF_vsN.pdf | fig_hsm_vs_lamf_vsN.py | canonical_results.json, hsm_perpool_sweep.json, hsm_rho_sweep.json | identical |
| 9 | 3.license-aware/LA_01_cost_structure.pdf | use_cases/licence/results/plots/LA_01_cost_structure.pdf | fig_la_cost_structure.py | canonical_results.json, data_la_cost_structure.json | identical |
| 9 | 3.license-aware/LA_02_overhead_vs_n.pdf | use_cases/licence/results/plots/LA_02_overhead_vs_n.pdf | fig_la_vs_n.py | canonical_results.json, data_la_vs_n.json | identical |
| 9 | 3.license-aware/LA_03_lic_per_done_vs_n.pdf | use_cases/licence/results/plots/LA_03_lic_per_done_vs_n.pdf | fig_la_vs_n.py | canonical_results.json, data_la_vs_n.json | identical |
| 9 | 3.license-aware/LA_04_eff_util_vs_n.pdf | use_cases/licence/results/plots/LA_04_eff_util_vs_n.pdf | fig_la_efficiency.py | canonical_results.json, data_la_efficiency.json | identical |
| 9 | 3.license-aware/LA_05_waste_usd_vs_n.pdf | use_cases/licence/results/plots/LA_05_waste_usd_vs_n.pdf | fig_la_efficiency.py | canonical_results.json, data_la_efficiency.json | identical |
| 9 | 3.license-aware/LA_06_tokensec_per_done_vs_n.pdf | use_cases/licence/results/plots/LA_06_tokensec_per_done_vs_n.pdf | fig_la_efficiency.py | canonical_results.json, data_la_efficiency.json | identical |
| 9 | 3.license-aware/LA_07_completion_by_solver.pdf | use_cases/licence/results/plots/LA_07_completion_by_solver.pdf | fig_la_solver_pool.py | canonical_results.json, data_la_solver_pool.json | identical |
| 9 | 3.license-aware/LA_08_pool_util.pdf | use_cases/licence/results/plots/LA_08_pool_util.pdf | fig_la_solver_pool.py | canonical_results.json, data_la_solver_pool.json | identical |
| 9 | 3.license-aware/LA_08b_lic_share.pdf | use_cases/licence/results/plots/LA_08b_lic_share.pdf | fig_la_solver_pool.py | canonical_results.json, data_la_solver_pool.json | identical |
| 9 | 3.license-aware/LA_boundary.pdf | use_cases/licence/results/plots/LA_boundary.pdf | fig_la_boundary.py | data_la_boundary.json | identical |
| 9 | 3.license-aware/LA_scarcity.pdf | use_cases/licence/results/plots/LA_scarcity.pdf | fig_la_scarcity.py | data_la_scarcity.json | identical |
| 9 | 3.license-aware/XWORKLOAD_elastic_vs_static.pdf | use_cases/licence/results/plots/XWORKLOAD_elastic_vs_static.pdf | fig_xworkload_elastic_vs_static.py | canonical_results.json, plain_results_per_run.json, total_cost_per_run.json | identical |
| 8 | speedup.pdf | elastiflow/plots/speedup.pdf | speedup_plot.py |  | identical |
| 8 | speedup_HPO.pdf | elastiflow/plots/speedup_HPO.pdf | speedup_plot_HPO.py |  | identical |
| 8 | submit_times.pdf | elastiflow/plots/submit_times.pdf | plot_submit_times.py |  | identical |
| 8 | KSENS_chains_per_node.pdf | use_cases/seissol/results/plots/KSENS_chains_per_node.pdf | fig_k_sensitivity.py | k_sensitivity_snapshot.json, k_sweep_per_run.json | identical |
| 8 | RHOSENS_hsm_gate.pdf | use_cases/licence/results/plots/RHOSENS_hsm_gate.pdf | fig_hsm_rho.py | canonical_results.json, hsm_rho_snapshot.json, hsm_rho_sweep.json | identical |
| 8 | PRELSENS_partial_release.pdf | use_cases/licence/results/plots/PRELSENS_partial_release.pdf | fig_partial_release_sensitivity.py | partial_release_snapshot.json, partial_release_sweep.json | identical |
| 7 | mape.png | use_cases/seissol/validation/modelled/figures/thesis/thesis_fig3_mape_context.png | make_figures_thesis.py | modelled/*.csv, out/*.csv (parse_logs.py from an external raw-log directory) | hand-renamed copy; generator output use_cases/seissol/validation/modelled/figures/thesis/thesis_fig3_mape_context.png |
| 7 | cost-fidelity.png | use_cases/seissol/validation/modelled/figures/thesis/thesis_fig4_cost.png | make_figures_thesis.py | modelled/*.csv, out/*.csv (parse_logs.py from an external raw-log directory) | identical |
| 7 | sim_infra_gantt.png.png | use_cases/seissol/validation/modelled/figures/thesis/thesis_fig1_gantt.png | make_figures_thesis.py | modelled/*.csv, out/*.csv (parse_logs.py from an external raw-log directory) | identical |
| 5 | urgency_weight_schedule.pdf | use_cases/seissol/validation/urgency_weight_schedule.pdf | urgency_weights.py |  | identical |
