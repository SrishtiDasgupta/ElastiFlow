"""
License-Aware Workflow Generator for LAMF Simulation

Generates SeisSol workflows with mandatory license requirements.
Each workflow is randomly assigned ANSYS, ABAQUS, or LSDYNA licenses.
"""

import random
import numpy as np
import yaml
import uuid
from collections import Counter
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd

# Import constants from centralized config
import sys
import os
from elastiflow.config.constants_LA import (
    TOTAL_WORKFLOWS,
    AVG_TINYDA_ITERATIONS,
    AVG_WORKFLOW_ITERATIONS,
    LICENSE_DISTRIBUTION,
    LICENSE_SOFTWARE_ID,
    MESH_DISTRIBUTION,
    WORKFLOW_OUTPUT_DIR_LA
)


def gaussian(x, a, b, c):
    """Gaussian function for distribution plots"""
    return a * np.exp(-(x - b) ** 2 / (2 * c ** 2))


def budget_and_deadline_generation(mesh_count, mesh_arr):
    """
    Generate budget and deadline constraints based on mesh size

    Returns: (budget_values, deadline_values) lists
    """
    # Worst (runtime in secs * on-demand cost/s) * chains * tinyda * workflow iterations
    budget1000 = 0.5141 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS
    budget750 = 0.8595 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS
    budget500 = 2.7422 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS

    # Slowest runtime in secs * tinyda * workflow iterations * factor
    deadline1000 = 253 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2
    deadline750 = 557 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2
    deadline500 = 2281.28 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2

    print("MESH ------------- MIN ----------- MAX ---------------- AVG")

    deadline_values_1000 = np.random.normal(loc=deadline1000, scale=100, size=mesh_count[1000])
    print(f"deadline_1000: {min(deadline_values_1000):.1f}, {max(deadline_values_1000):.1f}, {np.mean(deadline_values_1000):.1f}")
    deadline_values_750 = np.random.normal(loc=deadline750, scale=100, size=mesh_count[750])
    print(f"deadline_750: {min(deadline_values_750):.1f}, {max(deadline_values_750):.1f}, {np.mean(deadline_values_750):.1f}")
    deadline_values_500 = np.random.normal(loc=deadline500, scale=200, size=mesh_count[500])
    print(f"deadline_500: {min(deadline_values_500):.1f}, {max(deadline_values_500):.1f}, {np.mean(deadline_values_500):.1f}")

    budget_values_1000 = np.random.normal(loc=budget1000, scale=10, size=mesh_count[1000])
    print(f"budget_1000: {min(budget_values_1000):.2f}, {max(budget_values_1000):.2f}, {np.mean(budget_values_1000):.2f}")
    budget_values_750 = np.random.normal(loc=budget750, scale=10, size=mesh_count[750])
    print(f"budget_750: {min(budget_values_750):.2f}, {max(budget_values_750):.2f}, {np.mean(budget_values_750):.2f}")
    budget_values_500 = np.random.normal(loc=budget500, scale=15, size=mesh_count[500])
    print(f"budget_500: {min(budget_values_500):.2f}, {max(budget_values_500):.2f}, {np.mean(budget_values_500):.2f}")

    # Plot budget distribution
    plt.figure(figsize=(12, 6))
    x1 = pd.DataFrame({"value": budget_values_1000, "Mesh size": "1000"})
    x2 = pd.DataFrame({"value": budget_values_750, "Mesh size": "750"})
    x3 = pd.DataFrame({"value": budget_values_500, "Mesh size": "500"})
    df = pd.concat([x1, x2, x3])
    sns.kdeplot(df, x="value", hue="Mesh size")
    plt.title(f'Budget Values Distribution for {TOTAL_WORKFLOWS} LAMF Workflows')
    plt.xlabel('Budget in USD')
    plt.ylabel('Density')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig('./budget_plot_LA.png')
    plt.close()

    # Interleave budget/deadline values based on mesh order
    iterator_1000, iterator_750, iterator_500 = 0, 0, 0
    budget_values, deadline_values = [], []

    for mesh in mesh_arr:
        if mesh == 1000:
            budget_values.append(budget_values_1000[iterator_1000])
            deadline_values.append(deadline_values_1000[iterator_1000])
            iterator_1000 += 1
        if mesh == 750:
            budget_values.append(budget_values_750[iterator_750])
            deadline_values.append(deadline_values_750[iterator_750])
            iterator_750 += 1
        if mesh == 500:
            budget_values.append(budget_values_500[iterator_500])
            deadline_values.append(deadline_values_500[iterator_500])
            iterator_500 += 1

    return budget_values, deadline_values


def mesh_size_generation():
    """
    Generate mesh sizes with specified distribution from constants

    Returns: Array of mesh sizes (1000, 750, 500)
    """
    arr = list(MESH_DISTRIBUTION.keys())
    proportions = [MESH_DISTRIBUTION[mesh] * 100 for mesh in arr]

    proportions_float = np.array(proportions) / 100
    counts_float = proportions_float * TOTAL_WORKFLOWS
    counts = np.floor(counts_float).astype(int)

    # Handle rounding
    remaining = TOTAL_WORKFLOWS - counts.sum()
    if remaining > 0:
        decimal_parts = counts_float - counts
        indices = np.argsort(decimal_parts)[-int(remaining):]
        counts[indices] += 1

    result = np.repeat(arr, counts)
    np.random.shuffle(result)

    return result


