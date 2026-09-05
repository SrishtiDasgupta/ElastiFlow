"""k-sensitivity figure for the Plain SeisSol-TinyDA moldable scale-down.

Reads k_sweep_per_run.json (produced by sweep_K_PLAIN.py) and plots the
scheduler-level outcomes vs the packing ceiling k = CHAINS_PER_NODE, with the
deployed value k = 3 highlighted. Each line is a moldable variant; points are
the seed-mean and the shaded band is +/-1 seed std (Plain `02_*_vs_n` grammar).

Panels (lower is better on all): batch makespan, on-demand cost (USD),
deadline-miss rate. A companion panel shows scale-down nodes freed (higher =
more aggressive consolidation) to expose the mechanism behind the cost curve.

Emits plots/KSENS_chains_per_node.{pdf,png} and a snapshot json.
"""
import json
import sys
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / 'k_sweep_per_run.json'
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

EUR_TO_USD = 1.10
DEPLOYED_K = 3

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

# Display name + style per moldable variant (matches the chapter's families).
VARIANTS = {
    'edf_moldable_r':  dict(label='Elastic-EDF',  c='#1D4ED8', marker='o'),
    'fcfs_moldable_r': dict(label='Elastic-FCFS', c='#F59E0B', marker='s'),
}

# (key fn -> per-cell value, ylabel, title, lower_is_better)
PANELS = [
    (lambda c: c.get('batch_makespan_s'),
     'Batch makespan (s)', r'Makespan vs $\eta_{\max}$', True),
    (lambda c: (c.get('total_cost_on_demand') or 0) * EUR_TO_USD
     if c.get('total_cost_on_demand') is not None else None,
     'On-demand cost (USD)', r'On-demand cost vs $\eta_{\max}$', True),
    (lambda c: c.get('deadline_miss_rate'),
     'Deadline miss rate', r'Deadline miss rate vs $\eta_{\max}$', True),
    (lambda c: c.get('scale_down_nodes_freed'),
     'Scale-down nodes freed', r'Nodes freed vs $\eta_{\max}$', False),
]


def load_cells():
    if not DATA.exists():
        sys.exit(f'no data file: {DATA}  (run sweep_K_PLAIN.py first)')
    return json.loads(DATA.read_text())


def series(cells, variant, fn):
    """Return (ks, means, stds, counts) over seeds for one variant."""
    by_k = {}
    for c in cells.values():
        if c.get('_variant') != variant or c.get('_parse_failed'):
            continue
        v = fn(c)
        if v is not None:
            by_k.setdefault(c['_k'], []).append(v)
    ks = sorted(by_k)
    means = [mean(by_k[k]) for k in ks]
    stds = [stdev(by_k[k]) if len(by_k[k]) > 1 else 0.0 for k in ks]
    counts = [len(by_k[k]) for k in ks]
    return ks, np.array(means), np.array(stds), counts


def main():
    cells = load_cells()
    present = sorted({c['_variant'] for c in cells.values()
                      if not c.get('_parse_failed')})
    variants = [v for v in VARIANTS if v in present]
    if not variants:
        sys.exit('no moldable variant cells in data')

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    snap = {}
    full_seeds = 0  # max seed support seen across all points (for "n<full" labels)
    for ax, (fn, ylabel, title, lower_better) in zip(axes.flat, PANELS):
        all_ks = set()
        for v in variants:
            st = VARIANTS[v]
            ks, ms, sds, ns = series(cells, v, fn)
            if not ks:
                continue
            all_ks.update(ks)
            full_seeds = max(full_seeds, max(ns))
            ax.plot(ks, ms, label=st['label'], color=st['c'], marker=st['marker'],
                    linewidth=2.2, markersize=9, mfc='white', mew=2)
            ax.fill_between(ks, ms - sds, ms + sds, color=st['c'], alpha=0.08)
            snap.setdefault(st['label'], {})[title] = {
                int(k): round(float(m), 3) for k, m in zip(ks, ms)}
            # Label points with reduced seed support (tail cells dropped by the
            # pre-existing executor freeResources bug) so the thinner evidence
            # is explicit, not silent.
            for kk, mm, nn in zip(ks, ms, ns):
                if nn < max(ns):
                    ax.annotate(rf'$s{{=}}{nn}$', (kk, mm),
                                textcoords='offset points',
                                xytext=(0, 9), ha='center', fontsize=9,
                                color=st['c'], fontweight='bold')
        # Mark the deployed eta_max = 3 (the cost / schedule-quality compromise).
        ax.axvline(DEPLOYED_K, color='#888888', ls=':', lw=1.6, zorder=0)
        ymax = ax.get_ylim()[1]
        ax.text(DEPLOYED_K, ymax, r' deployed' '\n' r' $\eta_{\max} = 3$',
                fontsize=LEG_FS, color='#555555', ha='left', va='top')
        ax.set_xlabel(r'Consolidation cap  $\eta_{\max}$', fontsize=LABEL_FS)
        ax.set_ylabel(ylabel, fontsize=LABEL_FS)
        ax.set_title(title, fontsize=TITLE_FS)
        ax.set_xticks(sorted(all_ks))
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
        # Use 10^x offset notation so large-magnitude axes (makespan, cost)
        # show a compact mantissa instead of strings of zeroes.
        fmt = matplotlib.ticker.ScalarFormatter(useMathText=True)
        fmt.set_powerlimits((-2, 3))
        ax.yaxis.set_major_formatter(fmt)
        ax.yaxis.get_offset_text().set_fontsize(TICK_FS)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=len(labels),
               loc='upper center', bbox_to_anchor=(0.5, 0.075), framealpha=0.92)
    fig.suptitle(r'Consolidation cap $\eta_{\max}$: cost falls with $\eta_{\max}$ '
                 r'while Elastic-EDF deadlines rise — '
                 r'$\eta_{\max} = 3$ is the deployed compromise',
                 fontsize=TITLE_FS, y=0.998)
    fig.tight_layout(rect=[0, 0.06, 1, 0.97])
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'KSENS_chains_per_node.{ext}', dpi=150,
                    bbox_inches='tight')
    (HERE / 'k_sensitivity_snapshot.json').write_text(json.dumps(snap, indent=2))
    print('wrote plots/KSENS_chains_per_node.{pdf,png}')
    print(json.dumps(snap, indent=2))


if __name__ == '__main__':
    main()
