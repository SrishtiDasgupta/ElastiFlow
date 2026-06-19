"""Parse the stdout of a single LA simulator run into a structured dict.

The simulator prints a human-readable summary at the end which we scrape.
All monetary quantities stay in EUR here; the plotting layer converts to USD.
"""
import re
import sys


def parse(text: str) -> dict:
    """Return a flat dict of metrics extracted from the simulator stdout."""
    out: dict = {}

    def grab_num(pat, conv=float):
        m = re.search(pat, text)
        return conv(m.group(1)) if m else None

    out['total_workflows'] = grab_num(r'Total workflows:\s+(\d+)', int)
    out['executed_workflows'] = grab_num(r'Executed workflows:\s+(\d+)', int)
    out['incomplete_workflows'] = grab_num(
        r'Incomplete workflows:\s+(\d+)', int)

    out['avg_flowtime_s'] = grab_num(r'Average Flowtime:\s+([\d.]+)')
    out['avg_cost_eur'] = grab_num(r'Average Cost:\s+€([\d.]+)')
    out['avg_hardware_cost_eur'] = grab_num(
        r'Average Hardware Cost:\s+€([\d.]+)')
    out['avg_license_cost_eur'] = grab_num(
        r'Average License Cost:\s+€([\d.]+)')
    out['license_cost_pct'] = grab_num(r'License Cost %:\s+([\d.]+)%')
    out['avg_wait_time_s'] = grab_num(r'Average Wait Time:\s+([\d.]+)')

    out['total_hardware_cost_eur'] = grab_num(
        r'Total Hardware Cost:\s+€([\d.]+)')
    out['total_license_cost_eur'] = grab_num(
        r'Total License Cost \(additional\):\s+€([\d.]+)')
    out['total_combined_cost_eur'] = grab_num(
        r'Total Combined Cost:\s+€([\d.]+)')
    out['avg_resource_util_pct'] = grab_num(
        r'Average Resource Utilization:\s+([\d.]+)%')

    # Per-pool license utilization
    pools = {}
    for pool_name in ('ABAQUS', 'ANSYS', 'LSDYNA'):
        block_m = re.search(
            rf'{pool_name}:\s*\n\s*Average Utilization:\s+([\d.]+)%\s*\n'
            rf'\s*Peak Allocated:\s+(\d+)/(\d+) tokens \(([\d.]+)%\)',
            text)
        if block_m:
            pools[pool_name] = {
                'avg_util_pct': float(block_m.group(1)),
                'peak_tokens': int(block_m.group(2)),
                'pool_total': int(block_m.group(3)),
                'peak_util_pct': float(block_m.group(4)),
            }
    out['license_pools'] = pools

    out['deadline_miss_rate'] = grab_num(r'Deadline miss rate:\s+([\d.]+)')
    out['budget_miss_rate'] = grab_num(r'Budget miss rate:\s+([\d.]+)')
    out['overall_miss_rate'] = grab_num(r'Overall miss rate:\s+([\d.]+)')

    # Sanity: at least one core field must be present
    if out['total_workflows'] is None or out['avg_flowtime_s'] is None:
        out['_parse_failed'] = True
    return out


if __name__ == '__main__':
    txt = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1]).read()
    import json
    print(json.dumps(parse(txt), indent=2))
