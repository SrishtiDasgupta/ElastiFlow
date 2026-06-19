import numpy as np
import matplotlib.pyplot as plt

# -----------------------------
# Global plot styling
# -----------------------------
plt.rcParams.update(
    {
        "font.weight": "bold",
        "axes.titleweight": "bold",
        "axes.labelweight": "bold",
        "mathtext.default": "bf",
        "axes.titlesize": 24,
        "axes.labelsize": 22,
        "xtick.labelsize": 20,
        "ytick.labelsize": 20,
        "legend.fontsize": 20,
    }
)

# -----------------------------
# Data
# -----------------------------
i = np.array([1, 2, 3, 4, 5])
I_w = 5

alpha = np.array([0.40, 0.55, 0.70, 0.88, 1.00])
d_rem = np.array([10.0, 8.2, 6.5, 4.8, 3.2])

d_exposed = alpha * d_rem
d_withheld = (1 - alpha) * d_rem

# -----------------------------
# Annotation styling
# -----------------------------
callout_fs = 17
note_fs = 17

note_box = dict(
    boxstyle="round,pad=0.35",
    fc="white",
    ec="0.55",
    alpha=0.96,
)

callout_box = dict(
    boxstyle="circle,pad=0.25",
    fc="white",
    ec="black",
    alpha=0.98,
)

arrow = dict(
    arrowstyle="->",
    linewidth=1.4,
    color="black",
    shrinkA=4,
    shrinkB=4,
)

# -----------------------------
# Figure
# -----------------------------
fig, axes = plt.subplots(1, 2, figsize=(18, 7.2))

# ============================================================
# Left panel: urgency weight schedule
# ============================================================
ax = axes[0]

ax.plot(i, alpha, marker="o", linewidth=2.5, zorder=3)
ax.fill_between(i, 0, alpha, alpha=0.18, zorder=1)
ax.axhline(1.0, linestyle="--", linewidth=1.5, zorder=2)

ax.set_title(r"Urgency weight schedule $\{\alpha_i^w\}$")
ax.set_xlabel(r"iteration index $i$")
ax.set_ylabel(r"$\alpha_i^w$", rotation=0, labelpad=18)

ax.set_xlim(0.75, 5.25)
ax.set_ylim(0, 1.08)

ax.set_xticks(i)
ax.set_xticklabels(["1", "2", "3", "4", r"$5 = I^w$"])

ax.set_yticks([0.25, 0.50, 0.75, 1.00])
ax.set_yticklabels(["0.25", "0.50", "0.75", "1.00"])

ax.grid(axis="y", alpha=0.25, zorder=0)

# Small callouts only inside the data area
ax.annotate(
    "1",
    xy=(2.6, 0.70),
    xytext=(2.25, 0.86),
    fontsize=callout_fs,
    ha="center",
    va="center",
    bbox=callout_box,
    arrowprops=arrow,
    zorder=5,
)

ax.annotate(
    "2",
    xy=(3.0, 0.70),
    xytext=(3.35, 0.58),
    fontsize=callout_fs,
    ha="center",
    va="center",
    bbox=callout_box,
    arrowprops=arrow,
    zorder=5,
)

ax.annotate(
    "3",
    xy=(5.0, 1.0),
    xytext=(4.55, 0.96),
    fontsize=callout_fs,
    ha="center",
    va="center",
    bbox=callout_box,
    arrowprops=arrow,
    zorder=5,
)

# Explanatory text collected in one non-overlapping box
ax.text(
    0.04,
    0.06,
    "1  Withheld fraction: "
    r"$1-\alpha_i^w$"
    "\n"
    "2  Monotone non-decreasing schedule"
    "\n"
    "3  Full residual exposed at "
    r"$i=I^w$",
    transform=ax.transAxes,
    fontsize=note_fs,
    va="bottom",
    ha="left",
    bbox=note_box,
    zorder=6,
)

