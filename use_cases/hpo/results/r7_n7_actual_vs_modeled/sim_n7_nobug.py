"""
sim_n7_nobug.py — Hypothetical R7 outcome IF EDF scheduler bug #2 were fixed.

ACTUAL R7 carries TWO known scheduler bugs:
  Bug #1 (FIXED in 166cff7): silent submit drop after slow allocations. Affects
    queue→heap drainage. Already fixed before this run; verified data5 entered
    the heap correctly.
  Bug #2 (NOT FIXED): updateFreedResources() does NOT call
    setResourcesAvailable(True). So when a wf scales down (sendFreedResources),
    blocked wfs sitting at heap top are not retried. Only full wf completion
    (returnResources) triggers a retry.

This script models what would have happened if bug #2 had also been fixed:
every scale-down event also makes resourcesAvailable=True, so blocked wfs
get a fresh allocation attempt with the just-freed slots.

GROUND TRUTH = actual R7 timestamps, allocations, scale events, completions.
MODELED = same events, but blocked wfs (notably data5) get earlier retries
that succeed because freed slots are visible.

Usage:
    python sim_n7_nobug.py
"""
import math

# -----------------------------------------------------------------------------
# CONSTANTS (per-epoch runtime in seconds, from R3-R5 measured + verified)
# -----------------------------------------------------------------------------
EPOCH = {
    'g4': {'vgg19': 22.17, 'wide_resnet101_2': 29.66, 'convnext_large': 34.03},
    'g5': {'vgg19': 16.50, 'wide_resnet101_2': 22.30, 'convnext_large': 22.95},
}
RATE = {'slurm': 0.84, 'res_g4': 0.227, 'od_g4': 0.526, 'res_g5': 0.435, 'od_g5': 1.006}

T0 = 1778413716  # data8 submit time (R7 zero point)
def t(epoch_seconds):
    """Convert absolute epoch to seconds-since-R7-start for readability."""
    return epoch_seconds - T0


# -----------------------------------------------------------------------------
# ACTUAL R7 EVENTS (from main_HPO log + executor.outs)
# -----------------------------------------------------------------------------
# Format: wf_short_id: (full_id, submit_epoch, deadline_epoch)
WFS = {
    'data8':  ('hpo-60bf3eb4', 1778413716, 1778419876),  # vgg19
    'data9':  ('hpo-b03a4442', 1778413987, 1778422460),  # convnext_large
    'data5':  ('hpo-df19b440', 1778414106, 1778421395),  # wide_resnet101_2
    'data7':  ('hpo-73120874', 1778414188, 1778419943),  # wide_resnet101_2
    'data3':  ('hpo-a87b8123', 1778414203, 1778419398),  # vgg19
    'data12': ('hpo-ccb43739', 1778414218, 1778419947),  # vgg19
    'data1':  ('hpo-417bb2bd', 1778414224, 1778419419),  # vgg19
}
WF_MODEL = {
    'data8': 'vgg19', 'data9': 'convnext_large', 'data5': 'wide_resnet101_2',
    'data7': 'wide_resnet101_2', 'data3': 'vgg19', 'data12': 'vgg19', 'data1': 'vgg19',
}

# Actual initial allocations (from main_HPO log "EDF moldable allocation" lines)
ACTUAL_INITIAL_ALLOC = {
    'data8':  {'cluster': 'slurm',     'lanes': 4, 'res': 4, 'od': 0},
    'data9':  {'cluster': 'cloud_g4',  'lanes': 3, 'res': 2, 'od': 1},
    'data3':  {'cluster': 'slurm',     'lanes': 2, 'res': 2, 'od': 0},  # partial via on-prem-first
    'data1':  {'cluster': 'cloud_g5',  'lanes': 4, 'res': 2, 'od': 2},
    'data7':  {'cluster': 'cloud_g4',  'lanes': 2, 'res': 0, 'od': 2},
    'data12': {'cluster': 'cloud_g5',  'lanes': 1, 'res': 0, 'od': 1},
    'data5':  {'cluster': 'cloud_g5',  'lanes': 1, 'res': 0, 'od': 1},  # degraded — late alloc
}

# Actual scale events (EDF Scheduler freeing / SCALE-DOWN lines)
ACTUAL_SCALE_EVENTS = [
    # (time_offset_from_T0, wf, type, count, cluster)
    # data8 scaled down 2 slurm lanes mid-iter — ABSORBED by data3 via on-prem-first
    (28*60,  'data8', 'down', 2, 'slurm',    'absorbed_by_data3'),
    # data1 scaled down 2 OD g5 — terminated (cost saving)
    (35*60,  'data1', 'down', 2, 'od_g5',    'terminated'),
    # data12 scaled down 1 OD g5 — terminated
    (47*60,  'data12','down', 1, 'od_g5',    'terminated'),
]

