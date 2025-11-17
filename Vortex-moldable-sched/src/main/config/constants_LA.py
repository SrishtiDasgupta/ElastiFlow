"""
Constants for License-Aware (LAMF) Scheduler

Extends base constants with LA-specific settings for simulation mode.
"""

# Import all base constants
from .constants import *

# ============================================================================
# SIMULATION MODE SETTINGS
# ============================================================================

# LAMF runs in simulation mode by default
SIMULATE = True

# LAMF is moldable (dynamic resource adjustment)
MOLDABLE = True

# Enable resource freeing (scale-down)
FREE_RESOURCES = True

# ============================================================================
# WORKFLOW GENERATION SETTINGS
# ============================================================================

# Total workflows to generate and simulate
# NOTE: Must match between workflow_generator_LA.py and dispatcher_LA.py
TOTAL_WORKFLOWS = 4  # Changed from 5 for meaningful license testing

# Workflow size distribution (mesh sizes)
MESH_DISTRIBUTION = {
    1000: 0.25,  # 25% large mesh
    750: 0.25,   # 25% medium mesh
    500: 0.50    # 50% small mesh
}

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

# License pool capacities (must match /Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml)
LICENSE_POOL_CAPACITY = {
    'ANSYS': 150,
    'ABAQUS': 100,
    'LSDYNA': 120
}

# License calculation strategies
LICENSE_STRATEGIES = {
    'ANSYS': 'ansys_workgroup',  # MEBA-style calculation
    'ABAQUS': 'powerlaw',         # a × cores^b
    'LSDYNA': 'linear'            # 1 token per core
}

# ============================================================================
# DIRECTORY PATHS
# ============================================================================

# Workflow directories for LA simulation
WORKFLOWS_DIR_LA = 'sample_workflows_LA'
WORKFLOW_OUTPUT_DIR_LA = '/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/workflow/sample_workflows_LA'

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