# ============================================================
# Right panel: effect on residual slack
# ============================================================
ax = axes[1]

bar_width = 0.45

ax.bar(
    i,
    d_exposed,
    width=bar_width,
    label=r"$\tilde{d}_{\mathrm{rem}}^w(i)=\alpha_i^w \cdot d_{\mathrm{rem}}^w(i)$: exposed to iteration $i$",
    zorder=3,
)

ax.bar(
    i,
    d_withheld,
    width=bar_width,
    bottom=d_exposed,
    color="lightgray",
    edgecolor="black",
    linewidth=0.8,
    label="reserve withheld for future iterations",
    zorder=3,
)

ax.plot(
    i,
    d_rem,
    linestyle="--",
    linewidth=2,
    color="black",
    label=r"$d_{\mathrm{rem}}^w(i)$: total residual",
    zorder=4,
)

ax.set_title(
    r"Effect on urgency-weighted residual" "\n"
    r"$\tilde{d}_{\mathrm{rem}}^w(i)=\alpha_i^w \cdot d_{\mathrm{rem}}^w(i)$",
)

ax.set_xlabel(r"iteration index $i$")
ax.set_ylabel(
    r"$d_{\mathrm{rem}}^w(i)$"
    "\nremaining deadline slack\n(arbitrary units)",
)

ax.set_xlim(0.5, 5.5)
ax.set_ylim(0, max(d_rem) * 1.18)

ax.set_xticks(i)
ax.set_xticklabels(["1", "2", "3", "4", r"$5 = I^w$"])

ax.set_yticks([])

ax.grid(axis="y", alpha=0.20, zorder=0)

# Small callouts only
ax.annotate(
    "1",
    xy=(1.0, d_exposed[0] + d_withheld[0] * 0.55),
    xytext=(0.75, 7.7),
    fontsize=callout_fs,
    ha="center",
    va="center",
    bbox=callout_box,
    arrowprops=arrow,
    zorder=6,
)

ax.annotate(
    "2",
    xy=(3.0, d_rem[2]),
    xytext=(3.35, 8.2),
    fontsize=callout_fs,
    ha="center",
    va="center",
    bbox=callout_box,
    arrowprops=arrow,
    zorder=6,
)

ax.annotate(
    "3",
    xy=(5.0, d_exposed[-1]),
    xytext=(4.65, 4.4),
    fontsize=callout_fs,
    ha="center",
    va="center",
    bbox=callout_box,
    arrowprops=arrow,
    zorder=6,
)

# Explanatory text in one box, away from bars and ticks
ax.text(
    0.08,
    0.08,
    "1  Early iterations withhold more slack"
    "\n"
    "2  Dashed curve: total residual "
    r"$d_{\mathrm{rem}}^w(i)$"
    "\n"
    "3  Final iteration exposes full residual: "
    r"$\alpha_{I^w}^w=1$",
    transform=ax.transAxes,
    fontsize=note_fs,
    va="bottom",
    ha="left",
    bbox=note_box,
    zorder=6,
)

# -----------------------------
# Styling
# -----------------------------
for ax in axes:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", which="major", labelsize=20)

# Single legend centered under both subplots
# -----------------------------
# Legend (clean, compact, centered)
# -----------------------------
handles, labels = axes[1].get_legend_handles_labels()
fig.legend(
    handles,
    labels,
    loc="lower center",
    # Place legend in the reserved bottom margin (below both subplots)
    bbox_to_anchor=(0.5, 0.005),
    frameon=True,
    ncol=3,
    columnspacing=1.2,
    handletextpad=0.6,
    borderpad=0.6,
)

# Reserve enough room so the legend never overlaps axis labels
fig.subplots_adjust(bottom=0.30, wspace=0.25)

# Save PDF with only the plot
plt.savefig("urgency_weight_schedule.pdf", bbox_inches="tight", pad_inches=0.05)
plt.close(fig)