"""Per-pool P_thresh (rho) sensitivity figure for HSM at N=400.

Two panels (deadline miss | licence cost) vs P_thresh, three curves (one per
pool). Each curve sweeps that pool's threshold with the other two held at their
deployed values. Deployed choices marked with dashed verticals (0.60 ANSYS/ABAQUS,
0.95 LSDYNA). Reads hsm_pthresh_sweep.json -> plots/PTHRESH_hsm.{pdf,png}.

Set env PT_PERSOLVER=1 to use per-solver deadline miss (workflows of that pool
only) instead of the overall rate -- cleaner if the overall signal is diluted.
"""
import json, os, sys
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / 'hsm_pthresh_sweep.json'
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

EUR_TO_USD = 1.10
DEPLOYED = {'ANSYS': 0.60, 'ABAQUS': 0.60, 'LSDYNA': 0.95}
OTHERS = {'ANSYS': ('ABAQUS', 'LSDYNA'), 'ABAQUS': ('ANSYS', 'LSDYNA'),
          'LSDYNA': ('ANSYS', 'ABAQUS')}
RHO_KEY = {'ANSYS': '_rho_ansys', 'ABAQUS': '_rho_abaqus', 'LSDYNA': '_rho_lsdyna'}
STYLE = {'ANSYS': dict(c='#1D4ED8', marker='o'),
         'ABAQUS': dict(c='#DC2626', marker='s'),
         'LSDYNA': dict(c='#059669', marker='D')}
PERSOLVER = os.environ.get('PT_PERSOLVER') == '1'

plt.rcParams.update({
    "font.family": "sans-serif", "font.weight": "bold",
    "axes.labelweight": "bold", "axes.titleweight": "bold",
    "figure.titleweight": "bold", "figure.facecolor": "white",
    "axes.facecolor": "white", "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS, LEG_FS = 17, 15, 13, 12


def cells():
    return [c for c in json.loads(DATA.read_text()).values() if not c.get('_parse_failed')]


def curve(data, pool, fn):
    """Points where this pool's rho varies and the other two are at deployed."""
    o1, o2 = OTHERS[pool]
    by = {}
    for c in data:
        if abs(c[RHO_KEY[o1]] - DEPLOYED[o1]) > 1e-9: continue
        if abs(c[RHO_KEY[o2]] - DEPLOYED[o2]) > 1e-9: continue
        v = fn(c, pool)
        if v is not None:
            by.setdefault(c[RHO_KEY[pool]], []).append(v)
    xs = sorted(by)
    return xs, np.array([mean(by[x]) for x in xs]), \
        np.array([stdev(by[x]) if len(by[x]) > 1 else 0.0 for x in xs])


def f_deadline(c, pool):
    if PERSOLVER:
        ps = c.get('per_solver', {}).get(pool)
        if not ps or not ps['n']: return None
        return (ps['n'] - ps['done']) / ps['n']
    return c.get('deadline_miss_rate')


def f_cost(c, pool):
    v = c.get('total_license_cost_eur')
    return v * EUR_TO_USD if v is not None else None


def main():
    if not DATA.exists():
        sys.exit(f'no data: {DATA}')
    data = cells()
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(15, 6))
    dl_lab = ('Per-solver deadline-miss fraction' if PERSOLVER
              else 'Deadline-miss fraction')
    for ax, fn, ylab in [(axL, f_deadline, dl_lab),
                         (axR, f_cost, 'Total licence cost (USD)')]:
        allx = set()
        for pool in ['ANSYS', 'ABAQUS', 'LSDYNA']:
            xs, ms, sds = curve(data, pool, fn)
            if not xs: continue
            allx.update(xs)
            st = STYLE[pool]
            ax.plot(xs, ms, label=pool, color=st['c'], marker=st['marker'],
                    lw=2.2, ms=8, mfc='white', mew=2)
            ax.fill_between(xs, ms - sds, ms + sds, color=st['c'], alpha=0.10)
            ax.axvline(DEPLOYED[pool], color=st['c'], ls='--', lw=1.3, alpha=0.7, zorder=0)
        ax.set_xlabel(r'Per-pool release threshold  $P_{\mathrm{thresh}}$', fontsize=LABEL_FS)
        ax.set_ylabel(ylab, fontsize=LABEL_FS)
        ax.set_xticks(sorted(allx))
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, alpha=0.3)
        fmt = matplotlib.ticker.ScalarFormatter(useMathText=True)
        fmt.set_powerlimits((-2, 3)); ax.yaxis.set_major_formatter(fmt)
        ax.yaxis.get_offset_text().set_fontsize(TICK_FS)
    axL.set_title('Deadline miss vs ' + r'$P_{\mathrm{thresh}}$', fontsize=TITLE_FS)
    axR.set_title('Licence cost vs ' + r'$P_{\mathrm{thresh}}$', fontsize=TITLE_FS)
    h, l = axL.get_legend_handles_labels()
    fig.legend(h, l, fontsize=LEG_FS, ncol=3, loc='upper center',
               bbox_to_anchor=(0.5, 0.07), framealpha=0.92, title='pool (others held at deployed)')
    fig.suptitle(r'Per-pool release-threshold $P_{\mathrm{thresh}}$ sensitivity (N = 400, 6 seeds); '
                 r'dashed = deployed (0.60 ANSYS/ABAQUS, 0.95 LSDYNA)',
                 fontsize=TITLE_FS, y=0.99)
    fig.tight_layout(rect=[0, 0.10, 1, 0.95])
    suff = '_persolver' if PERSOLVER else ''
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'PTHRESH_hsm{suff}.{ext}', dpi=150, bbox_inches='tight')
    print(f'wrote plots/PTHRESH_hsm{suff}.{{pdf,png}}')


if __name__ == '__main__':
    main()
