"""Driver: regenerate the full LA license-analysis figure suite.

Every figure reads the committed snapshots (canonical_results.json,
data_la_boundary.json, data_la_scarcity.json) -- nothing needs to be re-run.
Labels come from policy_names.DISPLAY (Static/Elastic terminology). Output:
license_results/plots/*.{pdf,png}.

  Group 1  cost structure   LA_01 (stack)  LA_02 (intensity)  LA_03 (lic/done)
  Group 2  efficiency/waste LA_04 (effUtil) LA_05 (waste$)    LA_06 (tok-s/done)
  Group 3  solver & pool    LA_07 (completion) LA_08 (pool util) LA_08b (lic share)
  Group 4  boundaries       LA_boundary (Abaqus) LA_scarcity (budget trade-off)
"""
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODULES = [
    'fig_la_cost_structure',   # LA_01
    'fig_la_vs_n',             # LA_02, LA_03
    'fig_la_efficiency',       # LA_04, LA_05, LA_06
    'fig_la_solver_pool',      # LA_07, LA_08, LA_08b
    'fig_la_boundary',         # LA_boundary
    'fig_la_scarcity',         # LA_scarcity
    'fig_xworkload_elastic_vs_static',  # cross-workload % improvement heatmap
]


def main():
    for name in MODULES:
        print(f'[{name}]')
        mod = importlib.import_module(name)
        mod.main()
    print(f'\nDone — full LA suite written to {HERE / "plots"}')


if __name__ == '__main__':
    main()
