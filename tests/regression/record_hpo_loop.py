"""Record the HPO request loops (see hpo_loop.py) into
tests/regression/baseline_hpo_loop.json. Run only when the reference is meant
to move, and say so in the commit."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpo_loop import MAX_SLEEPS, compute  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'baseline_hpo_loop.json'


def main() -> None:
    first, second = compute(), compute()
    assert first == second, 'the HPO loop record is not deterministic; not writing'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    OUT.write_text(json.dumps({'_provenance': {'recorded': date.today().isoformat(), 'git_head': head, 'python': sys.version.split()[0], 'max_sleeps': MAX_SLEEPS,
                                               'note': 'Every event of every loop must be reproduced exactly by tests/regression/test_hpo_loop_baseline.py.'},
                               'records': first}, indent=1, sort_keys=True) + '\n')
    print(f'wrote {OUT.relative_to(REPO)}: ' + ', '.join(f'{k}={len(v)} events, {v[-1][0]}' for k, v in first.items()))


if __name__ == '__main__':
    main()
