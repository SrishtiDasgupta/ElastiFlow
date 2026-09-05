"""Cross-workload figure: elastic-vs-static improvement, one consistent convention.

Every cell is the MEAN IMPROVEMENT ACROSS MATCHED elastic/static policy pairs at
that experiment's headline batch size, computed live from the same datasets that
Sections 9.2-9.4 use. Nothing here is hardcoded.

  SeisSol   N=400  ../plain_results/plain_results_per_run.json
                   pairs: {FCFS,EDF} x {r,c} elastic vs its own static counterpart
  HPO       N=7    ../HPO/results/plots/total_cost_per_run.json
                   pairs: {FCFS,EDF} elastic vs its own static counterpart
  Licence   N=400  ./canonical_results.json      (same N as SeisSol, shared fleet)
                   pairs: FCFS-LAMF/FCFS-ST-LA, EDF-LAMF/EDF-ST-LA, HSM/EDF-ST-LA

Columns run SeisSol -> HPO -> Licence, ordered by decreasing scope for the
scheduler to act on the binding constraint. SeisSol has pool headroom, HPO is
capped at fourteen nodes with per-cluster pinning, and in the licence-constrained
case the binding constraint lies outside the compute tier altogether.

Cost and time rows are percentage improvements. Miss-rate and licence-utilisation
rows are PERCENTAGE POINTS, because a percentage change on a small miss rate is
unstable (0.134 against 0.0525 is -155%, which says more about the base than the
policy). This matches how 9.2-9.4 report those metrics.

Positive = elastic better on every row.

Emits plots/XWORKLOAD_elastic_vs_static.{pdf,png}.
"""
import json
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = HERE / 'plots'
OUT_DIR.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.family": "sans-serif", "font.weight": "bold",
    "axes.labelweight": "bold", "axes.titleweight": "bold",
    "figure.titleweight": "bold", "figure.facecolor": "white",
    "axes.facecolor": "white", "pdf.fonttype": 42,
})
TITLE_FS, LABEL_FS, TICK_FS = 16, 15, 13

# ------------------------------------------------------------------ datasets
PLAIN = json.loads((ROOT / 'plain_results/plain_results_per_run.json').read_text())
HPO = json.loads((ROOT / 'HPO/results/plots/total_cost_per_run.json').read_text())
LIC = json.loads((HERE / 'canonical_results.json').read_text())
LIC_KEY = {'FCFS-ST-LA': 'FCFS-ST-LA', 'FCFS-LAMF': 'LAMF',
           'EDF-ST-LA': 'EDF-ST-LA', 'EDF-LAMF': 'EDF-LAMF', 'HSM': 'EDF-HSM'}
N_SEIS, N_HPO, N_LIC, N_ELU = 400, 7, 400, 400

SEIS_PAIRS = [
    # Cost-sorted only. The licence experiment is cost-sorted throughout
    # (9.4) and HPO has no runtime/cost-sort variants, so averaging the
    # SeisSol runtime-sorted variants in here would compute the three
    # columns of a cross-workload comparison on different bases.
    ('fcfs_moldable_c', 'fcfs_static_c'),
    ('edf_moldable_c', 'edf_static_c'),
]
LIC_PAIRS = [('FCFS-LAMF', 'FCFS-ST-LA'), ('EDF-LAMF', 'EDF-ST-LA'), ('HSM', 'EDF-ST-LA')]


def seis(v, n=N_SEIS):
    return {x['_seed']: x for x in PLAIN.values() if x['_variant'] == v and x['_N'] == n}


def lic(p, n=N_LIC):
    return {x['_seed']: x for x in LIC.values()
            if x['_policy'] == LIC_KEY[p] and x['_N'] == n}


def hpo(mode, order):
    return HPO[f'N{N_HPO}_{mode}_{order}']['per_run']


def _reduce(pairs, get, f, unit, scale=1.0):
    """Mean over pairs of the per-run mean improvement. unit is 'pct' or 'pp'."""
    per_pair = []
    for e, s in pairs:
        E, S = get(e), get(s)
        keys = sorted(set(E) & set(S))
        vals = []
        for k in keys:
            a, b = f(E[k]), f(S[k])
            if unit == 'pct':
                if b:
                    vals.append(100.0 * (1.0 - a / b))
            else:
                vals.append(scale * (b - a))
        if vals:
            per_pair.append(mean(vals))
    return mean(per_pair) if per_pair else np.nan


def hpo_metric(f, unit, higher=False):
    per_pair = []
    for o in ('fcfs', 'edf'):
        E, S = hpo('moldable', o), hpo('static', o)
        vals = []
        for e, s in zip(E, S):
            a, b = f(e), f(s)
            if unit == 'pct':
                if b:
                    vals.append(100.0 * (1.0 - a / b))
            else:
                vals.append(100.0 * (b - a) / N_HPO)
        if vals:
            per_pair.append(mean(vals))
    return mean(per_pair) if per_pair else np.nan


