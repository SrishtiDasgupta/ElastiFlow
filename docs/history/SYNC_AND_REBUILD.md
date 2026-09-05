# AWS Sync & Rebuild Plan

*Goal: ensure everything needed to (a) write the thesis without re-running
experiments and (b) recreate the entire experimental setup from scratch on
a future AWS account is captured locally and in git.*

## Phase 1 — Pre-close sync from EFS to local

EFS `fs-0c7ed8d283368b734` (eu-north-1) is currently the only running AWS
resource. Everything on it must be mirrored locally before the account is
closed.

### Step 1.1 — Start one reserved instance to access EFS

```bash
# From local machine. Pick any of the 4 reserved instances (they all mount /fsx).
aws ec2 describe-instances \
  --region eu-north-1 \
  --filters "Name=instance-state-name,Values=stopped" \
            "Name=tag:Name,Values=hpo-reserved*" \
  --query 'Reservations[*].Instances[*].[InstanceId,Tags[?Key==`Name`].Value|[0]]' \
  --output table

# Pick one ID and start it:
aws ec2 start-instances --instance-ids i-XXXXXXXX --region eu-north-1

# Wait ~30s, then get public IP:
aws ec2 describe-instances --instance-ids i-XXXXXXXX --region eu-north-1 \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

### Step 1.2 — SSH in and verify EFS mount

```bash
ssh -i ~/.ssh/aws/hpo-exp.pem ec2-user@<PUBLIC_IP>
mount | grep fsx
# expect: fs-0c7ed8d283368b734.efs.eu-north-1.amazonaws.com:/ on /fsx type nfs4

# If not mounted, mount it:
sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576 \
  fs-0c7ed8d283368b734.efs.eu-north-1.amazonaws.com:/ /fsx

