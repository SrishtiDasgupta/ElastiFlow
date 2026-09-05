from enum import Enum

class Cluster(Enum):
    ONPREM = 'on-prem'
    RESERVED = 'reserved'
    ONDEMAND = 'on-demand'

def getEstimate(per_iteration, tinyda_iterations, workflow_iterations = 1, chains = 1) -> float:
    return per_iteration * tinyda_iterations * workflow_iterations * chains

def getConstraintsFromWorkflow(wf_plan):
    constraints = {
        'budget': wf_plan['constraints']['budget'], # Gaussian
        'deadline': wf_plan['submit_time'] + wf_plan['constraints']['deadline'], # Absolute timestamp
        'deadline_duration': wf_plan['constraints']['deadline'], # Raw duration in seconds (for HPO optimizer)
        'min_instances': wf_plan['constraints']['chains'], # random - we assume 1 chain runs on 1 instance
        'tinyda_iterations': 1 + wf_plan['constraints']['tinydaIterations'], # random
        'mesh': wf_plan['config']['mesh'],
        # HPO-specific fields (also used by SeisSol)
        'chains': wf_plan['constraints']['chains'],
        'tinydaIterations': wf_plan['constraints']['tinydaIterations']
    }
    return constraints