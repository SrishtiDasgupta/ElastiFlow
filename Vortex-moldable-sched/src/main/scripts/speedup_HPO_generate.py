# speedup_HPO_generate.py
import json
import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score

MODEL_FACTORS = {
    'vgg19': 1.0,
    'wide_resnet101_2': 1.37,
    'convnext_large': 2.03
}

def extract_runtime_data(filename):
    data = []
    with open(filename, 'r') as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                data.append({
                    'model': entry['configuration']['model_name'],
                    'workers': entry['configuration']['num_workers'],
                    'epochs': entry['configuration']['epochs'],
                    'total_time': entry['timing_breakdown']['total_training_time']
                })
    return data

def exponential_model(X, a, b, c, d):
    workers, model_factor = X
    return a * np.exp(b * workers + c * model_factor) + d

def power_law_model(X, a, b, c):
    workers, model_factor = X
    return a * (workers ** b) * model_factor + c

def linear_efficiency_model(X, base, efficiency):
    workers, model_factor = X
    return (base * model_factor) / (workers * efficiency)

def fit_all_models(data, instance_name):
    workers = np.array([d['workers'] for d in data])
    model_factors = np.array([MODEL_FACTORS[d['model']] for d in data])
    epochs = np.array([d['epochs'] for d in data])
    total_times = np.array([d['total_time'] for d in data])

    runtime_per_epoch = total_times / epochs
    X = (workers, model_factors)
    y = runtime_per_epoch

    results = {}
    try:
        popt_exp, _ = curve_fit(exponential_model, X, y, p0=[50, -0.3, 0.5, 1],
                                maxfev=50000,
                                bounds=([0, -5, -5, 0], [1000, 0, 5, 50]))
        r2_exp = r2_score(y, exponential_model(X, *popt_exp))
        results['exponential'] = (popt_exp, r2_exp)
    except: results['exponential'] = None

    try:
        popt_pow, _ = curve_fit(power_law_model, X, y, p0=[40, -0.95, 1],
                                maxfev=50000,
                                bounds=([0, -2, 0], [200, -0.1, 50]))
        r2_pow = r2_score(y, power_law_model(X, *popt_pow))
        results['power_law'] = (popt_pow, r2_pow)
    except: results['power_law'] = None

    try:
        popt_lin, _ = curve_fit(linear_efficiency_model, X, y, p0=[40, 0.90],
                                bounds=([0, 0.5], [200, 1.0]))
        r2_lin = r2_score(y, linear_efficiency_model(X, *popt_lin))
        results['linear'] = (popt_lin, r2_lin)
    except: results['linear'] = None

    best_model = max(
        [(m, v) for m, v in results.items() if v is not None],
        key=lambda x: x[1][1]
    )
    return best_model

def generate_code(instance_name, model_type, params, r2):
    if model_type == 'exponential':
        a, b, c, d = params
        return f"""
def getRuntime_{instance_name}(workers, model, epochs):
    model_factors = {MODEL_FACTORS}
    runtime_per_epoch = {a:.6f} * np.exp({b:.6f}*workers + {c:.6f}*model_factors[model]) + {d:.6f}
    return runtime_per_epoch * epochs
"""
    elif model_type == 'power_law':
        a, b, c = params
        return f"""
def getRuntime_{instance_name}(workers, model, epochs):
    model_factors = {MODEL_FACTORS}
    runtime_per_epoch = {a:.6f} * (workers**{b:.6f}) * model_factors[model] + {c:.6f}
    return runtime_per_epoch * epochs
"""
    else:  # linear
        base, efficiency = params
        return f"""
def getRuntime_{instance_name}(workers, model, epochs):
    model_factors = {MODEL_FACTORS}
    runtime_per_epoch = ({base:.6f} * model_factors[model]) / (workers * {efficiency:.6f})
    return runtime_per_epoch * epochs
"""

if __name__ == "__main__":
    g4_data = extract_runtime_data("/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/HPO/g4.jsonl")
    g5_data = extract_runtime_data("/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/HPO/g5.jsonl")

    best_g4 = fit_all_models(g4_data, "g4")
    best_g5 = fit_all_models(g5_data, "g5")

    with open("speedup_HPO_runtime.py", "w") as f:
        f.write("import numpy as np\n\n")
        for name, best in [("g4", best_g4), ("g5", best_g5)]:
            model_type, (params, r2) = best
            code = generate_code(name, model_type, params, r2)
            f.write(code)

    print("✅ Generated runtime functions in speedup_HPO_runtime.py")

"""
from speedup_HPO_runtime import getRuntime_g4, getRuntime_g5

print(getRuntime_g4(2, "vgg19", 10))   # runtime in seconds
print(getRuntime_g5(4, "convnext_large", 20))
"""