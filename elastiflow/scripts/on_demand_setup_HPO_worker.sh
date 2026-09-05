#!/bin/bash
# HPO Cloud Instance Setup Script
# Sets up any cloud instance as executor-capable (Redis + executor process)

WORKER_IP=$1
# Persist logs to local /tmp first (EFS not mounted yet); copy to EFS on exit.
# Trap fires on success OR failure, so we get logs even when setup crashes.
mkdir -p /tmp
: > /tmp/setup.out
: > /tmp/executor.out
ln -sf /tmp/setup.out ~/setup.out
ln -sf /tmp/executor.out ~/executor.out
_persist_logs_to_efs() {
    local ts=$(date +%Y%m%d_%H%M%S)
    local efs_dir="/fsx/ondemand_logs/${WORKER_IP}_${ts}"
    if mountpoint -q /fsx 2>/dev/null; then
        sudo mkdir -p "$efs_dir" 2>/dev/null
        sudo cp /tmp/setup.out "$efs_dir/setup.out" 2>/dev/null || true
        sudo cp /tmp/executor.out "$efs_dir/executor.out" 2>/dev/null || true
        sudo chown -R ubuntu:ubuntu "$efs_dir" 2>/dev/null || true
        echo "Logs persisted to: $efs_dir"
    else
        echo "WARN: /fsx not mounted at exit, logs only on local /tmp (lost on terminate)"
    fi
}
trap _persist_logs_to_efs EXIT


set -e  # Exit immediately on any command failure

# Robust apt-get update: retry up to 3x on transient mirror errors
_apt_update_retry() {
    for i in 1 2 3; do
        if sudo apt-get update -y -o Acquire::Retries=3; then return 0; fi
        echo "apt-get update failed (attempt $i/3), retrying in 15s..."
        sleep 15
    done
    return 1
}

export DEBIAN_FRONTEND=noninteractive

echo "Starting HPO Worker setup on ${WORKER_IP}..."

# Disable restart prompts
sudo mkdir -p /etc/needrestart/conf.d/
echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/conf.d/99-restart.conf > /dev/null

# Wait for cloud-init to finish (it rewrites /etc/apt/sources.list during boot).
# Without this, apt-get races with cloud-init and sees a transient corrupt sources.list
# (Type 'ty' is not known on line 51).
echo "Waiting for cloud-init to finish..."
sudo cloud-init status --wait || true
echo "cloud-init done"

# Wait for unattended-upgrades / dpkg lock to release before any apt calls
echo "Waiting for dpkg lock..."
while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
    echo "  dpkg lock held by unattended-upgrades, waiting 5s..."
    sleep 5
done
echo "dpkg lock free"

# Fix corrupted /etc/apt/sources.list if present (AMI has bad 'ty' entry on line 51)
sudo sed -i '/^ty /d' /etc/apt/sources.list 2>/dev/null || true

# Mount EFS (replaces FSx Lustre — no Lustre client needed)
_apt_update_retry >> ~/setup.out 2>&1
sudo apt-get install -y nfs-common >> ~/setup.out 2>&1

sudo mkdir -p /fsx

sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576 \
    fs-0c7ed8d283368b734.efs.eu-north-1.amazonaws.com:/ /fsx

# Verify EFS mount succeeded
if ! mountpoint -q /fsx; then
    echo "FATAL: EFS mount failed"
    exit 1
fi

# --- Install + verify NVIDIA driver ---
# Plain Ubuntu AMI ships without NVIDIA drivers. Install nvidia-driver-535-server
# (supports Tesla T4 on g4dn and A10G on g5). After install, try modprobe; if it
# fails (e.g. nouveau still loaded), the script exits and asks for a reboot.
if ! command -v nvidia-smi &> /dev/null || ! nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA driver missing — installing nvidia-driver-535-server..."
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get install -y --no-install-recommends linux-headers-$(uname -r) build-essential dkms
    sudo apt-get install -y nvidia-driver-535-server
    lsmod | grep -q nouveau && sudo modprobe -r nouveau || true
    sudo modprobe nvidia || true
    if ! nvidia-smi >/dev/null 2>&1; then
        echo "FATAL: NVIDIA driver installed but kernel module did not load. Reboot and re-run setup."
        exit 1
    fi
fi
nvidia-smi
echo "GPU verified"

# Install AWS CLI if not present
if ! command -v aws &> /dev/null; then
    cp /fsx/install_aws.sh ~
    ./install_aws.sh
else
    echo "AWS CLI is already installed"
fi

# Copy SSH key (needed for cloud_runner to SSH between worker instances)
mkdir -p ~/.ssh
cp /fsx/hpo-exp.pem ~/.ssh/hpo-exp.pem
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
_apt_update_retry >> ~/setup.out 2>&1
sudo apt-get install -y python3.10-venv >> ~/setup.out 2>&1

python3 -m venv ~/rayenv
source ~/rayenv/bin/activate
pip install --upgrade pip >> ~/setup.out 2>&1
pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128 >> ~/setup.out 2>&1
pip install "ray[default]" "ray[tune]" >> ~/setup.out 2>&1
pip install numpy pandas filelock boto3 paramiko pyyaml redis scikit-learn requests >> ~/setup.out 2>&1
# Patch torchvision to skip md5 check on EFS-hosted CIFAR (HF-rebuilt batches)
~/rayenv/bin/python3 - <<'PYPATCH'
import re, os
fp = os.path.expanduser("~/rayenv/lib/python3.10/site-packages/torchvision/datasets/cifar.py")
with open(fp) as f: s = f.read()
n = re.sub(r'\["(data_batch_\d|test_batch)",\s*"[a-f0-9]{32}"\]', r'["\1",None]', s)
n = re.sub(r'"md5":\s*"[a-f0-9]{32}"', '"md5": None', n)
if n != s:
    with open(fp, "w") as f: f.write(n)
    print("torchvision: patched")
else:
   print("torchvision: already patched")
PYPATCH


echo "PyTorch + Ray venv created at ~/rayenv"

cd ~

# Symlink Vortex codebase to EFS (single source of truth, no stale copy)
if [ ! -L ~/ElastiFlow ]; then
    rm -rf ~/ElastiFlow
    ln -s /fsx/ElastiFlow ~/ElastiFlow
fi

# Verify codebase exists
if [ ! -f ~/ElastiFlow/elastiflow/executor_HPO.py ]; then
    echo "FATAL: Vortex codebase copy failed"
    exit 1
fi

cd ElastiFlow/elastiflow

# Install and start Redis server (required for executor queue)
echo "Installing Redis server..."
sudo apt-get install -y lsb-release curl gpg >> ~/setup.out 2>&1
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -sc) main" \
    | sudo tee /etc/apt/sources.list.d/redis.list
_apt_update_retry >> ~/setup.out 2>&1
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

# CIFAR-10 from EFS (no download — Toronto mirror is dead, batches pre-built)
if [ ! -L ~/cifar10 ]; then
     rm -rf ~/cifar10
     ln -s /fsx/cifar10 ~/cifar10
fi
echo "CIFAR-10 symlinked from /fsx/cifar10"

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
cd ~/ElastiFlow/elastiflow
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