# Inventory:
du -sh /fsx/* | sort -h
find /fsx -type f -newer /fsx/setup.sh -mtime -90 | head -50  # recent files
```

### Step 1.3 — Rsync everything to local

Back on local machine:

```bash
BACKUP_DIR=~/Backups/efs_2026-05-11
mkdir -p "$BACKUP_DIR"

rsync -avP --stats \
  -e "ssh -i ~/.ssh/aws/hpo-exp.pem" \
  ec2-user@<PUBLIC_IP>:/fsx/ "$BACKUP_DIR/"
```

Expect ~1-5 GB depending on accumulated `hpo_logs/`. Verify after:

```bash
du -sh "$BACKUP_DIR"
ls "$BACKUP_DIR" | head -20
```

### Step 1.4 — Cherry-pick anything genuinely new into the repo

Compare EFS backup against current local `Vortex-moldable-sched/fsx/`:

```bash
diff -rq ~/Backups/efs_2026-05-11/Vortex-mid/Vortex-moldable-sched/fsx/ \
        Vortex-moldable-sched/fsx/ | head -30

# For files unique to EFS, copy them in:
rsync -av --ignore-existing \
  ~/Backups/efs_2026-05-11/Vortex-mid/Vortex-moldable-sched/fsx/ \
  Vortex-moldable-sched/fsx/
```

Also check for HPO trial outputs that aren't yet in `HPO/results/`:

```bash
# Per-iter trial logs for R1, R3, R5 may only exist on EFS:
ls ~/Backups/efs_2026-05-11/hpo_logs/ 2>/dev/null

# If yes, copy into the matching results dir:
cp ~/Backups/efs_2026-05-11/hpo_logs/hpo-df19b440_*.jsonl \
   Vortex-moldable-sched/HPO/results/r3_n3_actual/hpo_logs/
# (similar for R1, R5)
```

### Step 1.5 — Look for the FCFS-pair YAMLs

The `hpo-5cf32563`, `hpo-23bd6b42`, `hpo-a82342ac`, `hpo-375a9a34`, `hpo-e10d65ba` workflow IDs from the March 2026 campaign were never found locally. Check the EFS backup:

```bash
find ~/Backups/efs_2026-05-11 -name "*5cf32563*" -o -name "*23bd6b42*" 2>/dev/null
```

If found, copy into `HPO/results/5/yamls_used/` so the campaign is reproducible.

### Step 1.6 — Stop the instance and confirm

```bash
aws ec2 stop-instances --instance-ids i-XXXXXXXX --region eu-north-1
```

Verify backup integrity by listing top-level contents and file count:

```bash
find ~/Backups/efs_2026-05-11 -type f | wc -l
du -sh ~/Backups/efs_2026-05-11
```

## Phase 2 — Commit any newly-discovered files

```bash
cd /Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid
git status
# Review additions. Likely candidates:
#   - HPO/results/*/hpo_logs/*.jsonl  (per-trial training logs from past runs)
#   - HPO/results/5/yamls_used/        (if found)
#   - Vortex-moldable-sched/fsx/*       (anything new on EFS)
# Exclude: caches, .pyc, very large .out files we don't need

git add -p   # selectively stage
git commit -m "Pre-AWS-close: sync EFS-only artefacts into repo"
git push origin hpo
```

## Phase 3 — Capture AWS-side metadata that isn't on EFS

Before closing, dump these to local plain-text files for documentation:

```bash
mkdir -p ~/Backups/aws_metadata_2026-05-11
cd ~/Backups/aws_metadata_2026-05-11

# Custom AMIs (if any were built for HPO):
aws ec2 describe-images --owners self --region eu-north-1 > amis.json

# Instance types used in the campaign:
aws ec2 describe-instances --region eu-north-1 \
  --query 'Reservations[*].Instances[*].[InstanceId,InstanceType,Tags[?Key==`Name`].Value|[0],State.Name]' \
  --output table > instances.txt

# EFS configuration:
aws efs describe-file-systems --region eu-north-1 > efs.json
aws efs describe-mount-targets \
  --file-system-id fs-0c7ed8d283368b734 --region eu-north-1 > efs_mounts.json

# Security groups + VPCs:
aws ec2 describe-vpcs --region eu-north-1 > vpcs.json
aws ec2 describe-security-groups --region eu-north-1 > security_groups.json
aws ec2 describe-subnets --region eu-north-1 > subnets.json

# Cost report (optional, for thesis cost analysis):
# Use AWS Cost Explorer console → export CSV for Mar 2026 – May 2026.
# Save to: ~/Backups/aws_metadata_2026-05-11/cost_report.csv

# S3 buckets (if any):
aws s3 ls > s3_buckets.txt
```

## Phase 4 — Rebuild-readiness checklist

Before closing the account, verify that **a future AWS account would let you
fully reproduce the experiment** by walking through this list:

### Code & config
- [ ] `git log --oneline -10` shows the latest scheduler fixes (must include `166cff7` silent-submit-drop fix)
- [ ] `IaC_scripts/hpo-cluster-minimal.yml` exists and references correct instance types
- [ ] `IaC_scripts/CLUSTER_SETUP_GUIDE.txt` walks through phase-by-phase cluster bring-up
- [ ] All setup scripts present: `slurm_head_setup.sh`, `slurm_compute_setup.sh`, `reserved_instance_setup.sh`, `on_demand_setup_HPO_executor.sh`, `on_demand_setup_HPO_worker.sh`

### Data
- [ ] 15 HPO workflow YAMLs in `src/main/workflow/sample_workflows_HPO/data{0..14}.yaml`
- [ ] All live run logs in `HPO/results/r{1,3,5,7}*/`
- [ ] FCFS-pair data in `HPO/results/5/` and `HPO/results/10/`
- [ ] Per-iter `hpo_logs/*.jsonl` for at least R7 (R3/R5 nice-to-have)
- [ ] EFS backup at `~/Backups/efs_2026-05-11/` complete (file count > 100)

### Reproducibility metadata
- [ ] AWS region + AMI IDs recorded in `IaC_scripts/hpo-cluster-minimal.yml`
- [ ] EFS filesystem ID `fs-0c7ed8d283368b734` documented (will be irrelevant
      after close — the new account creates a new EFS — but useful for
      cross-referencing old paths)
- [ ] SSH key `hpo-exp.pem` available (note: the .pem is a credential for the
      *current* AWS account and won't work in a future account; new key pair
      must be generated then)
- [ ] AWS service quota recorded: 56 G-instance vCPU quota (needs request in
      new account before scaling)

### Documentation
- [ ] `THESIS_TABLE.md` exists with all 12 cells filled
- [ ] `PLOT_GUIDE.md` exists with each plot's data source
- [ ] `SUMMARY_4CORNER.md` reflects final numbers (or is superseded by `THESIS_TABLE.md`)
- [ ] This file (`SYNC_AND_REBUILD.md`) committed to git

## Phase 5 — Fresh-account rebuild procedure (future use)

If you ever want to redo the experiment in a fresh AWS account:

1. **Request vCPU quota.** Service Quotas → EC2 → "Running On-Demand G instances" → request 56 vCPUs in eu-north-1. Allow 1-3 business days for approval.

2. **Generate SSH key.**
   ```bash
   aws ec2 create-key-pair --key-name hpo-exp --region eu-north-1 \
     --query 'KeyMaterial' --output text > ~/.ssh/aws/hpo-exp.pem
   chmod 400 ~/.ssh/aws/hpo-exp.pem
   ```

3. **Create EFS.**
   ```bash
   aws efs create-file-system --region eu-north-1 --tags Key=Name,Value=hpo-efs
   # Note the new fs-XXXXXXXX ID and create mount targets in each AZ.
   # Update IaC_scripts/hpo-cluster-minimal.yml with the new EFS ID.
   ```

4. **Restore EFS contents.**
   - Start a minimal EC2 (`t3.micro`), mount the new EFS, rsync `~/Backups/efs_2026-05-11/` up to it.
   - Stop / terminate the t3.micro once rsync completes.

5. **Create ParallelCluster.** Follow `IaC_scripts/CLUSTER_SETUP_GUIDE.txt`
   verbatim. New AMI IDs may be needed in `hpo-cluster-minimal.yml` since the
   originals belong to the old account.

6. **Bring up reserved instances** using `IaC_scripts/main.tf` (Terraform) and
   `src/main/scripts/reserved_instance_setup.sh`.

7. **Verify by running a smoke test:**
   ```bash
   cd /fsx/Vortex-mid/Vortex-moldable-sched
   python src/main/main.py --scheduler edf --moldable &
   # Submit one workflow, confirm executor picks it up.
   ```

8. **Run experiments.** All scripts (`dispatcher_HPO.py`, `simulate_main_HPO.py`)
   work unchanged. Live runs should reproduce the modelled trends.

## When to actually close the account

After completing Phases 1–4 and confirming the rebuild-readiness checklist is
100 % green:

1. Delete EFS (`aws efs delete-file-system --file-system-id fs-0c7ed8d283368b734 --region eu-north-1`)
   — **only after** verifying the local backup is intact.
2. Terminate any remaining stopped EC2 instances.
3. Empty/delete any S3 buckets.
4. Close the account through AWS Billing console.

## Files referenced by this plan

- `IaC_scripts/hpo-cluster-minimal.yml` — ParallelCluster spec
- `IaC_scripts/CLUSTER_SETUP_GUIDE.txt` — phase-by-phase rebuild
- `IaC_scripts/main.tf` — Terraform for reserved instances
- `src/main/scripts/reserved_instance_setup.sh` — node bootstrap
- `src/main/scripts/on_demand_setup_HPO_*.sh` — OD spawn bootstrap
- `fsx/hyperparameter_test/` — HPO trial scripts (Ray Tune driver)
- `HPO/results/` — all live + modelled outputs
- `HPO/results/r7_n7_actual_vs_modeled/THESIS_TABLE.md` — final results
- `HPO/results/r7_n7_actual_vs_modeled/PLOT_GUIDE.md` — figure-generation guide
