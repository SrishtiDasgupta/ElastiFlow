import os
import time
import torch
import torch.nn as nn
import torch.optim as optim
import psutil
import json
from datetime import datetime

import torchvision
import torchvision.transforms as T
from torch.utils.data import DataLoader

from ray import tune
import ray.train
from ray.train import ScalingConfig
from ray.train import RunConfig as AirRunConfig
from ray.train.torch import TorchTrainer, prepare_model, prepare_data_loader, TorchConfig

# ---- Recommended for fragmentation reduction (optional but helps) ----
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

def get_system_info():
    """Collect system and GPU information for overhead analysis"""
    system_info = {
        "instance_type": os.environ.get("INSTANCE_TYPE", "unknown"),
        "cluster_size": os.environ.get("CLUSTER_SIZE", "unknown"),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", "all"),
        "pytorch_cuda_alloc_conf": os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "none"),
        "system_load_avg": os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0,
        "free_memory_gb": psutil.virtual_memory().available // (1024**3),
        "total_memory_gb": psutil.virtual_memory().total // (1024**3),
        "cpu_count": os.cpu_count()
    }
    
    if torch.cuda.is_available():
        device_props = torch.cuda.get_device_properties(0)
        system_info.update({
            "gpu_name": device_props.name,
            "gpu_memory_total_gb": device_props.total_memory // (1024**3),
            "gpu_compute_capability": f"{device_props.major}.{device_props.minor}",
            "gpu_multiprocessor_count": device_props.multi_processor_count
        })
    
    return system_info

def measure_model_initialization(model_name, out_dim, train_backbone):
    """Measure model creation ( Ray will place it on GPU via prepare_model)."""
    import time
    timing_data = {}
    init_start = time.perf_counter()
    model = build_model(model_name=model_name, out_dim=out_dim, train_backbone=train_backbone)
    timing_data["model_creation_time"] = time.perf_counter() - init_start
    # placement happens later via prepare_model; report 0 here
    timing_data["gpu_transfer_time"] = 0.0
    return model, timing_data

def measure_data_loading_overhead(data_dir, batch_size, image_size):
    """Measure data loading and preparation overhead"""
    timing_data = {}
    
    # Data loading setup
    dataloader_start = time.perf_counter()
    
    tfm_train = T.Compose([
        T.Resize(image_size),
        T.RandomCrop(image_size, padding=4),
        T.RandomHorizontalFlip(),
        T.ToTensor(),
        T.Normalize((0.485,0.456,0.406),(0.229,0.224,0.225)),
    ])
    tfm_test = T.Compose([
        T.Resize(image_size),
        T.ToTensor(),
        T.Normalize((0.485,0.456,0.406),(0.229,0.224,0.225)),
    ])
    
    train_ds = torchvision.datasets.CIFAR10(data_dir, train=True, download=True, transform=tfm_train)
    test_ds = torchvision.datasets.CIFAR10(data_dir, train=False, download=True, transform=tfm_test)
    
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=2, pin_memory=True)
    test_dl = DataLoader(test_ds, batch_size=256, shuffle=False, num_workers=2, pin_memory=True)
    
    timing_data["dataloader_creation_time"] = time.perf_counter() - dataloader_start
    
    # Ray data preparation
    ray_prep_start = time.perf_counter()
    train_dl = prepare_data_loader(train_dl)
    test_dl = prepare_data_loader(test_dl)
    timing_data["ray_dataloader_prep_time"] = time.perf_counter() - ray_prep_start
    
    # First batch loading (cache effects)
    first_batch_start = time.perf_counter()
    first_batch = next(iter(train_dl))
    timing_data["first_batch_loading_time"] = time.perf_counter() - first_batch_start
    
    return train_dl, test_dl, timing_data

