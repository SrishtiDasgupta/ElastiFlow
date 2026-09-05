"""HSM licence-pool-pressure gate (rho) sensitivity figure.

Reads hsm_rho_sweep.json (sweep_hsm_rho.py) and the EDF-LAMF baseline from
canonical_results.json, and plots HSM outcomes vs the contention threshold rho,
with the EDF-LAMF baseline as a dashed reference line and the deployed rho marked.
Each point is the seed-mean, band is +/-1 std.

rho gates the static->moldable release: HSM holds its allocation (forbids cost
scale-down) while pool pressure >= rho. Lower rho => more holding => better
deadline/effLU at a cost premium; rho above the contention ceiling (~0.85) is
inert (the gate never engages). Emits plots/RHOSENS_hsm_gate.{pdf,png}.
"""
import json, sys
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / 'hsm_rho_sweep.json'
CANON = HERE / 'canonical_results.json'
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

EUR_TO_USD = 1.10
DEPLOYED_RHO = 0.70
BASELINE_POLICY = 'EDF-LAMF'
N_FIX = 300

plt.rcParams.update({
    "font.family": "sans-serif", "font.weight": "bold",
    "axes.labelweight": "bold", "axes.titleweight": "bold",
    "figure.titleweight": "bold", "figure.facecolor": "white",
    "axes.facecolor": "white", "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS = 18, 16, 14, 12
HSM_C = '#7C3AED'

PANELS = [
    (lambda c: c.get('deadline_miss_rate'), 'Deadline miss rate',
     r'Deadline misses vs $P_{\mathrm{thresh}}$', False),
    (lambda c: c.get('overall_miss_rate'), 'Overall miss rate',
     r'Overall misses vs $P_{\mathrm{thresh}}$', False),
    (lambda c: (c.get('avg_cost_eur') or 0) * EUR_TO_USD
     if c.get('avg_cost_eur') is not None else None,
     'Avg cost per workflow (USD)', r'Cost vs $P_{\mathrm{thresh}}$', False),
    (lambda c: c.get('eff_lic_util'), 'Effective license utilisation (%)',
     r'Effective LU vs $P_{\mathrm{thresh}}$', True),
]


def hsm_series(cells, fn):
    by = {}
    for c in cells.values():
        if c.get('_policy') != 'HSM' or c.get('_parse_failed') or c.get('_N') != N_FIX:
            continue
        v = fn(c)
        if v is not None:
            by.setdefault(c['_rho'], []).append(v)
    rs = sorted(by)
    return rs, np.array([mean(by[r]) for r in rs]), \
        np.array([stdev(by[r]) if len(by[r]) > 1 else 0.0 for r in rs]), \
        [len(by[r]) for r in rs]


def baseline_value(fn):
    if not CANON.exists():
        return None
    d = json.loads(CANON.read_text())
    vals = [fn(v) for v in d.values()
            if v.get('_policy') == BASELINE_POLICY and v.get('_N') == N_FIX
            and not v.get('_parse_failed')]
    vals = [v for v in vals if v is not None]
    return mean(vals) if vals else None


def main():
    if not DATA.exists():
        sys.exit(f'no data: {DATA}')
    cells = json.loads(DATA.read_text())

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    snap = {}
    for ax, (fn, ylabel, title, higher_better) in zip(axes.flat, PANELS):
        rs, ms, sds, ns = hsm_series(cells, fn)
        ax.plot(rs, ms, label='HSM (licence-aware gate)', color=HSM_C,
                marker='o', linewidth=2.2, markersize=9, mfc='white', mew=2)
        ax.fill_between(rs, ms - sds, ms + sds, color=HSM_C, alpha=0.10)
        snap[title] = {float(r): round(float(m), 4) for r, m in zip(rs, ms)}
        for rr, mm, nn in zip(rs, ms, ns):
            if nn < max(ns):
                ax.annotate(f'runs={nn}', (rr, mm), textcoords='offset points',
                            xytext=(0, 11), ha='center', fontsize=9, color=HSM_C,
                            fontweight='bold', zorder=6,
                            bbox=dict(boxstyle='round,pad=0.15', fc='white',
                                      ec='none', alpha=0.85))
        # EDF-LAMF baseline reference line.
        b = baseline_value(fn)
        if b is not None:
            ax.axhline(b, color='#DC2626', ls='--', lw=1.8, zorder=1,
                       label='EDF-LAMF baseline')
        # Deployed rho marker.
        ax.axvline(DEPLOYED_RHO, color='#888888', ls=':', lw=1.6, zorder=0)
        ax.text(0.03, 0.97, r'deployed' '\n' r'$P_{\mathrm{thresh}} = 0.7$', transform=ax.transAxes,
                fontsize=LEG_FS, color='#555555', ha='left', va='top', zorder=6,
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#cccccc', alpha=0.9))
        ax.set_xlabel(r'Pool-pressure release threshold  $P_{\mathrm{thresh}}$', fontsize=LABEL_FS)
        ax.set_ylabel(ylabel, fontsize=LABEL_FS)
        ax.set_title(title, fontsize=TITLE_FS)
        ax.set_xticks(rs)
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
        fmt = matplotlib.ticker.ScalarFormatter(useMathText=True)
        fmt.set_powerlimits((-2, 3))
        ax.yaxis.set_major_formatter(fmt)
        ax.yaxis.get_offset_text().set_fontsize(TICK_FS)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=len(labels),
               loc='upper center', bbox_to_anchor=(0.5, 0.065), framealpha=0.92)
    fig.suptitle(r'Licence-aware HSM gate: pool-pressure release threshold $P_{\mathrm{thresh}}$'
                 '\n'
                 r'(N = 300, r = 0.90) — lower $P_{\mathrm{thresh}}$ holds longer: more deadline '
                 r'protection at a cost premium',
                 fontsize=TITLE_FS, y=0.999)
    fig.tight_layout(rect=[0, 0.06, 1, 0.94])
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'RHOSENS_hsm_gate.{ext}', dpi=150, bbox_inches='tight')
    (HERE / 'hsm_rho_snapshot.json').write_text(json.dumps(snap, indent=2))
    print('wrote plots/RHOSENS_hsm_gate.{pdf,png}')
    print(json.dumps(snap, indent=2))


if __name__ == '__main__':
    main()
