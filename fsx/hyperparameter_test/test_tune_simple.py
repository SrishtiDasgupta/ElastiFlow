# Save as: test_tune_single_trial.py

import ray
import os

os.environ["RAY_TRAIN_WORKER_GROUP_START_TIMEOUT_S"] = "180"

from ray import tune
from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig

# Clean start
ray.shutdown()
ray.init(address="auto", ignore_reinit_error=True)

print(f"Available: {ray.available_resources()}")

def train_worker(config):
    import torch
    import time
    
    print("=" * 60)
    print(f"WORKER: CUDA available: {torch.cuda.is_available()}")
    
    if torch.cuda.is_available():
        device = torch.device("cuda:0")
        print(f"WORKER: Using {torch.cuda.get_device_name(0)}")
        
        x = torch.randn(5000, 5000, device=device)
        for i in range(3):
            y = torch.mm(x, x)
            torch.cuda.synchronize()
            print(f"WORKER: Iteration {i+1}/3 - GPU mem: {torch.cuda.memory_allocated()/1e9:.2f} GB")
            time.sleep(1)
    
    print("=" * 60)
    
    from ray.train import report
    report({"accuracy": 0.95})

def tune_trainable(config):
    print(f"TRIAL: Starting...")
    
    trainer = TorchTrainer(
        train_loop_per_worker=train_worker,
        scaling_config=ScalingConfig(
            num_workers=1,
            use_gpu=True,
        ),
        train_loop_config=config,
    )
    
    result = trainer.fit()
    
    accuracy = 0.0
    if result.metrics is not None:
        accuracy = result.metrics.get("accuracy", 0.0)
    
    print(f"TRIAL: Done, accuracy={accuracy}")
    return {"accuracy": accuracy}

# Single trial, no resource wrapper
tuner = tune.Tuner(
    tune_trainable,  # No tune.with_resources wrapper
    tune_config=tune.TuneConfig(
        num_samples=1,  # Just 1 trial
        max_concurrent_trials=1,
    ),
    param_space={"lr": 0.01},
)

print("Starting tuner.fit()...")
results = tuner.fit()
print(f"Done! Best: {results.get_best_result().metrics}")