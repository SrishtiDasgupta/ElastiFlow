"""
Constants for License-Aware (LAMF) Scheduler

Extends base constants with LA-specific settings for simulation mode.
"""

# Import all base constants
from .constants import *
from elastiflow.config.paths import PACKAGE_DIR

# ============================================================================
# SIMULATION MODE SETTINGS
# ============================================================================

# LAMF runs in simulation mode by default

# LAMF is moldable (dynamic resource adjustment)
MOLDABLE = True

# Enable resource freeing (scale-down)
FREE_RESOURCES = True

# ============================================================================
# WORKFLOW GENERATION SETTINGS
# ============================================================================

# Total workflows to generate and simulate
# NOTE: Must match between workflow_generator_LA.py and dispatcher_LA.py
TOTAL_WORKFLOWS = 700  # Increased for comprehensive testing

# Workflow size distribution (mesh sizes)
MESH_DISTRIBUTION = {
    1000: 0.25,  # 25% large mesh
    750: 0.25,   # 25% medium mesh
    500: 0.50    # 50% small mesh
}

# ============================================================================
# TEMPORAL SCALING SETTINGS (for dispatcher)
# ============================================================================

# Temporal compression factor for arrival pattern
# 1.0  = Baseline (20-hour window, original trace)
# 0.75 = Moderate peak (1.33× arrival rate, 15-hour window)
# 0.5  = Peak demand (2× arrival rate, 10-hour window)
# 0.25 = Extreme burst (4× arrival rate, 5-hour window)
TEMPORAL_COMPRESSION_FACTOR = 0.5

# Submission jitter window (minutes)
# Controls workflow clustering within each time slot
# 20 = Original (workflows spread across 20-minute slot)
# 10 = Moderate clustering
# 2  = Tight clustering (burst scenario)
# 1  = Very tight clustering (extreme burst)
SUBMISSION_JITTER_MINUTES = 2

# ============================================================================
# LICENSE SETTINGS
# ============================================================================

# License type distribution for workflow generation
LICENSE_DISTRIBUTION = {
    'ANSYS': 0.33,    # 33% workflows use ANSYS
    'ABAQUS': 0.33,   # 33% workflows use ABAQUS
    'LSDYNA': 0.34    # 34% workflows use LSDYNA
}

# Software ID mapping
LICENSE_SOFTWARE_ID = {
    'ANSYS': 1,
    'ABAQUS': 2,
    'LSDYNA': 3
}

# License pool capacities (must match elastiflow/config/licenses.yaml)
# Sized to 1.15x the BASELINE STATIC (EDF-ST-LA) peak token demand at N=400, measured
# buffer-free across 6 seeds (max-seed peak: ANSYS=5071, ABAQUS=1376, LSDYNA=5408).
# => static at ~87% peak occupancy. Policy-independent reference (static never buffers),
# replacing the earlier elastic-inflated peak (which oversized the pools by 13-28%).
LICENSE_POOL_CAPACITY = {
    'ANSYS': 5832,    # 1.15 x 5071 static peak (was 6700)
    'ABAQUS': 1582,   # 1.15 x 1376 static peak (was 2200)
    'LSDYNA': 6219    # 1.15 x 5408 static peak (was 7600)
}

# License calculation strategies
LICENSE_STRATEGIES = {
    'ANSYS': 'ansys_workgroup',  # MEBA-style calculation
    'ABAQUS': 'powerlaw',         # a × cores^b
    'LSDYNA': 'linear'            # 1 token per core
}

# === Solver-aware scale-down economics (Henkel & Treiber 2015) ===
# Under the breadth lever (chains-per-instance), shrinking a workflow's footprint
# changes its license cost by sign(d cost / d cores):
#   - linear scaling (LS-Dyna): token-seconds conserved -> license-FLAT -> safe to shrink
#   - concave powerlaw (Abaqus) & workgroup+flat-MEBA (Ansys): shrinking RAISES license cost
#     (measured: shrink 8->1 inst => Abaqus +233%, Ansys +15%) -> NOT safe to shrink
# Since license >> hardware (~10x, paper §6.1), the hardware saved by shrinking the
# non-flat solvers is dwarfed by the license increase, so the license-aware policy
# holds their footprint instead of trimming it.
LICENSE_SHRINK_SAFE = {
    LICENSE_SOFTWARE_ID['LSDYNA']: True,   # linear            -> license-flat
    LICENSE_SOFTWARE_ID['ABAQUS']: False,  # powerlaw (concave) -> shrinking costs more
    LICENSE_SOFTWARE_ID['ANSYS']:  False,  # workgroup + flat MEBA -> shrinking costs more
}

