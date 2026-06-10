"""LA per-solver & per-pool figures (Group 3): where the tokens actually go.

  LA_07  completion rate (%) by solver x policy at headline N   (per_solver done/n)
  LA_08  per-pool active token utilisation (%) by policy        (pool_tw_util)

LA_07 is the chapter's fairness-mechanism plot: deadline-driven ordering (the
EDF family) balances completion across the three commercial solvers, whereas
FCFS-class ordering starves the heaviest-token solver (ANSYS) because long
ANSYS jobs sit behind cheaper work. Static bars are hatched (//), matching the
Plain grammar. Reads canonical_results.json; labels via policy_names.DISPLAY.
Snapshots to data_la_solver_pool.json.
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
SOLVERS = ['ANSYS', 'ABAQUS', 'LSDYNA']
SOLVER_COL = {'ANSYS': '#8B5CF6', 'ABAQUS': '#F59E0B', 'LSDYNA': '#10B981'}

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
            and not v.get('_parse_failed')]


def _mean_std(vs):
    vs = [v for v in vs if v is not None]
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


def _grouped_bar(metric_fn, ylabel, title, fname, ylim_top=None, fmt='{:.0f}'):
    """metric_fn(cell, solver) -> per-seed value or None. Grouped bars:
    x = policy, 3 solver/pool bars per group, static groups hatched."""
    pols = PN.INTERNAL_ORDER
    x = np.arange(len(pols))
    w = 0.26
    fig, ax = plt.subplots(figsize=(13, 6.5))
    snap = {}
    for j, sv in enumerate(SOLVERS):
        ms, sds = [], []
        for p in pols:
            vals = [metric_fn(c, sv) for c in cells(p, HEADLINE_N)]
            m, s = _mean_std(vals)
            ms.append(m); sds.append(s)
            snap.setdefault(PN.DISPLAY[p], {})[sv] = round(m, 1)
        ms, sds = np.array(ms), np.array(sds)
        bars = ax.bar(x + (j - 1) * w, ms, w, yerr=sds, capsize=3,
                      color=SOLVER_COL[sv], label=sv,
                      edgecolor='black', linewidth=0.8)
        for i, p in enumerate(pols):
            if p in PN.STATIC:
                bars[i].set_hatch('//')
    ax.set_xticks(x)
    ax.set_xticklabels([PN.DISPLAY[p] for p in pols], rotation=20, ha='right')
    ax.set_ylabel(ylabel, fontsize=LABEL_FS)
    ax.set_title(title, fontsize=TITLE_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.grid(True, axis='y', alpha=0.3)
    if ylim_top:
        ax.set_ylim(0, ylim_top)
    from matplotlib.patches import Patch
    handles = [Patch(facecolor=SOLVER_COL[s], edgecolor='black', label=s) for s in SOLVERS]
    handles += [Patch(facecolor='#d9d9d9', edgecolor='black', hatch='//', label='Static'),
                Patch(facecolor='#d9d9d9', edgecolor='black', label='Elastic')]
    ax.legend(handles=handles, fontsize=LEG_FS, loc='upper center',
              bbox_to_anchor=(0.5, -0.18), ncol=5, framealpha=0.92)
    fig.tight_layout()
    _save(fig, fname)
    return snap


def _completion(c, solver):
    ps = c.get('per_solver', {}).get(solver)
    if not ps or not ps.get('n'):
        return None
    return 100 * ps['done'] / ps['n']


def _pool_util(c, pool):
    return c.get('pool_tw_util', {}).get(pool)


def _lic_share(c, solver):
    ps = c.get('per_solver', {})
    tot = sum(v.get('lic', 0) for v in ps.values())
    if not tot or solver not in ps:
        return None
    return 100 * ps[solver].get('lic', 0) / tot


def main():
    out = {}
    out['LA_07_completion_by_solver'] = _grouped_bar(
        _completion,
        'Completion rate (%)',
        f'Per-solver completion at N = {HEADLINE_N}  '
        '(FCFS starves ANSYS; EDF balances)',
        'LA_07_completion_by_solver', ylim_top=109)
    out['LA_08_pool_util'] = _grouped_bar(
        _pool_util,
        'Active token utilisation (%)',
        f'Per-pool active utilisation at N = {HEADLINE_N}',
        'LA_08_pool_util', ylim_top=None)
    out['LA_08b_lic_share'] = _grouped_bar(
        _lic_share,
        'Share of license spend (%)',
        f'Per-solver license-cost share at N = {HEADLINE_N}  '
        '(ANSYS dominates the budget)',
        'LA_08b_lic_share', ylim_top=70)
    (HERE / 'data_la_solver_pool.json').write_text(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
