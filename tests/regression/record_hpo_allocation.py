"""
Record the HPO schedulers' allocation decisions (see hpo_allocation.py) into
tests/regression/baseline_hpo_allocation.json. Run only when the reference is
meant to move, and say so in the commit:

    vortex_venv/bin/python3 tests/regression/record_hpo_allocation.py
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpo_allocation import EPOCHS, HPO_POLICIES, REPO, SCALES, TRIALS, WORKFLOWS, compute  # noqa: E402

OUT = Path(__file__).resolve().parent / 'baseline_hpo_allocation.json'


def main() -> None:
    first, second = compute(), compute()
    assert first == second, 'the allocation record is not deterministic; not writing'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    doc = {
        '_provenance': {
            'recorded': date.today().isoformat(), 'git_head': head, 'python': sys.version.split()[0],
            'policies': list(HPO_POLICIES), 'workflows': [p.name for p in WORKFLOWS],
            'select_grid': {'budget_scales': list(SCALES), 'deadline_scales': list(SCALES), 'trials': list(TRIALS), 'epochs': list(EPOCHS)},
            'note': 'Every field must be reproduced exactly (floats to 1e-12) by tests/regression/test_hpo_allocation_baseline.py.',
        },
        'records': first,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    print(f'wrote {OUT.relative_to(REPO)}: {len(first)} records')


if __name__ == '__main__':
    main()
