# Move-On Checklist

*From "experiments done" to "thesis written + AWS billing zero". Items are
ordered by dependency — finish each block before starting the next.*

## Block 1 — Save everything from AWS (must do before deleting EFS)

- [ ] 1.1  Start one stopped reserved EC2 instance via AWS console
- [ ] 1.2  SSH in: `ssh -i ~/.ssh/aws/hpo-exp.pem ec2-user@<IP>`
- [ ] 1.3  Confirm /fsx is mounted (`mount | grep fsx`); if not, mount per `SYNC_AND_REBUILD.md` §1.2
- [ ] 1.4  From local: `rsync -avP -e "ssh -i ~/.ssh/aws/hpo-exp.pem" ec2-user@<IP>:/fsx/ ~/Backups/efs_2026-05-11/`
- [ ] 1.5  Verify backup: `du -sh ~/Backups/efs_2026-05-11` (expect ~1-5 GB), `find ~/Backups/efs_2026-05-11 -type f | wc -l`
- [ ] 1.6  Cherry-pick into repo any EFS-only files not in `HPO/results/`:
  - per-iter `hpo_logs/*.jsonl` for R1, R3, R5
  - R3 scheduler log (referenced in `R3_N3_ACTUAL.md` but missing locally)
  - the March-2026 FCFS-pair YAMLs (`hpo-5cf32563` etc., if they exist)
- [ ] 1.7  Stop the EC2 instance (don't terminate — keeps the instance config intact in case you need to resume)
- [ ] 1.8  Dump AWS metadata to `~/Backups/aws_metadata_2026-05-11/` per `SYNC_AND_REBUILD.md` §3 (AMIs, instances, EFS config, security groups, cost report)

## Block 2 — Delete cost-incurring resources

*Account stays open; just zero the billing.*

- [ ] 2.1  Delete EFS: `aws efs delete-mount-target --mount-target-id ...` for all mount targets, then `aws efs delete-file-system --file-system-id fs-0c7ed8d283368b734`
- [ ] 2.2  Terminate all stopped EC2 instances (not just stop — terminate removes attached EBS volumes which charge per GB-month even when stopped)
- [ ] 2.3  Delete any EBS snapshots: `aws ec2 describe-snapshots --owner-ids self --region eu-north-1` → delete what isn't needed
- [ ] 2.4  Delete any unattached EBS volumes: `aws ec2 describe-volumes --filters "Name=status,Values=available" --region eu-north-1` → delete
- [ ] 2.5  Delete custom AMIs you no longer need: `aws ec2 describe-images --owners self --region eu-north-1` → `deregister-image` + delete the underlying snapshot
- [ ] 2.6  Empty + delete any S3 buckets used for this project
- [ ] 2.7  Delete ParallelCluster CloudFormation stacks if any orphans remain: `aws cloudformation list-stacks --region eu-north-1`
- [ ] 2.8  Check AWS Billing console next day → confirm "Free Tier" or near-zero forecast

## Block 3 — Commit & push final state

- [ ] 3.1  `git status` in `Vortex-mid/` — review untracked files
- [ ] 3.2  Add anything important from Block 1.6 and commit: `git commit -m "Pre-pause: sync EFS-only artefacts"`
- [ ] 3.3  Commit the new doc files from this thread:
  - `Vortex-moldable-sched/SYNC_AND_REBUILD.md`
  - `Vortex-moldable-sched/CHECKLIST.md` (this file)
  - `HPO/results/r7_n7_actual_vs_modeled/THESIS_TABLE.md`
  - `HPO/results/r7_n7_actual_vs_modeled/PLOT_GUIDE.md`
  - `HPO/results/r7_n7_actual_vs_modeled/metrics_n{3,5,7}.py`
  - `HPO/results/r7_n7_actual_vs_modeled/trace_n3_mold_edf.py`
- [ ] 3.4  Push: `git push origin hpo`
- [ ] 3.5  Tag a snapshot: `git tag thesis-data-final-2026-05-11 && git push --tags`

## Block 4 — Optional: address the negotiation-latency finding

*The R7 instrumentation showed real `request_handoff_s` median 100s, p95 300s
— ~3× larger than the simulator's `INTER_ITER_OVERHEAD_S = 30s`. If this
matters for thesis defensibility:*

- [ ] 4.1  Edit `sim_4corners_calibrated.py`: change `INTER_ITER_OVERHEAD_S = 30` → `INTER_ITER_OVERHEAD_S = 100`
- [ ] 4.2  Re-run `metrics_n{3,5,7}.py`, save output as `THESIS_TABLE_NEG_CALIBRATED.md`
- [ ] 4.3  Compare: does moldable's advantage shrink, stay, or grow under the higher tax?
- [ ] 4.4  Add a paragraph to the thesis: either "result is robust" or "result is sensitive to inter-iter overhead; we report both bounds"

If skipping this block, add a one-line caveat to the thesis's modelling-limitations section noting the gap.

## Block 5 — Thesis write-up (the actual writing)

Order matches the chapter outline in `PLOT_GUIDE.md` §"Suggested figure order".

- [ ] 5.1  Setup / methodology chapter — topology, dispatch, calibration source (R7)
- [ ] 5.2  Plot 1 (cost-vs-N) + Plot 2 (misses-vs-N) → from `metrics_n*.py` outputs
- [ ] 5.3  Plot 4 (paired-diff) → from same data, paired-diff section
- [ ] 5.4  Plot 5 (per-wf heatmap) → from per-wf `hit` field
- [ ] 5.5  Plot 6 (slack box) + Plot 7 (cost CV) → fairness + predictability section
- [ ] 5.6  Live-validation appendix — narrative around R3, R5, R1, FCFS-pair, R7
- [ ] 5.7  Negotiation latency section/figure (if Block 4 done) — Plot A1 Gantt
- [ ] 5.8  Limitations: no live Static EDF; live-vs-modelled N=3 divergence; INTER_ITER_OVERHEAD calibration gap; HPO sampling extrapolated outside R3-R7 envelope

## Block 6 — Pre-defense sanity

- [ ] 6.1  Re-run `metrics_n{3,5,7}.py` from a clean clone to confirm numbers reproduce bit-exact
- [ ] 6.2  Walk every plot caption back to its source script — no orphan numbers
- [ ] 6.3  Read `THESIS_TABLE.md` + `PLOT_GUIDE.md` end-to-end; flag any inconsistencies
- [ ] 6.4  Spot-check one live run by re-deriving the makespan from its raw logs (e.g., R3 data5 timeline from `R3_N3_ACTUAL.md` cross-checked against the executor.out timestamps)

## Done when

- AWS Billing shows zero recurring charges
- `git push` complete with the `thesis-data-final-2026-05-11` tag
- `~/Backups/efs_2026-05-11/` exists with EFS contents
- All thesis chapters drafted with figures sourced from local files

## What this checklist deliberately skips

- **Reproducing live runs in a fresh AWS account** — not needed; you're keeping the account, just zeroing the bill. The `SYNC_AND_REBUILD.md` Phase 5 stays available if circumstances change.
- **Extending to N=10 modelled or live** — out of scope; the N=10 anchor is single-corner and not in the main table by decision.
- **Running additional negotiation-latency campaigns** — R7's 7-request sample is small but sufficient as a limitation note.
