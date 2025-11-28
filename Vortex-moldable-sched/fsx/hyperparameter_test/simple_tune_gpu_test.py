# Save as: simple_tune_gpu_test.py

import ray
from ray import tune
from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig, RunConfig
import os

ray.init(address="auto", ignore_reinit_error=True)
print("=" * 60)
print(f"Cluster resources: {ray.cluster_resources()}")
print(f"Available resources: {ray.available_resources()}")
print("=" * 60)

def train_worker(config):
    """This runs on GPU workers"""
    import torch
    print("=" * 50)
    print(f"WORKER: CUDA_VISIBLE_DEVICES = {os.environ.get('CUDA_VISIBLE_DEVICES')}")
    print(f"WORKER: torch.cuda.is_available() = {torch.cuda.is_available()}")
    print(f"WORKER: torch.cuda.device_count() = {torch.cuda.device_count()}")
    if torch.cuda.is_available():
        print(f"WORKER: Device = {torch.cuda.get_device_name(0)}")
    print("=" * 50)
    
    from ray.train import report
    for i in range(3):
        report({"accuracy": 0.5 + i * 0.1, "epoch": i})

def tune_trainable(config):
    """This is the Tune trial - creates TorchTrainer"""
    print(f"TRIAL: Starting with config = {config}")
    
    trainer = TorchTrainer(
        train_loop_per_worker=train_worker,
        scaling_config=ScalingConfig(
            num_workers=1,
            use_gpu=True,
            resources_per_worker={"CPU": 1, "GPU": 1},
        ),
        train_loop_config=config,
    )
    
    result = trainer.fit()
    accuracy = result.metrics.get("accuracy", 0.0)
    print(f"TRIAL: Finished with accuracy = {accuracy}")
    tune.report(accuracy=accuracy)

# Key: wrap with resources
trainable_with_gpu = tune.with_resources(
    tune_trainable,
    {"CPU": 1, "GPU": 1}
)

tuner = tune.Tuner(
    trainable_with_gpu,
    tune_config=tune.TuneConfig(
        num_samples=2,
        max_concurrent_trials=2,
    ),
    param_space={
        "lr": tune.uniform(0.001, 0.1),
    },
)

print("Starting tuner.fit()...")
results = tuner.fit()
print("Done!")
print(f"Best result: {results.get_best_result()}")