"""LA boundary figure: parity-vs-scarcity map for the Abaqus-concave regime.

Heatmap of Δcost% = EDF-HSM (moldable) − EDF-ST (static), seed-averaged, over
N {300,400,500} (rows) x ABAQUS pool capacity x {4,2,1.3,1.0} (cols), on the
75%-ABAQUS deck. Green = moldable parity/win, red = moldable worse; cells where
the ABAQUS pool is binding (peak >=95%) get a hatch. Shows the narrow parity
pocket (N300, slack pool) and its breakdown under load / scarcity.

Reads /tmp/boundary_runs (produced by boundary_map.py), snapshots the grid to
license_results/data_la_boundary.json, emits plots/LA_boundary.{pdf,png}.
"""
import glob
import json
import os
import sys
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_la_run  # noqa: E402

RUN_ROOT = Path('/tmp/boundary_runs')
NS = [300, 400, 500]
ABQ = [4.0, 2.0, 1.3, 1.0]
SEEDS = [7, 107, 207]
STATIC, MOLD = 'EDF-ST-LA', 'EDF-HSM'


def cost(policy, N, abq, seed):
    d = RUN_ROOT / f'{policy}__N{N}__abq{abq}__seed{seed}'
    logs = glob.glob(str(d / 'stdout.log'))
    if not logs:
        return None
    p = parse_la_run.parse(open(logs[0]).read())
    return p.get('avg_cost_eur')


def abq_peak(N, abq, seed):
    d = RUN_ROOT / f'{MOLD}__N{N}__abq{abq}__seed{seed}'
    import csv
    ug = glob.glob(str(d / '*_license_usage.csv'))
    if not ug:
        return None
    cap = peak = 0
    for r in csv.DictReader(open(ug[0])):
        if r['Pool'] != 'ABAQUS':
            continue
        t = float(r['Timestamp'])
        if t > 1e6:
            continue
        cap = float(r['Total_Tokens'])
        peak = max(peak, float(r['Allocated_Tokens']))
    return (peak / cap * 100) if cap else None


def main():
    grid = np.full((len(NS), len(ABQ)), np.nan)
    bind = np.zeros((len(NS), len(ABQ)), dtype=bool)
    snapshot = {}
    for i, N in enumerate(NS):
        for j, a in enumerate(ABQ):
            sc = [cost(STATIC, N, a, s) for s in SEEDS]
            mc = [cost(MOLD, N, a, s) for s in SEEDS]
            sc = [x for x in sc if x is not None]; mc = [x for x in mc if x is not None]
            if not sc or not mc:
                continue
            d = (mean(mc) / mean(sc) - 1) * 100
            grid[i, j] = d
            pk = [abq_peak(N, a, s) for s in SEEDS]
            pk = [x for x in pk if x is not None]
            bind[i, j] = bool(pk and mean(pk) >= 95)
            snapshot[f'N{N}_abq{a}'] = dict(
                delta_cost_pct=round(d, 2), static_cost=round(mean(sc), 1),
                moldable_cost=round(mean(mc), 1),
                abaqus_peak_util=round(mean(pk), 1) if pk else None)
    (HERE / 'data_la_boundary.json').write_text(json.dumps(snapshot, indent=2))

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    vmax = np.nanmax(np.abs(grid))
    im = ax.imshow(grid, cmap='RdYlGn_r', vmin=-vmax, vmax=vmax, aspect='auto')
    ax.set_xticks(range(len(ABQ)))
    ax.set_xticklabels([f'×{a:g}' for a in ABQ])
    ax.set_yticks(range(len(NS)))
    ax.set_yticklabels([f'N={n}' for n in NS])
    ax.set_xlabel('ABAQUS pool capacity  (×4 = well-provisioned  →  ×1.0 = binding)')
    ax.set_ylabel('Workload size')
    ax.set_title('Moldable − Static EDF cost gap, 75% ABAQUS deck\n'
                 'green = moldable parity/win,  red = moldable worse', fontsize=11)
    for i in range(len(NS)):
        for j in range(len(ABQ)):
            if np.isnan(grid[i, j]):
                continue
            txt = f'{grid[i,j]:+.1f}%'
            if bind[i, j]:
                txt += '\n[binding]'
                ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False,
                                           hatch='///', edgecolor='black', lw=0))
            ax.text(j, i, txt, ha='center', va='center', fontsize=9,
                    color='black', fontweight='bold')
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('Δ cost % (moldable − static)')
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(HERE / 'plots' / f'LA_boundary.{ext}', dpi=200, bbox_inches='tight')
    print('wrote plots/LA_boundary.{pdf,png} and data_la_boundary.json')
    print('parity cells (|Δcost|<=2%):',
          [k for k, v in snapshot.items() if abs(v['delta_cost_pct']) <= 2])


if __name__ == '__main__':
    main()
