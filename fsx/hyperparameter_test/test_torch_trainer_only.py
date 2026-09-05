# Save as: test_torch_trainer_only.py

import ray
import os

os.environ["RAY_TRAIN_WORKER_GROUP_START_TIMEOUT_S"] = "120"

from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig, RunConfig

ray.init(address="auto", ignore_reinit_error=True)

print(f"Available: {ray.available_resources()}")

def train_worker(config):
    import torch
    import os
    print("=" * 50)
    print(f"WORKER STARTED!")
    print(f"CUDA_VISIBLE_DEVICES: {os.environ.get('CUDA_VISIBLE_DEVICES')}")
    print(f"torch.cuda.is_available(): {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"Device: {torch.cuda.get_device_name(0)}")
    print("=" * 50)
    
    from ray.train import report
    report({"accuracy": 0.95})

print("Creating TorchTrainer...")
trainer = TorchTrainer(
    train_loop_per_worker=train_worker,
    scaling_config=ScalingConfig(
        num_workers=1,
        use_gpu=True,
        resources_per_worker={"CPU": 1, "GPU": 1},
    ),
    train_loop_config={"lr": 0.01},
)

print("Starting trainer.fit()...")
result = trainer.fit()
print(f"SUCCESS! Result: {result.metrics}")