# ============================================================================
# DIRECTORY PATHS
# ============================================================================

# Workflow directories for LA simulation
WORKFLOWS_DIR_LA = 'sample_workflows_LA'
# LA_WORKFLOW_DIR lets an experiment point a run at an alternate workflow set
# (e.g. a solver-skewed deck) without touching the canonical 800-file baseline.
import os as _os
WORKFLOW_OUTPUT_DIR_LA = _os.environ.get(
    'LA_WORKFLOW_DIR',
    f'{PACKAGE_DIR}/workflow/sample_workflows_LA')

# ============================================================================
# MOLDABLE SCHEDULING PARAMETERS (inherited from base)
# ============================================================================

# OPTIM_FCFS_BFACTOR - Budget allocation weights per iteration
# OPTIM_FCFS_DFACTOR - Deadline allocation weights per iteration
# (Already defined in base constants.py)

# ============================================================================
# PERFORMANCE TUNING
# ============================================================================

# Polling intervals (seconds)
WORKFLOW_POLLING = 30           # Check for new workflows every 30s
RESOURCE_UTILIZATION_POLLING = 20 * 60  # Resource metrics every 20 mins

# Timeouts
RESOURCE_REQUEST_TIMEOUT = 3 * 60  # 3 minutes for moldable requests
LICENSE_HOLD_TTL = 5 * 60          # 5 minutes for license holds (two-phase commit)

# Thresholds
SPEEDUP_THRESHOLD = 1.4         # Minimum speedup for heterogeneous instances
DEADLINE_BUFFER = 180           # 3 minute buffer before deadline

# ============================================================================
# DEBUGGING & LOGGING
# ============================================================================

# Enable detailed license logging
DEBUG_LICENSE_ALLOCATION = True

# Log moldable decisions
DEBUG_MOLDABLE_DECISIONS = True

# Track license utilization metrics
TRACK_LICENSE_METRICS = True

# ============================================================================
# DDM-EDF (Deadline-Driven Moldable EDF) PARAMETERS
# ============================================================================

# ============================================================================
# DDM-EDF v3: REFINED URGENCY-BASED PARAMETERS
# ============================================================================
# Key improvements:
# - Separate thresholds for scale-up vs scale-down (no conflicts)
# - Pure time-based slack: slack_ratio = time_remaining / time_elapsed
# - No estimation dependency, works for dynamic workflows
# ============================================================================

# SCALE-UP URGENCY THRESHOLDS
# Trigger intervention when workflow is running out of time
SCALE_UP_URGENCY_CRITICAL = 0.4    # time_remaining < 40% of time_elapsed → force scale-up
SCALE_UP_URGENCY_WARNING = 0.8     # time_remaining < 80% of time_elapsed → attempt scale-up

# SCALE-DOWN URGENCY THRESHOLDS
# Only release resources when workflow has significant excess time
SCALE_DOWN_SAFE_THRESHOLD = 2.0    # time_remaining > 200% of time_elapsed → allow scale-down
SCALE_DOWN_EXCESS_THRESHOLD = 3.5  # time_remaining > 350% of time_elapsed → aggressive scale-down + relax guards

# URGENCY BOOST FACTORS
# Multiply resource allocation aggressiveness based on urgency
URGENCY_BOOST_CRITICAL = 2.5   # 150% more aggressive for critical workflows
URGENCY_BOOST_WARNING = 1.8    # 80% more aggressive for warning workflows
URGENCY_BOOST_NORMAL = 1.0     # Normal allocation (no urgency)

# PREEMPTIVE REALLOCATION
# Keep disabled - overhead > benefit in license-constrained environments
PREEMPTIVE_REALLOC_ENABLED = False

# LEGACY PARAMETERS (for backward compatibility - not used in v3)
DEADLINE_URGENCY_CRITICAL = SCALE_UP_URGENCY_CRITICAL
DEADLINE_URGENCY_WARNING = SCALE_UP_URGENCY_WARNING
DEADLINE_URGENCY_SAFE = 1.5
EXCESS_SLACK_THRESHOLD = SCALE_DOWN_EXCESS_THRESHOLD
URGENCY_BOOST_SAFE = URGENCY_BOOST_NORMAL
