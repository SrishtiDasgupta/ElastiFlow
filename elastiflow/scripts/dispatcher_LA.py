"""
License-Aware Dispatcher for LAMF Simulation

Loads and dispatches license-aware workflows from sample_workflows_LA/ directory.
All workflows are guaranteed to have license requirements (ANSYS/ABAQUS/LSDYNA).
"""

import time
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import requests
import yaml
import sys
import os

# Import constants from centralized config
from elastiflow.config.constants_LA import (
    TOTAL_WORKFLOWS,
    WORKFLOW_OUTPUT_DIR_LA,
    TEMPORAL_COMPRESSION_FACTOR,
    SUBMISSION_JITTER_MINUTES
)
from elastiflow.utils.validate_workflow import validate_workflow
from elastiflow.config.paths import PACKAGE_DIR


def plotSubmitTimes(submitTimes):
    """Plot workflow submission time distribution"""
    df = submitTimes.copy()
    df['count'] = ""
    df.set_index('submit times', inplace=True)

    # Resample to create continuous time series
    resampled_data = df.resample('20min').count()
    resampled_data = resampled_data.reset_index()

    # Plot
    plt.figure(figsize=(12, 6))
    sns.lineplot(data=resampled_data, x=resampled_data.index, y=resampled_data['delays'], marker='o')
    plt.title('Submit Times Distribution (LAMF Workflows)')
    plt.xlabel('Submit Times')
    plt.ylabel('Number of Submissions')
    plt.xticks(rotation=45)
    plt.grid()
    plt.tight_layout()
    plt.savefig('submitTimes_LA.png')