# CPR = gamma_total / |Omega_v|, with the 10% overhead applied as elsewhere.
cpr_seis = lambda x: x['total_cost_eur'] * 1.10 / ((1 - x['overall_miss_rate']) * N_SEIS)
# avg_cost_eur divides by COMPLETED workflows, but overall_miss_rate is a rate over
# SUBMITTED ones, so the old form divided by executed*(1-OMR), which counts nothing.
# |Omega_v| = |Omega| * (1 - OMR), matching cpr_seis and cpr_hpo and the sec 9.2 definition.
cpr_lic = lambda x: (x['total_combined_cost_eur'] * 1.10
                     / (x['total_workflows'] * (1 - x['overall_miss_rate'])))
def cpr_hpo(r):
    valid = sum(1 for w in r['per_wf'].values() if not (w['miss'] or w['budget_miss']))
    return r['cost_total'] / max(1, valid)

NAN = np.nan
ROWS = [
    ('CPR', '%',
     hpo_metric(cpr_hpo, 'pct'),
     _reduce(SEIS_PAIRS, seis, cpr_seis, 'pct'),
     _reduce(LIC_PAIRS, lic, cpr_lic, 'pct')),
    ('Queue wait', '%',
     hpo_metric(lambda r: r['sum_wait_s'], 'pct'),
     _reduce(SEIS_PAIRS, seis, lambda x: x['avg_wait_time_s'], 'pct'),
     _reduce(LIC_PAIRS, lic, lambda x: x['avg_wait_time_s'], 'pct')),
    ('Budget miss', 'pp',
     hpo_metric(lambda r: r['budget_misses'], 'pp'),
     _reduce(SEIS_PAIRS, seis, lambda x: x['budget_miss_rate'], 'pp', 100.0),
     _reduce(LIC_PAIRS, lic, lambda x: x['budget_miss_rate'], 'pp', 100.0)),
    ('Deadline miss', 'pp',
     hpo_metric(lambda r: r['misses'], 'pp'),
     _reduce(SEIS_PAIRS, seis, lambda x: x['deadline_miss_rate'], 'pp', 100.0),
     _reduce(LIC_PAIRS, lic, lambda x: x['deadline_miss_rate'], 'pp', 100.0)),
    ('Turnaround', '%',
     hpo_metric(lambda r: r['sum_turnaround_s'], 'pct'),
     _reduce(SEIS_PAIRS, seis, lambda x: x['avg_flowtime_s'], 'pct'),
     _reduce(LIC_PAIRS, lic, lambda x: x['avg_flowtime_s'], 'pct')),
    ('Licence util.\n($N=400$)', 'pp', NAN, NAN,
     _reduce(LIC_PAIRS, lambda p: lic(p, N_ELU),
             lambda x: -x['eff_lic_util'], 'pp', 1.0)),
]

# columns reordered: SeisSol -> HPO -> Licence
GRID = np.array([[r[3], r[2], r[4]] for r in ROWS])
UNITS = [r[1] for r in ROWS]
METRICS = [r[0] for r in ROWS]
WORKLOADS = ['SeisSol--TinyDA\n($N=400$)', 'HPO\n($N=7$)',
             'Licence-\nConstrained\n($N=400$)']


def main():
    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    vmax = np.nanmax(np.abs(GRID))
    masked = np.ma.masked_invalid(GRID)
    cmap = plt.cm.RdYlGn.copy()
    cmap.set_bad('#e8e8e8')
    im = ax.imshow(masked, cmap=cmap, vmin=-vmax, vmax=vmax, aspect='auto')
    ax.set_xticks(range(len(WORKLOADS)))
    ax.set_xticklabels(WORKLOADS, fontsize=TICK_FS)
    ax.set_yticks(range(len(METRICS)))
    ax.set_yticklabels([f'{m}  [{u}]' for m, u in zip(METRICS, UNITS)],
                       fontsize=TICK_FS)
    ax.set_xlabel('Decreasing scope for the scheduler to act on the binding constraint',
                  fontsize=LABEL_FS - 2)
    ax.set_title('Elastic vs matched static, mean over policy pairs\n'
                 'green = elastic better,  red = elastic worse',
                 fontsize=TITLE_FS)
    for i in range(len(METRICS)):
        for j in range(len(WORKLOADS)):
            v = GRID[i, j]
            if np.isnan(v):
                ax.text(j, i, 'n/a', ha='center', va='center',
                        fontsize=11, color='#888888', fontweight='bold')
            else:
                suffix = '%' if UNITS[i] == '%' else ' pp'
                ax.text(j, i, f'{v:+.1f}{suffix}', ha='center', va='center',
                        fontsize=12.5, color='black', fontweight='bold')
    ax.set_xticks(np.arange(-.5, len(WORKLOADS), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(METRICS), 1), minor=True)
    ax.grid(which='minor', color='white', linewidth=2)
    ax.tick_params(which='minor', length=0)
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('improvement of elastic over matched static', fontsize=LABEL_FS - 4)
    cbar.ax.tick_params(labelsize=TICK_FS - 2)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(OUT_DIR / f'XWORKLOAD_elastic_vs_static.{ext}', dpi=150,
                    bbox_inches='tight')
    print('wrote plots/XWORKLOAD_elastic_vs_static.{pdf,png}')
    for m, u, row in zip(METRICS, UNITS, GRID):
        print(f'  {m.replace(chr(10)," "):26s} [{u:2s}] '
              + '  '.join('  n/a  ' if np.isnan(v) else f'{v:+7.1f}' for v in row))


if __name__ == '__main__':
    main()
