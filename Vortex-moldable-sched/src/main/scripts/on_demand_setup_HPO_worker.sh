#!/bin/bash
# HPO Cloud Instance Setup Script
# Sets up any cloud instance as executor-capable (Redis + executor process)

WORKER_IP=$1

set -e  # Exit immediately on any command failure

export DEBIAN_FRONTEND=noninteractive

echo "Starting HPO Worker setup on ${WORKER_IP}..."

# Disable restart prompts
sudo mkdir -p /etc/needrestart/conf.d/
echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/conf.d/99-restart.conf > /dev/null

# Wait for unattended-upgrades / dpkg lock to release before any apt calls
echo "Waiting for dpkg lock..."
while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
    echo "  dpkg lock held by unattended-upgrades, waiting 5s..."
    sleep 5
done
echo "dpkg lock free"

# Fix corrupted /etc/apt/sources.list if present (AMI has bad 'ty' entry on line 51)
sudo sed -i '/^ty /d' /etc/apt/sources.list 2>/dev/null || true

# Setup FSx Lustre client
wget -O - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc | gpg --dearmor | sudo tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null

yes | sudo bash -c 'echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main" > /etc/apt/sources.list.d/fsxlustreclientrepo.list && yes | apt-get update >> ~/setup.out'

yes | sudo apt install -y lustre-client-modules-$(uname -r) >> ~/setup.out

sudo mkdir -p /fsx

# Mount FSx file system
sudo mount -t lustre -o noatime,flock 172.31.14.168@tcp:/5ynhnbev /fsx

# Verify FSx mount succeeded
if ! mountpoint -q /fsx; then
    echo "FATAL: FSx mount failed"
    exit 1
fi

# Install AWS CLI if not present
if ! command -v aws &> /dev/null; then
    cp /fsx/install_aws.sh ~
    ./install_aws.sh
else
    echo "AWS CLI is already installed"
fi

# Copy SSH key (needed for cloud_runner to SSH between worker instances)
mkdir -p ~/.ssh
cp /fsx/.ssh/hpo-exp.pem ~/.ssh/hpo-exp.pem
chmod 600 ~/.ssh/hpo-exp.pem

# Copy AWS credentials (skip if not present — IAM role provides credentials)
if [ -d "/fsx/.aws" ]; then
    cp -r /fsx/.aws ~
else
    echo "No /fsx/.aws found — using IAM role for AWS credentials"
fi

cd /fsx

# --- Python venv with PyTorch + Ray (must match reserved_instance_setup.sh) ---
echo "Creating ~/rayenv Python venv..."
sudo apt-get update -y >> ~/setup.out 2>&1
sudo apt-get install -y python3.10-venv >> ~/setup.out 2>&1

python3 -m venv ~/rayenv
source ~/rayenv/bin/activate
pip install --upgrade pip >> ~/setup.out 2>&1
pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128 >> ~/setup.out 2>&1
pip install "ray[default]" "ray[tune]" >> ~/setup.out 2>&1
pip install numpy pandas filelock boto3 paramiko pyyaml redis scikit-learn >> ~/setup.out 2>&1

echo "PyTorch + Ray venv created at ~/rayenv"

cd ~

# Copy Vortex codebase
sudo cp -r /fsx/Vortex-mid/Vortex-moldable-sched/ ~

# Verify codebase exists
if [ ! -f ~/Vortex-moldable-sched/src/main/executor_HPO.py ]; then
    echo "FATAL: Vortex codebase copy failed"
    exit 1
fi

cd Vortex-moldable-sched/src/main

# Install and start Redis server (required for executor queue)
echo "Installing Redis server..."
sudo apt-get install -y lsb-release curl gpg >> ~/setup.out 2>&1
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -sc) main" \
    | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update -y >> ~/setup.out 2>&1
sudo apt-get install -y redis-server >> ~/setup.out 2>&1

# Verify Redis is actually installed
if ! command -v redis-server &>/dev/null; then
    echo "FATAL: redis-server not installed"
    exit 1
fi

# Start Redis service
echo "Starting Redis server..."
sudo systemctl enable redis-server >> ~/setup.out 2>&1
sudo systemctl start redis-server >> ~/setup.out 2>&1

# Verify Redis is running
sleep 2
if sudo systemctl is-active --quiet redis-server; then
    echo "Redis server started successfully"
else
    echo "WARNING: Redis not started via systemd, trying manual start"
    sudo redis-server --daemonize yes >> ~/setup.out 2>&1
fi

# Copy HPO-specific scripts and data to home (optional — cloud_runner uses /fsx/ directly)
if [ -d "/fsx/hyperparameter_test/" ]; then
    sudo cp -r /fsx/hyperparameter_test/ ~/
fi

# Setup CIFAR-10 dataset if not exists
source ~/rayenv/bin/activate
if [ ! -d "$HOME/cifar10" ]; then
    echo "Setting up CIFAR-10 dataset..."
    python3 -c "
import torchvision
import torchvision.transforms as transforms
import os

# Download CIFAR-10 to standard location
transform = transforms.Compose([transforms.ToTensor()])
dataset = torchvision.datasets.CIFAR10(root=os.path.expanduser('~/cifar10'), train=True, download=True, transform=transform)
print('CIFAR-10 dataset downloaded successfully')
"
fi

echo "Verifying Redis is accepting connections..."
REDIS_OK=false
for attempt in $(seq 1 10); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
        echo "Redis ready (attempt $attempt)"
        REDIS_OK=true
        break
    fi
    if [ "$attempt" -eq 10 ]; then
        echo "ERROR: Redis not responding after 10 attempts, trying manual start..."
        sudo redis-server --daemonize yes >> ~/setup.out 2>&1
        sleep 2
        if redis-cli ping 2>/dev/null | grep -q PONG; then
            echo "Redis ready after manual start"
            REDIS_OK=true
        fi
    fi
    sleep 2
done

if [ "$REDIS_OK" = false ]; then
    echo "FATAL: Redis is not running — reinstalling..."
    sudo apt-get install -y redis-server >> ~/setup.out 2>&1
    sudo systemctl start redis-server >> ~/setup.out 2>&1
    sleep 3
    if redis-cli ping 2>/dev/null | grep -q PONG; then
        echo "Redis recovered after reinstall"
    else
        echo "FATAL: Redis still not running, executor will fail"
    fi
fi

echo "Starting HPO Executor on ${WORKER_IP}..."

# Start the HPO-specific executor (use rayenv python)
cd ~/Vortex-moldable-sched/src/main
nohup ~/rayenv/bin/python3 executor_HPO.py --ip ${WORKER_IP} > ~/executor.out 2>&1 &

echo "HPO Executor started successfully"

# Only write marker if executor process is running
sleep 2
if pgrep -f "executor_HPO.py" > /dev/null; then
    echo "HPO_READY:$(date)" > ~/hpo_ready.marker
else
    echo "FATAL: executor_HPO.py process not running"
    exit 1
fi

sleep 5

echo "HPO cloud instance setup on ${WORKER_IP} completed"