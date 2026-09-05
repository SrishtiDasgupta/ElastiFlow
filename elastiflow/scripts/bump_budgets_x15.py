"""Apply ×1.5 in-place multiplier to budget field of every HPO data*.yaml.

Symmetric to bump_deadlines_x15.py. Rationale: yaml budgets were generated using
G5-on-demand worst-case sizing (epoch_cost × trials × tinyda × wf_iters × 1.0).
On reserved g4dn or on-prem slurm the actual cost rate is similar per epoch but
mid-run cumulative cost can EXCEED yaml budget, causing the moldable scale-up
budget gate (edf_optimized_HPO.py:571) to deny critical late-iter scale-ups.

R1 modeled analysis (HPO/results/r1_moldable_edf_5/R1_R2_FINALIZED.md cross-effect)
showed data5's iter-3 scale-up could be denied if it lands on slurm at $0.84/h.
×1.5 budget bump gives moldable enough headroom to scale up reliably.

Idempotent guard: refuses to bump if a wf already shows ×1.5-style budget
(heuristic: ratio > 1.3× the original-formula expected value).
"""
import glob
import os
import sys

import yaml

# epoch_cost (G5-on-demand) per model, in $/epoch — from constants_HPO.EPOCH_COST formula
G5_RUNTIMES_12EP = {
    'vgg19': 198.00,
    'wide_resnet101_2': 267.62,
    'convnext_large': 275.45,
}
G5_ON_DEMAND_RATE = 1.006  # $/hr
AVG_PARALLEL_TRIALS = 3
AVG_TINYDA = 20
AVG_WF_ITERS = 4

DIRS = ['elastiflow/workflow/sample_workflows_HPO']

def epoch_cost(model):
    return (G5_RUNTIMES_12EP[model] / 12) / 3600 * G5_ON_DEMAND_RATE

def already_bumped(budget, model):
    base = epoch_cost(model) * AVG_PARALLEL_TRIALS * AVG_TINYDA * AVG_WF_ITERS
    if base <= 0:
        return False
    return (budget / base) >= 1.2   # original ×1.0 ratios are ~0.9–1.1; bumped ×1.5 ~1.4–1.6

def main(repo_root):
    n_total = n_bumped = n_skipped = 0
    for d in DIRS:
        full = os.path.join(repo_root, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(glob.glob(os.path.join(full, 'data*.yaml'))):
            n_total += 1
            with open(f) as fh:
                doc = yaml.safe_load(fh)
            mesh = doc.get('config', {}).get('mesh')
            old = doc.get('constraints', {}).get('budget')
            if mesh is None or old is None or not isinstance(mesh, str) or mesh not in G5_RUNTIMES_12EP:
                print(f"  SKIP {os.path.basename(f)}: not an HPO yaml (mesh={mesh!r})")
                n_skipped += 1
                continue
            if already_bumped(old, mesh):
                print(f"  SKIP {os.path.basename(f)}: budget {old:.2f} already bumped")
                n_skipped += 1
                continue
            new = round(old * 1.5, 2)
            doc['constraints']['budget'] = new
            with open(f, 'w') as fh:
                yaml.dump(doc, fh, default_flow_style=False, sort_keys=False)
            print(f"  BUMP {os.path.basename(f):14} {mesh:18} {old:8.2f} → {new:8.2f}")
            n_bumped += 1
    print(f"\nDone. {n_bumped} bumped, {n_skipped} skipped, {n_total} total.")

if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched")
    main(repo)
