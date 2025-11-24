import os
import time
import torch
import torch.nn as nn
import torch.optim as optim

import torchvision
import torchvision.transforms as T
from torch.utils.data import DataLoader

from ray import tune
from ray.air import session
from ray.train import ScalingConfig
from ray.air.config import RunConfig as AirRunConfig
from ray.train.torch import TorchTrainer, prepare_model, prepare_data_loader, TorchConfig

# ---- Recommended for fragmentation reduction (optional but helps) ----
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# ----------------------------- Model factory -----------------------------
def build_model(model_name: str, out_dim: int, train_backbone: bool) -> nn.Module:
    """
    Create a torchvision model with ImageNet weights, replace the classification head to out_dim,
    and set requires_grad so that only the new head is guaranteed trainable when train_backbone=False.

    Supported:
      - resnext50_32x4d
      - resnet50
      - wide_resnet101_2
      - vgg19
      - densenet201
      - mobilenet_v3_large
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
                         f"vgg19, densenet201, mobilenet_v3_large")

    # Freeze policy: everything = train_backbone; head = always trainable
    for p in model.parameters():
        p.requires_grad = bool(train_backbone)
    for p in head_module.parameters():
        p.requires_grad = True

    return model

# ----------------------------- Train loop -----------------------------
def train_cifar10_torch(config):
    # ----- HParams & knobs -----
    epochs         = int(config.get("epoch", 12))
    lr             = float(config.get("learning_rate", 5e-3))
    momentum       = float(config.get("momentum", 0.9))
    out_dim        = int(config.get("hidden", 10))            # CIFAR-10 classes
    batch_size     = int(config.get("batch_size", 64))
    data_dir       = config.get("data_dir", os.path.expanduser("~/cifar10"))
    image_size     = int(config.get("image_size", 160))        # smaller than 224 → VRAM win
    amp            = bool(config.get("amp", True))             # enable AMP
    train_backbone = bool(config.get("train_backbone", False)) # freeze trunk by default
    model_name     = str(config.get("model_name", os.environ.get("MODEL_NAME", "resnext50_32x4d")))

    # DEBUG: Log which model is being used
    print(f"🔍 TRAINING WITH MODEL: {model_name}")
    print(f"🔍 FULL CONFIG: epochs={epochs}, lr={lr}, batch_size={batch_size}, image_size={image_size}")

    # ----- Transforms -----
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

    train_ds = torchvision.datasets.CIFAR10(data_dir, train=True,  download=True, transform=tfm_train)
    test_ds  = torchvision.datasets.CIFAR10(data_dir, train=False, download=True, transform=tfm_test)

    # Fewer loader workers keeps host RAM lower and avoids spikes
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True,  num_workers=2, pin_memory=True)
    test_dl  = DataLoader(test_ds,  batch_size=256,        shuffle=False, num_workers=2, pin_memory=True)

    # ----- Model (generic) -----
    model = build_model(model_name=model_name, out_dim=out_dim, train_backbone=train_backbone)

    # Optional perf tweak
    #torch.backends.cudnn.benchmark = True

    # Wrap for Ray Train
    model = prepare_model(model)
    train_dl = prepare_data_loader(train_dl)
    test_dl  = prepare_data_loader(test_dl)



    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=lr, momentum=momentum, weight_decay=5e-4, nesterov=True
    )

    # New AMP API
    scaler = torch.amp.GradScaler('cuda', enabled=amp)

    best = 0.0
    wall_start = time.perf_counter()

    for ep in range(1, epochs+1):
        model.train()
        for xb, yb in train_dl:
            xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast('cuda', enabled=amp):
                logits = model(xb)
                loss = criterion(logits, yb)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

        # Eval
        model.eval()
        correct = total = 0
        with torch.no_grad():
            for xb, yb in test_dl:
                xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
                with torch.amp.autocast('cuda', enabled=amp):
                    preds = model(xb).argmax(1)
                correct += (preds == yb).sum().item()
                total   += yb.size(0)
        acc = correct / max(1,total)
        best = max(best, acc)
        # per-epoch stats; keep or remove as you like
        session.report({
            "epoch": ep, "accuracy": acc, "best_accuracy": best,
            "avg_epoch_time_s": (time.perf_counter() - wall_start) / ep
        })

# ----------------------------- Ray Train driver -----------------------------
def train_driver_fn(config):
    num_workers = int(config["num_workers"])
    trainer = TorchTrainer(
        train_loop_per_worker=train_cifar10_torch,
        scaling_config=ScalingConfig(
            num_workers=num_workers,
            use_gpu=True,
            resources_per_worker={"GPU": 1},
        ),
        # If you ever keep genuinely unused params in some model variant, flip this True.
        torch_config=TorchConfig(backend="nccl"),
        train_loop_config=config,
        run_config=AirRunConfig(
            name="train",
            storage_path=os.path.expanduser("~/ray_results"),
        ),
    )
    result = trainer.fit()
    final_acc = result.metrics.get("accuracy") or result.metrics.get("best_accuracy")
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
    rem  = max(0, total_hosts - base * num_samples)
    alloc = [base + (1 if i < rem else 0) for i in range(num_samples)]
    it = iter(alloc)

    if MEASURE:
        num_samples = 1 # 1 trial only
        # each trial gets all HOSTS GPUs as DDP workers
        stop_cond = None
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

    else:
        stop_cond = {"accuracy": 0.99}
        param_space = {
                "model_name": model_name,
                "epoch": tune.randint(2,5),
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
    print("Best:", results.get_best_result(metric="accura export HOSTS=4cy", mode="max").config)



