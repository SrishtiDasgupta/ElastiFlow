"""The dispatcher: one arrival loop for every use case (B7.6).

`dispatcher(backend, arrivals)` sleeps the inter-arrival delays of an
`Arrivals` profile on the backend's clock, loads each workflow plan, stamps its
submit time and sends it on the backend's workflows channel, then waits and
sends END. The profiles live with their use case: `SEISSOL` below,
`dispatcher_LA.LICENCE`, `dispatcher_HPO.arrivals(...)`. Their delay
generators and loaders are the use cases' own, moved verbatim, so a seeded run
draws the same random sequence as before the merge.
"""
import time
import math
from dataclasses import dataclass
from typing import Callable
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import requests
import yaml

from elastiflow.config.constants import SEED, TOTAL_WORKFLOWS
from elastiflow.utils.validate_workflow import validate_workflow
from elastiflow.config.paths import PACKAGE_DIR

# DEPRECATED
def delay_generation(workflows):
    # slowest runtime in secs * tinyda iterations * workflow iterations
    np.random.seed(0)
    mean_time = 1800
    x = 0
    y=0
    request_times = np.random.normal(loc=mean_time, scale=1000, size=workflows)
    request_times = np.clip(request_times,0, None)
    delays = []
    request_times = np.sort(request_times)
    for request_time in request_times:
        delay = request_time - request_times[y]
        delay = np.abs(delay)
        # delay = (delay * delay_factor) + constant_delay
        delays.append(delay)
        y = x
        x = x + 1
    # print(delays)
    return delays

def plotSubmitTimes(submitTimes, title='Submit Times Distribution', path='submitTimes.png'):
    df = submitTimes.copy()
    df['count'] = ""
    df.set_index('submit times', inplace=True)

    # Resample the data to create a continuous time series (e.g., hourly)
    # You can change 'H' to 'D' for daily, 'M' for monthly, etc.
    resampled_data = df.resample('20min').count()  # Count submissions per hour

    # Reset the index to have a DataFrame for plotting
    resampled_data = resampled_data.reset_index()
    # print(resampled_data)  # Count submissions per hour
    # Plot the time series
    plt.figure(figsize=(12, 6))
    sns.lineplot(data=resampled_data, x=resampled_data.index, y=resampled_data['delays'], marker='o')
    plt.title(title)
    plt.xlabel('Submit Times')
    plt.ylabel('Number of Submissions')
    plt.xticks(rotation=45)
    plt.grid()
    plt.tight_layout()
    plt.savefig(path)

def delayGenerationFromSubmitTimes(workflows):
    np.random.seed(SEED)  # Seed lifted to config.constants.SEED so
                          # simulate_sweep.py can override per cell.
    data = pd.read_csv(f'{PACKAGE_DIR}/scripts/submitTimes.csv', sep="\t")
    data['submit times'] = pd.to_datetime(data['submit times'])
    data['submit_time_only'] = data['submit times'].dt.time
    totalSubmitTimes = data['ID'].sum()
    newSubmitTimes = []
    newTotalSubmits = 0
    for i in range(data.shape[0]):
        number_of_submits = data.at[i,'ID']
        proportion = number_of_submits/ totalSubmitTimes
        data.at[i,'ID'] = int(workflows * proportion) if proportion > 0.02 else math.ceil(workflows * proportion)
        newTotalSubmits += data.at[i,'ID']
    data.at[int(data.shape[0]/2), 'ID'] += workflows - newTotalSubmits
    for i in range(data.shape[0]):
        times = np.random.uniform(0,20, data.at[i,'ID'])
        currTime = pd.to_datetime(data.at[i,'submit times'])
        for time in times:
            newSubmitTimes.append(currTime + pd.to_timedelta(time, unit='m'))
        newSubmitTimes.sort()
    newSubmitTimesData = pd.DataFrame({"submit times": newSubmitTimes})
    submit_times = newSubmitTimesData['submit times'].to_list()
    # print(submit_times)
    newSubmitTimesData['delays'] = newSubmitTimesData['submit times'].diff()
    newSubmitTimesData['delays'] = newSubmitTimesData['delays'].dt.total_seconds()
    delays = newSubmitTimesData['delays'].to_list()
    delays[0] = 0
    # plotSubmitTimes(newSubmitTimesData) #if want to plot the submit time graph
    # print(delays[0:workflows])
    return delays[:workflows]
    # print(newSubmitTimesData['submit times'])


