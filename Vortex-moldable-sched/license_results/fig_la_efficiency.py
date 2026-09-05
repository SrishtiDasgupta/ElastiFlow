"""LA efficiency & waste figures (Group 2): how well the license budget is used.

  LA_04  effective license utilisation (%) vs N      (eff_lic_util = 100 - waste)
  LA_05  license waste (%) vs N                       (waste_frac)
  LA_06  token-seconds per completed workflow vs N    (token_sec_total / n_done)

Effective utilisation = share of the license-token budget spent on workflows
that COMPLETE. This is the one axis where elasticity (fail-fast token
reclamation in the elastic EDF variants) helps: at low load (N=150-200) the
elastic policies direct a larger share of the expensive token budget to
successful work, crossing back to parity/loss as load rises.

Plain `02_*_vs_n` grammar; labels via policy_names.DISPLAY. Reads
canonical_results.json; snapshots to data_la_efficiency.json.
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


def _vals(policy, N, fn):
    out = []
    for c in cells(policy, N):
        v = fn(c)
        if v is not None:
            out.append(v)
    return out


def _save(fig, name):
    pdf = OUT_DIR / f'{name}.pdf'
    png = OUT_DIR / f'{name}.png'
    fig.savefig(pdf, bbox_inches='tight')
    fig.savefig(png, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'  wrote {pdf.name} + {png.name}')


def plot_vs_n(fn, ylabel, title, fname):
    fig, ax = plt.subplots(figsize=(10, 6.5))
    snap = {}
    for p in PN.INTERNAL_ORDER:
        ms, sds = [], []
        for n in NS:
            vs = _vals(p, n, fn)
            ms.append(mean(vs) if vs else float('nan'))
            sds.append(stdev(vs) if len(vs) > 1 else 0.0)
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
    out['LA_04_eff_util'] = plot_vs_n(
        lambda c: c.get('eff_lic_util'),
        'Effective license utilisation (%)',
        'Effective license utilisation vs N',
        'LA_04_eff_util_vs_n')
    # NB: waste_frac == 100 - eff_lic_util exactly, so a waste% plot would just
    # mirror LA_04. Instead show the dollar magnitude of wasted license spend
    # per workflow (= tot_lic * waste_frac/100 / N, in USD) -- complementary info.
    out['LA_05_waste_usd'] = plot_vs_n(
        lambda c: (c['tot_lic'] * c['waste_frac'] / 100 / c['_N'] * 1.10)
        if c.get('tot_lic') is not None and c.get('waste_frac') is not None
        and c.get('_N') else None,
        '$\\gamma^{\\mathrm{waste}}_{\\mathrm{lic}}$ (license waste per workflow, USD)',
        'License spend on incomplete work vs N',
        'LA_05_waste_usd_vs_n')
    out['LA_06_tokensec_per_done'] = plot_vs_n(
        lambda c: (c['token_sec_total'] / c['n_done'] / 3600.0)
        if c.get('token_sec_total') and c.get('n_done') else None,
        'Token-hours per completed workflow',
        'License-time per completed workflow vs N',
        'LA_06_tokensec_per_done_vs_n')
    (HERE / 'data_la_efficiency.json').write_text(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
