"""Cross-workload figure: best-elastic-vs-matching-static % improvement.

Heatmap of the percentage improvement of the BEST elastic policy over its
corresponding static baseline, per metric (rows) x workload (cols). Workloads
are ordered hardware-dominated -> license-dominated (HPO, Plain SeisSol, LA) so
the elastic advantage gradient reads left-to-right. Positive (green) = elastic
better; negative (red) = best elastic still loses; blank = not applicable.

Values are the synthesis numbers from ELASTIC_VS_STATIC_METRICS.md:
  Plain  N=400 6-seed (plain_results_per_run.json, _c sort-key)
  LA     N=300 6-seed (canonical_results.json)
  HPO    N=3 real-cluster 4-corner (HPO/results/SUMMARY_4CORNER.md)
All lower-is-better metrics; improvement = (static - elastic)/static * 100.

Emits plots/XWORKLOAD_elastic_vs_static.{pdf,png}.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.weight": "bold",
    "axes.labelweight": "bold",
    "axes.titleweight": "bold",
    "figure.titleweight": "bold",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "pdf.fonttype": 42,
})
TITLE_FS = 16
LABEL_FS = 15
TICK_FS = 13

# rows = metrics, cols = workloads (hardware-dominated -> license-dominated)
METRICS = ['CPR / cost', 'Queue wait', 'Budget miss', 'Deadline miss', 'Turnaround']
WORKLOADS = ['HPO\n(hardware)', 'Plain SeisSol\n(hardware,\ndeadline-tight)', 'LA / CAE\n(license)']
NAN = np.nan
# % improvement of best elastic vs matching static (positive = elastic better)
GRID = np.array([
    [ 69.8,  41.1,   5.7],   # CPR / cost
    [  NAN,  40.2,  12.5],   # Queue wait
    [  NAN,  75.0,  20.3],   # Budget miss
    [  0.0,  -1.3,   2.3],   # Deadline miss
    [ 14.9,   3.1,  -7.7],   # Turnaround
])


def main():
    fig, ax = plt.subplots(figsize=(8.4, 5.6))
    vmax = np.nanmax(np.abs(GRID))
    masked = np.ma.masked_invalid(GRID)
    cmap = plt.cm.RdYlGn.copy()
    cmap.set_bad('#e8e8e8')   # n/a cells
    im = ax.imshow(masked, cmap=cmap, vmin=-vmax, vmax=vmax, aspect='auto')

    ax.set_xticks(range(len(WORKLOADS)))
    ax.set_xticklabels(WORKLOADS, fontsize=TICK_FS)
    ax.set_yticks(range(len(METRICS)))
    ax.set_yticklabels(METRICS, fontsize=TICK_FS)
    ax.set_xlabel('Workload  (binding constraint: hardware  →  license)',
                  fontsize=LABEL_FS)
    ax.set_title('Best elastic vs matching static — % improvement\n'
                 'green = elastic better,  red = elastic worse',
                 fontsize=TITLE_FS)

    for i in range(len(METRICS)):
        for j in range(len(WORKLOADS)):
            v = GRID[i, j]
            if np.isnan(v):
                ax.text(j, i, 'n/a', ha='center', va='center',
                        fontsize=11, color='#888888', fontweight='bold')
            else:
                ax.text(j, i, f'{v:+.0f}%', ha='center', va='center',
                        fontsize=13, color='black', fontweight='bold')
    # grid lines between cells
    ax.set_xticks(np.arange(-.5, len(WORKLOADS), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(METRICS), 1), minor=True)
    ax.grid(which='minor', color='white', linewidth=2)
    ax.tick_params(which='minor', length=0)

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('% improvement (elastic − static)', fontsize=LABEL_FS - 3)
    cbar.ax.tick_params(labelsize=TICK_FS - 2)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'XWORKLOAD_elastic_vs_static.{ext}', dpi=150,
                    bbox_inches='tight')
    print('wrote plots/XWORKLOAD_elastic_vs_static.{pdf,png}')


if __name__ == '__main__':
    main()
