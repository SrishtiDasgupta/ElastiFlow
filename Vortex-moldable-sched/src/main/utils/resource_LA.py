"""
License-Aware Resource Utilities

Extends resource utilities with license constraint extraction.
"""

from .resource import getEstimate, Cluster


def getConstraintsFromWorkflow(wf_plan):
    """
    Extract constraints from license-aware workflow plan

    Extended to include software_id and license_pool from workflow config.

    Args:
        wf_plan: Workflow plan dict

    Returns:
        Dict with keys:
        - budget, deadline, min_instances, tinyda_iterations, mesh
        - chains, tinydaIterations (for moldable logic)
        - software_id: int (0 = unlicensed, 1 = ANSYS, 2 = ABAQUS, 3 = LSDYNA)
        - license_pool: str (e.g., 'ANSYS', 'ABAQUS', 'LSDYNA') or None
        - wf_id: str (workflow identifier for license tracking)
    """
    constraints = {
        'budget': wf_plan['constraints']['budget'],
        'deadline': wf_plan['submit_time'] + wf_plan['constraints']['deadline'],
        'min_instances': wf_plan['constraints']['chains'],
        'tinyda_iterations': 1 + wf_plan['constraints']['tinydaIterations'],
        'mesh': wf_plan['config']['mesh'],
        'chains': wf_plan['constraints']['chains'],
        'tinydaIterations': wf_plan['constraints']['tinydaIterations'],

        # NEW: License-aware fields
        'software_id': wf_plan.get('config', {}).get('software_id', 0),
        'license_pool': wf_plan.get('constraints', {}).get('license_pool', None),
        'wf_id': wf_plan.get('id', 'unknown')
    }

    # Auto-detect license pool from software_id if not explicitly set
    if constraints['software_id'] > 0 and not constraints['license_pool']:
        pool_map = {
            1: 'ANSYS',
            2: 'ABAQUS',
            3: 'LSDYNA'
        }
        constraints['license_pool'] = pool_map.get(constraints['software_id'])

    return constraints