def fetchWorkflow(i, path = f"{PACKAGE_DIR}/sample_workflows/data"):
    file_name = path + str(i) + ".yaml"
    with open(file_name, 'r') as stream:
        try:
            workflow = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(f" error: {exc}")
            return None
    if workflow is not None and workflow.get('id') != 'END':
        violations = validate_workflow(workflow, 'PLAIN')
        if violations:
            msg = (f"Workflow {workflow.get('id', '<no id>')} ({file_name}) "
                   f"failed PLAIN schema validation:\n  - " + "\n  - ".join(violations))
            raise ValueError(msg)
    return workflow

def send_workflow(workflow, scheduler_host='0.0.0.0', port=8080):
    """POST a workflow to the Gateway (the real-mode submitter below)."""
    proxies = {
    "http": None,
    "https": None
    }
    print(workflow)
    response = requests.post(f'http://{scheduler_host}:{port}', proxies=proxies, json=workflow)
    if response.status_code == 200:
        print('Success:', response.json())
    else:
        print('Error:', response.status_code, response.text)


@dataclass
class Arrivals:
    """A use case's arrival process: how many workflows, their inter-arrival
    delays, where the plans come from, the wait before END, and the log lines
    the use case's dispatcher printed."""
    name: str
    total: int
    delays: Callable[[int], list]              # n -> the n inter-arrival delays
    workflow: Callable[[int], dict | None]     # i -> the plan (None when it cannot be loaded)
    end_delay: float                           # simulated seconds between the last submission and END
    banner: Callable[[int, object], None] = lambda n, backend: print(f'Starting dispatcher for {n} workflows at {backend.now()}...')
    announce: Callable[[int, object], None] = lambda i, backend: print(f'Sending wf{i} at {backend.now()}')
    missing: Callable[[int], None] = lambda i: print(f'  Failed to load workflow {i}, skipping')
    before_end: Callable[[object], None] = lambda backend: None
    at_end: Callable[[object], None] = lambda backend: None


def end_workflow():
    return fetchWorkflow('end', f"{PACKAGE_DIR}/")


def dispatcher(backend, arrivals: Arrivals, workflows: int | None = None):
    """The arrival loop, as a process on the dispatcher's simulator."""
    n = arrivals.total if workflows is None else workflows
    delays = arrivals.delays(n)
    arrivals.banner(n, backend)
    for i in range(n):
        backend.sleep(delays[i])
        arrivals.announce(i, backend)
        workflow = arrivals.workflow(i)
        if workflow is None:
            arrivals.missing(i)
            continue
        workflow['submit_time'] = backend.now()
        backend.workflows.send(workflow)

    # Send END after all requests are complete
    arrivals.before_end(backend)
    backend.sleep(arrivals.end_delay)
    arrivals.at_end(backend)
    backend.workflows.send(end_workflow())


SEISSOL = Arrivals('seissol', TOTAL_WORKFLOWS, delayGenerationFromSubmitTimes, fetchWorkflow, end_delay=300000)


if __name__ == "__main__":
    # The real-mode submitter: fixed delays, HTTP to the Gateway.
    delays = [1, 60, 60, 80, 100, 250, 300, 40, 120, 200, 150, 100]
    print(f"Starting the dispatcher at {time.time()}")
    for i in range(0,TOTAL_WORKFLOWS):
        print(f"sending workflow at {time.time()}")
        wf = fetchWorkflow(i)
        send_workflow(wf)
        time.sleep(delays[i])
