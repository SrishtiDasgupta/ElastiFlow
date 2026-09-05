# HPO Workload Description

## 1. Overview

The HPO (Hyperparameter Optimization) workload is a representative machine learning use-case for evaluating the Vortex moldable scheduling system. Each HPO workflow performs automated hyperparameter tuning for image classification using transfer learning on CIFAR-10. The workload is designed to exhibit the computational patterns that benefit from moldable resource allocation: iterative execution with variable parallelism, GPU-bound computation, and multi-round optimization with feedback loops.

## 2. Machine Learning Task

**Task**: Image classification via transfer learning.

Pre-trained CNN backbones (ImageNet weights) are fine-tuned on CIFAR-10 by replacing and retraining only the classification head. The backbone parameters are frozen (`train_backbone=False`), making training fast and focused on the final layer.

### 2.1 Dataset

**CIFAR-10** (Canadian Institute for Advanced Research, 10 classes):
- 50,000 training images, 10,000 test images
- Native resolution: 32x32 RGB
- Upsampled to **64x64** pixels for GPU training on xlarge instances
- 10 classes: airplane, automobile, bird, cat, deer, dog, frog, horse, ship, truck

**Data augmentation** (training set only):
- `Resize(64)` — upsample to 64x64
- `RandomCrop(64, padding=4)` — random spatial crop with 4-pixel padding
- `RandomHorizontalFlip()` — 50% chance horizontal flip
- `Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))` — ImageNet channel statistics

**Test transforms**: `Resize(64)` + `Normalize` only (no augmentation).

### 2.2 Model Architectures

Three CNN architectures are used, selected to span a range of computational costs while sharing the same transfer learning setup:

| Model | Architecture | Pretrained Weights | Head Replacement | Trainable Params |
|---|---|---|---|---|
| **VGG19** | 19-layer VGG | `VGG19_Weights.IMAGENET1K_V1` | `classifier[-1]` → `Linear(4096, 10)` | ~40K (head only) |
| **Wide ResNet101-2** | 101-layer wide residual network | `Wide_ResNet101_2_Weights.IMAGENET1K_V1` | `fc` → `Linear(2048, 10)` | ~20K (head only) |
| **ConvNeXt Large** | Modern ConvNet (2022) | `ConvNeXt_Large_Weights.IMAGENET1K_V1` | `classifier[-1]` → `Linear(1536, 10)` | ~15K (head only) |

All models use `torchvision.models` with ImageNet-pretrained weights. The full backbone is frozen; only the replaced classification head is trained.

### 2.3 Training Configuration

| Parameter | Value |
|---|---|
| **Optimizer** | SGD with Nesterov momentum (`nesterov=True`) |
| **Weight decay** | 5e-4 |
| **Loss function** | CrossEntropyLoss |
| **Mixed precision** | Enabled (PyTorch AMP: `torch.amp.autocast` + `GradScaler`) |
| **Output classes** | 10 (CIFAR-10) |
| **DataLoader workers** | 2 per GPU (with `pin_memory=True`) |
| **Test batch size** | 256 (fixed) |
| **Training batch size** | Variable (32, 64, or 128 — tuned hyperparameter) |

### 2.4 Per-Epoch Training Loop

```
for each epoch:
    model.train()
    for (images, labels) in train_loader:
        optimizer.zero_grad()
        with autocast:
            logits = model(images)
            loss = CrossEntropyLoss(logits, labels)
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()

    model.eval()
    correct = total = 0
    with no_grad:
        for (images, labels) in test_loader:
            predictions = model(images).argmax(dim=1)
            correct += (predictions == labels).sum()
            total += labels.size(0)
    accuracy = correct / total

    ray.train.report({
        "epoch": epoch,
        "accuracy": accuracy,
        "best_accuracy": max(accuracy, previous_best),
        "avg_epoch_time_s": elapsed / epoch
    })
```

## 3. Hyperparameter Optimization

### 3.1 Hyperparameters Tuned

| Hyperparameter | Initial Search Range | Refined Range (Iteration 2+) | Type |
|---|---|---|---|
| **learning_rate** | `uniform(0.001, 0.1)` | `loguniform(best × 0.1, best × 10)` | Continuous (log-scale refinement) |
| **momentum** | `uniform(0.80, 0.99)` | `uniform(best × 0.9, best × 1.1)` | Continuous |
| **batch_size** | `choice([32, 64, 128])` | Fixed to best from prior round | Categorical |

### 3.2 HPO Framework

**Ray Tune** (part of the Ray AI Runtime) is used for distributed hyperparameter search.

