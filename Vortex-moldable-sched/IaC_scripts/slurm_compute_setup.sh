#!/bin/bash
# =============================================================================
# Slurm Compute Node Setup (ParallelCluster OnNodeConfigured)
# Role: GPU worker — runs Ray, trains models
# ParallelCluster provides: FSx mount at /fsx, AWS CLI, Slurm
# AMI provides: CUDA drivers, NVIDIA runtime
# =============================================================================
set -ex

exec > >(tee /var/log/hpo-compute-setup.log) 2>&1
echo "=== HPO Slurm Compute Setup starting at $(date) ==="

export DEBIAN_FRONTEND=noninteractive

# Disable restart prompts
mkdir -p /etc/needrestart/conf.d/
echo "\$nrconf{restart} = 'a';" | tee /etc/needrestart/conf.d/99-restart.conf > /dev/null

# --- Verify FSx is mounted (ParallelCluster should have done this) ---
if ! mountpoint -q /fsx; then
    echo "ERROR: /fsx is not mounted. ParallelCluster should mount this."
    exit 1
fi
echo "FSx verified at /fsx"

# --- Verify GPU is available ---
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi
    echo "GPU verified"
else
    echo "WARNING: nvidia-smi not found. GPU drivers may not be installed."
fi

# --- Python venv with PyTorch + Ray ---
apt-get update -y
apt-get install -y python3.10-venv

sudo -u ubuntu bash <<'USEREOF'
set -ex
python3 -m venv ~/rayenv
source ~/rayenv/bin/activate
pip install --upgrade pip
pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install "ray[default]" "ray[tune]"
pip install numpy pandas filelock boto3 paramiko pyyaml scikit-learn
echo "PyTorch + Ray venv created"
USEREOF

# --- Copy Vortex codebase and HPO scripts from FSx ---
sudo -u ubuntu bash <<'USEREOF'
set -ex
cp -r /fsx/Vortex ~/Vortex
echo "Vortex codebase copied"

if [ -d /fsx/hyperparameter_test ]; then
    cp -r /fsx/hyperparameter_test ~/hyperparameter_test
elif [ -d /fsx/hyperparametr_test ]; then
    cp -r /fsx/hyperparametr_test ~/hyperparametr_test
fi
echo "HPO scripts copied"
USEREOF

# --- Copy AWS credentials if available on FSx ---
if [ -d /fsx/.aws ]; then
    sudo -u ubuntu cp -r /fsx/.aws /home/ubuntu/
    echo "AWS credentials copied"
fi

# --- Download CIFAR-10 dataset ---
sudo -u ubuntu bash <<'USEREOF'
set -ex
source ~/rayenv/bin/activate
if [ ! -d ~/cifar10 ]; then
    python3 -c "
import torchvision
import torchvision.transforms as transforms
import os
transform = transforms.Compose([transforms.ToTensor()])
dataset = torchvision.datasets.CIFAR10(root=os.path.expanduser('~/cifar10'), train=True, download=True, transform=transform)
print('CIFAR-10 downloaded successfully')
"
else
    echo "CIFAR-10 already exists"
fi
USEREOF

# --- Create ready marker ---
sudo -u ubuntu bash -c 'echo "HPO_WORKER_READY:$(date)" > ~/hpo_worker_ready.marker'

echo "=== HPO Slurm Compute Setup completed at $(date) ==="
