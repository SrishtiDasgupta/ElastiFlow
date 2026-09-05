import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score

# Publication-style fonts (PDF-friendly vector text)
TITLE_FS = 24
AXIS_LABEL_FS = 20
TICK_FS = 18
LEGEND_FS = 18
plt.rcParams.update(
    {
        "font.family": "serif",
        "axes.titleweight": "bold",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)

nodes = np.array([1, 2, 4])
meshes = np.array([1000, 750, 500])

# hpc7a.24xlarge measured runtimes (seconds)
hpc24x = {
    1000: np.array([120 + 9.7587, 60 + 58.83335, 60 + 24.34855]),
    750: np.array([180 + 36.8668, 120 + 25.8152, 60 + 0.6822]),
    500: np.array([660 + 29.9875, 7 * 60 + 42.61635, 4 * 60 + 3.0312]),
}

MESH_COLORS = {1000: 'cornflowerblue', 750: 'darkorange', 500: 'green'}


def exp_func_mesh(X, a, b, c, d):
    x, y = X
    return a * np.exp(b * x + c * y) + d


runtimes = hpc24x

fig, ax = plt.subplots(figsize=(8, 6))

N_vals = np.tile(nodes, len(meshes)) / max(nodes)
M_vals = np.repeat(meshes, len(nodes)) / max(meshes)
y_vals = np.concatenate((runtimes[1000], runtimes[750], runtimes[500]))

params, _ = curve_fit(
    exp_func_mesh, (N_vals, M_vals), y_vals, p0=[1000, -1, -1, 100], maxfev=5000
)
pred = exp_func_mesh((N_vals, M_vals), *params)
r2 = r2_score(y_vals, pred)
print(f'hpc7a.24xlarge  params={params}  R2={r2:.4f}')

x_smooth = np.linspace(1, 8, 100)
for mesh in meshes:
    y = runtimes[mesh]
    color = MESH_COLORS[mesh]
    ax.scatter(nodes, y, color=color, s=70, edgecolors='black',
               linewidth=1.0, zorder=3)
    preds = exp_func_mesh(
        (x_smooth / max(nodes), np.full_like(x_smooth, mesh / 1000.0)), *params
    )
    ax.plot(x_smooth, preds, color=color, linewidth=2.2)

# Mark upper bound of profiling range (measured nodes in {1, 2, 4})
ax.axvline(x=4, color='black', linestyle='--', linewidth=1.2,
           alpha=0.6, zorder=2)
ax.grid(True, alpha=0.4)
ax.set_title(f'SeisSol runtimes — hpc7a.24xlarge   (R² = {r2:.4f})',
             fontsize=TITLE_FS, fontweight='bold')
ax.set_xlabel('Number of instances', fontsize=AXIS_LABEL_FS,
              fontweight='bold')
ax.set_ylabel('Runtime in seconds', fontsize=AXIS_LABEL_FS,
              fontweight='bold')
ax.tick_params(axis='both', labelsize=TICK_FS)
for label in ax.get_xticklabels() + ax.get_yticklabels():
    label.set_fontweight('bold')

from matplotlib.patches import Patch
custom_legend = [
    Patch(facecolor='green', label='Mesh Resolution: 500'),
    Patch(facecolor='darkorange', label='Mesh Resolution: 750'),
    Patch(facecolor='cornflowerblue', label='Mesh Resolution: 1000'),
]
ax.legend(handles=custom_legend, loc='upper right', frameon=True,
          prop={'size': LEGEND_FS, 'weight': 'bold'})

plt.tight_layout()
plt.savefig('src/main/plots/speedup_hpc24x.pdf', bbox_inches='tight',
            pad_inches=0.2)
plt.savefig('src/main/plots/speedup_hpc24x.png', bbox_inches='tight',
            pad_inches=0.2, dpi=150)