**Search scheduler**: `AsyncHyperBandScheduler` (ASHA — Asynchronous Successive Halving Algorithm)
- Performs aggressive early stopping of underperforming trials
- Allocates more training resources (epochs) to promising configurations
- `max_t=200`: maximum training budget per trial (epochs)
- Asynchronous: does not wait for all trials to reach the same epoch before making pruning decisions

### 3.3 Optimization Objective

- **Metric**: Test set accuracy on CIFAR-10 (fraction correct, range 0.0–1.0)
- **Direction**: Maximize (`mode="max"`)
- **Target threshold**: 0.99 (99% accuracy) — if reached, remaining phases are skipped

### 3.4 Convergence Criteria

Three levels of stopping:

1. **Per-trial** (ASHA): Underperforming trials are early-stopped at intermediate epoch checkpoints via successive halving. Trials that lag behind the top performers are terminated.

2. **Per-phase target**: If any trial achieves accuracy >= 0.99, the entire HPO run stops early (remaining phases are skipped).

3. **Pipeline-level** (across iterations):
   - `improvement_threshold = 1e-3` (0.1% absolute accuracy improvement)
   - `patience = 2` rounds without improvement triggers stop

### 3.5 Adaptive Trial Count

The number of trials per workflow iteration is **adaptive** based on the success rate of the previous round:

- If success rate > 50%: reduce trials — `next_trials = max(3, current × 0.5)` (exploit promising region)
- If success rate <= 50%: increase trials — `next_trials = min(10, current × 1.5)` (explore more broadly)

This mimics a Bayesian optimization strategy of alternating exploration and exploitation.

### 3.6 Search Space Refinement

Between workflow iterations, the search space is **narrowed** around the best configuration found (`refine_space()` function):

| Parameter | Refinement Strategy |
|---|---|
| `learning_rate` | Log-uniform around best: `[best × 0.1, best × 10]` |
| `momentum` | Uniform around best: `[best × 0.9, best × 1.1]` (capped at 0.1–0.99) |
| `batch_size` | Fixed to the best value (no further search) |
| `image_size` | Fixed to the best value |
| `epoch` | Integer range: `[best × 0.8, best × 1.5]` |

This progressive narrowing concentrates the search in promising regions of the hyperparameter space.

## 4. Distributed Training

### 4.1 PyTorch Distributed Data Parallel (DDP)

