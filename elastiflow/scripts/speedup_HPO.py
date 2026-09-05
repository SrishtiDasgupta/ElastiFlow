import json
import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score
import matplotlib.pyplot as plt

# ==================== DATA EXTRACTION ====================
def extract_runtime_data(filename):
    """Extract workers, model, epochs, and runtime from JSONL"""
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

# Model complexity mapping (based on your runtime ratios)
MODEL_FACTORS = {
    'vgg19': 1.0,
    'wide_resnet101_2': 1.37,
    'convnext_large': 2.03
}

# ==================== MODEL DEFINITIONS ====================
def exponential_model(X, a, b, c, d):
    """Like SeisSol Equation 6.1: runtime = a*e^(b*workers + c*model) + d"""
    workers, model_factor = X
    return a * np.exp(b * workers + c * model_factor) + d

def power_law_model(X, a, b, c):
    """runtime = a * workers^b * model_factor + c"""
    workers, model_factor = X
    return a * (workers ** b) * model_factor + c

def linear_efficiency_model(X, base, efficiency):
    """runtime = (base * model_factor) / (workers * efficiency)"""
    workers, model_factor = X
    return (base * model_factor) / (workers * efficiency)

# ==================== CURVE FITTING ====================
def fit_all_models(data, instance_name):
    """Fit all three models and return best one"""
    
    # Prepare data
    workers = np.array([d['workers'] for d in data])
    model_factors = np.array([MODEL_FACTORS[d['model']] for d in data])
    epochs = np.array([d['epochs'] for d in data])
    total_times = np.array([d['total_time'] for d in data])
    
    # Calculate per-epoch runtime (the base unit)
    runtime_per_epoch = total_times / epochs
    
    X = (workers, model_factors)
    y = runtime_per_epoch
    
    results = {}
    
    # Fit Exponential
    try:
        popt_exp, _ = curve_fit(exponential_model, X, y, 
                                p0=[50, -0.3, 0.5, 1], 
                                maxfev=50000,
                                bounds=([0, -5, -5, 0], [1000, 0, 5, 50]))
        y_pred_exp = exponential_model(X, *popt_exp)
        r2_exp = r2_score(y, y_pred_exp)
        results['exponential'] = {
            'params': popt_exp,
            'r2': r2_exp,
            'predictions': y_pred_exp,
            'equation': f"runtime = {popt_exp[0]:.2f} * e^({popt_exp[1]:.4f}*workers + {popt_exp[2]:.4f}*model) + {popt_exp[3]:.2f}"
        }
    except Exception as e:
        print(f"Exponential fit failed for {instance_name}: {e}")
        results['exponential'] = None
    
    # Fit Power Law
    try:
        popt_pow, _ = curve_fit(power_law_model, X, y,
                                p0=[40, -0.95, 1],
                                maxfev=50000,
                                bounds=([0, -2, 0], [200, -0.1, 50]))
        y_pred_pow = power_law_model(X, *popt_pow)
        r2_pow = r2_score(y, y_pred_pow)
        results['power_law'] = {
            'params': popt_pow,
            'r2': r2_pow,
            'predictions': y_pred_pow,
            'equation': f"runtime = {popt_pow[0]:.2f} * workers^{popt_pow[1]:.4f} * model + {popt_pow[2]:.2f}"
        }
    except Exception as e:
        print(f"Power law fit failed for {instance_name}: {e}")
        results['power_law'] = None
    
    # Fit Linear Efficiency
    try:
        popt_lin, _ = curve_fit(linear_efficiency_model, X, y,
                                p0=[40, 0.90],
                                bounds=([0, 0.5], [200, 1.0]))
        y_pred_lin = linear_efficiency_model(X, *popt_lin)
        r2_lin = r2_score(y, y_pred_lin)
        results['linear'] = {
            'params': popt_lin,
            'r2': r2_lin,
            'predictions': y_pred_lin,
            'equation': f"runtime = ({popt_lin[0]:.2f} * model) / (workers * {popt_lin[1]:.4f})"
        }
    except Exception as e:
        print(f"Linear fit failed for {instance_name}: {e}")
        results['linear'] = None
    
    # Select best model
    best_model = max(
        [(name, res) for name, res in results.items() if res is not None],
        key=lambda x: x[1]['r2']
    )
    
    return results, best_model, (workers, model_factors, y, epochs)

# ==================== EXECUTE FITTING ====================
print("=" * 60)
print("SPEEDUP CURVE FITTING ANALYSIS")
print("=" * 60)

# Load data
g4_data = extract_runtime_data('g4.jsonl')
g5_data = extract_runtime_data('g5.jsonl')

print(f"\ng4dn.2xlarge: {len(g4_data)} data points")
print(f"g5.2xlarge: {len(g5_data)} data points\n")

# Fit g4
print("\n" + "=" * 60)
print("FITTING g4dn.2xlarge (Tesla T4)")
print("=" * 60)
g4_results, g4_best, g4_raw = fit_all_models(g4_data, 'g4')

for model_name, result in g4_results.items():
    if result:
        print(f"\n{model_name.upper()}:")
        print(f"  R² = {result['r2']:.6f}")
        print(f"  {result['equation']}")

print(f"\n*** BEST MODEL: {g4_best[0].upper()} (R² = {g4_best[1]['r2']:.6f}) ***")

# Fit g5
print("\n" + "=" * 60)
print("FITTING g5.2xlarge (NVIDIA A10G)")
print("=" * 60)
g5_results, g5_best, g5_raw = fit_all_models(g5_data, 'g5')

