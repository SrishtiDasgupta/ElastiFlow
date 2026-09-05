import time
import math
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

def plotSubmitTimes(submitTimes):
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
    plt.title('Submit Times Distribution')
    plt.xlabel('Submit Times')
    plt.ylabel('Number of Submissions')
    plt.xticks(rotation=45)
    plt.grid()
    plt.tight_layout()
    plt.savefig('submitTimes.png')

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

def send_workflow(workflow):
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


def dispatcher(sim, wf_mb):
    delays = delayGenerationFromSubmitTimes(TOTAL_WORKFLOWS)
    # delays = [1, 60, 60, 80, 100, 250, 300, 40, 120, 200, 150, 100, 1000]
    print(f'Starting dispatcher for {TOTAL_WORKFLOWS} workflows at {sim.now}...')
    for i in range(TOTAL_WORKFLOWS):
        sim.sleep(delays[i])
        print(f'Sending wf{i} at {sim.now}')
        workflow = fetchWorkflow(i)
        workflow['submit_time'] = sim.now
        sim.sync().send(sim, wf_mb, str(workflow))

    # Send END after all requests are complete
    sim.sleep(300000)
    sim.sync().send(sim, wf_mb, str(fetchWorkflow('end', f"{PACKAGE_DIR}/")))


if __name__ == "__main__":
    # delays = delayGenerationFromSubmitTimes(TOTAL_WORKFLOWS)
    delays = [1, 60, 60, 80, 100, 250, 300, 40, 120, 200, 150, 100]
    print(f"Starting the dispatcher at {time.time()}")
    for i in range(0,TOTAL_WORKFLOWS):
        print(f"sending workflow at {time.time()}")
    delays = [1, 60, 60, 80, 100, 250, 300, 40, 120, 200, 150, 100]
    print(f"Starting the dispatcher at {time.time()}")
    for i in range(0,TOTAL_WORKFLOWS):
        print(f"sending workflow at {time.time()}")
        wf = fetchWorkflow(i)
        send_workflow(wf)
        time.sleep(delays[i])
    # delayGenerationFromSubmitTimes(100)