def measure_gpu_warmup(model, train_dl, device, amp=True):
    """Measure GPU warmup effects"""
    timing_data = {}
    
    warmup_start = time.perf_counter()
    
    model.train()
    warmup_batch = next(iter(train_dl))
    xb, yb = warmup_batch[0].to(device, non_blocking=True), warmup_batch[1].to(device, non_blocking=True)
    
    # Warmup forward pass
    with torch.amp.autocast('cuda', enabled=amp):
        logits = model(xb)
        criterion = nn.CrossEntropyLoss()
        loss = criterion(logits, yb)
    
    # Warmup backward pass
    loss.backward()
    
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    
    timing_data["gpu_warmup_time"] = time.perf_counter() - warmup_start
    
    return timing_data

def measure_distributed_setup_overhead(num_workers, model):
    """Measure distributed training setup overhead"""
    timing_data = {}
    
    if num_workers > 1:
        # DDP initialization time
        ddp_init_start = time.perf_counter()
        
        # Ray model preparation (includes DDP setup)
        ray_prep_start = time.perf_counter()
        model = prepare_model(model)
        device = next(model.parameters()).device
        timing_data["ray_model_prep_time"] = time.perf_counter() - ray_prep_start
        
        timing_data["ddp_initialization_time"] = time.perf_counter() - ddp_init_start
    else:
        # Single GPU Ray preparation
        ray_prep_start = time.perf_counter()
        model = prepare_model(model)
        device = next(model.parameters()).device
        timing_data["ray_model_prep_time"] = time.perf_counter() - ray_prep_start
        timing_data["ddp_initialization_time"] = 0
    
    return model, timing_data

# ----------------------------- Model factory -----------------------------
def build_model(model_name: str, out_dim: int, train_backbone: bool) -> nn.Module:
    """
    Create a torchvision model with ImageNet weights, replace the classification head to out_dim,
    and set requires_grad so that only the new head is guaranteed trainable when train_backbone=False.
    """
    name = model_name.lower()

    if name == "resnext50_32x4d":
        weights = torchvision.models.ResNeXt50_32X4D_Weights.IMAGENET1K_V1
        model = torchvision.models.resnext50_32x4d(weights=weights)
        in_dim = model.fc.in_features
        model.fc = nn.Linear(in_dim, out_dim)
        head_module = model.fc

    elif name == "resnet50":
        weights = torchvision.models.ResNet50_Weights.IMAGENET1K_V1
        model = torchvision.models.resnet50(weights=weights)
        in_dim = model.fc.in_features
        model.fc = nn.Linear(in_dim, out_dim)
        head_module = model.fc

    elif name == "wide_resnet101_2":
        weights = torchvision.models.Wide_ResNet101_2_Weights.IMAGENET1K_V1
        model = torchvision.models.wide_resnet101_2(weights=weights)
        in_dim = model.fc.in_features
        model.fc = nn.Linear(in_dim, out_dim)
        head_module = model.fc

    elif name == "vgg19":
        weights = torchvision.models.VGG19_Weights.IMAGENET1K_V1
        model = torchvision.models.vgg19(weights=weights)
        in_dim = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_dim, out_dim)
        head_module = model.classifier[-1]

    elif name == "densenet201":
        weights = torchvision.models.DenseNet201_Weights.IMAGENET1K_V1
        model = torchvision.models.densenet201(weights=weights)
        in_dim = model.classifier.in_features
        model.classifier = nn.Linear(in_dim, out_dim)
        head_module = model.classifier

    elif name == "mobilenet_v3_large":
        weights = torchvision.models.MobileNet_V3_Large_Weights.IMAGENET1K_V1
        model = torchvision.models.mobilenet_v3_large(weights=weights)
        in_dim = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_dim, out_dim)
        head_module = model.classifier[-1]

    elif name == "convnext_large":
        weights = torchvision.models.ConvNeXt_Large_Weights.IMAGENET1K_V1
        model = torchvision.models.convnext_large(weights=weights)
        in_dim = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_dim, out_dim)
        head_module = model.classifier[-1]

    else:
        raise ValueError(f"Unsupported model_name '{model_name}'. "
                         f"Choose from: resnext50_32x4d, resnet50, wide_resnet101_2, "
                         f"vgg19, densenet201, mobilenet_v3_large, convnext_large")

    # Freeze policy: everything = train_backbone; head = always trainable
    for p in model.parameters():
        p.requires_grad = bool(train_backbone)
    for p in head_module.parameters():
        p.requires_grad = True

    return model

