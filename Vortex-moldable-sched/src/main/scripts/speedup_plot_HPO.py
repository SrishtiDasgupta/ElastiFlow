import os
import matplotlib.pyplot as plt
from matplotlib import rcParams
import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score

# ---------------------------------------------------------------------------
# Publication-style fonts (matches speedup_plot.py exactly)
# ---------------------------------------------------------------------------
TITLE_FS      = 24
AXIS_LABEL_FS = 20
TICK_FS       = 18
LEGEND_FS     = 18
rcParams.update({
    "font.family":        "serif",
    "axes.titleweight":   "bold",
    "axes.labelweight":   "bold",
    "figure.titleweight": "bold",
    "pdf.fonttype":       42,
    "ps.fonttype":        42,
})

# ---------------------------------------------------------------------------
# HPO profiling data (Option A — per-epoch runtime, collapsed over epochs)
#
# Source: HPO/g4_img64.jsonl, HPO/g5_img64.jsonl (36 measurements each;
# grid = 3 models x 3 worker counts x 4 epoch counts; single-shot per cell).
# Per-epoch values = total_training_time / epochs, averaged over
# epochs in {3, 6, 9, 12}.  image_size = batch_size = 64.
# ---------------------------------------------------------------------------
workers = np.array([1, 2, 4])
models  = ['vgg19', 'wide_resnet101_2', 'convnext_large']

MODEL_FACTORS = {'vgg19': 1.0, 'wide_resnet101_2': 1.34, 'convnext_large': 1.46}

# Pretty labels for the legend (avoid Python identifier shape)
MODEL_LABELS = {
    'vgg19':            'VGG19',
    'wide_resnet101_2': 'Wide ResNet-101-2',
    'convnext_large':   'ConvNeXt-Large',
}

g4_xlarge = {
    'vgg19':            np.array([22.22, 12.76,  6.84]),
    'wide_resnet101_2': np.array([29.74, 18.18,  9.95]),
    'convnext_large':   np.array([35.15, 19.90, 11.98]),
}
g5_xlarge = {
    'vgg19':            np.array([16.87,  9.13,  5.02]),
    'wide_resnet101_2': np.array([22.44, 13.81,  7.76]),
    'convnext_large':   np.array([23.29, 13.82,  8.33]),
}

# ---------------------------------------------------------------------------
# Fit:  runtime_per_epoch = a * workers^(-b) * model_factor
# ---------------------------------------------------------------------------
def power_law_model(X, a, b):
    w, mf = X
    return a * (w ** -b) * mf

def fit_instance(runtimes):
    W_vals = np.tile(workers, len(models))
    M_vals = np.repeat(np.array([MODEL_FACTORS[m] for m in models]), len(workers))
    y_vals = np.concatenate([runtimes[m] for m in models])
    params, _ = curve_fit(power_law_model, (W_vals, M_vals), y_vals,
                          p0=[40, 0.8],
                          bounds=([0, 0.1], [1000, 2.0]),
                          maxfev=50000)
    pred = power_law_model((W_vals, M_vals), *params)
    return params, r2_score(y_vals, pred)

# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------
fig, axs = plt.subplots(1, 2, figsize=(17, 7.5), sharey=True)

colors = {'vgg19': 'cornflowerblue',
          'wide_resnet101_2': 'darkorange',
          'convnext_large': 'green'}

def plot_instance(ax, runtimes, title):
    (a, b), r2 = fit_instance(runtimes)
    print(f"{title}: a={a:.6f}, b={b:.6f}, R^2={r2:.4f}")

    w_smooth = np.linspace(1, 4, 200)
    for m in models:
        y = runtimes[m]
        ax.scatter(workers, y, color=colors[m], s=80, edgecolors='black',
                   linewidth=1.2, zorder=3)
        mf = MODEL_FACTORS[m]
        preds = power_law_model((w_smooth, np.full_like(w_smooth, mf)), a, b)
        ax.plot(w_smooth, preds, color=colors[m], linewidth=2.2,
                label=f'{MODEL_LABELS[m]} (factor = {mf})')

    title_eq = (f'{title}   (R² = {r2:.4f})\n'
                r'$T_{epoch} = $' + f'{a:.3f}' +
                r' $\cdot$ workers$^{-' + f'{b:.3f}' + r'} \cdot$ model_factor')
    ax.set_title(title_eq, fontsize=AXIS_LABEL_FS, fontweight='bold')
    ax.set_xticks([1, 2, 4])
    ax.tick_params(axis='both', labelsize=TICK_FS)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontweight('bold')
    ax.grid(True, alpha=0.4)

print("=" * 70)
print("HPO SPEEDUP CURVE FITTING (power law: a * workers^-b * model_factor)")
print("=" * 70)

plot_instance(axs[0], g4_xlarge, 'g4dn.xlarge (Tesla T4)')
plot_instance(axs[1], g5_xlarge, 'g5.xlarge (NVIDIA A10G)')

# Shared figure-level axis labels (match speedup_plot.py)
fig.supxlabel('Number of workers (GPUs)', fontsize=AXIS_LABEL_FS,
              fontweight='bold', y=0.055)
fig.supylabel('Runtime per epoch (seconds)', fontsize=AXIS_LABEL_FS,
              fontweight='bold', x=0.045)

# Shared legend
handles, labels = axs[0].get_legend_handles_labels()
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.02),
           ncol=3, frameon=False,
           prop={'size': LEGEND_FS, 'weight': 'bold'})

fig.suptitle('HPO runtime per epoch — power-law fit',
             fontsize=TITLE_FS, fontweight='bold')

plt.tight_layout(rect=[0.08, 0.14, 0.98, 0.92])

out_dir = os.path.join(os.path.dirname(__file__), '..', 'plots')
os.makedirs(out_dir, exist_ok=True)
out_pdf = os.path.join(out_dir, 'speedup_HPO.pdf')
out_png = os.path.join(out_dir, 'speedup_HPO.png')
plt.savefig(out_pdf, bbox_inches='tight')
plt.savefig(out_png, dpi=200, bbox_inches='tight')
print(f"\nSaved: {out_pdf}")
print(f"Saved: {out_png}")
