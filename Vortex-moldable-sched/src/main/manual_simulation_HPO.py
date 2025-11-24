#!/usr/bin/env python3
"""
Manual HPO Scheduler Simulation
Compares Static vs Moldable scheduling for 5 diverse HPO workflows
"""

import math
from collections import defaultdict

# Runtime functions (from speedup_HPO_runtime.py)
def getRuntime_g4(workers, model, epochs):
    """Get runtime for g4dn instances"""
    model_factors = {
        'vgg19': 1.0,
        'convnext_large': 1.38,
        'wide_resnet101_2': 1.52
    }
    base_runtime = 84.468 * (workers ** -0.956) * model_factors.get(model, 1.0)
    return base_runtime * epochs

def getRuntime_g5(workers, model, epochs):
    """Get runtime for g5 instances"""
    model_factors = {
        'vgg19': 1.0,
        'convnext_large': 1.38,
        'wide_resnet101_2': 1.52
    }
    base_runtime = 38.207 * (workers ** -0.956) * model_factors.get(model, 1.0)
    return base_runtime * epochs

# Resource costs (per second, including FSX for cloud)
COSTS = {
    'on-prem': 0.90 / 3600,      # $0.90/hr
    'g4-reserved': 0.805 / 3600,  # $0.503 + $0.302 FSX
    'g4-ondemand': 1.10 / 3600,   # $0.798 + $0.302 FSX
    'g5-reserved': 1.102 / 3600,  # $0.80 + $0.302 FSX
    'g5-ondemand': 1.582 / 3600,  # $1.28 + $0.302 FSX
}

# Available resources
RESOURCES = {
    'on-prem': 2,       # 2 on-prem g5-equivalent slots
    'g4-reserved': 2,
    'g4-ondemand': 2,
    'g5-reserved': 2,
    'g5-ondemand': 2,
}

class ResourceManager:
    def __init__(self):
        self.available = RESOURCES.copy()
        self.allocated = defaultdict(lambda: defaultdict(int))

    def allocate(self, wf_id, resource_type, count):
        """Allocate resources to workflow"""
        if self.available[resource_type] >= count:
            self.available[resource_type] -= count
            self.allocated[wf_id][resource_type] += count
            return True
        else:
            actual = self.available[resource_type]
            if actual > 0:
                self.available[resource_type] = 0
                self.allocated[wf_id][resource_type] += actual
                return actual
            return 0

    def free(self, wf_id, resource_type, count):
        """Free resources from workflow"""
        self.allocated[wf_id][resource_type] -= count
        self.available[resource_type] += count

def selectOptimalInstanceType(budget, deadline, model, trials, epochs, rm):
    """
    Select optimal instance type and number of hosts
    Returns: (instance_type, num_hosts, cost, runtime)
    """
    instance_types = {
        'g5': (getRuntime_g5, COSTS['on-prem']),  # On-prem g5 (cheapest)
        'g4': (getRuntime_g4, COSTS['g4-reserved']),  # g4 reserved (cheapest g4)
    }

    best_type = None
    best_num_hosts = 1
    best_score = float('inf')
    best_cost = 0
    best_runtime = 0

    for inst_type, (runtime_func, cost_per_sec) in instance_types.items():
        for num_hosts in range(1, trials + 1):
            # Calculate runtime
            if num_hosts >= trials:
                workers_per_trial = num_hosts // trials
                runtime = runtime_func(workers_per_trial, model, epochs)
            else:
                batches = math.ceil(trials / num_hosts)
                runtime = batches * runtime_func(1, model, epochs)

            # Calculate cost
            cost = runtime * cost_per_sec * num_hosts

            # Check constraints
            if runtime <= deadline and cost <= budget:
                score = cost + (runtime / deadline) * 0.1
                if score < best_score:
                    best_score = score
                    best_num_hosts = num_hosts
                    best_type = inst_type
                    best_cost = cost
                    best_runtime = runtime

    if best_type is None:
        # Default to cheapest
        best_type = 'g4'
        best_num_hosts = 1
        runtime_func = getRuntime_g4
        best_runtime = math.ceil(trials / 1) * runtime_func(1, model, epochs)
        best_cost = best_runtime * COSTS['g4-reserved'] * 1

    return best_type, best_num_hosts, best_cost, best_runtime

