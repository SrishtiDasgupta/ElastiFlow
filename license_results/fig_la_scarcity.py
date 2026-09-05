"""LA scarcity figure (Group 4, plot 2): the cost/deadline/wait trade under
a tightening license budget.

Three EDF-family policies (STATIC-EDF, ELASTIC-EDF, ELASTIC-HSM) at N=400 as the
global license token budget is scaled to x{1.0, 0.7, 0.5} of baseline. Tightening
the pool throttles admission: fewer workflows run concurrently, so compute
contention and on-demand bursting fall -> LOWER cost and FEWER deadline misses,
paid for by LONGER queue waits. Static EDF dominates the elastic variants at
every budget level.

1x3 panel (cost | overall miss | wait) sharing the x-axis; Plain `02_*_vs_n`
visual grammar; labels via policy_names.DISPLAY. Reads the committed snapshot
license_results/data_la_scarcity.json (raw /tmp/scarcity_runs not retained).
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import policy_names as PN  # noqa: E402

OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)
SNAP = json.loads((HERE / 'data_la_scarcity.json').read_text())

SCALES = [1.0, 0.7, 0.5]          # plotted left->right = looser -> scarcer
POLICIES = ['EDF-ST-LA', 'EDF-LAMF', 'EDF-HSM']
EUR_TO_USD = 1.10

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
LEG_FS = 12

FAMILY = {'EDF': '#1D4ED8', 'HSM': '#059669'}
STYLE = {
    'EDF-ST-LA': dict(c=FAMILY['EDF'], ls='--', marker='o', mfc='white'),
    'EDF-LAMF':  dict(c=FAMILY['EDF'], ls='-',  marker='o'),
    'EDF-HSM':   dict(c=FAMILY['HSM'], ls='-',  marker='D'),
}

# (policy, scale) -> {metric: [per-seed values]}
AGG = defaultdict(lambda: defaultdict(list))
for k, v in SNAP.items():
    m = re.match(r'(.+)__s([\d.]+)__N\d+__seed\d+', k)
    pol, sc = m.group(1), float(m.group(2))
    for metric, val in v.items():
        AGG[(pol, sc)][metric].append(val)


def series(pol, metric, scale=1.0):
    ms, sds = [], []
    for sc in SCALES:
        vs = [x * scale for x in AGG[(pol, sc)][metric]]
        ms.append(mean(vs) if vs else float('nan'))
        sds.append(stdev(vs) if len(vs) > 1 else 0.0)
    return np.array(ms), np.array(sds)


def main():
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    panels = [
        ('avg_cost_eur', 'Average cost per workflow (USD)', 'Cost', EUR_TO_USD),
        ('overall_miss_rate', 'Overall miss-rate', 'Deadline/budget misses', 1.0),
        ('avg_wait_time_s', 'Average wait time (s)', 'Queue wait (the trade-off)', 1.0),
    ]
    xs = np.arange(len(SCALES))
    for ax, (metric, ylab, title, scale) in zip(axes, panels):
        for pol in POLICIES:
            ms, sds = series(pol, metric, scale)
            ax.plot(xs, ms, label=PN.DISPLAY[pol], linewidth=2.2, markersize=9,
                    **STYLE[pol])
            ax.fill_between(xs, ms - sds, ms + sds, color=STYLE[pol]['c'], alpha=0.08)
        ax.set_xticks(xs)
        ax.set_xticklabels([f'×{s:g}' for s in SCALES], fontsize=TICK_FS)
        ax.set_xlabel('License token budget (× baseline)\nlooser → scarcer',
                      fontsize=LABEL_FS - 2)
        ax.set_ylabel(ylab, fontsize=LABEL_FS)
        ax.set_title(title, fontsize=TITLE_FS)
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=3, loc='lower center',
               bbox_to_anchor=(0.5, -0.04), framealpha=0.92)
    fig.suptitle('License scarcity trade-off at N = 400  (EDF family)',
                 fontsize=TITLE_FS + 2, y=1.02)
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'LA_scarcity.{ext}', dpi=150, bbox_inches='tight')
    print('wrote plots/LA_scarcity.{pdf,png}')


if __name__ == '__main__':
    main()
