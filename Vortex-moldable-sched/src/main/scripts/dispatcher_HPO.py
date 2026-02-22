import yaml
import os
import time
import argparse
import requests
import numpy as np

from config.constants_HPO import TOTAL_WORKFLOWS as DEFAULT_TOTAL_WORKFLOWS, AVG_INTERARRIVAL_TIME


def generate_poisson_delays(num_workflows, avg_interarrival):
    """
    Generate exponential inter-arrival times (Poisson process).
    First workflow always has delay 0.
    """
    delays = np.random.exponential(avg_interarrival, size=num_workflows)
    delays[0] = 0  # First workflow arrives immediately
    return delays


def fetchWorkflow(i, use_generated=False):
    """
    Fetch workflow YAML file for simulation or real mode.

    Args:
        i: workflow index (int) or 'end' for END signal
        use_generated: if True, read from data{i}.yaml (generated workflows)
                       if False, read from sim_wf{i+1}.yaml (hand-crafted workflows)
    """
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if i == 'end':
        file_name = os.path.join(base_path, "end.yaml")
    elif use_generated:
        file_name = os.path.join(base_path, "workflow/sample_workflows_HPO", f"data{i}.yaml")
    else:
        file_name = os.path.join(base_path, "workflow/sample_workflows_HPO", f"sim_wf{i+1}.yaml")

    with open(file_name, 'r') as stream:
        try:
            workflow = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            workflow = None
            print(f"Error loading workflow: {exc}")
    return workflow


def send_workflow(workflow, scheduler_host='0.0.0.0', port=8080):
    """POST workflow to scheduler's HTTP server"""
    proxies = {"http": None, "https": None}
    url = f'http://{scheduler_host}:{port}'
    response = requests.post(url, proxies=proxies, json=workflow)
    if response.status_code == 200:
        print(f'  Success: {workflow["id"]}')
    else:
        print(f'  Error: {response.status_code} {response.text}')


def dispatcher(sim, wf_mb, num_workflows=None, use_generated=False, poisson=False, avg_delay=None):
    """
    HPO workflow dispatcher for simulation mode.

    Supports two modes:
    1. Hand-crafted (default): 5 workflows with hardcoded delays (sim_wf*.yaml)
    2. Generated + Poisson: N workflows from data*.yaml with Poisson inter-arrival times

    Args:
        sim: simulus simulator instance
        wf_mb: workflow mailbox name
        num_workflows: number of workflows to dispatch (default: 5 for hand-crafted, or TOTAL_WORKFLOWS)
        use_generated: use generated data*.yaml files instead of sim_wf*.yaml
        poisson: use Poisson-distributed inter-arrival times
        avg_delay: average inter-arrival time in seconds (default: AVG_INTERARRIVAL_TIME)
    """
    if use_generated or poisson:
        # Generated workflow mode with Poisson arrivals
        if num_workflows is None:
            num_workflows = DEFAULT_TOTAL_WORKFLOWS
        if avg_delay is None:
            avg_delay = AVG_INTERARRIVAL_TIME

        if poisson:
            delays = generate_poisson_delays(num_workflows, avg_delay)
        else:
            delays = [0] + [avg_delay] * (num_workflows - 1)

        print(f'Starting HPO dispatcher for {num_workflows} generated workflows at {sim.now}...')
        print(f'  Arrival pattern: {"Poisson" if poisson else "Fixed"} (avg={avg_delay:.0f}s)')

        for i in range(num_workflows):
            sim.sleep(float(delays[i]))
            print(f'Dispatching workflow {i} (data{i}.yaml) at t={sim.now:.0f}s')
            workflow = fetchWorkflow(i, use_generated=True)
            if workflow is None:
                print(f'  Failed to load data{i}.yaml, skipping')
                continue
            workflow['submit_time'] = sim.now
            sim.sync().send(sim, wf_mb, str(workflow))
    else:
        # Original hand-crafted mode (5 workflows)
        if num_workflows is None:
            num_workflows = 5
        delays = [0, 100, 200, 150, 150]

        print(f'Starting HPO dispatcher for {num_workflows} workflows at {sim.now}...')

        for i in range(num_workflows):
            sim.sleep(delays[i])
            print(f'Dispatching workflow {i+1} at t={sim.now}s')
            workflow = fetchWorkflow(i, use_generated=False)
            workflow['submit_time'] = sim.now
            sim.sync().send(sim, wf_mb, str(workflow))

    # Send END after all workflows complete (large delay to ensure completion)
    sim.sleep(150000)
    print(f'Sending END signal at t={sim.now}s')
    sim.sync().send(sim, wf_mb, str(fetchWorkflow('end')))


