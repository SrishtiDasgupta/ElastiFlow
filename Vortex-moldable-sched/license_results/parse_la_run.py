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

    # Admission-time licence gate. Unlike renegotiation, admission performs no
    # descent over k: the shape is sized at min_instances and either takes its
    # tokens or the workflow stays queued and is retried. These are counted from
    # the event lines, so they are available for runs made before the gate summary
    # block existed.
    adm = {
        'admission_grants': len(re.findall(r'✓ Allocated \d+ licenses from pool', text)),
        'admission_licence_failures': len(re.findall(r'✗ License allocation failed', text)),
        'admission_impossible': len(re.findall(r'Workflow REJECTED - impossible to allocate', text)),
    }
    if any(adm.values()):
        out['admission'] = adm

    # Stage 1 licence-gate outcomes, in the decision vocabulary of Ch.5 Sec. 5.6.
    # Absent from runs produced before the gate was instrumented, in which case
    # these keys are simply omitted rather than defaulted to zero.
    neg = {}
    for key, pat in (
        ('gate_evaluations', r'Gate evaluations:\s+(\d+)'),
        ('approve',          r'\n\s*Approve:\s+(\d+)'),
        ('modify',           r'Modify \(partial allocation\):\s+(\d+)'),
        ('deny_licence',     r'Deny \(licence\):\s+(\d+)'),
        ('deny_compute',     r'Rejected by compute gate before licence test:\s+(\d+)'),
    ):
        v = grab_num(pat, int)
        if v is not None:
            neg[key] = v
    m = re.search(r'Partial allocation depth:\s+(\d+)/(\d+) instances granted \(([\d.]+)%', text)
    if m:
        neg['partial_granted_instances'] = int(m.group(1))
        neg['partial_requested_instances'] = int(m.group(2))
        neg['partial_depth_pct'] = float(m.group(3))
    m = re.search(r'Token headroom \(available - needed\):\s+min (-?\d+), median (-?\d+)', text)
    if m:
        neg['token_headroom_min'] = int(m.group(1))
        neg['token_headroom_median'] = int(m.group(2))
    v = grab_num(r'Peak need/available ratio:\s+([\d.]+)')
    if v is not None:
        neg['peak_need_avail_ratio'] = v
    if neg:
        out['negotiation'] = neg

    # Scale-up / scale-down negotiation summary. The scale-up failure breakdown
    # and the scale-down block breakdown each reconcile to their own total.
    mold = {}
    for key, pat in (
        ('scale_up_attempts',   r'Scale-up attempts:\s+(\d+)'),
        ('scale_up_successes',  r'Scale-up attempts:\s+\d+\s*\n\s*Successes:\s+(\d+)'),
        ('scale_down_attempts', r'Scale-down attempts:\s+(\d+)'),
        ('scale_down_successes', r'Scale-down attempts:\s+\d+\s*\n\s*Successes:\s+(\d+)'),
        ('scale_down_blocked',  r'Scale-down attempts:\s+\d+(?:.|\n)*?Blocked:\s+(\d+)'),
    ):
        v = grab_num(pat, int)
        if v is not None:
            mold[key] = v
    up_fail = {}
    for key, lab in (('insufficient_compute', 'Insufficient compute'),
                     ('insufficient_licenses', 'Insufficient licenses'),
                     ('budget_exhausted', 'Budget exhausted'),
                     ('time_exhausted', 'Time exhausted'),
                     ('unattributed', 'Unattributed')):
        v = grab_num(rf'-\s+{lab}:\s+(\d+)', int)
        if v is not None:
            up_fail[key] = v
    if up_fail:
        mold['scale_up_failures_by_reason'] = up_fail
    down_block = {}
    for key, lab in (('license_cost_adverse_saturated', r'Licence cost-adverse under pool saturation'),
                     ('late_iteration', r'Late iteration'),
                     ('deadline_proximity', r'Deadline proximity'),
                     ('time_progress', r'Time progress'),
                     ('budget_or_time_progress', r'Budget or time progress'),
                     ('min_instance_limit', r'Minimum instance limit'),
                     ('other', r'Other or unattributed')):
        v = grab_num(rf'-\s+{lab}:\s+(\d+)', int)
        if v is not None:
            down_block[key] = v
    if down_block:
        mold['scale_down_blocked_by_reason'] = down_block
    if mold:
        out['moldability'] = mold

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
