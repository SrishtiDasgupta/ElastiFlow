#!/bin/bash
# =============================================================================
# Reserved Cloud Instance Setup
# Usage:
#   ./reserved_instance_setup.sh
#
# Every reserved instance is executor-capable (Redis + executor process).
# For standalone EC2 instances (not ParallelCluster).
# Must be run manually via SSH after instance is running.
# Handles FSx mount, PyTorch, Ray, CIFAR-10, Vortex codebase, Redis, executor.
# =============================================================================
set -ex

# --- CONFIGURE THESE ---
FSX_DNS="fs-04a4223998940b3ef.fsx.eu-north-1.amazonaws.com"
FSX_MOUNT_NAME="5ynhnbev"
# ------------------------

exec > >(tee ~/hpo-reserved-setup.log) 2>&1
echo "=== HPO Reserved Instance Setup starting at $(date) ==="

export DEBIAN_FRONTEND=noninteractive

# Disable restart prompts
sudo mkdir -p /etc/needrestart/conf.d/
echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/conf.d/99-restart.conf > /dev/null

# --- Mount FSx Lustre ---
if mountpoint -q /fsx; then
    echo "FSx already mounted"
else
    # Install Lustre client
    wget -O - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc \
        | gpg --dearmor | sudo tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main" \
        | sudo tee /etc/apt/sources.list.d/fsxlustreclientrepo.list
    sudo apt-get update -y
    sudo apt-get install -y lustre-client-modules-$(uname -r)

    sudo mkdir -p /fsx
    sudo mount -t lustre -o noatime,flock ${FSX_DNS}@tcp:/${FSX_MOUNT_NAME} /fsx

    # Persist across reboots
    echo "${FSX_DNS}@tcp:/${FSX_MOUNT_NAME} /fsx lustre defaults,noatime,flock,_netdev 0 0" \
        | sudo tee -a /etc/fstab

    echo "FSx mounted at /fsx"
fi

# --- Verify GPU ---
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi
    echo "GPU verified"
else
    echo "WARNING: nvidia-smi not found. Check that you're using the Deep Learning AMI."
fi

# --- Install AWS CLI if needed ---
if ! command -v aws &> /dev/null; then
    if [ -f /fsx/install_aws.sh ]; then
        cp /fsx/install_aws.sh ~ && bash ~/install_aws.sh
    else
        sudo apt-get install -y awscli
    fi
fi

# --- Copy AWS credentials from FSx ---
if [ -d /fsx/.aws ]; then
    cp -r /fsx/.aws ~/
    echo "AWS credentials copied"
fi

# --- Python venv with PyTorch + Ray ---
sudo apt-get update -y
sudo apt-get install -y python3.10-venv

python3 -m venv ~/rayenv
source ~/rayenv/bin/activate
pip install --upgrade pip
pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install "ray[default]" "ray[tune]"
pip install numpy pandas filelock boto3 paramiko pyyaml redis scikit-learn

echo "PyTorch + Ray venv created"

# --- Copy Vortex codebase ---
cp -r /fsx/Vortex-mid/Vortex-moldable-sched ~/Vortex-moldable-sched
echo "Vortex codebase copied"

# --- Copy HPO scripts ---
if [ -d /fsx/hyperparameter_test ]; then
    cp -r /fsx/hyperparameter_test ~/hyperparameter_test
elif [ -d /fsx/hyperparametr_test ]; then
    cp -r /fsx/hyperparametr_test ~/hyperparametr_test
fi
echo "HPO scripts copied"

# --- Download CIFAR-10 ---
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

# --- Install + start Redis (all instances are executor-capable) ---
echo "=== Setting up Redis + executor ==="

sudo apt-get install -y lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -sc) main" \
    | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update -y
sudo apt-get install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server

sleep 2
if sudo systemctl is-active --quiet redis-server; then
    echo "Redis server running"
else
    echo "WARNING: Redis not started via systemd, trying manual start"
    sudo redis-server --daemonize yes
fi

# Start executor
source ~/rayenv/bin/activate
cd ~/Vortex-moldable-sched/src/main
EXECUTOR_IP=$(hostname -I | awk '{print $1}')
nohup python3 executor_HPO.py --ip ${EXECUTOR_IP} > ~/executor.out 2>&1 &
echo "Executor started on ${EXECUTOR_IP}"

# --- Create ready marker ---
echo "HPO_READY:$(date)" > ~/hpo_ready.marker

echo "=== HPO Reserved Instance Setup completed at $(date) ==="
echo ""
echo "Summary:"
echo "  Venv:     ~/rayenv"
echo "  Vortex:   ~/Vortex-moldable-sched"
echo "  CIFAR-10: ~/cifar10"
echo "  FSx:      /fsx"
echo "  Executor: running (log: ~/executor.out)"
echo "  Redis:    running on default port"
