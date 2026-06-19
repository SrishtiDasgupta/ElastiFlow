# Save as: test_gpu_actual_work.py

import ray
import os

os.environ["RAY_TRAIN_WORKER_GROUP_START_TIMEOUT_S"] = "120"

from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig

ray.init(address="auto", ignore_reinit_error=True)

def train_worker(config):
    import torch
    import time
    
    print("=" * 60)
    print(f"CUDA available: {torch.cuda.is_available()}")
    print(f"CUDA device count: {torch.cuda.device_count()}")
    
    if torch.cuda.is_available():
        print(f"Device name: {torch.cuda.get_device_name(0)}")
        
        # Actually USE the GPU - create tensors and do computation
        print("Creating large tensor on GPU...")
        device = torch.device("cuda:0")
        
        # Create a large tensor on GPU
        x = torch.randn(10000, 10000, device=device)
        print(f"Tensor device: {x.device}")
        
        # Do some computation
        print("Running matrix multiplication on GPU...")
        for i in range(10):
            y = torch.mm(x, x)
            torch.cuda.synchronize()  # Wait for GPU to finish
            print(f"  Iteration {i+1}/10 - GPU memory used: {torch.cuda.memory_allocated()/1e9:.2f} GB")
            time.sleep(1)  # Give time to see nvidia-smi
        
        print(f"Final GPU memory: {torch.cuda.memory_allocated()/1e9:.2f} GB")
    else:
        print("ERROR: CUDA not available!")
    
    print("=" * 60)
    
    from ray.train import report
    report({"accuracy": 0.95})

print("Starting TorchTrainer...")
trainer = TorchTrainer(
    train_loop_per_worker=train_worker,
    scaling_config=ScalingConfig(
        num_workers=1,
        use_gpu=True,
        resources_per_worker={"CPU": 1, "GPU": 1},
    ),
    train_loop_config={},
)

result = trainer.fit()
print("Done!")