for model_name, result in g5_results.items():
    if result:
        print(f"\n{model_name.upper()}:")
        print(f"  R² = {result['r2']:.6f}")
        print(f"  {result['equation']}")

print(f"\n*** BEST MODEL: {g5_best[0].upper()} (R² = {g5_best[1]['r2']:.6f}) ***")

# ==================== GENERATE PYTHON FUNCTIONS ====================
print("\n" + "=" * 60)
print("GENERATED SPEEDUP FUNCTIONS")
print("=" * 60)

def generate_function_code(instance_name, best_model, params):
    """Generate the getRuntime function code"""
    model_type, result = best_model
    
    if model_type == 'exponential':
        a, b, c, d = params
        return f"""
def getRuntime_{instance_name}(workers, model, epochs):
    \"\"\"
    Runtime in seconds for {instance_name} instance (Exponential model)
    R² = {result['r2']:.6f}
    
    Args:
        workers: Number of GPU nodes (1, 2, 4)
        model: 'vgg19', 'wide_resnet101_2', 'convnext_large'
        epochs: Number of training epochs
    \"\"\"
    model_factors = {{
        'vgg19': 1.0,
        'wide_resnet101_2': 1.37,
        'convnext_large': 2.03
    }}
    
    a = {a:.6f}
    b = {b:.6f}
    c = {c:.6f}
    d = {d:.6f}
    
    runtime_per_epoch = a * np.exp(b * workers + c * model_factors[model]) + d
    return runtime_per_epoch * epochs
"""
    
    elif model_type == 'power_law':
        a, b, c = params
        return f"""
def getRuntime_{instance_name}(workers, model, epochs):
    \"\"\"
    Runtime in seconds for {instance_name} instance (Power Law model)
    R² = {result['r2']:.6f}
    
    Args:
        workers: Number of GPU nodes (1, 2, 4)
        model: 'vgg19', 'wide_resnet101_2', 'convnext_large'
        epochs: Number of training epochs
    \"\"\"
    model_factors = {{
        'vgg19': 1.0,
        'wide_resnet101_2': 1.37,
        'convnext_large': 2.03
    }}
    
    a = {a:.6f}
    b = {b:.6f}
    c = {c:.6f}
    
    runtime_per_epoch = a * (workers ** b) * model_factors[model] + c
    return runtime_per_epoch * epochs
"""
    
    else:  # linear
        base, efficiency = params
        return f"""
def getRuntime_{instance_name}(workers, model, epochs):
    \"\"\"
    Runtime in seconds for {instance_name} instance (Linear Efficiency model)
    R² = {result['r2']:.6f}
    
    Args:
        workers: Number of GPU nodes (1, 2, 4)
        model: 'vgg19', 'wide_resnet101_2', 'convnext_large'
        epochs: Number of training epochs
    \"\"\"
    model_factors = {{
        'vgg19': 1.0,
        'wide_resnet101_2': 1.37,
        'convnext_large': 2.03
    }}
    
    base = {base:.6f}
    efficiency = {efficiency:.6f}
    
    runtime_per_epoch = (base * model_factors[model]) / (workers * efficiency)
    return runtime_per_epoch * epochs
"""

print(generate_function_code('g4', g4_best, g4_best[1]['params']))
print(generate_function_code('g5', g5_best, g5_best[1]['params']))

# ==================== VISUALIZATION ====================
fig, axes = plt.subplots(2, 3, figsize=(18, 12))
fig.suptitle('HPO Speedup Curves: Data vs Fitted Models', fontsize=16, fontweight='bold')

instances = [('g4', g4_results, g4_raw, 'Tesla T4'), 
             ('g5', g5_results, g5_raw, 'A10G')]

for row, (inst_name, results, raw_data, gpu_name) in enumerate(instances):
    workers, model_factors, y_actual, epochs = raw_data
    
    for col, model_name in enumerate(['exponential', 'power_law', 'linear']):
        ax = axes[row, col]
        
        if results[model_name]:
            result = results[model_name]
            y_pred = result['predictions']
            r2 = result['r2']
            
            # Plot
            ax.scatter(workers, y_actual, c=model_factors, cmap='viridis', 
                      s=100, alpha=0.7, edgecolors='black', linewidth=1.5,
                      label='Actual data')
            ax.scatter(workers, y_pred, c=model_factors, cmap='plasma',
                      s=100, alpha=0.7, marker='x', linewidths=3,
                      label='Fitted')
            
            ax.set_xlabel('Number of Workers (GPU Nodes)', fontsize=11, fontweight='bold')
            ax.set_ylabel('Runtime per Epoch (seconds)', fontsize=11, fontweight='bold')
            ax.set_title(f'{gpu_name}: {model_name.replace("_", " ").title()}\nR² = {r2:.6f}', 
                        fontsize=12, fontweight='bold')
            ax.legend()
            ax.grid(True, alpha=0.3)
            ax.set_xticks([1, 2, 4])
        else:
            ax.text(0.5, 0.5, 'Fit Failed', ha='center', va='center',
                   transform=ax.transAxes, fontsize=14)
            ax.set_title(f'{gpu_name}: {model_name}', fontsize=12)

plt.tight_layout()
plt.savefig('hpo_speedup_curves.png', dpi=300, bbox_inches='tight')
print("\n*** Plots saved to 'hpo_speedup_curves.png' ***")

print("\n" + "=" * 60)
print("ANALYSIS COMPLETE")
print("=" * 60)