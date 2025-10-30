import yaml
import os

TOTAL_WORKFLOWS = 5

def fetchWorkflow(i):
    """Fetch workflow YAML file for simulation"""
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

def dispatcher(sim, wf_mb):
    """
    HPO workflow dispatcher with staggered submission times

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
