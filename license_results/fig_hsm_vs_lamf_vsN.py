"""HSM (licence-aware gate, rho=0.7) vs EDF-LAMF across the workload-size grid.

The single-N rho sweep is dominated by seed noise; this figure tests whether the
HSM gate's advantage is CONSISTENT across workload sizes N -- the robust
justification. HSM cells come from hsm_rho_sweep.json (_rho == RHO); the EDF-LAMF
baseline is read from canonical_results.json (unchanged code, no re-run).

Two lines per panel (HSM vs EDF-LAMF), seed-mean +/-1 std band, vs N. Also prints
a per-N table and a directional win-count (how many N HSM beats EDF-LAMF on each
metric) -- the consistency argument that survives per-N noise.

Emits plots/HSM_vs_LAMF_vsN.{pdf,png}.
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
CANON = HERE / 'canonical_results.json'
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

# Mode: 'perpool' (LSDYNA rho=.95, ANSYS/ABAQUS rho=.60) or 'uniform' (rho=0.7).
MODE = 'perpool' if 'perpool' in sys.argv else 'uniform'
if MODE == 'perpool':
    DATA = HERE / 'hsm_perpool_sweep.json'
    HSM_POLICY = 'HSM-perpool'
    HSM_LABEL = r'HSM (per-pool: LSDYNA $\rho$=.95, ANSYS/ABAQUS $\rho$=.60)'
    OUT_NAME = 'HSM_PERPOOL_vs_LAMF_vsN'
    SUB = r'per-pool licence-aware gate'
else:
    DATA = HERE / 'hsm_rho_sweep.json'
    HSM_POLICY = 'HSM'
    HSM_LABEL = r'HSM (gate, $\rho$=0.7)'
    OUT_NAME = 'HSM_vs_LAMF_vsN'
    SUB = r'uniform gate $\rho=0.7$'

EUR_TO_USD = 1.10
RHO = 0.7
BASELINE = 'EDF-LAMF'

plt.rcParams.update({
    "font.family": "sans-serif", "font.weight": "bold",
    "axes.labelweight": "bold", "axes.titleweight": "bold",
    "figure.titleweight": "bold", "figure.facecolor": "white",
    "axes.facecolor": "white", "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS = 18, 16, 14, 12
HSM_C, LAMF_C = '#7C3AED', '#DC2626'

# (fn, ylabel, title, lower_is_better)
PANELS = [
    (lambda c: c.get('deadline_miss_rate'), 'Deadline miss rate',
     'Deadline misses vs N', True),
    (lambda c: c.get('overall_miss_rate'), 'Overall miss rate',
     'Overall misses vs N', True),
    (lambda c: (c.get('avg_cost_eur') or 0) * EUR_TO_USD
     if c.get('avg_cost_eur') is not None else None,
     'Avg cost per workflow (USD)', 'Cost vs N', True),
    (lambda c: c.get('eff_lic_util'), 'Effective license utilisation (%)',
     'Effective LU vs N', False),
]


def hsm_cells():
    d = json.loads(DATA.read_text()) if DATA.exists() else {}
    return [c for c in d.values()
            if c.get('_policy') == HSM_POLICY
            and (MODE == 'perpool' or c.get('_rho') == RHO)
            and not c.get('_parse_failed')]


def lamf_cells():
    d = json.loads(CANON.read_text()) if CANON.exists() else {}
    return [c for c in d.values()
            if c.get('_policy') == BASELINE and not c.get('_parse_failed')]


def series(cells, fn):
    by = {}
    for c in cells:
        v = fn(c)
        if v is not None and c.get('_N') is not None:
            by.setdefault(c['_N'], []).append(v)
    ns = sorted(by)
    return ns, np.array([mean(by[n]) for n in ns]), \
        np.array([stdev(by[n]) if len(by[n]) > 1 else 0.0 for n in ns])


def main():
    hsm, lamf = hsm_cells(), lamf_cells()
    if not hsm:
        sys.exit('no HSM cells in hsm_rho_sweep.json')

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    wins = {}
    for ax, (fn, ylabel, title, lower_better) in zip(axes.flat, PANELS):
        hn, hm, hs = series(hsm, fn)
        ln, lm, ls = series(lamf, fn)
        ax.plot(hn, hm, label=HSM_LABEL, color=HSM_C,
                marker='o', lw=2.2, ms=9, mfc='white', mew=2)
        ax.fill_between(hn, hm - hs, hm + hs, color=HSM_C, alpha=0.10)
        ax.plot(ln, lm, label='EDF-LAMF', color=LAMF_C, marker='s',
                lw=2.2, ms=8, mfc='white', mew=2, ls='--')
        ax.fill_between(ln, lm - ls, lm + ls, color=LAMF_C, alpha=0.08)
        # win count at shared N
        lmap = {n: m for n, m in zip(ln, lm)}
        w = sum(1 for n, m in zip(hn, hm) if n in lmap
                and ((m < lmap[n]) == lower_better) and m != lmap[n])
        tot = sum(1 for n in hn if n in lmap)
        wins[title] = (w, tot)
        ax.set_xlabel('Workload size  N (workflows)', fontsize=LABEL_FS)
        ax.set_ylabel(ylabel, fontsize=LABEL_FS)
        ax.set_title(f'{title}   [HSM better: {w}/{tot} N]', fontsize=TITLE_FS - 2)
        ax.set_xticks(hn)
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
        fmt = matplotlib.ticker.ScalarFormatter(useMathText=True)
        fmt.set_powerlimits((-2, 3))
        ax.yaxis.set_major_formatter(fmt)
        ax.yaxis.get_offset_text().set_fontsize(TICK_FS)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=LEG_FS, ncol=2,
               loc='upper center', bbox_to_anchor=(0.5, 0.065), framealpha=0.92)
    fig.suptitle(f'Licence-aware HSM ({SUB}) vs EDF-LAMF across workload size'
                 '\n(r = 0.90, 6 seeds/point; consistency across N is the robust test)',
                 fontsize=TITLE_FS, y=0.999)
    fig.tight_layout(rect=[0, 0.06, 1, 0.94])
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'{OUT_NAME}.{ext}', dpi=150, bbox_inches='tight')

    # per-N table + win summary
    print('wrote plots/HSM_vs_LAMF_vsN.{pdf,png}\n')
    print('HSM better than EDF-LAMF, by metric (directional, shared N):')
    for t, (w, tot) in wins.items():
        print(f'  {t:24s}: {w}/{tot} N')
    print('\nper-N deadline miss (HSM / LAMF):')
    hn, hm, _ = series(hsm, lambda c: c.get('deadline_miss_rate'))
    ln, lm, _ = series(lamf, lambda c: c.get('deadline_miss_rate'))
    lmap = {n: m for n, m in zip(ln, lm)}
    for n, m in zip(hn, hm):
        if n in lmap:
            d = m - lmap[n]
            print(f'  N={n:4d}: {m:.3f} / {lmap[n]:.3f}  ({"HSM" if d<0 else "LAMF"} by {abs(d):.3f})')


if __name__ == '__main__':
    main()