# Actual completions (workflow freed at ...)
ACTUAL_COMPLETIONS = {
    'data8':  1778417386,  # 12:49:46 UTC, makespan 61 min, HIT by 42 min
    'data1':  1778417927,  # 12:58:47 UTC, makespan 62 min, HIT by 25 min
    'data3':  1778418047,  # 13:00:47 UTC, makespan 64 min, HIT by 22 min
    'data12': 1778422670,  # 14:17:50 UTC, makespan 141 min, MISS by 45 min
    # PENDING (R7 still running at time of writing — fill in after run finishes):
    # 'data9':  ?,
    # 'data5':  ?,
    # 'data7':  ?,
}


# -----------------------------------------------------------------------------
# BUG-FREE MODEL: every scale-down triggers blocked-wf retry
# -----------------------------------------------------------------------------
# Key hypothesis: data5 was allocated 1 OD g5 lane at 12:43 UTC because that
# was the earliest moment resourcesAvailable became True (likely a separate
# code path, or after data8 happened to scale-down).
#
# In a bug-free model, data5 would have been retried at:
#   - t=35min (data1 scale-down freed 2 OD g5) → cluster-g5 utilization drops,
#     data5 could grab those just-freed slots OR force fresh OD spawn
#   - Result: data5 allocated lanes=3 (chains=3) instead of lanes=1
#
# Per-iter runtime for data5 wide_resnet101_2 (g5):
#   lanes=1: 25 epochs * ceil(3/1)=3 chunks * 22.30s = 1672s = 28 min/iter
#   lanes=3: 25 epochs * ceil(3/3)=1 chunk  * 22.30s = 558s  = 9 min/iter
#
# data5 has 5+ iterations (per yaml). So:
#   ACTUAL (lanes=1):      5 iters * 28 min = 140 min runtime
#   MODELED (lanes=3):     5 iters * 9 min  = 45 min runtime
#
# data5 deadline window: submit 11:55, deadline 13:56 → 121 min.
#   ACTUAL:   submit + ~48 min wait (12:43 alloc) + 140 min = 199 min → MISS by 78 min
#   MODELED:  submit + ~35 min retry (12:30 alloc) + 45 min = 80 min  → HIT by 41 min
# -----------------------------------------------------------------------------

def per_iter_runtime(model, family, chains, lanes, tinyda):
    """Estimate one iteration's runtime in seconds."""
    return tinyda * math.ceil(chains / max(lanes, 1)) * EPOCH[family][model]


def report_data5():
    print("=" * 70)
    print("data5 HYPOTHETICAL OUTCOME (bug #2 fixed)")
    print("=" * 70)
    chains = 3
    tinyda_avg = 22  # observed in R7 actuals (chains 0.5272→...→...)
    n_iters = 5      # data5.yaml workflowIterations
    for label, lanes in [("ACTUAL (lanes=1)", 1), ("MODELED (lanes=3)", 3)]:
        per_iter = per_iter_runtime('wide_resnet101_2', 'g5', chains, lanes, tinyda_avg)
        total = n_iters * per_iter
        print(f"  {label:25} per-iter={per_iter:.0f}s  total={total:.0f}s = {total/60:.1f} min")


def report_overall():
    print("\n" + "=" * 70)
    print("R7 OVERALL: ACTUAL vs MODELED (bug #2 fixed)")
    print("=" * 70)
    print(f"{'wf':6} | {'ACTUAL ms':>10} | {'ACTUAL':6} | {'MODELED ms':>10} | {'MODELED':7}")
    print("-" * 60)

    for wf in ['data8', 'data1', 'data3', 'data12', 'data5', 'data9', 'data7']:
        full_id, submit, deadline = WFS[wf]
        deadline_min = (deadline - submit) / 60
        if wf in ACTUAL_COMPLETIONS:
            actual_ms = (ACTUAL_COMPLETIONS[wf] - submit) / 60
            actual_status = "HIT" if actual_ms <= deadline_min else "MISS"
        else:
            actual_ms = None
            actual_status = "running"

        # Modeled: only data5/data12 differ from actual (because of bug #2 affecting them)
        # Other wfs were not blocked → modeled ≈ actual
        if wf == 'data5':
            modeled_ms = 80  # see report_data5()
            modeled_status = "HIT" if modeled_ms <= deadline_min else "MISS"
        elif wf == 'data12':
            # data12's iter 0 took ~33 min on lanes=2 OD g5. Bug #2 didn't
            # specifically impact data12's allocation path (it got OD instances
            # spawned). MISS was due to chain growth + slow OD setup.
            modeled_ms = actual_ms if actual_ms else 110  # similar to actual
            modeled_status = "MISS" if modeled_ms > deadline_min else "HIT"
        elif wf in ACTUAL_COMPLETIONS:
            modeled_ms = actual_ms
            modeled_status = actual_status
        else:
            modeled_ms = None
            modeled_status = "running"

        a_str = f"{actual_ms:.0f}m {actual_status}" if actual_ms else "running"
        m_str = f"{modeled_ms:.0f}m {modeled_status}" if modeled_ms else "running"
        print(f"{wf:6} | {a_str:>15} | {m_str:>15}")


if __name__ == '__main__':
    report_data5()
    report_overall()
    print("\nNOTE: data9, data7 still running at time of script write.")
    print("Fill in ACTUAL_COMPLETIONS once R7 finishes for full comparison.")
