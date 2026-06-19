"""Iteration-0 OPTIM-factor (f0) sensitivity figure for the LAMF policies.

Reads iter0_sweep.json (from sweep_LAMF_iter0.py) and plots the LA outcomes vs
the iteration-0 budget/deadline residual factor f0, with the deployed f0 = 0.60
marked. Each line is a LAMF variant; points are the seed-mean, band is +/-1 std.

The justification: f0 hedges allocation-under-zero-observation. Expect a
U-shaped overall-miss curve (too-low f0 under-provisions -> deadline debt;
too-high f0 over-commits -> budget/deadline blow-ups) with monotone cost, so
0.60 is justified iff it minimises misses or sits at the cost/deadline knee.

Emits plots/F0SENS_iter0_factor.{pdf,png} + a snapshot json.
"""
import json
import sys
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import policy_names as PN  # noqa: E402

DATA = HERE / 'iter0_sweep.json'
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

EUR_TO_USD = 1.10
DEPLOYED_F0 = 0.60

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
TITLE_FS = 18
LABEL_FS = 16
TICK_FS = 14
LEG_FS = 12

# internal policy key -> style (display name via policy_names.DISPLAY)
VARIANTS = {
    'EDF-LAMF': dict(c='#1D4ED8', marker='o'),
    'LAMF':     dict(c='#F59E0B', marker='s'),
}

PANELS = [
    (lambda c: c.get('deadline_miss_rate'),
     'Deadline miss rate', 'Deadline misses vs f0'),
    (lambda c: c.get('budget_miss_rate'),
     'Budget miss rate', 'Budget misses vs f0'),
    (lambda c: c.get('overall_miss_rate'),
     'Overall miss rate', 'Overall misses vs f0'),
    (lambda c: (c.get('avg_cost_eur') or 0) * EUR_TO_USD
     if c.get('avg_cost_eur') is not None else None,
     'Avg cost per workflow (USD)', 'Cost vs f0'),
]


def load_cells():
    if not DATA.exists():
        sys.exit(f'no data file: {DATA}  (run sweep_LAMF_iter0.py first)')
    return json.loads(DATA.read_text())


def series(cells, variant, fn, N):
    by = {}
    for c in cells.values():
        if c.get('_policy') != variant or c.get('_parse_failed') or c.get('_N') != N:
            continue
        v = fn(c)
        if v is not None:
            by.setdefault(c['_f0'], []).append(v)
    fs = sorted(by)
    ms = [mean(by[f]) for f in fs]
    sds = [stdev(by[f]) if len(by[f]) > 1 else 0.0 for f in fs]
    ns = [len(by[f]) for f in fs]
    return fs, np.array(ms), np.array(sds), ns


def main():
    cells = load_cells()
    present = sorted({c['_policy'] for c in cells.values() if not c.get('_parse_failed')})
    variants = [v for v in VARIANTS if v in present]
    Ns = sorted({c['_N'] for c in cells.values() if not c.get('_parse_failed')})
    N = Ns[0] if Ns else 300

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    snap = {}
    full = 0
    for ax, (fn, ylabel, title) in zip(axes.flat, PANELS):
        all_f = set()
        for v in variants:
            st = VARIANTS[v]
            fs, ms, sds, ns = series(cells, v, fn, N)
            if not fs:
                continue
            all_f.update(fs)
            full = max(full, max(ns))
            ax.plot(fs, ms, label=PN.DISPLAY.get(v, v), color=st['c'],
                    marker=st['marker'], linewidth=2.2, markersize=9,
                    mfc='white', mew=2)
            ax.fill_between(fs, ms - sds, ms + sds, color=st['c'], alpha=0.08)
            snap.setdefault(PN.DISPLAY.get(v, v), {})[title] = {
                float(f): round(float(m), 4) for f, m in zip(fs, ms)}
            for ff, mm, nn in zip(fs, ms, ns):
                if nn < max(ns):
                    ax.annotate(f'n={nn}', (ff, mm), textcoords='offset points',
                                xytext=(0, 9), ha='center', fontsize=9,
                                color=st['c'], fontweight='bold')
        ax.axvline(DEPLOYED_F0, color='#888888', ls=':', lw=1.6, zorder=0)
        ymax = ax.get_ylim()[1]
        ax.text(DEPLOYED_F0, ymax, ' deployed\n f0 = 0.6', fontsize=LEG_FS,
                color='#555555', ha='left', va='top')
        ax.set_xlabel('Iteration-0 OPTIM factor  f0  (budget & deadline)',
                      fontsize=LABEL_FS)
        ax.set_ylabel(ylabel, fontsize=LABEL_FS)
        ax.set_title(title, fontsize=TITLE_FS)
        ax.set_xticks(sorted(all_f))
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=len(labels),
               loc='upper center', bbox_to_anchor=(0.5, 0.075), framealpha=0.92)
    fig.suptitle(f'Iteration-0 residual factor f0 for the LAMF policies '
                 f'(N = {N}) — deployed f0 = 0.60',
                 fontsize=TITLE_FS, y=0.998)
    fig.text(0.5, 0.018,
             f'Up to {full} seeds/point (seed-mean +/-1 std band). f0 scales the '
             'iteration-0 budget & deadline residual envelope; only LAMF applies it.',
             ha='center', fontsize=10, color='#444444')
    fig.tight_layout(rect=[0, 0.09, 1, 0.97])
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'F0SENS_iter0_factor.{ext}', dpi=150,
                    bbox_inches='tight')
    (HERE / 'iter0_sensitivity_snapshot.json').write_text(json.dumps(snap, indent=2))
    print('wrote plots/F0SENS_iter0_factor.{pdf,png}')
    print(json.dumps(snap, indent=2))


if __name__ == '__main__':
    main()
