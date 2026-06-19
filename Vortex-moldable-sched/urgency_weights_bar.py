import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle, Patch

plt.rcParams.update({
    "font.family": "sans-serif",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "font.weight": "bold",
    "axes.labelweight": "bold",
    "axes.titleweight": "bold",
    "xtick.labelsize": 18,
    "ytick.labelsize": 18,
})

x = np.arange(6)
exposed = np.array([0.60, 0.70, 0.80, 0.90, 0.95, 1.00])
reserved = np.array([0.40, 0.30, 0.20, 0.10, 0.05, 0.00])

COL_EXPOSED = "#0A84C8"
COL_RESERVED = "#CBD5E1"
ZONE_WARM = "#FFF7ED"
ZONE_COOL = "#EFF6FF"
ZONE_LABEL = "#64748B"

BAR_W = 0.6

fig, ax = plt.subplots(figsize=(10, 5))

# Background zones (drawn behind the bars)
ax.add_patch(Rectangle((-0.5, 0), 3.0, 1.0, facecolor=ZONE_WARM,
                        edgecolor="none", zorder=0))
ax.add_patch(Rectangle((2.5, 0), 3.0, 1.0, facecolor=ZONE_COOL,
                        edgecolor="none", zorder=0))

# Stacked bars
ax.bar(x, exposed, width=BAR_W, color=COL_EXPOSED, zorder=2)
ax.bar(x, reserved, width=BAR_W, bottom=exposed, color=COL_RESERVED,
       zorder=2)

# Percentage annotations centred inside the bottom segment
for xi, ev in zip(x, exposed):
    ax.text(xi, ev / 2.0, f"{int(round(ev * 100))}%",
            ha="center", va="center", color="white",
            fontweight="bold", fontsize=18, zorder=3)

# Axes
ax.set_xlabel("Renegotiation Index", fontsize=18, fontweight="bold")
ax.set_ylabel("Fraction of Remaining Slack", fontsize=18,
              fontweight="bold")
ax.set_xlim(-0.5, 5.5)
ax.set_ylim(0, 1.0)
ax.set_xticks(x)
ax.set_xticklabels([str(i) for i in range(6)])
ax.set_yticks([0, 0.25, 0.50, 0.75, 1.00])

ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

# Legend (top-right inside the plot)
legend_handles = [
    Patch(facecolor=COL_EXPOSED, label="Exposed to current iteration"),
    Patch(facecolor=COL_RESERVED, label="Reserved for future iterations"),
]
legend = ax.legend(handles=legend_handles, loc="upper right",
                   frameon=True, fontsize=15)
for txt in legend.get_texts():
    txt.set_fontweight("bold")

plt.tight_layout()
plt.savefig("urgency_weights.png", dpi=300, bbox_inches="tight")
