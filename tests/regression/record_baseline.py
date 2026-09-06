"""
Record the behaviour of every simulated policy at one (N, seed) each.

Writes tests/regression/baseline_all_policies.json: every parsed metric of the
11 SeisSol--TinyDA variants (N=100, seed 7), the 4 uncited SeisSol policies
(same N and seed) and the 5 licence policies (N=150, seed 7), produced by the cell runners in conftest.py, which are the ones the
regression tests and the sweep drivers use. tests/smoke compares the current
tree against this file exactly.

Run only when the reference is meant to move, and say so in the commit:

    vortex_venv/bin/python3 tests/regression/record_baseline.py

The tree it is recorded from must itself pass tests/regression, so that the
baseline is the behaviour of the tagged datasets for every policy.
"""
import json
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import (LICENCE_N, LICENCE_POLICIES, LICENCE_SEED, REPO, SEISSOL_N,  # noqa: E402
                      SEISSOL_SEED, UNCITED_SEISSOL_VARIANTS, licence_cell, seissol_cell,
                      seissol_variants, strip_bookkeeping)

OUT = Path(__file__).resolve().parent / 'baseline_all_policies.json'


def main() -> None:
    python = sys.executable
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    cells = {}
    with tempfile.TemporaryDirectory() as tmp:
        for variant in sorted(seissol_variants()) + sorted(UNCITED_SEISSOL_VARIANTS):
            t0 = time.time()
            cells[f'seissol/{variant}'] = strip_bookkeeping(seissol_cell(python, variant, Path(tmp) / 'seissol' / variant))
            print(f'  seissol/{variant:22s} {time.time() - t0:5.1f}s', flush=True)
        for policy in LICENCE_POLICIES:
            t0 = time.time()
            cells[f'licence/{policy}'] = strip_bookkeeping(licence_cell(python, policy, Path(tmp) / 'licence' / policy))
            print(f'  licence/{policy:22s} {time.time() - t0:5.1f}s', flush=True)
    doc = {
        '_provenance': {
            'recorded': date.today().isoformat(), 'git_head': head, 'python': sys.version.split()[0],
            'seissol': {'N': SEISSOL_N, 'seed': SEISSOL_SEED, 'runner': 'elastiflow/simulate_sweep.py',
                        'uncited': sorted(UNCITED_SEISSOL_VARIANTS)},
            'licence': {'N': LICENCE_N, 'seed': LICENCE_SEED, 'runner': 'elastiflow/simulate_main_LA.py'},
            'note': 'Every field of every cell must be reproduced exactly (floats to 1e-12) by tests/smoke.',
        },
        'cells': cells,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    print(f'wrote {OUT.relative_to(REPO)}: {len(cells)} cells, {sum(len(c) for c in cells.values())} fields')


if __name__ == '__main__':
    main()