if __name__ == "__main__":
    """
    Real-mode HPO workflow submission.

    Usage:
      # Submit single hand-crafted workflow:
      python scripts/dispatcher_HPO.py --wf 1

      # Submit all 5 hand-crafted workflows with delays:
      python scripts/dispatcher_HPO.py --all

      # Submit N generated workflows with Poisson delays:
      python scripts/dispatcher_HPO.py --count 10 --poisson --host 172.31.8.202

      # Submit N generated workflows with fixed delay:
      python scripts/dispatcher_HPO.py --count 10 --avg-delay 120 --host 172.31.8.202
    """
    parser = argparse.ArgumentParser(description='HPO Workflow Submitter (real mode)')
    parser.add_argument('--wf', type=int, nargs='+', help='Hand-crafted workflow numbers to submit (e.g. 1 2 3)')
    parser.add_argument('--all', action='store_true', help='Submit all 5 hand-crafted workflows with delays')
    parser.add_argument('--count', type=int, default=None, help='Number of generated workflows to submit')
    parser.add_argument('--poisson', action='store_true', help='Use Poisson-distributed inter-arrival times')
    parser.add_argument('--avg-delay', type=float, default=120.0, help='Average delay between submissions in seconds (default: 120)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Scheduler host (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8080, help='Scheduler port (default: 8080)')
    parser.add_argument('--delay', type=int, default=0, help='Fixed delay between hand-crafted submissions (default: 0)')
    parser.add_argument('--seed', type=int, default=None, help='Random seed for Poisson delays')
    parser.add_argument('--end', action='store_true', help='Only send END signal (no workflows)')

    args = parser.parse_args()

    if args.seed is not None:
        np.random.seed(args.seed)

    if args.end:
        # Only send END signal
        print(f'Sending END signal to {args.host}:{args.port}')
        end_wf = fetchWorkflow('end')
        send_workflow(end_wf, args.host, args.port)
        print('Scheduler will compute metrics and exit.')
        exit(0)

    workflows_submitted = 0

    if args.count is not None:
        # Generated workflow mode
        num_workflows = args.count

        if args.poisson:
            delays = generate_poisson_delays(num_workflows, args.avg_delay)
            print(f'Submitting {num_workflows} generated HPO workflows with Poisson delays (avg={args.avg_delay:.0f}s)')
        else:
            delays = [0] + [args.avg_delay] * (num_workflows - 1)
            print(f'Submitting {num_workflows} generated HPO workflows with fixed delay ({args.avg_delay:.0f}s)')

        for i in range(num_workflows):
            if i > 0 and delays[i] > 0:
                print(f'  Waiting {delays[i]:.0f}s before next submission...')
                time.sleep(delays[i])
            workflow = fetchWorkflow(i, use_generated=True)
            if workflow:
                workflow['submit_time'] = time.time()
                model = workflow.get('config', {}).get('mesh', 'unknown')
                print(f'Submitting data{i}.yaml (id: {workflow["id"]}, model: {model})')
                send_workflow(workflow, args.host, args.port)
                workflows_submitted += 1
            else:
                print(f'  Failed to load data{i}.yaml')

    elif args.all:
        # All hand-crafted workflows
        wf_indices = list(range(5))
        delays = [0, 100, 200, 150, 150]

        print(f'Submitting {len(wf_indices)} hand-crafted HPO workflow(s) to {args.host}:{args.port}')
        for idx, wf_i in enumerate(wf_indices):
            if idx > 0 and delays[idx] > 0:
                print(f'  Waiting {delays[idx]}s before next submission...')
                time.sleep(delays[idx])
            workflow = fetchWorkflow(wf_i, use_generated=False)
            if workflow:
                print(f'Submitting sim_wf{wf_i+1}.yaml (id: {workflow["id"]})')
                send_workflow(workflow, args.host, args.port)
                workflows_submitted += 1
            else:
                print(f'  Failed to load sim_wf{wf_i+1}.yaml')

    elif args.wf:
        # Specific hand-crafted workflows
        wf_indices = [w - 1 for w in args.wf]
        delays = [args.delay] * len(wf_indices)

        print(f'Submitting {len(wf_indices)} HPO workflow(s) to {args.host}:{args.port}')
        for idx, wf_i in enumerate(wf_indices):
            if idx > 0 and delays[idx] > 0:
                print(f'  Waiting {delays[idx]}s before next submission...')
                time.sleep(delays[idx])
            workflow = fetchWorkflow(wf_i, use_generated=False)
            if workflow:
                print(f'Submitting sim_wf{wf_i+1}.yaml (id: {workflow["id"]}, chains: {workflow["constraints"]["chains"]})')
                send_workflow(workflow, args.host, args.port)
                workflows_submitted += 1
            else:
                print(f'  Failed to load sim_wf{wf_i+1}.yaml')
    else:
        parser.print_help()
        exit(1)

    print(f'\nAll {workflows_submitted} workflow(s) submitted.')
    print(f'Check progress:  cat workflow_status.log')
    print(f'Send END when done:  PYTHONPATH=. python3 scripts/dispatcher_HPO.py --end --host {args.host}')
