#!/usr/bin/env python3
"""
HPO Workflow Generator

Generates N workflow YAML files for HPO experiments, following the pattern
of workflow_generator.py (plain SeisSol) but adapted for HPO models.

Model distribution (3 models instead of 3 mesh sizes):
- convnext_large: 50% (slowest, most expensive — heaviest computation)
- wide_resnet101_2: 25% (medium)
- vgg19: 25% (fastest, cheapest — lightest computation)

Usage:
    python scripts/workflow_generator_HPO.py --count 10
    python scripts/workflow_generator_HPO.py --count 20 --output workflow/sample_workflows_HPO
    python scripts/workflow_generator_HPO.py --count 10 --seed 42
"""

import random
import numpy as np
import yaml
import uuid
import os
import argparse
from collections import Counter

from config.constants_HPO import (
    AVG_BUDGET, AVG_DEADLINE,
    MIN_WORKFLOW_ITERATIONS, MAX_WORKFLOW_ITERATIONS,
    MIN_TRIALS, MAX_TRIALS,
    MIN_EPOCHS, MAX_EPOCHS
)


def model_distribution(num_workflows):
    """
    Generate model assignments with fixed proportions:
    - convnext_large: 50%
    - wide_resnet101_2: 25%
    - vgg19: 25%
    """
    models = ['convnext_large', 'wide_resnet101_2', 'vgg19']
    proportions = [50, 25, 25]

    proportions_float = np.array(proportions) / 100
    counts_float = proportions_float * num_workflows
    counts = np.floor(counts_float).astype(int)

    remaining = num_workflows - counts.sum()
    if remaining > 0:
        decimal_parts = counts_float - counts
        indices = np.argsort(decimal_parts)[-int(remaining):]
        counts[indices] += 1

    result = np.repeat(models, counts)
    np.random.shuffle(result)

    return result


def budget_and_deadline_generation(model_counts, model_arr):
    """
    Generate budget and deadline values using Normal distributions
    centered on the AVG_BUDGET/AVG_DEADLINE from constants_HPO.py
    """
    # Budget: Normal distribution per model
    budget_values_by_model = {}
    deadline_values_by_model = {}

    for model in ['vgg19', 'wide_resnet101_2', 'convnext_large']:
        count = model_counts.get(model, 0)
        if count == 0:
            continue

        avg_budget = AVG_BUDGET[model]
        avg_deadline = AVG_DEADLINE[model]

        # Scale proportional to mean (10% std dev)
        budget_scale = avg_budget * 0.10
        deadline_scale = avg_deadline * 0.10

        budget_values_by_model[model] = np.random.normal(
            loc=avg_budget, scale=budget_scale, size=count
        )
        deadline_values_by_model[model] = np.random.normal(
            loc=avg_deadline, scale=deadline_scale, size=count
        )

        print(f"{model}: budget avg=${avg_budget:.2f}, deadline avg={avg_deadline/3600:.1f}h")

    # Map back to workflow order
    iterators = {m: 0 for m in ['vgg19', 'wide_resnet101_2', 'convnext_large']}
    budget_values = []
    deadline_values = []

    for model in model_arr:
        idx = iterators[model]
        budget_values.append(float(budget_values_by_model[model][idx]))
        deadline_values.append(float(deadline_values_by_model[model][idx]))
        iterators[model] += 1

    return budget_values, deadline_values


def generate_hyperparameters():
    """Generate random initial hyperparameters for an HPO workflow"""
    return {
        "learning_rate": round(random.uniform(0.001, 0.1), 4),
        "momentum": round(random.uniform(0.8, 0.99), 2),
        "batch_size": random.choice([32, 64, 128]),
    }


def sample_workflow_generator(wf_id, budget, deadline, model):
    """Generate a single HPO workflow YAML structure"""
    workflow_iterations = random.randint(MIN_WORKFLOW_ITERATIONS, MAX_WORKFLOW_ITERATIONS)
    chains = random.randint(MIN_TRIALS, MAX_TRIALS)
    tinyda_iterations = random.randint(MIN_EPOCHS, MAX_EPOCHS)
    hyperparams = generate_hyperparameters()

    data = {
        'api': '4.7.0',
        'id': wf_id,
        'constraints': {
            'budget': float(round(budget, 2)),
            'deadline': float(round(deadline, 2)),
            'chains': chains,
            'tinydaIterations': tinyda_iterations
        },
        'config': {
            'mesh': model,
            'workflowIterations': workflow_iterations
        },
        'vars': [
            {
                'id': 'input_config',
                'value': {
                    'learning_rate': hyperparams['learning_rate'],
                    'momentum': hyperparams['momentum'],
                    'batch_size': hyperparams['batch_size'],
                    'epochs': tinyda_iterations,
                    'next_trials': chains,
                    'model': model
                }
            }
        ],
        'actions': [
            {
                'type': 'for',
                'input': 'input_config',
                'enumerator': 'i',
                'yieldToInput': 'output_config',
                'actions': [
                    {
                        'type': 'execute',
                        'service': '/fsx/Vortex-mid/Vortex-moldable-sched/service/run_hpo.py',
                        'inputs': [
                            {
                                'id': 'config',
                                'var': 'i'
                            }
                        ],
                        'outputs': [
                            {
                                'id': 'config_out',
                                'var': 'output_config'
                            }
                        ]
                    }
                ]
            }
        ]
    }
    return data


def generate_all_workflows(num_workflows=10, output_dir=None):
    """
    Generate N HPO workflow YAML files.

    Args:
        num_workflows: Number of workflows to generate
        output_dir: Output directory (default: workflow/sample_workflows_HPO/)
    """
    if output_dir is None:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(base_path, 'workflow', 'sample_workflows_HPO')

    os.makedirs(output_dir, exist_ok=True)

    model_list = model_distribution(num_workflows)
    model_counts = Counter(model_list)

    print(f"Generating {num_workflows} HPO workflows:")
    print(f"  Model distribution: {dict(model_counts)}")

    budget_list, deadline_list = budget_and_deadline_generation(model_counts, model_list)

    for i in range(num_workflows):
        wf_id = f"hpo-{uuid.uuid4().hex[:8]}"
        workflow = sample_workflow_generator(
            wf_id, budget_list[i], deadline_list[i], model_list[i]
        )

        file_name = os.path.join(output_dir, f"data{i}.yaml")
        with open(file_name, 'w') as f:
            yaml.dump(workflow, f, default_flow_style=False)

    print(f"Generated {num_workflows} workflows in {output_dir}")
    print(f"  Files: data0.yaml ... data{num_workflows-1}.yaml")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='HPO Workflow Generator')
    parser.add_argument('--count', type=int, default=10,
                        help='Number of workflows to generate (default: 10)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output directory (default: workflow/sample_workflows_HPO/)')
    parser.add_argument('--seed', type=int, default=0,
                        help='Random seed for reproducibility (default: 0)')

    args = parser.parse_args()

    np.random.seed(args.seed)
    random.seed(args.seed)

    generate_all_workflows(num_workflows=args.count, output_dir=args.output)
