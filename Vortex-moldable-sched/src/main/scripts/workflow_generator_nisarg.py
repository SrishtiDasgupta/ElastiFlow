import random
import numpy as np 
import yaml
import uuid
from collections import Counter
import matplotlib.pyplot as plt
import seaborn as sns

AVG_TINYDA_ITERATIONS = 8
AVG_WORKFLOW_ITERATIONS = 4
TOTAL_WORKFLOWS = 300

def gaussian(x, a, b, c):
    return a * np.exp(-(x - b) ** 2 / (2 * c ** 2))

def budget_and_deadline_generation(mesh_count, mesh_arr):

    # worst(runtime in secs * on-demand cost/s(Stockholm prices)) * chains * tinyda iterations * workflow iterations
    
    budget1000 = 0.5141 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS
    budget750 = 0.8595 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS
    budget500 = 2.7422 * 4 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS

    # slowest runtime in secs * tinyda iterations * workflow iterations * factor

    deadline1000 = 253 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2
    deadline750 = 557 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2
    deadline500 = 2281.28 * AVG_TINYDA_ITERATIONS * AVG_WORKFLOW_ITERATIONS * 2

    print("MESH ------------- MIN ----------- MAX ---------------- AVG")
    
    deadline_values_1000 = np.random.normal(loc=deadline1000, scale=100, size=mesh_count[1000])
    print(f"deadline_1000: {min(deadline_values_1000)}, {max(deadline_values_1000)}, {sum(deadline_values_1000)/len(deadline_values_1000)}")
    deadline_values_750 = np.random.normal(loc=deadline750, scale=100, size=mesh_count[750])
    print(f"deadline_750: {min(deadline_values_750)}, {max(deadline_values_750)}, {sum(deadline_values_750)/len(deadline_values_750)}")
    deadline_values_500 = np.random.normal(loc=deadline500, scale=200, size=mesh_count[500])
    print(f"deadline_500: {min(deadline_values_500)}, {max(deadline_values_500)}, {sum(deadline_values_500)/len(deadline_values_500)}")

    # # Set parameters for the Gaussian function
    
    budget_values_1000 = np.random.normal(loc=budget1000, scale=10, size=mesh_count[1000])
    print(f"budget_1000: {min(budget_values_1000)}, {max(budget_values_1000)}, {sum(budget_values_1000)/len(budget_values_1000)}")
    budget_values_750 = np.random.normal(loc=budget750, scale=10, size=mesh_count[750])
    print(f"budget_750: {min(budget_values_750)}, {max(budget_values_750)}, {sum(budget_values_750)/len(budget_values_750)}")
    budget_values_500 = np.random.normal(loc=budget500, scale=20, size=mesh_count[500])
    print(f"budget_500: {min(budget_values_500)}, {max(budget_values_500)}, {sum(budget_values_500)/len(budget_values_500)}")

    plt.figure(figsize=(12, 6))

    # Plot deadlines
    sns.kdeplot(deadline_values_1000, bw_method=10)
    sns.kdeplot(deadline_values_750, bw_method=10)
    sns.kdeplot(deadline_values_500, bw_method=10)
    plt.title('Deadline Values Distribution')
    plt.xlabel('Deadline')
    plt.ylabel('Density')

    # Plot budgets
    # plt.title('Budget Values Distribution')
    # plt.xlabel('Budget Values')
    # plt.ylabel('Budget')
    # plt.legend()

    # Save the plot to a file
    # plt.tight_layout()
    # plt.savefig('./deadline_plot.png')  # Save as PNG file
    plt.close()

    iterator_1000, iterator_750, iterator_500 = 0, 0, 0

    budget_values, deadline_values =  [], []

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
    arr = [1000, 750, 500]
    proportions = [25, 25, 50]
    
    proportions_float = np.array(proportions)/100
    counts_float = proportions_float * TOTAL_WORKFLOWS
    counts = np.floor(counts_float).astype(int)

    remaining = TOTAL_WORKFLOWS - counts.sum()
    if remaining > 0:
        decimal_parts = counts_float - counts
        indices = np.argsort(decimal_parts)[-int(remaining):]
        counts[indices] += 1
    
    result = np.repeat(arr, counts)

    np.random.shuffle(result)

    return result

def chain_generation():
    y = random.randint(2,6)
    return y

def tinyDA_generation():
    y = random.randint(1,13)
    return y

def generate_chains_tinyDA(workflow_iterations, chains, tinyDA, mesh):
    x = [{
            'chains': chains,
            'tinydaIterations': tinyDA
        }]
    for i in range(0, (workflow_iterations-1)):
        data = {
            'chains': chain_generation(),
            'tinydaIterations': tinyDA_generation()
        }
        x.append(data)
    final_data = {"mesh": int(mesh), "workflowIterations": workflow_iterations, 'workflowConfig':x}
    return final_data

def workflow_iteration_generation():
    y = random.randint(2,6)
    return y

def sample_workflow_generator(wf_id, budget, deadline, workflow_iterations, mesh):
    chains = chain_generation()
    tinyDA = tinyDA_generation()
    
    data = {
        'api': '4.7.0',
        'id': wf_id,
        'constraints': {
            'budget': float(budget),
            'deadline': float(deadline),
            'chains': chains,
            'tinydaIterations': tinyDA
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
        'config': generate_chains_tinyDA(workflow_iterations, chains, tinyDA, mesh) # scheduler will not have knowledge of this
        
    }
    return data

def generate_all_workflows():
    mesh_list = mesh_size_generation()
    mesh_counts = Counter(mesh_list)
    budget_list, deadline_list = budget_and_deadline_generation(mesh_counts, mesh_list)
    for x in range(0,TOTAL_WORKFLOWS):
        wf_id = "test-" + str(uuid.uuid4()) 
        workflow = sample_workflow_generator(wf_id, budget_list[x],deadline_list[x], workflow_iteration_generation(), mesh_list[x])
        file_name = "/home/ubuntu/Vortex/src/main/sample_workflows/data" + str(x) + ".yaml"
        with open(file_name, 'w') as file:
            yaml.dump(workflow, file)
        # print("Workflow generated for workflow" + str(x))

if __name__ == "__main__":
    np.random.seed(0)
    generate_all_workflows()


