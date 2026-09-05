"""Compare n-4 (baseline canonical) vs n-2 (Henkel anshpc) at N=300."""
import json
from statistics import mean
from pathlib import Path

HERE = Path(__file__).resolve().parent
USD = 1.10
N = 300
BASE = json.loads((HERE / 'canonical_results.json').read_text())   # n-4
NEW = json.loads((HERE / 'ansys_n2_N300.json').read_text())        # n-2
POLS = ['FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM']


def cells(src, pol):
    return [c for k, c in src.items()
            if k.split('__')[0] == pol and int(k.split('__')[1][1:]) == N]


def m(src, pol, field, sc=1.0):
    xs = [c[field] for c in cells(src, pol) if c.get(field) is not None]
    return mean(xs) * sc if xs else float('nan')


def ansys_pscr(src, pol):
    xs = [100 * c['per_solver']['ANSYS']['done'] / c['per_solver']['ANSYS']['n']
          for c in cells(src, pol) if c.get('per_solver', {}).get('ANSYS', {}).get('n')]
    return mean(xs) if xs else float('nan')


def ansys_share(src, pol):
    out = []
    for c in cells(src, pol):
        ps = c.get('per_solver', {})
        tot = sum(v.get('lic', 0) for v in ps.values())
        if tot and 'ANSYS' in ps:
            out.append(100 * ps['ANSYS'].get('lic', 0) / tot)
    return mean(out) if out else float('nan')


hdr = f"{'policy':12} | {'cost$ (n4->n2)':>20} | {'lic$ (n4->n2)':>20} | {'ANSYS PSCR':>16} | {'ANSYS lic%':>16} | {'ELU':>14} | {'OMR':>14}"
print(hdr)
print('-' * len(hdr))
for p in POLS:
    c4, c2 = m(BASE, p, 'avg_cost_eur', USD), m(NEW, p, 'avg_cost_eur', USD)
    l4, l2 = m(BASE, p, 'avg_license_cost_eur', USD), m(NEW, p, 'avg_license_cost_eur', USD)
    a4, a2 = ansys_pscr(BASE, p), ansys_pscr(NEW, p)
    s4, s2 = ansys_share(BASE, p), ansys_share(NEW, p)
    e4, e2 = m(BASE, p, 'eff_lic_util'), m(NEW, p, 'eff_lic_util')
    o4, o2 = m(BASE, p, 'overall_miss_rate', 100), m(NEW, p, 'overall_miss_rate', 100)
    print(f"{p:12} | {c4:8.1f}->{c2:8.1f} | {l4:8.1f}->{l2:8.1f} | {a4:6.1f}->{a2:6.1f} | "
          f"{s4:6.1f}->{s2:6.1f} | {e4:5.1f}->{e2:5.1f} | {o4:5.1f}->{o2:5.1f}")

# license fraction of total cost (structural claim)
print("\nlicense % of total cost (n4 -> n2):")
for p in POLS:
    lf4 = m(BASE, p, 'license_cost_pct')
    lf2 = m(NEW, p, 'license_cost_pct')
    print(f"  {p:12} {lf4:5.1f}% -> {lf2:5.1f}%")
