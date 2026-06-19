"""Regenerate the SeisSol-plain submission-times distribution plot.

KDE over normalized submission timestamps (in seconds) for:
  - original trace (submitTimes.csv, weighted by per-slot ID count)
  - 200-workflow proportional sample
  - 500-workflow proportional sample

Reproduces students/Kavitha S./Thesis/figures/submit_times.png.
"""
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde

CSV = '/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/scripts/submitTimes.csv'
OUT_PNG = '/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/plots/submit_times.png'
OUT_PDF = '/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/plots/submit_times.pdf'


def sample_submission_times(N, seed=0):
    """Replicates dispatcher.delayGenerationFromSubmitTimes(N) but returns timestamps."""
    np.random.seed(seed)
    data = pd.read_csv(CSV, sep='\t')
    data['submit times'] = pd.to_datetime(data['submit times'])
    total = data['ID'].sum()
    counts = []
    for i in range(data.shape[0]):
        p = data.at[i, 'ID'] / total
        counts.append(int(N * p) if p > 0.02 else math.ceil(N * p))
    counts = np.array(counts)
    counts[len(counts) // 2] += N - counts.sum()

    timestamps = []
    for i, c in enumerate(counts):
        jitter = np.random.uniform(0, 20, c)  # minutes
        base = data.at[i, 'submit times']
        for j in jitter:
            timestamps.append(base + pd.to_timedelta(j, unit='m'))

    timestamps = sorted(timestamps)[:N]
    return timestamps


def trace_timestamps():
    """Original trace: expand each slot's ID count into that many copies of its timestamp."""
    data = pd.read_csv(CSV, sep='\t')
    data['submit times'] = pd.to_datetime(data['submit times'])
    out = []
    for _, row in data.iterrows():
        out.extend([row['submit times']] * int(row['ID']))
    return out


def to_seconds(ts_list, origin):
    return [(t - origin).total_seconds() for t in ts_list]


def main():
    orig = trace_timestamps()
    s200 = sample_submission_times(200, seed=0)
    s500 = sample_submission_times(500, seed=0)

    origin = min(orig + s200 + s500)
    orig_s = to_seconds(orig, origin)
    s200_s = to_seconds(s200, origin)
    s500_s = to_seconds(s500, origin)

    all_vals = np.array(orig_s + s200_s + s500_s)
    grid = np.linspace(all_vals.min() - 5000, all_vals.max() + 5000, 1000)

    def scaled_kde(data, weight):
        # Wider bandwidth (~2× Scott's rule) to match thesis figure
        kde = gaussian_kde(data, bw_method='scott')
        kde.set_bandwidth(kde.factor * 1.35)
        return kde(grid) * weight / max(len(orig), 1)

    y_200  = scaled_kde(s200_s, len(s200_s))
    y_orig = scaled_kde(orig_s, len(orig_s))
    y_500  = scaled_kde(s500_s, len(s500_s))

    plt.rcParams.update({
        'font.family': 'serif',
        'axes.titleweight': 'bold',
        'axes.labelweight': 'bold',
        'pdf.fonttype': 42,
    })

    fig, ax = plt.subplots(figsize=(13, 7))
    ax.plot(grid, y_200,  label='200 workflows', color='tab:blue',   linewidth=2)
    ax.plot(grid, y_orig, label='original data', color='tab:orange', linewidth=2)
    ax.plot(grid, y_500,  label='500 workflows', color='tab:green',  linewidth=2)
    ax.set_title('Submission Times Distribution', fontsize=24, fontweight='bold')
    ax.set_xlabel('Elapsed time since first submission (s)', fontsize=22, fontweight='bold')
    ax.set_ylabel('Proportion of Submissions', fontsize=22, fontweight='bold')
    ax.tick_params(axis='both', labelsize=18)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontweight('bold')
    ax.grid(True)
    leg = ax.legend(
        loc='upper center', bbox_to_anchor=(0.5, -0.18),
        ncol=3, frameon=False, fontsize=20,
    )
    for txt in leg.get_texts():
        txt.set_fontweight('bold')
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.22)
    plt.savefig(OUT_PNG, dpi=150)
    plt.savefig(OUT_PDF)
    print(f'Saved {OUT_PNG}')
    print(f'Saved {OUT_PDF}')


if __name__ == '__main__':
    main()