# ----------------------------- Train loop -----------------------------
def train_cifar10_torch(config):
    """Enhanced training function with comprehensive overhead measurement"""

    print("[WORKER] CUDA_VISIBLE_DEVICES=", os.getenv("CUDA_VISIBLE_DEVICES"))
    print("[WORKER] torch.cuda.is_available=", torch.cuda.is_available(), " device_count=", torch.cuda.device_count())
    
    # ========== MEASUREMENT START ==========
    total_measurement_start = time.perf_counter()
    
    # Configuration
    epochs = int(config.get("epoch", 12))
    lr = float(config.get("learning_rate", 5e-3))
    momentum = float(config.get("momentum", 0.9))
    out_dim = int(config.get("hidden", 10))
    batch_size = int(config.get("batch_size", 64))
    data_dir = config.get("data_dir", os.path.expanduser("~/cifar10"))
    image_size = int(config.get("image_size", 160))
    amp = bool(config.get("amp", True))
    train_backbone = bool(config.get("train_backbone", False))
    model_name = str(config.get("model_name", os.environ.get("MODEL_NAME", "resnext50_32x4d")))
    num_workers = int(config.get("num_workers", 1))
    
    # Collect system information
    system_info = get_system_info()
    
    # Initialize comprehensive overhead tracking
    overhead_data = {
        "configuration": {
            "model_name": model_name,
            "num_workers": num_workers,
            "epochs": epochs,
            "batch_size": batch_size,
            "image_size": image_size,
            "learning_rate": lr,
            "momentum": momentum,
            "amp": amp,
            "train_backbone": train_backbone
        },
        "system_info": system_info,
        "timing_breakdown": {}
    }
    
    try:
        # ========== MODEL INITIALIZATION ==========
        #model, device, model_timing = measure_model_initialization(model_name, out_dim, train_backbone)
        model, model_timing = measure_model_initialization(model_name, out_dim, train_backbone)
        overhead_data["timing_breakdown"].update(model_timing)
        
        # ========== DATA LOADING ==========
        train_dl, test_dl, data_timing = measure_data_loading_overhead(data_dir, batch_size, image_size)
        overhead_data["timing_breakdown"].update(data_timing)
        
        # ========== DISTRIBUTED SETUP ==========
        model, distributed_timing = measure_distributed_setup_overhead(num_workers, model)
        overhead_data["timing_breakdown"].update(distributed_timing)
        
        # ========== OPTIMIZER SETUP ==========
        optimizer_start = time.perf_counter()
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.SGD(
            filter(lambda p: p.requires_grad, model.parameters()),
            lr=lr, momentum=momentum, weight_decay=5e-4, nesterov=True
        )
        scaler = torch.amp.GradScaler('cuda', enabled=amp)
        overhead_data["timing_breakdown"]["optimizer_setup_time"] = time.perf_counter() - optimizer_start
        
        # ========== GPU WARMUP ==========
        device = next(model.parameters()).device
        warmup_timing = measure_gpu_warmup(model, train_dl, device, amp)
        overhead_data["timing_breakdown"].update(warmup_timing)
        
        # Clear gradients after warmup
        optimizer.zero_grad(set_to_none=True)
        
        # ========== ACTUAL TRAINING MEASUREMENT ==========
        training_start = time.perf_counter()
        best = 0.0
        epoch_overheads = []
        epoch_pure_compute_times = []
        
        for ep in range(1, epochs + 1):
            epoch_start = time.perf_counter()
            
            # ===== TRAINING PHASE =====
            pure_training_start = time.perf_counter()
            model.train()
            
            batch_count = 0
            for xb, yb in train_dl:
                batch_start = time.perf_counter()
                
                xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                
                with torch.amp.autocast('cuda', enabled=amp):
                    logits = model(xb)
                    loss = criterion(logits, yb)
                
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                
                batch_count += 1
                
                # Measure first and last batch separately (caching effects)
                if batch_count == 1:
                    first_batch_time = time.perf_counter() - batch_start
                elif batch_count == len(train_dl):
                    last_batch_time = time.perf_counter() - batch_start
            
            pure_training_end = time.perf_counter()
            pure_training_time = pure_training_end - pure_training_start
            
            # ===== EVALUATION PHASE =====
            pure_eval_start = time.perf_counter()
            model.eval()
            correct = total = 0
            
            with torch.no_grad():
                for xb, yb in test_dl:
                    xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
                    with torch.amp.autocast('cuda', enabled=amp):
                        preds = model(xb).argmax(1)
                    correct += (preds == yb).sum().item()
                    total += yb.size(0)
            
            pure_eval_end = time.perf_counter()
            pure_eval_time = pure_eval_end - pure_eval_start
            
            epoch_end = time.perf_counter()
            
            # ===== OVERHEAD CALCULATION =====
            pure_compute_time = pure_training_time + pure_eval_time
            total_epoch_time = epoch_end - epoch_start
            coordination_overhead = total_epoch_time - pure_compute_time
            
            epoch_overheads.append(coordination_overhead)
            epoch_pure_compute_times.append(pure_compute_time)
            
            acc = correct / max(1, total)
            best = max(best, acc)
            
            # Report enhanced metrics
            ray.train.report({
                "epoch": ep,
                "accuracy": acc,
                "best_accuracy": best,
                "pure_compute_time_s": pure_compute_time,
                "pure_training_time_s": pure_training_time,
                "pure_eval_time_s": pure_eval_time,
                "coordination_overhead_s": coordination_overhead,
                "total_epoch_time_s": total_epoch_time,
                "overhead_percentage": (coordination_overhead / total_epoch_time) * 100,
                "first_batch_time_s": first_batch_time if ep == 1 else 0,
                "last_batch_time_s": last_batch_time if ep == 1 else 0
            })
        
        training_end = time.perf_counter()
        total_training_time = training_end - training_start
        
        # ========== FINAL OVERHEAD SUMMARY ==========
        total_measurement_time = time.perf_counter() - total_measurement_start
        total_coordination_overhead = sum(epoch_overheads)
        total_pure_compute = sum(epoch_pure_compute_times)
        avg_overhead_per_epoch = total_coordination_overhead / len(epoch_overheads)
        
        overhead_data["timing_breakdown"].update({
            "total_measurement_time": total_measurement_time,
            "total_training_time": total_training_time,
            "total_coordination_overhead": total_coordination_overhead,
            "total_pure_compute": total_pure_compute,
            "avg_coordination_overhead_per_epoch": avg_overhead_per_epoch,
            "coordination_overhead_percentage": (total_coordination_overhead / total_training_time) * 100
        })
        
        overhead_data["final_metrics"] = {
            "best_accuracy": best,
            "total_epochs": epochs,
            "successful_completion": True
        }
        
        # Write comprehensive overhead data
        overhead_log_dir = os.environ.get('OVERHEAD_LOG_DIR', '/tmp')
        overhead_log_file = f'{overhead_log_dir}/overhead_measurements_{datetime.now().strftime("%Y%m%d")}.jsonl'
        with open(overhead_log_file, 'a') as f:
            f.write(json.dumps(overhead_data) + '\n')
        
        # Final summary report
        ray.train.report({
            "trial_summary": True,
            "total_coordination_overhead_s": total_coordination_overhead,
            "avg_coordination_overhead_per_epoch_s": avg_overhead_per_epoch,
            "coordination_overhead_percentage": (total_coordination_overhead / total_training_time) * 100,
            "total_pure_compute_s": total_pure_compute,
            "num_workers": num_workers,
            "model_name": model_name,
            "epochs": epochs,
            "instance_type": system_info.get("instance_type", "unknown"),
            "overhead_data_written": True
        })
        
    except Exception as e:
        # Log error with partial overhead data
        overhead_data["error"] = {
            "message": str(e),
            "type": type(e).__name__,
            "partial_measurement": True
        }
        
        overhead_log_dir = os.environ.get('OVERHEAD_LOG_DIR', '/tmp')
        overhead_log_file = f'{overhead_log_dir}/overhead_measurements_{datetime.now().strftime("%Y%m%d")}.jsonl'
        with open(overhead_log_file, 'a') as f:
            f.write(json.dumps(overhead_data) + '\n')

        raise