def delayGenerationFromSubmitTimes(workflows):
    """
    Generate realistic submission delays from historical submit times

    Returns: List of delays (in seconds) between consecutive workflow submissions
    """
    data = pd.read_csv(f'{PACKAGE_DIR}/scripts/submitTimes.csv', sep="\t")
    data['submit times'] = pd.to_datetime(data['submit times'])
    data['submit_time_only'] = data['submit times'].dt.time

    # For small workflow counts (testing), use simple uniform distribution
    # to avoid negative allocation issues with proportional distribution
    if workflows < 20:
        newSubmitTimes = []
        # Distribute workflows evenly across first few time slots
        workflows_per_slot = max(1, workflows // min(workflows, data.shape[0]))
        remaining = workflows

        for i in range(min(workflows, data.shape[0])):
            if remaining <= 0:
                break
            count = min(workflows_per_slot, remaining)
            # SOLUTION 4: Use configurable jitter for small workflow counts too
            times = np.random.uniform(0, SUBMISSION_JITTER_MINUTES, count)
            currTime = pd.to_datetime(data.at[i, 'submit times'])
            for time_val in times:
                newSubmitTimes.append(currTime + pd.to_timedelta(time_val, unit='m'))
            remaining -= count

        newSubmitTimes.sort()
        newSubmitTimesData = pd.DataFrame({"submit times": newSubmitTimes})
        newSubmitTimesData['delays'] = newSubmitTimesData['submit times'].diff()
        newSubmitTimesData['delays'] = newSubmitTimesData['delays'].dt.total_seconds()
        delays = newSubmitTimesData['delays'].to_list()
        delays[0] = 0

        # SOLUTION 4: Apply temporal compression to small workflow counts too
        delays = [max(1.0, d * TEMPORAL_COMPRESSION_FACTOR) for d in delays]

        return delays[:workflows]

    # Original logic for production (workflows >= 20)
    totalSubmitTimes = data['ID'].sum()
    newSubmitTimes = []
    newTotalSubmits = 0

    for i in range(data.shape[0]):
        number_of_submits = data.at[i, 'ID']
        proportion = number_of_submits / totalSubmitTimes
        data.at[i, 'ID'] = int(workflows * proportion) if proportion > 0.02 else math.ceil(workflows * proportion)
        newTotalSubmits += data.at[i, 'ID']

    data.at[int(data.shape[0] / 2), 'ID'] += workflows - newTotalSubmits

    for i in range(data.shape[0]):
        # SOLUTION 4: Reduced jitter for burst clustering
        times = np.random.uniform(0, SUBMISSION_JITTER_MINUTES, data.at[i, 'ID'])
        currTime = pd.to_datetime(data.at[i, 'submit times'])
        for time_val in times:
            newSubmitTimes.append(currTime + pd.to_timedelta(time_val, unit='m'))
        newSubmitTimes.sort()

    newSubmitTimesData = pd.DataFrame({"submit times": newSubmitTimes})
    newSubmitTimesData['delays'] = newSubmitTimesData['submit times'].diff()
    newSubmitTimesData['delays'] = newSubmitTimesData['delays'].dt.total_seconds()
    delays = newSubmitTimesData['delays'].to_list()
    delays[0] = 0

    # SOLUTION 4: Temporal compression for peak demand scenario
    # Compress timeline by TEMPORAL_COMPRESSION_FACTOR
    # 0.5 = 2× faster (20hrs → 10hrs), 0.25 = 4× faster (20hrs → 5hrs)
    delays = [max(1.0, d * TEMPORAL_COMPRESSION_FACTOR) for d in delays]

    return delays[:workflows]


def fetchWorkflow(i, path=None):
    """
    Load license-aware workflow from file

    Args:
        i: Workflow index (or 'end' for termination signal)
        path: Directory path containing workflow YAMLs (defaults to WORKFLOW_OUTPUT_DIR_LA)

    Returns:
        Workflow dict with license_pool and software_id fields
    """
    if path is None:
        # Default: workflows named data0.yaml, data1.yaml in sample_workflows_LA/
        file_name = WORKFLOW_OUTPUT_DIR_LA + "/data" + str(i) + ".yaml"
    else:
        # Custom path: e.g., 'end' workflow needs proper path separator
        file_name = os.path.join(path, str(i) + ".yaml")
    with open(file_name, 'r') as stream:
        try:
            workflow = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(f"  [✗] YAML error loading workflow {i}: {exc}")
            return None

    if workflow is not None and workflow.get('id') != 'END':
        violations = validate_workflow(workflow, 'LA')
        if violations:
            msg = (f"Workflow {workflow.get('id', '<no id>')} ({file_name}) "
                   f"failed LA schema validation:\n  - " + "\n  - ".join(violations))
            raise ValueError(msg)
        print(f"  [✓] Loaded {workflow['id']} (license: {workflow['constraints']['license_pool']})")

    return workflow


def send_workflow(workflow):
    """
    Send workflow via HTTP POST (for real execution mode)

    Not used in simulation mode, but kept for compatibility.
    """
    proxies = {
        "http": None,
        "https": None
    }
    print(workflow)
    response = requests.post('http://0.0.0.0:8080', proxies=proxies, json=workflow)
    if response.status_code == 200:
        print('Success:', response.json())
    else:
        print('Error:', response.status_code, response.text)


def dispatcher_LA(sim, wf_mb):
    """
    LAMF Dispatcher - SimPy process

    Loads license-aware workflows from sample_workflows_LA/ and sends them
    to the scheduler at realistic intervals.

    Args:
        sim: Simulus simulator instance
        wf_mb: Mailbox name for workflow submissions
    """
    delays = delayGenerationFromSubmitTimes(TOTAL_WORKFLOWS)
    # Alternative: Use fixed delays for testing
    # delays = [1, 60, 60, 80, 100, 250, 300, 40, 120, 200, 150, 100]

    print(f'Starting LAMF dispatcher for {TOTAL_WORKFLOWS} workflows at {sim.now}...')
    print(f'All workflows require licenses (ANSYS/ABAQUS/LSDYNA)')
    print('')
    print(f'Temporal Scaling Configuration (Solution 4):')
    print(f'  - Compression factor: {TEMPORAL_COMPRESSION_FACTOR} ({20*TEMPORAL_COMPRESSION_FACTOR:.1f}-hour window)')
    print(f'  - Submission jitter: {SUBMISSION_JITTER_MINUTES} minutes')
    print(f'  - Arrival rate multiplier: {1/TEMPORAL_COMPRESSION_FACTOR:.1f}×')
    print('')

    for i in range(TOTAL_WORKFLOWS):
        sim.sleep(delays[i])
        print(f'[{sim.now:8.1f}s] Dispatching workflow {i}...')

        workflow = fetchWorkflow(i)
        if workflow:
            workflow['submit_time'] = sim.now
            sim.sync().send(sim, wf_mb, str(workflow))
        else:
            print(f'  [✗] Failed to load workflow {i}, skipping')

    # Send END signal after all workflows have had time to finish.
    # The buffer is set generously large (~58 simulated days) so even the
    # slowest workflow at the largest workload size finishes before END
    # fires. Simulus advances through empty simulated time near-instantly,
    # so an oversized buffer adds negligible wall-clock cost — but a buffer
    # that is too small causes still-running workflows to be marked
    # "incomplete" and silently excluded from the cost / miss-rate averages,
    # which biases every metric. Do NOT shrink this value without verifying
    # `Incomplete workflows: 0` in the summary at the highest N tested.
    print('')
    print(f'[{sim.now:8.1f}s] All workflows dispatched, waiting for completion...')
    sim.sleep(5_000_000)  # generous: ~58 simulated days
    print(f'[{sim.now:8.1f}s] Sending END signal')
    sim.sync().send(sim, wf_mb, str(fetchWorkflow('end', f"{PACKAGE_DIR}")))


if __name__ == "__main__":
    """
    Standalone dispatcher for real execution mode (HTTP POST)

    Not typically used - simulation mode is preferred.
    """
    delays = [1, 60, 60, 80, 100, 250, 300, 40, 120, 200, 150, 100]
    print(f"Starting LAMF dispatcher (real mode) at {time.time()}")

    for i in range(0, TOTAL_WORKFLOWS):
        print(f"Sending workflow {i} at {time.time()}")
        wf = fetchWorkflow(i)
        if wf:
            send_workflow(wf)
        time.sleep(delays[i])