def allocateResources(wf_id, inst_type, num_hosts_requested, rm):
    """
    Allocate resources with 3-tier priority: on-prem -> reserved -> on-demand
    Returns: allocated resources dict
    """
    allocated = {}
    remaining = num_hosts_requested

    # Priority 1: On-prem (if g5 type)
    if inst_type == 'g5':
        avail = rm.allocate(wf_id, 'on-prem', remaining)
        if avail:
            allocated['on-prem'] = avail if isinstance(avail, int) else remaining
            remaining -= allocated['on-prem']

    # Priority 2: Reserved
    if remaining > 0:
        resource_key = f'{inst_type}-reserved'
        avail = rm.allocate(wf_id, resource_key, remaining)
        if avail:
            allocated[resource_key] = avail if isinstance(avail, int) else remaining
            remaining -= allocated[resource_key]

    # Priority 3: On-demand
    if remaining > 0:
        resource_key = f'{inst_type}-ondemand'
        avail = rm.allocate(wf_id, resource_key, remaining)
        if avail:
            allocated[resource_key] = avail if isinstance(avail, int) else remaining
            remaining -= allocated[resource_key]

    total_allocated = sum(allocated.values())
    return allocated, total_allocated

def simulateStatic(workflows):
    """Simulate static scheduler with proper timeline"""
    print("\n" + "="*80)
    print("STATIC SCHEDULER SIMULATION")
    print("="*80)

    rm = ResourceManager()
    results = []
    current_time = 0
    running_workflows = []  # List of (end_time, wf_id, allocated_resources)

    for wf in workflows:
        # Free completed workflows
        while running_workflows and running_workflows[0][0] <= wf['submit_time']:
            end_time, completed_id, allocated = running_workflows.pop(0)
            for res_type, count in allocated.items():
                rm.free(completed_id, res_type, count)
            print(f"\n[t={end_time:.0f}s] {completed_id} COMPLETED, resources freed")

        current_time = wf['submit_time']
        wf_id = wf['id']
        model = wf['config']['mesh']
        trials = wf['constraints']['chains']
        epochs = wf['constraints']['tinydaIterations']
        budget = wf['constraints']['budget']
        deadline = wf['constraints']['deadline']

        workflow_iterations = wf['config'].get('workflowIterations', 1)

        print(f"\n[t={current_time}s] {wf_id} submitted")
        print(f"  Model: {model}, Trials: {trials}, Epochs: {epochs}, Rounds: {workflow_iterations}")
        print(f"  Budget: ${budget:.2f}, Deadline: {deadline}s")

        # Select optimal configuration (static: same for all rounds)
        inst_type, num_hosts, cost, runtime = selectOptimalInstanceType(
            budget, deadline, model, trials, epochs, rm
        )

        print(f"  → Selected: {num_hosts} × {inst_type} (runtime: {runtime:.0f}s, cost: ${cost:.2f})")

        # Allocate resources
        allocated, total_allocated = allocateResources(wf_id, inst_type, num_hosts, rm)

        print(f"  → Allocated: {allocated} (total: {total_allocated}/{num_hosts} requested)")

        if total_allocated == 0:
            print(f"  ❌ FAILED: No resources available, workflow WAITING")
            wait_time = "WAITING"
            actual_runtime = "N/A"
            actual_cost = 0
            makespan = "N/A"
        else:
            # Calculate actual runtime with allocated resources (multiply by workflow iterations for static)
            if total_allocated >= trials:
                workers_per_trial = total_allocated // trials
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                round_runtime = runtime_func(workers_per_trial, model, epochs)
            else:
                batches = math.ceil(trials / total_allocated)
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                round_runtime = batches * runtime_func(1, model, epochs)

            actual_runtime = round_runtime * workflow_iterations  # Static: same allocation for all rounds

            # Calculate actual cost
            actual_cost = 0
            for res_type, count in allocated.items():
                cost_key = res_type if res_type == 'on-prem' else res_type.replace('-', '-')
                actual_cost += actual_runtime * COSTS[cost_key] * count

            start_time = current_time
            end_time = start_time + actual_runtime
            wait_time = 0
            makespan = end_time - wf['submit_time']

            # Add to running workflows (sorted by end time)
            running_workflows.append((end_time, wf_id, allocated))
            running_workflows.sort(key=lambda x: x[0])

            print(f"  ✓ Running: {round_runtime:.0f}s/round × {workflow_iterations} rounds = {actual_runtime:.0f}s total")
            print(f"  Cost: ${actual_cost:.2f}, Makespan: {makespan:.0f}s")

        results.append({
            'workflow': wf_id,
            'submit_time': wf['submit_time'],
            'allocated': allocated,
            'requested': num_hosts,
            'actual_allocated': total_allocated,
            'runtime': actual_runtime if total_allocated > 0 else 0,
            'cost': actual_cost,
            'makespan': makespan,
            'wait_time': wait_time,
            'degraded': total_allocated < num_hosts
        })

    return results

