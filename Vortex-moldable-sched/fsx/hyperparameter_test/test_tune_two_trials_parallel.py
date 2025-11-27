# Save as: test_tune_two_trials_parallel.py

import ray
import os

os.environ["RAY_TRAIN_WORKER_GROUP_START_TIMEOUT_S"] = "180"

from ray import tune
from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig

ray.shutdown()
ray.init(address="auto", ignore_reinit_error=True)

print(f"Available: {ray.available_resources()}")

def train_worker(config):
    import torch
    import time
    import socket
    
    hostname = socket.gethostname()
    print(f"WORKER on {hostname}: CUDA={torch.cuda.is_available()}")
    
    if torch.cuda.is_available():
        device = torch.device("cuda:0")
        x = torch.randn(5000, 5000, device=device)
        for i in range(3):
            y = torch.mm(x, x)
            torch.cuda.synchronize()
            print(f"WORKER on {hostname}: Iter {i+1}/3 - GPU mem: {torch.cuda.memory_allocated()/1e9:.2f} GB")
            time.sleep(1)
    
    from ray.train import report
    report({"accuracy": 0.9 + config.get("lr", 0.01)})

def tune_trainable(config):
    import socket
    print(f"TRIAL on {socket.gethostname()}: Starting with lr={config.get('lr')}")
    
    trainer = TorchTrainer(
        train_loop_per_worker=train_worker,
        scaling_config=ScalingConfig(
            num_workers=1,
            use_gpu=True,
        ),
        train_loop_config=config,
    )
    
    result = trainer.fit()
    accuracy = result.metrics.get("accuracy", 0.0) if result.metrics else 0.0
    print(f"TRIAL: Done, accuracy={accuracy}")
    return {"accuracy": accuracy}

tuner = tune.Tuner(
    tune_trainable,
    tune_config=tune.TuneConfig(
        num_samples=2,
        max_concurrent_trials=2,  # Run both in parallel!
        metric="accuracy",
        mode="max",
    ),
    param_space={"lr": tune.choice([0.01, 0.05])},
)

print("Starting tuner.fit()...")
results = tuner.fit()
print(f"Done!")
print(f"Best config: {results.get_best_result().config}")
print(f"Best metrics: {results.get_best_result().metrics}")