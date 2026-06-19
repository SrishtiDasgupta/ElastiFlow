"""LA cost-structure trend figures (Group 1, plots 2 & 3): vs-N line plots.

  LA_02  license-to-hardware cost ratio (%) vs N   (overhead = tot_lic/tot_hw)
  LA_03  license cost per COMPLETED workflow (USD) vs N   (lic_per_done)

Both follow the Plain chapter's `02_*_vs_n` grammar: figsize (10, 6.5),
per-policy family colour (FCFS amber / EDF blue / HSM emerald), static = dashed
+ open marker, elastic = solid + filled marker, std fill-band across the 6
seeds, legend below. Labels via policy_names.DISPLAY.

Reads canonical_results.json; snapshots to data_la_vs_n.json.
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

NS = [150, 200, 300, 400, 500, 600, 700]
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

FAMILY = {'FCFS': '#F59E0B', 'EDF': '#1D4ED8', 'HSM': '#059669'}
# Static = dashed + open marker (mfc white); Elastic = solid + filled marker.
STYLE = {
    'FCFS-ST-LA': dict(c=FAMILY['FCFS'], ls='--', marker='o', mfc='white'),
    'EDF-ST-LA':  dict(c=FAMILY['EDF'],  ls='--', marker='o', mfc='white'),
    'LAMF':       dict(c=FAMILY['FCFS'], ls='-',  marker='o'),
    'EDF-LAMF':   dict(c=FAMILY['EDF'],  ls='-',  marker='o'),
    'EDF-HSM':    dict(c=FAMILY['HSM'],  ls='-',  marker='D'),
}


def cells(policy, N):
    return [v for v in CANON.values()
            if v.get('_policy') == policy and v.get('_N') == N
            and not v.get('_parse_failed')]


def agg(policy, N, key, scale=1.0):
    vs = [c[key] * scale for c in cells(policy, N) if c.get(key) is not None]
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


def plot_vs_n(key, ylabel, title, fname, scale=1.0):
    fig, ax = plt.subplots(figsize=(10, 6.5))
    snap = {}
    for p in PN.INTERNAL_ORDER:
        ms, sds = [], []
        for n in NS:
            m, s = agg(p, n, key, scale)
            ms.append(m); sds.append(s)
        ms, sds = np.array(ms), np.array(sds)
        ax.plot(NS, ms, label=PN.DISPLAY[p], linewidth=2.2, markersize=8, **STYLE[p])
        ax.fill_between(NS, ms - sds, ms + sds, color=STYLE[p]['c'], alpha=0.08)
        snap[PN.DISPLAY[p]] = {f'N{n}': round(float(v), 2) for n, v in zip(NS, ms)}
    ax.set_xticks(NS)
    ax.set_xlabel('Batch size N (workflows)', fontsize=LABEL_FS)
    ax.set_ylabel(ylabel, fontsize=LABEL_FS)
    ax.set_title(title, fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=LEG_FS, ncol=3, loc='upper center',
              bbox_to_anchor=(0.5, -0.18), framealpha=0.92)
    fig.tight_layout()
    _save(fig, fname)
    return snap


def main():
    out = {}
    out['LA_02_overhead'] = plot_vs_n(
        'overhead',
        'License-to-hardware cost ratio (%)',
        'License intensity vs batch size N',
        'LA_02_overhead_vs_n')
    out['LA_03_lic_per_done'] = plot_vs_n(
        'lic_per_done',
        f'License cost per completed workflow ({CCY})',
        'License spend per completed workflow vs N',
        'LA_03_lic_per_done_vs_n', scale=EUR_TO_USD)
    (HERE / 'data_la_vs_n.json').write_text(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