def simulateMoldable(workflows):
    """Simulate moldable scheduler with dynamic reallocation"""
    print("\n" + "="*80)
    print("MOLDABLE SCHEDULER SIMULATION")
    print("="*80)

    rm = ResourceManager()
    results = []
    current_time = 0

    # Moldable factors for reallocation between rounds
    OPTIM_FCFS_DFACTOR = [1.0, 0.7, 0.5, 0.3, 0.2]
    OPTIM_FCFS_BFACTOR = [1.0, 0.6, 0.4, 0.3, 0.2]

    for wf in workflows:
        current_time = wf['submit_time']
        wf_id = wf['id']
        model = wf['config']['mesh']
        trials = wf['constraints']['chains']
        epochs = wf['constraints']['tinydaIterations']
        budget = wf['constraints']['budget']
        deadline = wf['constraints']['deadline']
        workflow_iterations = wf['config'].get('workflowIterations', 1)

        print(f"\n[t={current_time}s] {wf_id} submitted")
        print(f"  Model: {model}, Trials: {trials}, Epochs: {epochs}, Rounds: {workflow_iterations}")
        print(f"  Budget: ${budget:.2f}, Deadline: {deadline}s")

        total_runtime = 0
        total_cost = 0

        # Track allocation across rounds
        current_allocation = {}
        inst_type = None

        for round_idx in range(workflow_iterations):
            print(f"\n  Round {round_idx + 1}/{workflow_iterations}:")

            # Calculate remaining budget and deadline
            remaining_budget = budget - total_cost
            remaining_deadline = deadline - total_runtime

            # Apply moldable factors
            available_budget = remaining_budget * OPTIM_FCFS_BFACTOR[min(round_idx, len(OPTIM_FCFS_BFACTOR)-1)]
            available_deadline = remaining_deadline * OPTIM_FCFS_DFACTOR[min(round_idx, len(OPTIM_FCFS_DFACTOR)-1)]

            # Re-optimize for this round
            inst_type_new, num_hosts, cost, runtime = selectOptimalInstanceType(
                available_budget, available_deadline, model, trials, epochs, rm
            )

            # First round: allocate
            if round_idx == 0:
                inst_type = inst_type_new
                allocated, total_allocated = allocateResources(wf_id, inst_type, num_hosts, rm)
                current_allocation = allocated
                print(f"    Initial allocation: {allocated} (total: {total_allocated}/{num_hosts})")
            else:
                # Subsequent rounds: check if can scale down
                current_total = sum(current_allocation.values())
                if num_hosts < current_total:
                    # Scale down (free resources)
                    to_free = current_total - num_hosts
                    print(f"    Scaling DOWN: {current_total} → {num_hosts} (freeing {to_free})")

                    for res_type in list(current_allocation.keys()):
                        if to_free > 0 and current_allocation[res_type] > 0:
                            free_count = min(to_free, current_allocation[res_type])
                            rm.free(wf_id, res_type, free_count)
                            current_allocation[res_type] -= free_count
                            to_free -= free_count
                            if current_allocation[res_type] == 0:
                                del current_allocation[res_type]

                elif num_hosts > current_total:
                    # Try to scale up
                    needed = num_hosts - current_total
                    new_alloc, got = allocateResources(wf_id, inst_type, needed, rm)
                    if got > 0:
                        print(f"    Scaling UP: {current_total} → {current_total + got} (requested {num_hosts})")
                        for res_type, count in new_alloc.items():
                            current_allocation[res_type] = current_allocation.get(res_type, 0) + count
                    else:
                        print(f"    Scale up FAILED: no resources available")
                else:
                    print(f"    No scaling needed: {current_total} hosts")

            # Calculate runtime for this round
            actual_total = sum(current_allocation.values())
            if actual_total >= trials:
                workers_per_trial = actual_total // trials
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                round_runtime = runtime_func(workers_per_trial, model, epochs)
            else:
                batches = math.ceil(trials / actual_total)
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                round_runtime = batches * runtime_func(1, model, epochs)

            # Calculate cost
            round_cost = 0
            for res_type, count in current_allocation.items():
                cost_key = res_type if res_type == 'on-prem' else res_type
                round_cost += round_runtime * COSTS[cost_key] * count

            total_runtime += round_runtime
            total_cost += round_cost

            print(f"    Runtime: {round_runtime:.0f}s, Cost: ${round_cost:.2f}")
            print(f"    Cumulative: {total_runtime:.0f}s, ${total_cost:.2f}")

        # Free all resources at end
        for res_type, count in current_allocation.items():
            rm.free(wf_id, res_type, count)

        makespan = total_runtime
        print(f"  ✓ Completed: Total runtime: {total_runtime:.0f}s, Total cost: ${total_cost:.2f}")
        print(f"  Makespan: {makespan:.0f}s, Budget used: {(total_cost/budget)*100:.1f}%")

        results.append({
            'workflow': wf_id,
            'submit_time': wf['submit_time'],
            'runtime': total_runtime,
            'cost': total_cost,
            'makespan': makespan,
            'wait_time': 0,
        })

    return results

