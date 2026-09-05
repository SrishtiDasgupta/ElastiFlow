import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
base = json.loads((HERE/'canonical_results.pre_negotiation_2026-09-02.json').read_text())
new  = json.loads((HERE/'canonical_results.json').read_text()) if (HERE/'canonical_results.json').exists() else {}
FIELDS = ['total_workflows','executed_workflows','incomplete_workflows','avg_flowtime_s',
          'avg_cost_eur','avg_hardware_cost_eur','avg_license_cost_eur','license_cost_pct',
          'avg_wait_time_s','total_hardware_cost_eur','total_license_cost_eur',
          'total_combined_cost_eur','avg_resource_util_pct','deadline_miss_rate',
          'budget_miss_rate','overall_miss_rate','eff_lic_util','waste_frac','lic_per_done',
          'token_sec_total','n_done','n_miss','overhead','tot_hw',
          # nested blocks the figure scripts consume
          'per_solver','pool_tw_util','license_pools']
def eq(a,b):
    if a==b: return True
    if isinstance(a,(int,float)) and isinstance(b,(int,float)): return abs(a-b)<=1e-6*max(1,abs(a))
    return False
bad=[]; nneg=0
for k,v in new.items():
    if k not in base: bad.append((k,'MISSING_IN_BASELINE',None,None)); continue
    for f in FIELDS:
        if not eq(base[k].get(f), v.get(f)): bad.append((k,f,base[k].get(f),v.get(f)))
    if v.get('negotiation') is not None: nneg+=1
print(f'  cells re-run so far : {len(new)}/210')
print(f'  cells w/ negotiation: {nneg}')
print(f'  field mismatches    : {len(bad)}')
for b in bad[:15]: print('    DIFF', b)
if new and not bad: print('  >>> all re-run cells reproduce the baseline exactly')