# ----------------------------- Ray Train driver -----------------------------
def train_driver_fn(config):
    num_workers = int(os.getenv("HOSTS", str(config.get("num_workers", 1))))
    config["num_workers"] = num_workers
    trainer = TorchTrainer(
        train_loop_per_worker=train_cifar10_torch,
        scaling_config=ScalingConfig(
            num_workers=num_workers,
            use_gpu=True,
            resources_per_worker={"GPU": 1},
        ),
        torch_config=TorchConfig(backend="nccl"),
        train_loop_config=config,
        run_config=AirRunConfig(
            name="train",
            storage_path=os.path.expanduser("~/ray_results"),
        ),
    )
    result = trainer.fit()
    metrics = result.metrics or {}
    final_acc = metrics.get("accuracy") or metrics.get("best_accuracy") or 0.0
    tune.report({"accuracy": final_acc})

# ----------------------------- __main__ -----------------------------
if __name__ == "__main__":
    import ray
    ray.init(address="auto", ignore_reinit_error=True)

    MEASURE = os.environ.get("MEASURE", "0") == "1"
    fixed_epochs = int(os.environ.get("EPOCHS", "3"))
    fixed_batch_size = int(os.environ.get("BATCH_SIZE", "64"))
    fixed_img = int(os.environ.get("IMAGE_SIZE", "160"))
    model_name = str(os.environ.get("MODEL_NAME", "resnext50_32x4d"))

    total_hosts = int(os.environ.get("HOSTS", "1"))
    num_samples = int(os.environ.get("NUM_SAMPLES", "2"))
    base = max(1, total_hosts // max(1, num_samples))
    rem = max(0, total_hosts - base * num_samples)
    alloc = [base + (1 if i < rem else 0) for i in range(num_samples)]
    it = iter(alloc)

    if MEASURE:
        num_samples = 1  # 1 trial only
        param_space = {
            "epoch": fixed_epochs,
            "learning_rate": 5e-3,
            "momentum": 0.9,
            "hidden": 10,
            "batch_size": fixed_batch_size,
            "image_size": fixed_img,
            "amp": True,
            "train_backbone": False,
            "model_name": model_name,
            "data_dir": os.path.expanduser("~/cifar10"),
            "num_workers": total_hosts,
        }
        stop_cond = None
    else:
        stop_cond = {"accuracy": 0.99}
        param_space = {
            "model_name": model_name,
            "epoch": tune.randint(2, 5),
            "learning_rate": tune.loguniform(1e-4, 3e-2),
            "momentum": tune.uniform(0.6, 0.98),
            "hidden": tune.choice([10]),
            "batch_size": tune.qrandint(32, 128, 16),
            "image_size": tune.choice([128, 144, 160, 176]),
            "amp": tune.choice([True]),
            "train_backbone": tune.choice([False]),
            "data_dir": os.path.expanduser("~/cifar10"),
            "num_workers": tune.grid_search(alloc),
        }

    tuner = tune.Tuner(
        train_driver_fn,
        tune_config=tune.TuneConfig(num_samples=num_samples),
        run_config=tune.RunConfig(
            name="exp",
            stop=stop_cond,
            storage_path=os.path.expanduser("~/ray_results"),
        ),
        param_space=param_space,
    )

    results = tuner.fit()
    print("Best:", results.get_best_result(metric="accuracy", mode="max").config)