def compareResults(static_results, moldable_results):
    """Compare static vs moldable results"""
    print("\n" + "="*80)
    print("COMPARISON: STATIC vs MOLDABLE")
    print("="*80)

    print("\n{:<30s} {:>12s} {:>12s} {:>12s}".format(
        "Workflow", "Static Cost", "Moldable Cost", "Savings %"
    ))
    print("-" * 80)

    total_static_cost = 0
    total_moldable_cost = 0
    total_static_runtime = 0
    total_moldable_runtime = 0

    for s, m in zip(static_results, moldable_results):
        static_cost = s['cost']
        moldable_cost = m['cost']
        savings = ((static_cost - moldable_cost) / static_cost * 100) if static_cost > 0 else 0

        total_static_cost += static_cost
        total_moldable_cost += moldable_cost
        total_static_runtime += s['runtime'] if isinstance(s['runtime'], (int, float)) else 0
        total_moldable_runtime += m['runtime']

        print(f"{s['workflow']:<30s} ${static_cost:>11.2f} ${moldable_cost:>11.2f} {savings:>11.1f}%")

    print("-" * 80)
    print(f"{'TOTAL':<30s} ${total_static_cost:>11.2f} ${total_moldable_cost:>11.2f} "
          f"{((total_static_cost - total_moldable_cost) / total_static_cost * 100):>11.1f}%")

    print("\n" + "="*80)
    print("SUMMARY METRICS")
    print("="*80)
    print(f"Total Cost Reduction:     {((total_static_cost - total_moldable_cost) / total_static_cost * 100):.1f}%")
    print(f"Total Runtime Reduction:  {((total_static_runtime - total_moldable_runtime) / total_static_runtime * 100):.1f}%")
    print(f"\nStatic total cost:        ${total_static_cost:.2f}")
    print(f"Moldable total cost:      ${total_moldable_cost:.2f}")
    print(f"Savings:                  ${total_static_cost - total_moldable_cost:.2f}")

def loadWorkflows():
    """Load the 5 simulation workflows (hardcoded)"""
    workflows = [
        {
            'id': 'hpo-sim-wf1-vgg19-small',
            'submit_time': 0,
            'constraints': {'budget': 15.00, 'deadline': 3600, 'chains': 2, 'tinydaIterations': 4},
            'config': {'mesh': 'vgg19', 'workflowIterations': 3}
        },
        {
            'id': 'hpo-sim-wf2-convnext-medium',
            'submit_time': 100,
            'constraints': {'budget': 40.00, 'deadline': 5400, 'chains': 4, 'tinydaIterations': 8},
            'config': {'mesh': 'convnext_large', 'workflowIterations': 4}
        },
        {
            'id': 'hpo-sim-wf3-resnet-tight-deadline',
            'submit_time': 300,
            'constraints': {'budget': 50.00, 'deadline': 2400, 'chains': 6, 'tinydaIterations': 6},
            'config': {'mesh': 'wide_resnet101_2', 'workflowIterations': 2}
        },
        {
            'id': 'hpo-sim-wf4-vgg19-large-budget',
            'submit_time': 450,
            'constraints': {'budget': 80.00, 'deadline': 7200, 'chains': 8, 'tinydaIterations': 10},
            'config': {'mesh': 'vgg19', 'workflowIterations': 5}
        },
        {
            'id': 'hpo-sim-wf5-convnext-constrained',
            'submit_time': 600,
            'constraints': {'budget': 20.00, 'deadline': 4800, 'chains': 3, 'tinydaIterations': 5},
            'config': {'mesh': 'convnext_large', 'workflowIterations': 3}
        }
    ]
    return workflows

if __name__ == "__main__":
    print("Loading workflows...")
    workflows = loadWorkflows()

    print(f"\nSimulating {len(workflows)} HPO workflows...")

    # Run static simulation
    static_results = simulateStatic(workflows)

    # Run moldable simulation
    moldable_results = simulateMoldable(workflows)

    # Compare results
    compareResults(static_results, moldable_results)
