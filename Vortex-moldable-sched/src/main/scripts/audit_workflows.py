"""One-shot audit script that walks all three workflow YAML directories and
reports schema violations using utils.validate_workflow.validate_workflow.

Usage:
    PYTHONPATH=src/main python src/main/scripts/audit_workflows.py
    PYTHONPATH=src/main python src/main/scripts/audit_workflows.py --type HPO
    PYTHONPATH=src/main python src/main/scripts/audit_workflows.py --quiet
"""

import argparse
import os
import sys
from collections import defaultdict

import yaml

# Make sibling packages importable when run as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_MAIN = os.path.dirname(_HERE)
if _SRC_MAIN not in sys.path:
    sys.path.insert(0, _SRC_MAIN)

from utils.validate_workflow import validate_workflow  # noqa: E402


# (workflow_type, absolute_directory_path)
DIRS = [
    ('PLAIN', os.path.join(_SRC_MAIN, 'sample_workflows')),
    ('LA',    os.path.join(_SRC_MAIN, 'workflow', 'sample_workflows_LA')),
    ('HPO',   os.path.join(_SRC_MAIN, 'workflow', 'sample_workflows_HPO')),
]


def load_yaml(path):
    """Return (parsed_dict, parse_error_string_or_none)."""
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f), None
    except yaml.YAMLError as e:
        return None, f"YAML parse error: {e}"
    except OSError as e:
        return None, f"I/O error: {e}"


def audit_directory(wf_type, directory):
    """Walk one directory; return (per_file_results, summary_counts, violation_freq)."""
    results = []
    summary = {'total': 0, 'valid': 0, 'invalid': 0, 'unreadable': 0}
    violation_freq = defaultdict(int)

    if not os.path.isdir(directory):
        print(f"  [warn] directory does not exist: {directory}", file=sys.stderr)
        return results, summary, violation_freq

    files = sorted(f for f in os.listdir(directory) if f.endswith('.yaml'))
    for fname in files:
        path = os.path.join(directory, fname)
        summary['total'] += 1
        wf, err = load_yaml(path)
        if err:
            summary['unreadable'] += 1
            results.append((fname, [err]))
            violation_freq[err.split(':')[0]] += 1
            continue
        violations = validate_workflow(wf, wf_type)
        if violations:
            summary['invalid'] += 1
            results.append((fname, violations))
            for v in violations:
                # Group by the field/path prefix before the colon for frequency stats
                key = v.split(':', 1)[0] if ':' in v else v
                violation_freq[key] += 1
        else:
            summary['valid'] += 1
    return results, summary, violation_freq


def print_report(wf_type, directory, results, summary, violation_freq, quiet=False):
    print()
    print('=' * 78)
    print(f"{wf_type} workflows  ({directory})")
    print('=' * 78)
    print(f"  Total: {summary['total']}    Valid: {summary['valid']}    "
          f"Invalid: {summary['invalid']}    Unreadable: {summary['unreadable']}")

    if not results:
        return

    if not quiet:
        print()
        print("  Per-file violations:")
        for fname, violations in results:
            print(f"    {fname}")
            for v in violations:
                print(f"      - {v}")

    if violation_freq:
        print()
        print("  Violation frequency (top fields):")
        sorted_freq = sorted(violation_freq.items(), key=lambda kv: -kv[1])
        for field, count in sorted_freq[:15]:
            print(f"    {count:5d}  {field}")


def main():
    parser = argparse.ArgumentParser(description='Audit workflow YAMLs against schema.')
    parser.add_argument('--type', choices=['PLAIN', 'LA', 'HPO'],
                        help='Audit only one type (default: all three)')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress per-file violation listing; show only summary + frequency')
    args = parser.parse_args()

    targets = [(t, d) for t, d in DIRS if not args.type or t == args.type]

    grand_total = 0
    grand_valid = 0
    grand_invalid = 0
    grand_unreadable = 0

    for wf_type, directory in targets:
        results, summary, freq = audit_directory(wf_type, directory)
        print_report(wf_type, directory, results, summary, freq, quiet=args.quiet)
        grand_total += summary['total']
        grand_valid += summary['valid']
        grand_invalid += summary['invalid']
        grand_unreadable += summary['unreadable']

    print()
    print('=' * 78)
    print("GRAND TOTAL")
    print('=' * 78)
    print(f"  Files scanned: {grand_total}")
    print(f"  Valid:         {grand_valid}")
    print(f"  Invalid:       {grand_invalid}")
    print(f"  Unreadable:    {grand_unreadable}")

    return 0 if (grand_invalid == 0 and grand_unreadable == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
