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
    python scripts/workflow_generator_HPO.py --count 15 --seed 42          # Random
    python scripts/workflow_generator_HPO.py --designed --seed 42          # Designed configs
    python scripts/workflow_generator_HPO.py --designed --output /path/to  # Custom output dir
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

# ====================================================================================
# DESIGNED WORKFLOW CONFIGURATIONS — Optimized for Moldable vs Static Comparison
# ====================================================================================
# Each tuple: (model, chains, epochs, workflow_iterations, category)
#
# Design principles:
#   1. Wide-Short (chains=4) workflows first → create contention that static can't handle
#   2. Medium (chains=3) workflows → sustained load
#   3. Narrow-Long (chains=2, high epochs) workflows last → benefit most from moldable scale-up
#
# With MOLDABLE_INITIAL_CAP=0.5:
#   chains=4 → initial=2 (static=4)  → 2x resource savings
#   chains=3 → initial=2 (static=3)  → 1.5x resource savings
#   chains=2 → initial=1 (static=2)  → 2x resource savings
#
# Model distribution: convnext=7 (47%), wide_resnet=4 (27%), vgg19=4 (27%)
# ====================================================================================
DESIGNED_CONFIGS = [
    # idx 0-3: Narrow-Long convnext and Wide-Short vgg19
    ('convnext_large',    2, 20, 4),   # 0: Narrow-Long
    ('vgg19',             4, 12, 3),   # 1: Wide-Short
    ('convnext_large',    2, 24, 5),   # 2: Narrow-Long
    ('vgg19',             4, 15, 3),   # 3: Wide-Short
    # idx 4-6: Medium convnext and wide_resnet
    ('convnext_large',    3, 15, 4),   # 4: Medium
    ('wide_resnet101_2',  3, 18, 5),   # 5: Medium
    ('convnext_large',    2, 24, 4),   # 6: Narrow-Long
    # idx 7-8: Narrow wide_resnet and Wide-Short vgg19
    ('wide_resnet101_2',  2, 20, 4),   # 7: Narrow-Long
    ('vgg19',             4, 10, 3),   # 8: Wide-Short (fastest)
    # idx 9-10: Medium-Long and Narrow-Medium
    ('convnext_large',    3, 20, 5),   # 9: Medium-Long
    ('wide_resnet101_2',  2, 15, 3),   # 10: Narrow-Medium
    # idx 11-14: Tail — narrow convnext with high epochs (scale-up beneficiaries)
    ('convnext_large',    2, 18, 4),   # 11: Narrow-Medium
    ('vgg19',             4, 15, 4),   # 12: Wide-Medium
    ('convnext_large',    2, 28, 5),   # 13: Narrow-Long (heaviest)
    ('wide_resnet101_2',  3, 16, 4),   # 14: Medium
]


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

    return [str(m) for m in result]  # Convert np.str_ to plain str for YAML


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


def sample_workflow_generator(wf_id, budget, deadline, model,
                              chains=None, tinyda_iterations=None, workflow_iterations=None):
    """Generate a single HPO workflow YAML structure.
    If chains/tinyda_iterations/workflow_iterations are provided, use them (designed mode).
    Otherwise, sample randomly (random mode).
    """
    if workflow_iterations is None:
        workflow_iterations = random.randint(MIN_WORKFLOW_ITERATIONS, MAX_WORKFLOW_ITERATIONS)
    if chains is None:
        chains = random.randint(MIN_TRIALS, MAX_TRIALS)
    if tinyda_iterations is None:
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


def generate_all_workflows(num_workflows=10, output_dir=None, designed=False):
    """
    Generate N HPO workflow YAML files.

    Args:
        num_workflows: Number of workflows to generate
        output_dir: Output directory (default: workflow/sample_workflows_HPO/)
        designed: Use DESIGNED_CONFIGS for controlled experiments
    """
    if output_dir is None:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(base_path, 'workflow', 'sample_workflows_HPO')

    os.makedirs(output_dir, exist_ok=True)

    if designed:
        if num_workflows > len(DESIGNED_CONFIGS):
            print(f"Warning: --designed only has {len(DESIGNED_CONFIGS)} configs, capping count")
            num_workflows = len(DESIGNED_CONFIGS)

        model_list = [cfg[0] for cfg in DESIGNED_CONFIGS[:num_workflows]]
        model_counts = Counter(model_list)

        print(f"Generating {num_workflows} DESIGNED HPO workflows:")
        print(f"  Model distribution: {dict(model_counts)}")

        budget_list, deadline_list = budget_and_deadline_generation(model_counts, model_list)

        for i in range(num_workflows):
            model, chains, epochs, wf_iters = DESIGNED_CONFIGS[i]
            wf_id = f"hpo-{uuid.uuid4().hex[:8]}"
            workflow = sample_workflow_generator(
                wf_id, budget_list[i], deadline_list[i], model,
                chains=chains, tinyda_iterations=epochs, workflow_iterations=wf_iters
            )

            file_name = os.path.join(output_dir, f"data{i}.yaml")
            with open(file_name, 'w') as f:
                yaml.dump(workflow, f, default_flow_style=False)

            print(f"  data{i}.yaml: {model}, chains={chains}, epochs={epochs}, "
                  f"iters={wf_iters}, budget=${budget_list[i]:.2f}, deadline={deadline_list[i]:.0f}s")
    else:
        model_list = model_distribution(num_workflows)
        model_counts = Counter(model_list)

        print(f"Generating {num_workflows} RANDOM HPO workflows:")
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
    parser.add_argument('--count', type=int, default=15,
                        help='Number of workflows to generate (default: 15)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output directory (default: workflow/sample_workflows_HPO/)')
    parser.add_argument('--seed', type=int, default=0,
                        help='Random seed for reproducibility (default: 0)')
    parser.add_argument('--designed', action='store_true',
                        help='Use pre-designed configs optimized for moldable vs static comparison')

    args = parser.parse_args()

    np.random.seed(args.seed)
    random.seed(args.seed)

    generate_all_workflows(num_workflows=args.count, output_dir=args.output, designed=args.designed)
