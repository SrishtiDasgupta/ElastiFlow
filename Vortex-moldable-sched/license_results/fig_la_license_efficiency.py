"""LA license-efficiency figure: effective license utilisation, moldable vs static.

effective license utilisation = % of the license-token budget spent on workflows
that COMPLETE (= 100 − waste%). Moldable EDF's DDM-EDF preemptively reclaims
tokens from workflows about to miss (fail-fast), directing a larger share of the
expensive token budget to successful work.

Two panels:
  (A) effUtil(static) vs effUtil(moldable) across N {100,150,200,250}, bars with
      per-seed std error. N150/200 use the 6-seed confirmation; N100/250 the
      3-seed probe (annotated).
  (B) the per-N moldable−static gap (pp), with the seed count noted.

Reads /tmp/lown_lic_runs (3 seeds, N100-250) and /tmp/confirm_efflic_runs
(6 seeds, N150/200). Snapshots to license_results/data_la_license_efficiency.json,
emits plots/LA_license_efficiency.{pdf,png}.
"""
import glob
import json
import sys
from pathlib import Path
from statistics import mean, pstdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import license_analysis as LA  # noqa: E402

STATIC, MOLD = 'EDF-ST-LA', 'EDF-HSM'
# N -> (run_root, seeds)
SOURCES = {
    100: ('/tmp/lown_lic_runs', [7, 107, 207]),
    150: ('/tmp/confirm_efflic_runs', [7, 107, 207, 1007, 1107, 1207]),
    200: ('/tmp/confirm_efflic_runs', [7, 107, 207, 1007, 1107, 1207]),
    250: ('/tmp/lown_lic_runs', [7, 107, 207]),
}


def eff_utils(policy, N):
    root, seeds = SOURCES[N]
    vals = []
    for s in seeds:
        d = Path(root) / f'{policy}__N{N}__seed{s}'
        rg = glob.glob(str(d / '*_results.csv'))
        if not rg:
            continue
        vals.append(100 - LA.analyze_results(rg[0])['waste_frac'])
    return vals


def main():
    NS = sorted(SOURCES)
    snap = {}
    s_mean, s_sd, m_mean, m_sd, gaps, nseed = [], [], [], [], [], []
    for N in NS:
        sv, mv = eff_utils(STATIC, N), eff_utils(MOLD, N)
        s_mean.append(mean(sv)); s_sd.append(pstdev(sv) if len(sv) > 1 else 0)
        m_mean.append(mean(mv)); m_sd.append(pstdev(mv) if len(mv) > 1 else 0)
        gaps.append(mean(mv) - mean(sv)); nseed.append(len(sv))
        snap[f'N{N}'] = dict(seeds=len(sv),
                             static_effutil=round(mean(sv), 1),
                             moldable_effutil=round(mean(mv), 1),
                             gap_pp=round(mean(mv) - mean(sv), 1))
    (HERE / 'data_la_license_efficiency.json').write_text(json.dumps(snap, indent=2))

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(11, 4.2))
    x = np.arange(len(NS)); w = 0.38
    axA.bar(x - w / 2, s_mean, w, yerr=s_sd, capsize=3, label='static EDF',
            color='#6b7b8c')
    axA.bar(x + w / 2, m_mean, w, yerr=m_sd, capsize=3, label='moldable EDF',
            color='#2e8b57')
    axA.set_xticks(x); axA.set_xticklabels([f'N={n}\n({k} seeds)' for n, k in zip(NS, nseed)])
    axA.set_ylabel('Effective license utilisation (%)')
    axA.set_title('(A) Share of license budget spent on completed work')
    axA.legend(); axA.set_ylim(0, 100); axA.grid(axis='y', alpha=0.3)

    colors = ['#2e8b57' if g > 0 else '#b22222' for g in gaps]
    axB.bar(x, gaps, 0.5, color=colors)
    axB.axhline(0, color='black', lw=0.8)
    axB.set_xticks(x); axB.set_xticklabels([f'N={n}' for n in NS])
    axB.set_ylabel('Δ effective utilisation (pp)\nmoldable − static')
    axB.set_title('(B) Moldable advantage (fail-fast token reclamation)')
    axB.grid(axis='y', alpha=0.3)
    for xi, g in zip(x, gaps):
        axB.text(xi, g + (0.4 if g >= 0 else -0.8), f'{g:+.1f}', ha='center',
                 va='bottom' if g >= 0 else 'top', fontsize=9, fontweight='bold')
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(HERE / 'plots' / f'LA_license_efficiency.{ext}', dpi=200,
                    bbox_inches='tight')
    print('wrote plots/LA_license_efficiency.{pdf,png} and data_la_license_efficiency.json')
    print('gaps (pp):', {f'N{n}': round(g, 1) for n, g in zip(NS, gaps)})


if __name__ == '__main__':
    main()
