"""Partial-release-fraction (r) sensitivity figure for the moldable LA policies.

Reads partial_release_sweep.json (from sweep_partial_release.py) and plots the
LA outcomes vs the scale-down partial-release fraction r, with the deployed
r = 0.90 (release 90%, retain 10% buffer) marked. Each line is a moldable
policy; points are the seed-mean, band is +/-1 std.

r trades pool-sharing (r->1) against self-buffering (r->0); the expected
signature is an interior optimum / knee in overall-miss, so r = 0.90 is
justified iff it minimises misses or sits at the knee.

Emits plots/PRELSENS_partial_release.{pdf,png} + a snapshot json.
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

DATA = HERE / 'partial_release_sweep.json'
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

EUR_TO_USD = 1.10
DEPLOYED_R = 0.90

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

VARIANTS = {
    'EDF-LAMF': dict(c='#1D4ED8', marker='o'),
    'LAMF':     dict(c='#F59E0B', marker='s'),
    'EDF-HSM':  dict(c='#059669', marker='D'),
}

_WREL = r'$\omega_{\mathrm{rel}}$'
PANELS = [
    (lambda c: c.get('deadline_miss_rate'),
     'Deadline miss rate', f'Deadline misses vs {_WREL}'),
    (lambda c: c.get('overall_miss_rate'),
     'Overall miss rate', f'Overall misses vs {_WREL}'),
    (lambda c: c.get('eff_lic_util'),
     'Effective license utilisation (%)', f'Effective LU vs {_WREL}'),
    (lambda c: (c.get('avg_cost_eur') or 0) * EUR_TO_USD
     if c.get('avg_cost_eur') is not None else None,
     'Avg cost per workflow (USD)', f'Cost vs {_WREL}'),
]


def load_cells():
    if not DATA.exists():
        sys.exit(f'no data file: {DATA}  (run sweep_partial_release.py first)')
    return json.loads(DATA.read_text())


def series(cells, variant, fn, N):
    by = {}
    for c in cells.values():
        if c.get('_policy') != variant or c.get('_parse_failed') or c.get('_N') != N:
            continue
        v = fn(c)
        if v is not None:
            by.setdefault(c['_r'], []).append(v)
    rs = sorted(by)
    ms = [mean(by[r]) for r in rs]
    sds = [stdev(by[r]) if len(by[r]) > 1 else 0.0 for r in rs]
    ns = [len(by[r]) for r in rs]
    return rs, np.array(ms), np.array(sds), ns


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
        all_r = set()
        for v in variants:
            st = VARIANTS[v]
            rs, ms, sds, ns = series(cells, v, fn, N)
            if not rs:
                continue
            all_r.update(rs)
            full = max(full, max(ns))
            ax.plot(rs, ms, label=PN.DISPLAY.get(v, v), color=st['c'],
                    marker=st['marker'], linewidth=2.2, markersize=9,
                    mfc='white', mew=2)
            ax.fill_between(rs, ms - sds, ms + sds, color=st['c'], alpha=0.08)
            snap.setdefault(PN.DISPLAY.get(v, v), {})[title] = {
                float(r): round(float(m), 4) for r, m in zip(rs, ms)}
            for rr, mm, nn in zip(rs, ms, ns):
                if nn < max(ns):
                    ax.annotate(f'runs={nn}', (rr, mm), textcoords='offset points',
                                xytext=(0, 11), ha='center', fontsize=9,
                                color=st['c'], fontweight='bold', zorder=6,
                                bbox=dict(boxstyle='round,pad=0.15', fc='white',
                                          ec='none', alpha=0.85))
        ax.axvline(DEPLOYED_R, color='#888888', ls=':', lw=1.6, zorder=0)
        # Anchor the deployed-r label in the top-left corner (axes fraction) with a
        # white backing box so it never sits on top of the data lines.
        ax.text(0.03, 0.97,
                'deployed\n$\\omega_{\\mathrm{rel}}$ = 0.9\n'
                '($\\omega_{\\mathrm{ret}}$ = 0.1)',
                transform=ax.transAxes, fontsize=LEG_FS, color='#555555',
                ha='left', va='top', zorder=6,
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#cccccc',
                          alpha=0.9))
        ax.set_xlabel(r'Scale-down release fraction  $\omega_{\mathrm{rel}}$  '
                      r'(retain $1-\omega_{\mathrm{rel}}$)', fontsize=LABEL_FS)
        ax.set_ylabel(ylabel, fontsize=LABEL_FS)
        ax.set_title(title, fontsize=TITLE_FS)
        ax.set_xticks(sorted(all_r))
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=len(labels),
               loc='upper center', bbox_to_anchor=(0.5, 0.075), framealpha=0.92)
    fig.suptitle(r'Scale-down partial-release fraction $\omega_{\mathrm{rel}}$ '
                 'for the licence-aware elastic policies\n'
                 f'(N = {N}) — deployed ' r'$\omega_{\mathrm{rel}}$ = 0.90',
                 fontsize=TITLE_FS, y=0.999)
    fig.text(0.5, 0.018,
             r'$\omega_{\mathrm{rel}}$ = fraction of freed licence tokens '
             r'released on scale-down; '
             r'$\omega_{\mathrm{ret}} = 1-\omega_{\mathrm{rel}}$ retained as '
             r'self-buffer.',
             ha='center', fontsize=10, color='#444444')
    fig.tight_layout(rect=[0, 0.09, 1, 0.94])
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'PRELSENS_partial_release.{ext}', dpi=150,
                    bbox_inches='tight')
    (HERE / 'partial_release_snapshot.json').write_text(json.dumps(snap, indent=2))
    print('wrote plots/PRELSENS_partial_release.{pdf,png}')
    print(json.dumps(snap, indent=2))


if __name__ == '__main__':
    main()
