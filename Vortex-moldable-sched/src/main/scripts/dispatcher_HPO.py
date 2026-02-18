import yaml
import os
import time
import argparse
import requests

TOTAL_WORKFLOWS = 5

def fetchWorkflow(i):
    """Fetch workflow YAML file for simulation or real mode"""
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if i == 'end':
        file_name = os.path.join(base_path, "end.yaml")
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

def dispatcher(sim, wf_mb):
    """
    HPO workflow dispatcher with staggered submission times (simulation mode)

    Submission pattern:
    - WF1: t=0s    (vgg19, small, arrives first)
    - WF2: t=100s  (convnext, medium, competes with WF1)
    - WF3: t=300s  (resnet, tight deadline, high priority)
    - WF4: t=450s  (vgg19, large budget, resource-hungry)
    - WF5: t=600s  (convnext, constrained budget)
    """
    # Delays between workflow submissions (in seconds)
    delays = [0, 100, 200, 150, 150]  # t=0, 100, 300, 450, 600

    print(f'Starting HPO dispatcher for {TOTAL_WORKFLOWS} workflows at {sim.now}...')

    for i in range(TOTAL_WORKFLOWS):
        sim.sleep(delays[i])
        print(f'Dispatching workflow {i+1} at t={sim.now}s')
        workflow = fetchWorkflow(i)
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
      # Submit single workflow (sim_wf1.yaml):
      python scripts/dispatcher_HPO.py --wf 1

      # Submit workflows 1 through 3:
      python scripts/dispatcher_HPO.py --wf 1 2 3

      # Submit all 5 workflows with delays:
      python scripts/dispatcher_HPO.py --all

      # Specify scheduler host (default: 0.0.0.0 i.e. localhost):
      python scripts/dispatcher_HPO.py --wf 1 --host 172.31.8.202
    """
    parser = argparse.ArgumentParser(description='HPO Workflow Submitter (real mode)')
    parser.add_argument('--wf', type=int, nargs='+', help='Workflow numbers to submit (e.g. 1 2 3)')
    parser.add_argument('--all', action='store_true', help='Submit all 5 workflows with delays')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Scheduler host (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8080, help='Scheduler port (default: 8080)')
    parser.add_argument('--delay', type=int, default=0, help='Delay in seconds between submissions (default: 0)')

    args = parser.parse_args()

    if args.all:
        wf_indices = list(range(TOTAL_WORKFLOWS))
        delays = [0, 100, 200, 150, 150]
    elif args.wf:
        wf_indices = [w - 1 for w in args.wf]  # Convert 1-based to 0-based
        delays = [args.delay] * len(wf_indices)
    else:
        parser.print_help()
        exit(1)

    print(f'Submitting {len(wf_indices)} HPO workflow(s) to {args.host}:{args.port}')
    for idx, wf_i in enumerate(wf_indices):
        if idx > 0 and delays[idx] > 0:
            print(f'  Waiting {delays[idx]}s before next submission...')
            time.sleep(delays[idx])
        workflow = fetchWorkflow(wf_i)
        if workflow:
            print(f'Submitting sim_wf{wf_i+1}.yaml (id: {workflow["id"]}, chains: {workflow["constraints"]["chains"]})')
            send_workflow(workflow, args.host, args.port)
        else:
            print(f'  Failed to load sim_wf{wf_i+1}.yaml')
