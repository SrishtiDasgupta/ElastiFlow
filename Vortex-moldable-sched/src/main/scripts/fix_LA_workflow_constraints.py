#!/usr/bin/env python3
"""
Auto-fix budget and deadline constraints for LA sample workflows.

Uses the same logic as workflow_generator.py with proper 2x deadline buffer.
"""

import yaml
import os

# Runtime estimates from speedup.py (worst-case on-prem for each mesh)
WORST_RUNTIME_PER_ITERATION = {
    1000: 253,    # seconds for mesh 1000 (1 node, on-prem)
    750: 557,     # seconds for mesh 750
    500: 2281.28  # seconds for mesh 500
}

# Cost per second for worst-case on-demand instance (Stockholm c7i.24xlarge)
WORST_COST_PER_SECOND = {
    1000: 0.5141,  # $/second
    750: 0.8595,
    500: 2.7422
}

def calculate_constraints(mesh, chains, tinyda_iterations, workflow_iterations):
    """
    Calculate budget and deadline using original workflow_generator.py logic.

    Budget = worst_cost * chains * tinyda_iterations * workflow_iterations
    Deadline = worst_runtime * tinyda_iterations * workflow_iterations * 2  (2x buffer!)
    """
    budget = (WORST_COST_PER_SECOND[mesh] * chains *
              tinyda_iterations * workflow_iterations)

    deadline = (WORST_RUNTIME_PER_ITERATION[mesh] * tinyda_iterations *
                workflow_iterations * 2)  # 2x safety buffer

    return round(budget, 2), int(deadline)


def fix_workflow(filepath):
    """Read workflow, calculate proper constraints, update file."""
    with open(filepath, 'r') as f:
        workflow = yaml.safe_load(f)

    # Extract parameters
    mesh = workflow['config']['mesh']
    chains = workflow['constraints']['chains']
    tinyda_iterations = workflow['constraints']['tinydaIterations']
    workflow_iterations = workflow['config']['workflowIterations']

    # Calculate proper values
    budget, deadline = calculate_constraints(
        mesh, chains, tinyda_iterations, workflow_iterations
    )

    # Update workflow
    old_budget = workflow['constraints']['budget']
    old_deadline = workflow['constraints']['deadline']
    workflow['constraints']['budget'] = budget
    workflow['constraints']['deadline'] = deadline

    # Write back
    with open(filepath, 'w') as f:
        yaml.dump(workflow, f, default_flow_style=False, sort_keys=False)

    print(f"✓ {os.path.basename(filepath)}: {workflow['id']}")
    print(f"  Budget:   {old_budget:8.2f} → {budget:8.2f} USD")
    print(f"  Deadline: {old_deadline:8.0f} → {deadline:8.0f} seconds ({deadline/3600:.1f} hours)")
    print(f"  Parameters: mesh={mesh}, chains={chains}, "
          f"tinydaIter={tinyda_iterations}, wfIter={workflow_iterations}")
    print()

    return workflow['id'], budget, deadline


def main():
    """Fix all LA sample workflows."""
    workflow_dir = '/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/workflow/sample_workflows_LA'

    print('=' * 70)
    print('FIXING LA WORKFLOW BUDGET & DEADLINE CONSTRAINTS')
    print('=' * 70)
    print()

    # Process all data*.yaml files
    for i in range(10):  # Check data0.yaml through data9.yaml
        filepath = os.path.join(workflow_dir, f'data{i}.yaml')
        if os.path.exists(filepath):
            fix_workflow(filepath)

    print('=' * 70)
    print('ALL WORKFLOWS FIXED!')
    print('=' * 70)
    print()
    print('Using original workflow_generator.py logic:')
    print('  - Budget: worst_cost * chains * tinydaIter * wfIter')
    print('  - Deadline: worst_runtime * tinydaIter * wfIter * 2  (2x buffer)')
    print()


if __name__ == '__main__':
    main()
