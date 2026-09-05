import os
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
from ray.train.torch import TorchTrainer, prepare_model, prepare_data_loader

# ---- Recommended for fragmentation reduction (optional but helps) ----
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

def train_cifar10_torch(config):
    # ----- HParams & knobs -----
    epochs       = int(config.get("epoch", 12))
    lr           = float(config.get("learning_rate", 5e-3))
    momentum     = float(config.get("momentum", 0.9))
    out_dim      = int(config.get("hidden", 10))          # CIFAR-10 classes
    batch_size   = int(config.get("batch_size", 64))
    data_dir     = config.get("data_dir", os.path.expanduser("~/cifar10"))
    image_size   = int(config.get("image_size", 160))      # << smaller than 224
    amp          = bool(config.get("amp", True))           # << enable AMP
    train_backbone = bool(config.get("train_backbone", False))  # << freeze trunk by default

    # ----- Transforms (smaller spatial size reduces VRAM a LOT) -----
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

    # ----- Model: DenseNet-201 backbone + new classifier -----
    #weights = torchvision.models.DenseNet201_Weights.IMAGENET1K_V1
    #weights = torchvision.models.MobileNet_V3_Large_Weights.IMAGENET1K_V1
    #weights = torchvision.models.VGG19_Weights.IMAGENET1K_V1
    weights = torchvision.models.ResNeXt50_32X4D_Weights.IMAGENET1K_V1
    # weights = torchvision.models.Wide_ResNet101_2_Weights.IMAGENET1K_V1
    #model = torchvision.models.densenet201(weights=weights)
    #model = torchvision.models.mobilenet_v3_large(weights=weights)
    #model = torchvision.models.vgg19(weights=weights)
    model = torchvision.models.resnext50_32x4d(weights=weights)
    # model = torchvision.models.wide_resnet101_2(weights=weights)

    # Freeze backbone by default -> backprop only through classifier (big VRAM win)
    if hasattr(model, 'features'):
        for p in model.features.parameters():
            p.requires_grad = train_backbone

    in_dim = []
    if hasattr(model, 'fc'):
        in_dim = model.fc.in_features
    elif hasattr(model.classifier, 'in_features'):
        in_dim = model.classifier.in_features
    # Check if the classifier is a Sequential, then get the first layer's in_features
    elif isinstance(model.classifier, nn.Sequential):
        in_dim = model.classifier[0].in_features
    else:
        # Add more cases if you are using other models
        raise AttributeError("Cannot find in_features for the classifier layer")
    model.classifier = nn.Linear(in_dim, out_dim)

    model = prepare_model(model)
    train_dl = prepare_data_loader(train_dl)
    test_dl  = prepare_data_loader(test_dl)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(filter(lambda p: p.requires_grad, model.parameters()),
                          lr=lr, momentum=momentum, weight_decay=5e-4, nesterov=True)

    scaler = torch.cuda.amp.GradScaler(enabled=amp)

    best = 0.0
    for ep in range(1, epochs+1):
        model.train()
        for xb, yb in train_dl:
            xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.cuda.amp.autocast(enabled=amp):
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
                with torch.cuda.amp.autocast(enabled=amp):
                    preds = model(xb).argmax(1)
                correct += (preds == yb).sum().item()
                total   += yb.size(0)
        acc = correct / max(1,total)
        best = max(best, acc)
        session.report({"epoch": ep, "accuracy": acc, "best_accuracy": best})

def train_driver_fn(config):
    num_workers = int(config["num_workers"])
    trainer = TorchTrainer(
        train_loop_per_worker=train_cifar10_torch,
        scaling_config=ScalingConfig(
            num_workers=num_workers,
            use_gpu=True,
            resources_per_worker={"GPU": 1},
        ),
        train_loop_config=config,
        run_config=AirRunConfig(
            name="train",
            storage_path=os.path.expanduser("~/ray_results"),
        ),
    )
    result = trainer.fit()
    final_acc = result.metrics.get("accuracy") or result.metrics.get("best_accuracy")
    tune.report({"accuracy": final_acc})

if __name__ == "__main__":
    import ray
    ray.init(address="auto", ignore_reinit_error=True)

    MEASURE = os.environ.get("MEASURE", "0") == "1"
    fixed_epochs = int(os.environ.get("EPOCHS", "3"))
    fixed_batch_size = int(os.environ.get("BATCH_SIZE", "64"))
    fixed_img = int(os.environ.get("IMAGE_SIZE", "160"))

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
                "data_dir": os.path.expanduser("~/cifar10"),
                "num_workers": tune.sample_from(lambda _: next(it)),
                }
    else:
        stop_cond = {"accuracy": 0.99}
        param_space = { 
                "epoch": tune.randint(2,5),
                "learning_rate": tune.loguniform(1e-4, 3e-2),
                "momentum": tune.uniform(0.6, 0.98),
                "hidden": tune.choice([10]),
                "batch_size": tune.qrantint(32, 128, 16),
                "image_size": tune.choice([128, 144, 160, 176]),
                "amp": tune.choice([True]),
                "train_backbone": tune.choice([False]),
                "data_dir": os.path.expanduser("~/cifar10"),
                "num_workers": tune.sample_from(lambda _: next(it)),
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

"""
    tuner = tune.Tuner(
        train_driver_fn,
        tune_config=tune.TuneConfig(num_samples=num_samples),
        run_config=tune.RunConfig(
            name="exp",
            stop={"accuracy": 0.99},
            storage_path=os.path.expanduser("~/ray_results"),
        ),
        param_space={
            # safer ranges for a 16 GB T4
            "epoch": tune.randint(2, 5),
            "learning_rate": tune.loguniform(1e-4, 3e-2),
            "momentum": tune.uniform(0.6, 0.98),
            "hidden": tune.choice([10]),
            "batch_size": tune.qrandint(32, 128, 16),
            "image_size": tune.choice([128, 144, 160, 176]),
            "amp": tune.choice([True]),             # keep AMP on
            "train_backbone": tune.choice([False]), # freeze by default
            "data_dir": os.path.expanduser("~/cifar10"),
            "num_workers": tune.sample_from(lambda _: next(it)),
        },
    )
    """
results = tuner.fit()
print("Best:", results.get_best_result(metric="accuracy", mode="max").config)

