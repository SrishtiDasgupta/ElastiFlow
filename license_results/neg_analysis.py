"""Negotiation / licence-gate analysis for thesis Sec. 9.4, from the canonical dataset."""
import json, collections, statistics as st
from pathlib import Path
H = Path(__file__).resolve().parent
d = json.loads((H/'canonical_results.json').read_text())
ELASTIC = ['LAMF', 'EDF-LAMF', 'EDF-HSM']
NS = [150, 200, 300, 400, 500, 600, 700]

def cells(pol, N=None):
    return [r for r in d.values() if r['_policy'] == pol and (N is None or r['_N'] == N)]

def s(rows, *path, default=0):
    out = []
    for r in rows:
        v = r
        for k in path:
            v = (v or {}).get(k) if isinstance(v, dict) else None
        out.append(v if v is not None else default)
    return out

print('=' * 78)
print('1. STAGE 1 LICENCE GATE (renegotiation), summed over 6 runs per cell')
print('=' * 78)
print(f"  {'policy':<10}{'N':>5}{'gate':>8}{'approve':>9}{'modify':>8}{'denyLic':>9}{'denyCmp':>9}{'modify%':>9}")
for pol in ELASTIC:
    for N in NS:
        c = cells(pol, N)
        if not c: continue
        g  = sum(s(c, 'negotiation', 'gate_evaluations'))
        ap = sum(s(c, 'negotiation', 'approve'))
        mo = sum(s(c, 'negotiation', 'modify'))
        dl = sum(s(c, 'negotiation', 'deny_licence'))
        dc = sum(s(c, 'negotiation', 'deny_compute'))
        pct = 100 * mo / g if g else 0
        print(f"  {pol:<10}{N:>5}{g:>8}{ap:>9}{mo:>8}{dl:>9}{dc:>9}{pct:>8.1f}%")
    print()

print('=' * 78)
print('2. TOKEN HEADROOM AT THE GATE (how close scale-up came to the pool limit)')
print('=' * 78)
print(f"  {'policy':<10}{'N':>5}{'min headroom':>15}{'peak need/avail':>18}")
for pol in ELASTIC:
    for N in NS:
        c = cells(pol, N)
        if not c: continue
        hr = [v for v in s(c, 'negotiation', 'token_headroom_min', default=None) if v is not None]
        rt = [v for v in s(c, 'negotiation', 'peak_need_avail_ratio', default=None) if v is not None]
        if hr:
            print(f"  {pol:<10}{N:>5}{min(hr):>15}{max(rt):>18.3f}")
    print()

print('=' * 78)
print('3. ADMISSION LICENCE GATE (no k-descent; blocked workflows stay queued)')
print('=' * 78)
print(f"  {'policy':<12}{'N':>5}{'grants':>9}{'lic fail':>10}{'impossible':>12}{'fail/run':>10}")
for pol in ['FCFS-ST-LA','EDF-ST-LA'] + ELASTIC:
    for N in NS:
        c = cells(pol, N)
        if not c: continue
        gr = sum(s(c, 'admission', 'admission_grants'))
        fl = sum(s(c, 'admission', 'admission_licence_failures'))
        im = sum(s(c, 'admission', 'admission_impossible'))
        print(f"  {pol:<12}{N:>5}{gr:>9}{fl:>10}{im:>12}{fl/len(c):>10.1f}")
    print()

print('=' * 78)
print('4. SCALE-DOWN BLOCKS BY REASON (elastic only), summed over 6 runs')
print('=' * 78)
KEYS = ['license_cost_adverse_saturated','late_iteration','deadline_proximity',
        'time_progress','budget_or_time_progress','min_instance_limit','other']
print(f"  {'policy':<10}{'N':>5}{'attempts':>10}{'ok':>7}{'blocked':>9}" + ''.join(f'{k[:9]:>10}' for k in KEYS))
for pol in ELASTIC:
    for N in NS:
        c = cells(pol, N)
        if not c: continue
        at = sum(s(c, 'moldability', 'scale_down_attempts'))
        ok = sum(s(c, 'moldability', 'scale_down_successes'))
        bl = sum(s(c, 'moldability', 'scale_down_blocked'))
        row = [sum(s(c, 'moldability', 'scale_down_blocked_by_reason', k)) for k in KEYS]
        print(f"  {pol:<10}{N:>5}{at:>10}{ok:>7}{bl:>9}" + ''.join(f'{v:>10}' for v in row))
    print()

print('=' * 78)
print('5. SCALE-UP FAILURES BY REASON (licence vs budget vs compute)')
print('=' * 78)
FK = ['insufficient_compute','insufficient_licenses','budget_exhausted','time_exhausted','unattributed']
print(f"  {'policy':<10}{'N':>5}{'attempts':>10}{'ok':>7}{'fail':>7}" + ''.join(f'{k[:11]:>13}' for k in FK))
for pol in ELASTIC:
    for N in NS:
        c = cells(pol, N)
        if not c: continue
        at = sum(s(c, 'moldability', 'scale_up_attempts'))
        ok = sum(s(c, 'moldability', 'scale_up_successes'))
        row = [sum(s(c, 'moldability', 'scale_up_failures_by_reason', k)) for k in FK]
        print(f"  {pol:<10}{N:>5}{at:>10}{ok:>7}{at-ok:>7}" + ''.join(f'{v:>13}' for v in row))
    print()

print('=' * 78)
print('6. PEAK POOL OCCUPANCY (%) - mean of per-run peaks, and max over runs')
print('=' * 78)
for pool in ['ANSYS','ABAQUS','LSDYNA']:
    print(f'  -- {pool}')
    print(f"    {'policy':<12}" + ''.join(f'{n:>9}' for n in NS))
    for pol in ['FCFS-ST-LA','EDF-ST-LA'] + ELASTIC:
        row = ''
        for N in NS:
            c = cells(pol, N)
            v = [r['license_pools'][pool]['peak_util_pct'] for r in c if pool in (r.get('license_pools') or {})]
            row += f'{st.mean(v):>9.1f}' if v else f'{"-":>9}'
        print(f"    {pol:<12}{row}")
    print()