def license_assignment():
    """
    Randomly assign a license type based on distribution from constants

    Returns: (license_pool, software_id) tuple
        - license_pool: 'ANSYS', 'ABAQUS', or 'LSDYNA'
        - software_id: from LICENSE_SOFTWARE_ID mapping
    """
    rand = random.random()

    if rand < LICENSE_DISTRIBUTION['ANSYS']:
        return ('ANSYS', LICENSE_SOFTWARE_ID['ANSYS'])
    elif rand < LICENSE_DISTRIBUTION['ANSYS'] + LICENSE_DISTRIBUTION['ABAQUS']:
        return ('ABAQUS', LICENSE_SOFTWARE_ID['ABAQUS'])
    else:
        return ('LSDYNA', LICENSE_SOFTWARE_ID['LSDYNA'])


def chain_generation():
    """Generate random number of parallel chains (2-6)"""
    return random.randint(2, 6)


def tinyDA_generation():
    """Generate random number of TinyDA iterations (1-9)"""
    return random.randint(1, 9)


def generate_chains_tinyDA(workflow_iterations, chains, tinyDA, mesh):
    """
    Generate workflow configuration with varying chains/iterations

    Returns: Config dict with mesh, workflowIterations, and workflowConfig
    """
    x = [{
        'chains': chains,
        'tinydaIterations': tinyDA
    }]

    for i in range(0, (workflow_iterations - 1)):
        data = {
            'chains': chain_generation(),
            'tinydaIterations': tinyDA_generation()
        }
        x.append(data)

    final_data = {
        "mesh": int(mesh),
        "workflowIterations": workflow_iterations,
        'workflowConfig': x
    }

    return final_data


def workflow_iteration_generation():
    """Generate random number of workflow iterations (2-6)"""
    return random.randint(2, 6)


def sample_workflow_generator_LA(wf_id, budget, deadline, workflow_iterations, mesh):
    """
    Generate a license-aware workflow YAML structure

    ALL workflows are assigned a license (ANSYS/ABAQUS/LSDYNA).
    No unlicensed workflows are generated.

    Args:
        wf_id: Workflow identifier
        budget: Budget constraint
        deadline: Deadline constraint
        workflow_iterations: Number of feedback iterations
        mesh: Mesh size (500, 750, 1000)

    Returns:
        Workflow dict with license_pool and software_id
    """
    chains = chain_generation()
    tinyDA = tinyDA_generation()
    license_pool, software_id = license_assignment()

    data = {
        'api': '4.7.0',
        'id': wf_id,
        'constraints': {
            'budget': float(budget),
            'deadline': float(deadline),
            'chains': chains,
            'tinydaIterations': tinyDA,
            'license_pool': license_pool  # NEW: Required for all workflows
        },
        'vars': [
            {
                'id': 'input_coh',
                'value': '3'
            }
        ],
        'actions': [
            {
                'type': 'for',
                'input': 'input_coh',
                'enumerator': 'i',
                'yieldToInput': 'output_coh',
                'actions': [
                    {
                        'type': 'execute',
                        'service': 'scripts/simulate-tinyda-seissol.py',
                        'inputs': [
                            {
                                'id': 'tinyda_input',
                                'var': 'i'
                            }
                        ],
                        'outputs': [
                            {
                                'id': 'tinyda_output',
                                'var': 'output_coh'
                            }
                        ]
                    }
                ]
            }
        ],
        'config': {
            **generate_chains_tinyDA(workflow_iterations, chains, tinyDA, mesh),
            'software_id': software_id  # NEW: Required for all workflows
        }
    }

    return data


def generate_all_workflows():
    """
    Generate all license-aware workflows for LAMF simulation

    Outputs:
        - data{i}.yaml files in sample_workflows_LA/ directory
        - Budget distribution plot (budget_plot_LA.png)
        - License distribution summary
    """
    print(f'Generating {TOTAL_WORKFLOWS} license-aware workflows for LAMF...')
    print('')

    mesh_list = mesh_size_generation()
    mesh_counts = Counter(mesh_list)
    budget_list, deadline_list = budget_and_deadline_generation(mesh_counts, mesh_list)

    license_counts = {'ANSYS': 0, 'ABAQUS': 0, 'LSDYNA': 0}

    for x in range(0, TOTAL_WORKFLOWS):
        wf_id = "lamf-test-" + str(uuid.uuid4())
        workflow = sample_workflow_generator_LA(
            wf_id, budget_list[x], deadline_list[x],
            workflow_iteration_generation(), mesh_list[x]
        )

        # Track license distribution
        license_counts[workflow['constraints']['license_pool']] += 1

        file_name = WORKFLOW_OUTPUT_DIR_LA + "/data" + str(x) + ".yaml"
        with open(file_name, 'w') as file:
            yaml.dump(workflow, file)

        if (x + 1) % 20 == 0:
            print(f"  Generated {x + 1}/{TOTAL_WORKFLOWS} workflows...")

    print('')
    print('=' * 60)
    print('LICENSE DISTRIBUTION')
    print('=' * 60)
    for license_type, count in license_counts.items():
        percentage = (count / TOTAL_WORKFLOWS) * 100
        print(f'{license_type:8s}: {count:4d} workflows ({percentage:5.1f}%)')
    print('=' * 60)
    print('')
    print(f'✓ Generated {TOTAL_WORKFLOWS} license-aware workflows')
    print(f'✓ Output directory: sample_workflows_LA/')
    print(f'✓ Budget plot saved: budget_plot_LA.png')


if __name__ == "__main__":
    np.random.seed(0)
    random.seed(0)
    generate_all_workflows()
