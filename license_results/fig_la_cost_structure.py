"""LA cost-structure figure (Group 1, plot 1): hardware vs license cost.

Stacked bar per policy at a headline N: the average per-workflow cost split
into its hardware tier and its license tier. License cost dominates the
licence-bound LA regime; the annotation reports each policy's license share.
Error bar = std of TOTAL per-workflow cost across the 6 seeds.

This is the cost-structure analogue of the Plain chapter's 01b_cost_stack and
follows the same visual grammar (fonts, figsize, stacked bars + single total
error bar + on-top annotation + legend below). Reads the canonical 6-seed
dataset (canonical_results.json); labels via policy_names.DISPLAY.

Outputs data_la_cost_structure.json + plots/LA_01_cost_structure.{pdf,png}.
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

OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)
CANON = json.loads((HERE / 'canonical_results.json').read_text())

HEADLINE_N = 300

# Currency: internal metrics are in EUR; report in USD to match the Plain /
# HPO chapters (1 EUR = 1.10 USD).
EUR_TO_USD = 1.10
CCY = 'USD'

# --------------------------------------------------------------------- styling
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
ANNOT_FS = 11


def cells(policy, N):
    return [v for v in CANON.values()
            if v.get('_policy') == policy and v.get('_N') == N
            and not v.get('_parse_failed') and v.get('avg_cost_eur') is not None]


def agg(policy, N, key):
    vs = [c[key] for c in cells(policy, N) if c.get(key) is not None]
    if not vs:
        return (float('nan'), 0.0)
    return (mean(vs), stdev(vs) if len(vs) > 1 else 0.0)


def _save(fig, name):
    pdf = OUT_DIR / f'{name}.pdf'
    png = OUT_DIR / f'{name}.png'
    fig.savefig(pdf, bbox_inches='tight')
    fig.savefig(png, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'  wrote {pdf.name} + {png.name}')


def main():
    pols = PN.INTERNAL_ORDER
    x = np.arange(len(pols))
    fig, ax = plt.subplots(figsize=(12, 6.5))

    hw = np.array([agg(p, HEADLINE_N, 'avg_hardware_cost_eur')[0] * EUR_TO_USD for p in pols])
    lic = np.array([agg(p, HEADLINE_N, 'avg_license_cost_eur')[0] * EUR_TO_USD for p in pols])
    tot_std = np.array([agg(p, HEADLINE_N, 'avg_cost_eur')[1] * EUR_TO_USD for p in pols])

    bars_hw = ax.bar(x, hw, label='Hardware (compute)', color='#10B981',
                     edgecolor='white', linewidth=1.0)
    bars_lic = ax.bar(x, lic, bottom=hw, label='License (tokens)', color='#3B82F6',
                      edgecolor='white', linewidth=1.0)
    # Hatch static bars (// ) for at-a-glance static-vs-elastic distinction,
    # matching the Plain chapter's bar grammar.
    for i, p in enumerate(pols):
        if p in PN.STATIC:
            bars_hw[i].set_hatch('//')
            bars_lic[i].set_hatch('//')
    totals = hw + lic
    ax.errorbar(x, totals, yerr=tot_std, fmt='none', ecolor='black',
                capsize=4, lw=1.2, zorder=10)

    # Annotate total per-workflow cost (USD) above each bar.
    snap = {}
    seed_max = 0.0
    for i, p in enumerate(pols):
        lic_pct = 100 * lic[i] / totals[i] if totals[i] else 0
        # Per-seed total-cost dots (one per seed), jittered across the bar —
        # same idiom as the HPO bar charts; shows the spread behind the std bar.
        seed_tot = [c['avg_cost_eur'] * EUR_TO_USD for c in cells(p, HEADLINE_N)
                    if c.get('avg_cost_eur') is not None]
        if seed_tot:
            jit = (np.linspace(-0.12, 0.12, len(seed_tot))
                   if len(seed_tot) > 1 else np.array([0.0]))
            ax.scatter(np.full(len(seed_tot), i) + jit, seed_tot, s=22,
                       color='white', edgecolor='black', linewidth=0.9, zorder=11)
            seed_max = max(seed_max, max(seed_tot))
        ax.text(i, (totals[i] + tot_std[i]) * 1.02, f'${totals[i]:.0f}',
                ha='center', va='bottom', fontsize=ANNOT_FS, fontweight='bold')
        snap[PN.DISPLAY[p]] = dict(hardware_usd=round(hw[i], 2),
                                   license_usd=round(lic[i], 2),
                                   total_usd=round(totals[i], 2),
                                   total_std_usd=round(tot_std[i], 2),
                                   license_pct=round(lic_pct, 1))
    (HERE / 'data_la_cost_structure.json').write_text(
        json.dumps({'headline_N': HEADLINE_N, 'currency': CCY, 'by_policy': snap},
                   indent=2))

    ax.set_xticks(x)
    ax.set_xticklabels([PN.DISPLAY[p] for p in pols], rotation=35, ha='right')
    ax.set_ylabel(f'$\\bar{{\\gamma}}$ (cost per workflow, {CCY})', fontsize=LABEL_FS)
    ax.set_title(f'Cost structure at N = {HEADLINE_N}  (hardware + license)',
                 fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis='y', alpha=0.3)
    ax.set_ylim(top=max((totals + tot_std).max(), seed_max) * 1.12)
    from matplotlib.patches import Patch
    handles = [Patch(facecolor='#10B981', edgecolor='white', label='Hardware (compute)'),
               Patch(facecolor='#3B82F6', edgecolor='white', label='License (tokens)'),
               Patch(facecolor='#d9d9d9', edgecolor='black', hatch='//', label='Static'),
               Patch(facecolor='#d9d9d9', edgecolor='black', label='Elastic')]
    ax.legend(handles=handles, fontsize=LEG_FS, loc='upper center',
              bbox_to_anchor=(0.5, -0.28), ncol=4, framealpha=0.92)
    fig.tight_layout()
    _save(fig, 'LA_01_cost_structure')
    print('license shares:',
          {PN.DISPLAY[p]: snap[PN.DISPLAY[p]]['license_pct'] for p in pols})


if __name__ == '__main__':
    main()
