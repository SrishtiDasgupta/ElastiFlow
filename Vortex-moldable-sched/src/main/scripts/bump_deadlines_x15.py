"""Apply ×1.5 in-place multiplier to deadline field of every data*.yaml.

Rationale: existing YAMLs were generated under deadline = epoch × tinyda × wf_iters × 2
(the original ×2 contention factor). After R1 (2026-05-06), ×2 proved too tight: 3/5 wfs
missed deadline by margins as small as 55s, washing out the moldable-vs-static signal.

Constants formula was bumped to ×3 (this session, 2026-05-08), but the change does NOT
auto-regenerate YAMLs — the scheduler reads deadlines from YAML at runtime.

This script applies the ×1.5 multiplier in-place, preserving all other yaml fields
(chains, tinyda, budget, model, etc.) so R1↔R2↔R3↔R4 stay comparable on a per-wf basis.

Run once before R3 launch. Idempotent guard: refuses to bump if a wf already shows
×3-style deadline (heuristic: deadline > 1.4 × the ×2 expected value).
"""
import glob
import os
import sys

import yaml

EPOCH_RUNTIME = {
    'vgg19': 22.17,
    'wide_resnet101_2': 29.66,
    'convnext_large': 34.03,
}

DIRS = [
    'src/main/workflow/sample_workflows_HPO',
]

def already_x3(deadline, model):
    """Idempotency check. Generator uses fixed AVG values (not per-yaml):
        deadline = epoch × AVG_TINYDA(20) × AVG_WF_ITERS(4) × C  + Gaussian σ=10%
    so original ×2 yamls have ratio 1.8–2.2 and ×3 have 2.7–3.3. Threshold 2.4 splits."""
    base = EPOCH_RUNTIME[model] * 20 * 4   # the AVG_* values used at generation time
    if base <= 0:
        return False
    return (deadline / base) >= 2.4

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
            old = doc.get('constraints', {}).get('deadline')
            if mesh is None or old is None or not isinstance(mesh, str) or mesh not in EPOCH_RUNTIME:
                print(f"  SKIP {os.path.basename(f)}: not an HPO yaml (mesh={mesh!r})")
                n_skipped += 1
                continue
            if already_x3(old, mesh):
                print(f"  SKIP {os.path.basename(f)}: deadline {old:.2f} already at ×3 level")
                n_skipped += 1
                continue
            new = round(old * 1.5, 2)
            doc['constraints']['deadline'] = new
            with open(f, 'w') as fh:
                yaml.dump(doc, fh, default_flow_style=False, sort_keys=False)
            print(f"  BUMP {os.path.basename(f):14} {mesh:18} {old:8.2f} → {new:8.2f}")
            n_bumped += 1
    print(f"\nDone. {n_bumped} bumped, {n_skipped} skipped, {n_total} total.")

if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched")
    main(repo)
