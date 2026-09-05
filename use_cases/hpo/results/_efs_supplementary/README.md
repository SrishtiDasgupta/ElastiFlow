# EFS Supplementary Logs

Run-supporting artefacts pulled from `/fsx` on 2026-05-11 before AWS teardown.
None are required for the thesis main table — those live in `HPO/results/r*/`
and `HPO/results/r7_n7_actual_vs_modeled/`. These are kept for debugging or
post-hoc analysis.

## Contents

| dir | size | content |
|---|---|---|
| `slurm_res/` | 20 MB | per-slurm-job `.err` / `.out` files for HPO dispatcher invocations; one pair per `(wf_id, job_id)`. Useful to inspect Ray Tune trial-level stderr. |
| `ondemand_logs/` | 2.6 MB | per-OD-instance setup logs, copied by the trap added to `on_demand_setup_HPO_worker.sh`. Each subdir = one OD instance lifecycle. |
| `r2_archive/` | 80 KB | R2 archive (campaign 2026-05-05): `sched_moldable_edf.log`, executor.out, `workflow_status.log`. Pre-R3 attempt; superseded by R3 + later runs. |

## Not in git (kept only in `~/Backups/efs_2026-05-11/`)

| dir on EFS | size | why skipped |
|---|---|---|
| `cifar10/` | 176 MB | Standard CIFAR-10 dataset; re-downloadable from `https://www.cs.toronto.edu/~kriz/cifar.html` |
| `ray_results/` | 92 MB | Historical Ray Tune campaigns from Feb-Mar 2026 (pre-thesis exploration); not referenced in any results writeup |
| `_train_metrics_*.json` at EFS root | 3.4 MB / 875 files | Ray Tune per-trial training-metric dumps without run-grouping; superseded by `hpo_logs/*_results.jsonl` |
| `Vortex-mid/` (EFS-side git checkout) | 39 MB | Mirror of this repo; the only divergence (5 bug-fixed files) was already cherry-picked into commit `aac724c` |

If you need any of the above, restore from `~/Backups/efs_2026-05-11/<path>`.
