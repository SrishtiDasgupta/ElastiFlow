"""Record the three arrival processes (see dispatch_record.py) into
tests/regression/baseline_dispatch.json. Run only when the reference is meant
to move, and say so in the commit."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dispatch_record import compute  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'baseline_dispatch.json'


def main() -> None:
    first, second = compute(), compute()
    assert first == second, 'the dispatch record is not deterministic; not writing'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    OUT.write_text(json.dumps({'_provenance': {'recorded': date.today().isoformat(), 'git_head': head, 'python': sys.version.split()[0],
                                               'note': 'Every (id, time) of every arrival process must be reproduced exactly by tests/regression/test_dispatch_baseline.py.'},
                               'records': first}, indent=1, sort_keys=True) + '\n')
    print(f'wrote {OUT.relative_to(REPO)}: ' + ', '.join(f'{k}={len(v)}' for k, v in first.items()))


if __name__ == '__main__':
    main()
