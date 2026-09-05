# measure_baseline.py
import ray
import os
import json
from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig, RunConfig
from train_cifar10_torch_oomsafe_instrumented import train_cifar10_torch

ray.init(ignore_reinit_error=True)

config = {
    "epoch": 3,
    "learning_rate": 5e-3,
    "momentum": 0.9,
    "hidden": 10,
    "batch_size": 64,
    "image_size": 160,
    "amp": True,
    "train_backbone": False,
    "model_name": os.environ.get("MODEL_NAME", "resnet50"),
    "data_dir": os.path.expanduser("~/cifar10"),
}

num_workers = int(os.environ.get("HOSTS", "1"))
instance_type = os.environ.get("INSTANCE_TYPE", "unknown")

trainer = TorchTrainer(
    train_loop_per_worker=train_cifar10_torch,
    scaling_config=ScalingConfig(
        num_workers=num_workers,
        resources_per_worker={"GPU": 1},
        use_gpu=True
    ),
    train_loop_config=config,
    run_config=RunConfig(
        name=f"baseline_{config['model_name']}_{num_workers}nodes_{instance_type}",
        storage_path='/fsx/baseline_results'
    )
)

result = trainer.fit()

# Extract and save summary
summary = {
    "config": config,
    "num_workers": num_workers,
    "instance_type": instance_type,
    "final_accuracy": result.metrics.get('best_accuracy', 0),
    "metrics": result.metrics
}

output_file = f"/fsx/baseline_results/summary_{config['model_name']}_{num_workers}nodes.json"
with open(output_file, 'w') as f:
    json.dump(summary, f, indent=2)

print(f"Results saved to {output_file}")
print(f"Overhead measurements in: /tmp/overhead_measurements_*.jsonl")