When multiple GPUs are allocated to a single trial, **PyTorch DDP** (via Ray Train's `TorchTrainer`) distributes the training:

- **Communication backend**: NCCL
- **Data distribution**: Ray Train automatically shards the dataset across workers
- **Gradient synchronization**: All-reduce across workers after each mini-batch
- **Model wrapping**: `prepare_model()` and `prepare_data_loader()` handle DDP setup

### 4.2 DDP Scaling Efficiency

Measured from 36 profiling runs per instance type (3 models × 3 worker counts × 4 epoch counts):

| Workers | g4dn.xlarge (T4) | g5.xlarge (A10G) | Speedup | Efficiency |
|---|---|---|---|---|
| 1 GPU | 82.0 s/epoch | 37.9 s/epoch | 1.00x | 100% |
| 2 GPUs | 42.0 s/epoch | 19.8 s/epoch | 1.95x | 97.6% |
| 4 GPUs | 21.4 s/epoch | 10.5 s/epoch | 3.84x | 96.0% |

Scaling is near-linear due to the compute-bound nature of CNN forward/backward passes and the high bandwidth of NCCL over NVLink/PCIe.

### 4.3 Speedup Model (Power Law)

Fitted from profiling data:

```
runtime_per_epoch = a × (workers ^ b) × model_factor + c
```

| Instance | a | b | c | R² |
|---|---|---|---|---|
| g4dn (T4) | 23.039 | -0.789 | 0.0 | 0.983 |
| g5 (A10G) | 16.445 | -0.771 | 0.0 | 0.988 |

Model factors (relative to VGG19):
- VGG19: 1.00
- Wide ResNet101-2: 1.34
- ConvNeXt Large: 1.46

Runtime scales **linearly with epochs** (verified: coefficient of variation < 5% across 3/6/9/12 epoch profiling runs).

### 4.4 Hybrid Batching Algorithm

When the number of trials does not evenly divide the available GPUs, a **multi-phase hybrid batching** strategy maximizes GPU utilization:

1. **Full parallel batches**: When `trials >= GPUs`, run `floor(trials / GPUs)` batches of `GPUs` trials, each with 1 GPU.
2. **Handle remainder optimally**: For leftover trials, compare:
   - **Balanced**: If GPUs divide evenly among remainder trials, run all concurrently with multi-GPU each.
   - **Distributed sequential**: Run each remaining trial with ALL GPUs sequentially.
   - **Parallel with idle**: Run remaining trials with 1 GPU each, leaving some GPUs idle.
   - The strategy with the lowest estimated time is selected.

**Example** (4 GPUs, 5 trials):
- Phase 1: 4 trials × 1 GPU each (parallel) → time: 1.0 × T₁
- Phase 2: 1 trial × 4 GPUs (distributed) → time: 0.27 × T₁
- Total: 1.27 × T₁ (**36.5% faster** than naive 2-batch approach at 2.0 × T₁)

**Decision matrix** (4 GPUs):

| Trials | Strategy | Estimated Time (× T₁) |
|---|---|---|
| 1 | 1 trial × 4 GPUs distributed | 0.27 |
| 2 | 2 trials × 2 GPUs balanced | 0.54 |
| 3 | 3 trials × 4 GPUs distributed sequential | 0.82 |
| 4 | 4 trials × 1 GPU parallel | 1.00 |
| 5 | 4×1 parallel + 1×4 distributed | 1.27 |
| 6 | 4×1 parallel + 2×2 balanced | 1.54 |
| 8 | 2 batches of 4×1 parallel | 2.00 |

## 5. Workflow Structure

### 5.1 Workflow YAML Format

Each workflow is defined as a YAML file compatible with the Steep workflow API (v4.7.0):

```yaml
api: 4.7.0
id: hpo-60bf3eb4

config:
  mesh: vgg19                    # Model architecture (string)
  workflowIterations: 3          # Number of HPO optimization rounds

constraints:
  budget: 1.18                   # Maximum cost in USD
  chains: 4                      # Parallel trials per iteration
  deadline: 4107.07              # Maximum wall-clock time in seconds
  tinydaIterations: 10           # Epochs per trial

vars:
- id: input_config
  value:
    batch_size: 32               # Initial hyperparameter: batch size
    epochs: 10                   # Initial hyperparameter: epochs
    learning_rate: 0.0701        # Initial hyperparameter: learning rate
    model: vgg19                 # Model name passed to training function
    momentum: 0.86               # Initial hyperparameter: momentum
    next_trials: 4               # Initial trial count

actions:
- type: for                      # Iterative execution (workflowIterations times)
  input: input_config            # Initial config fed into first iteration
  enumerator: i
  yieldToInput: output_config    # Output of iteration N becomes input of iteration N+1
  actions:
  - type: execute
    service: /fsx/.../run_hpo.py # Service script that orchestrates Ray Tune
    inputs:
    - id: config
      var: i
    outputs:
    - id: config_out
      var: output_config         # Best config from this round (fed to next)
```

### 5.2 Iteration Chaining (Feedback Loop)

The Steep `for` action implements the sequential optimization loop:

```
Iteration 1: Initial config → Run N trials → Best config (accuracy + hyperparams)
                                                    ↓
Iteration 2: Refined search space → Run N' trials → Improved config
                                                    ↓
Iteration 3: Further refined → Run N'' trials → Final best config
```

The output of each iteration (`output_config`) includes the best hyperparameters found and the recommended trial count for the next round. This output becomes the input to the next iteration via `yieldToInput`, enabling progressive refinement of the search space.

### 5.3 Concept Mapping: SeisSol to HPO

The HPO workload maps onto the same scheduling abstractions as the SeisSol seismic simulation use-case:

| Scheduling Concept | SeisSol (Plain) | HPO |
|---|---|---|
| **Chain** | One parallel simulation run | One parallel HPO trial |
| **Chains (2-4)** | Parallel simulation instances | Concurrent trials per iteration |
| **TinyDA Iteration** | One sequential simulation step | One training epoch |
| **TinyDA Iterations (10-28)** | Sequential steps per chain | Epochs per trial |
| **Workflow Iteration (3-5)** | Data assimilation feedback round | HPO optimization round |
| **Workflow** | Complete seismic analysis | Complete hyperparameter search |

This mapping allows the same scheduling algorithms (FCFS, EDF) and resource management strategies (static vs. moldable) to be evaluated across fundamentally different workload types.

## 6. Constraint Formulation

### 6.1 Methodology

Constraints follow the same formula as Plain SeisSol, with the epoch as the atomic sequential unit:

**Deadline** (worst-case wall-clock time):
```
deadline = epoch_runtime × avg_epochs × workflow_iterations × 3
```

**Budget** (worst-case monetary cost):
```
budget = epoch_cost × trials × avg_epochs × workflow_iterations
```

- `epoch_runtime`: Per-epoch compute time on the **slowest** instance (g4dn T4, 1 GPU)
- `epoch_cost`: Per-epoch compute cost on the **most expensive** instance (g5 on-demand)
- `3× contention factor`: Covers cold start (measured ~330s for on-demand g4dn.xlarge in R1, 2026-05-06), setup overhead, queuing delays, DDP coordination, and moldable lane-allocation contention. Empirically tuned from R1: ×2 was too tight (3/5 wfs missed by <55s — near-binary on resource luck); ×3 isolates genuine race-loss cases. Standard in HPC scheduling (1.5–3× typical, this work uses the top end of the range).

### 6.2 Measured Per-Epoch Runtimes

| Model | g4dn (12 epochs) | Per-Epoch (g4dn) | g5 (12 epochs) | Per-Epoch (g5) |
|---|---|---|---|---|
| VGG19 | 266.02s | 22.17s | 198.00s | 16.50s |
| Wide ResNet101-2 | 355.88s | 29.66s | 267.62s | 22.30s |
| ConvNeXt Large | 408.32s | 34.03s | 275.45s | 22.95s |

### 6.3 Measured Overheads

| Overhead | Value | Source |
|---|---|---|
| Cold start (on-demand) | 400.52s | Thesis Section 5.3 |
| Setup: VGG19 | 5.78s | g4_img64.jsonl |
| Setup: Wide ResNet101-2 | 5.22s | g4_img64.jsonl |
| Setup: ConvNeXt Large | 12.91s | g4_img64.jsonl |
| DDP overhead (2 GPUs) | 0.9s | Measured |
| DDP overhead (4 GPUs) | 0.8s | Measured |
| Ray coordination | ~0% | Measured as ~0.000003% |

### 6.4 Example: VGG19 Workflow

```
Deadline = 22.17 s/epoch × 20 epochs × 4 iterations × 3 = 5,321s (89 min)
Budget   = (198.00/12)/3600 × $1.006/hr × 3 trials × 20 epochs × 4 iterations = $1.11
```

### 6.5 Constraint Ranges Across 15 Designed Workflows

| Model | Budget Range | Deadline Range (×3) | Epochs | Chains | Iterations |
|---|---|---|---|---|---|
| VGG19 (4 wfs) | $1.09–$1.77 | 5,196–8,241s | 10–15 | 4 | 3–4 |
| Wide ResNet101-2 (4 wfs) | $1.14–$1.73 | 5,338–7,290s | 15–20 | 2–3 | 3–5 |
| ConvNeXt Large (7 wfs) | $1.14–$1.77 | 7,003–8,473s | 15–28 | 2–3 | 4–5 |

*Deadlines updated 2026-05-08 to ×3 contention. Original ×2 ranges were 3,464–5,494 / 3,559–4,746 / 4,083–5,649 respectively.*

## 7. Infrastructure

### 7.1 GPU Instances

| Role | Instance Type | GPU | GPU Memory | Count | Cost/hr |
|---|---|---|---|---|---|
| Slurm compute | g4dn.xlarge | 1× NVIDIA T4 | 16 GB | 4 | $0.84 (TCO) |
| Reserved cloud (g4dn) | g4dn.xlarge | 1× NVIDIA T4 | 16 GB | 2 | $0.227 |
| Reserved cloud (g5) | g5.xlarge | 1× NVIDIA A10G | 24 GB | 2 | $0.435 |
| On-demand (g4dn) | g4dn.xlarge | 1× NVIDIA T4 | 16 GB | up to 3 | $0.526 |
| On-demand (g5) | g5.xlarge | 1× NVIDIA A10G | 24 GB | up to 3 | $1.006 |

**Total capacity**: 14 GPU slots (4 on-prem + 4 reserved + 6 on-demand).

### 7.2 Software Stack

- Python 3.10 (virtual environment at `~/rayenv`)
- PyTorch 2.7.1 + CUDA 12.8
- Ray 2.x (Tune + Train)
- torchvision (pretrained models + CIFAR-10)
- Redis (inter-component messaging queues)

## 8. Execution Architecture

### 8.1 Dedicated Executor Pattern

The HPO system uses a **dedicated executor** pattern that separates the control plane from the compute plane:

```
Vortex Scheduler
    │ POST workflow to executor
    ▼
Executor (lightweight EC2, no GPU participation in training)
    │ Spawns one thread per workflow
    ▼
CloudRunnerHPO / PlclRunnerHPO
    │ SSH to worker instances
    ▼
Ray Cluster Setup
    │ ray start --head (worker[0])
    │ ray start --address=head (worker[1..N])
    ▼
hpo_pipeline_verbose.py (TunePipeline.run())
    │ Calculates batch plan
    │ Creates tune.Tuner per phase
    ▼
Ray Tune (AsyncHyperBandScheduler)
    │ Manages trial lifecycle
    ▼
TorchTrainer (per trial)
    │ Distributed training via DDP
    ▼
train_cifar10_torch() on each GPU worker
    │ Reports accuracy per epoch
    ▼
Results propagate back → Executor → Scheduler
```

### 8.2 Key Design Properties

- **Executor does not train**: It orchestrates Ray clusters on worker instances but never participates in GPU-bound computation. This prevents straggling.
- **Full moldability**: The entire worker pool can be replaced between workflow iterations. The executor SSHs into new workers, sets up a fresh Ray cluster, and continues the next HPO iteration.
- **Isolation**: Each workflow gets its own Ray cluster (started and torn down per iteration). No interference between concurrent workflows.

## 9. Workflow Design (15 Designed Configurations)

### 9.1 Workflow Table

| ID | Model | Chains | Epochs | Iterations | Category |
|----|-------|--------|--------|------------|----------|
| 0 | convnext_large | 2 | 20 | 4 | Narrow-Long |
| 1 | vgg19 | 4 | 12 | 3 | Wide-Short |
| 2 | convnext_large | 2 | 24 | 5 | Narrow-Long |
| 3 | vgg19 | 4 | 15 | 3 | Wide-Short |
| 4 | convnext_large | 3 | 15 | 4 | Medium |
| 5 | wide_resnet101_2 | 3 | 18 | 5 | Medium |
| 6 | convnext_large | 2 | 24 | 4 | Narrow-Long |
| 7 | wide_resnet101_2 | 2 | 20 | 4 | Narrow-Long |
| 8 | vgg19 | 4 | 10 | 3 | Wide-Short (fastest) |
| 9 | convnext_large | 3 | 20 | 5 | Medium-Long |
| 10 | wide_resnet101_2 | 2 | 15 | 3 | Narrow-Medium |
| 11 | convnext_large | 2 | 18 | 4 | Narrow-Medium |
| 12 | vgg19 | 4 | 15 | 4 | Wide-Medium |
| 13 | convnext_large | 2 | 28 | 5 | Narrow-Long (heaviest) |
| 14 | wide_resnet101_2 | 3 | 16 | 4 | Medium |

**Model distribution**: ConvNeXt Large (7), Wide ResNet101-2 (4), VGG19 (4).
**Epoch range**: 10–28.

### 9.2 Dispatch Order

```
WORKFLOW_ORDER = [8, 3, 9, 7, 5, 12, 1, 4, 10, 14, 0, 6, 11, 2, 13]
```

**Strategy: Wide-Short first, Narrow-Long last.**

- **Positions 0–4** (5-wf batch): `[8, 3, 9, 7, 5]` — 2 Wide-Short VGG19 (chains=4) arrive first, demanding 4 instances each. Static scheduler greedily saturates the pool (16 demand > 14 supply). Moldable starts lean (9 of 14 slots used), leaving headroom for scale-up.

- **Positions 5–9** (added for 10-wf): `[12, 1, 4, 10, 14]` — Mixed workflows arrive into an already-loaded system. Static faces severe queuing (cumulative demand: 32). Moldable has moderate oversubscription (18) with ongoing resource recycling.

- **Positions 10–14** (added for 15-wf): `[0, 6, 11, 2, 13]` — All Narrow-Long ConvNeXt (chains=2, high epochs). These long-running workflows benefit most from moldable scale-up as earlier Wide-Short workflows complete and release resources.

## 10. What the Vortex Scheduler Controls

The Vortex scheduler does **not** control the HPO search algorithm, hyperparameter selection, or trial scheduling within Ray Tune. It controls:

1. **Resource allocation**: How many GPU instances each workflow receives (static: full greedy allocation; moldable: capped at `ceil(chains × 0.5)` initially, scaled up between iterations).
2. **Instance type selection**: g4dn (cheaper, slower) vs. g5 (expensive, faster) — moldable can switch mid-workflow.
3. **Workflow ordering**: FCFS (arrival order) vs. EDF (earliest deadline first).
4. **Resource redistribution**: When a workflow completes an iteration, its resources can be reallocated (moldable only).
5. **On-demand instance creation**: Spinning up additional cloud instances when reserved capacity is exhausted.

The HPO pipeline adapts to whatever GPU count the scheduler provides via the hybrid batching algorithm. More GPUs = more concurrent trials or faster per-trial training; fewer GPUs = smaller batches but still functional.
