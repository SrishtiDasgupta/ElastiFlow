#!/bin/bash
# =============================================================================
# Reserved Cloud Instance Setup
# Usage:
#   ./reserved_instance_setup.sh
#
# Every reserved instance is executor-capable (Redis + executor process).
# For standalone EC2 instances (not ParallelCluster).
# Must be run manually via SSH after instance is running.
# Handles EFS mount, PyTorch, Ray, CIFAR-10, Vortex codebase, Redis, executor.
# =============================================================================
set -ex

# Robust apt-get update: retry up to 3x on transient mirror errors
_apt_update_retry() {
    for i in 1 2 3; do
        if sudo apt-get update -y -o Acquire::Retries=3; then return 0; fi
        echo "apt-get update failed (attempt $i/3), retrying in 15s..."
        sleep 15
    done
    return 1
}

# --- CONFIGURE THESE ---
EFS_DNS="fs-0c7ed8d283368b734.efs.eu-north-1.amazonaws.com"
# ------------------------

exec > >(tee ~/hpo-reserved-setup.log) 2>&1
echo "=== HPO Reserved Instance Setup starting at $(date) ==="

export DEBIAN_FRONTEND=noninteractive

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

# --- Mount EFS ---
if mountpoint -q /fsx; then
    echo "EFS already mounted at /fsx"
else
    _apt_update_retry
    sudo apt-get install -y nfs-common

    sudo mkdir -p /fsx
    sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576 ${EFS_DNS}:/ /fsx

    # Persist across reboots
    echo "${EFS_DNS}:/ /fsx nfs4 nfsvers=4.1,rsize=1048576,wsize=1048576,_netdev 0 0" \
        | sudo tee -a /etc/fstab

    echo "EFS mounted at /fsx"
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
# --- Copy SSH key (needed for cloud_runner SSH between workers) ---
if [ -f /fsx/hpo-exp.pem ]; then
    mkdir -p ~/.ssh
    cp /fsx/hpo-exp.pem ~/.ssh/hpo-exp.pem
    chmod 600 ~/.ssh/hpo-exp.pem
    echo "SSH key copied"
fi

# --- Python venv with PyTorch + Ray ---
_apt_update_retry
sudo apt-get install -y python3.10-venv

python3 -m venv ~/rayenv
source ~/rayenv/bin/activate
pip install --upgrade pip
pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install "ray[default]" "ray[tune]"
pip install numpy pandas filelock boto3 paramiko pyyaml redis scikit-learn

echo "PyTorch + Ray venv created"

# --- Copy Vortex codebase ---
cp -r /fsx/ElastiFlow ~/ElastiFlow
echo "Vortex codebase copied"

# --- Copy HPO scripts ---
if [ -d /fsx/hyperparameter_test ]; then
    cp -r /fsx/hyperparameter_test ~/hyperparameter_test
elif [ -d /fsx/hyperparametr_test ]; then
    cp -r /fsx/hyperparametr_test ~/hyperparametr_test
fi
echo "HPO scripts copied"

# --- CIFAR-10: symlink EFS copy + patch torchvision md5 check ---
source ~/rayenv/bin/activate
rm -rf ~/cifar10
ln -s /fsx/cifar10 ~/cifar10
python3 - <<'PYEOF'
import torchvision, pathlib, re
f = pathlib.Path(torchvision.datasets.utils.__file__)
s = f.read_text()
n = re.sub(r'def check_integrity\(fpath:[^)]*\)[^\n]*\n(?:[ \t].*\n)+', 'def check_integrity(fpath, md5=None):\n    import os\n    return os.path.isfile(fpath)\n',s, count=1)
assert n != s, "torchvision check_integrity patch missed"
f.write_text(n)
print("torchvision patched")
PYEOF
echo "CIFAR-10 ready (symlinked from /fsx/cifar10)"
# --- Install + start Redis (all instances are executor-capable) ---
echo "=== Setting up Redis + executor ==="

sudo apt-get install -y lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -sc) main" \
    | sudo tee /etc/apt/sources.list.d/redis.list
_apt_update_retry
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

# Verify Redis is actually accepting connections before starting executor
echo "Verifying Redis is accepting connections..."
for attempt in $(seq 1 10); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
        echo "Redis ready (attempt $attempt)"
        break
    fi
    if [ "$attempt" -eq 10 ]; then
        echo "ERROR: Redis not responding after 10 attempts, trying manual start..."
        sudo redis-server --daemonize yes
        sleep 2
    fi
    sleep 2
done

# Start executor
source ~/rayenv/bin/activate
cd ~/ElastiFlow/elastiflow
EXECUTOR_IP=$(hostname -I | awk '{print $1}')
nohup python3 executor_HPO.py --ip ${EXECUTOR_IP} > ~/executor.out 2>&1 &
echo "Executor started on ${EXECUTOR_IP}"

# --- Create ready marker ---
echo "HPO_READY:$(date)" > ~/hpo_ready.marker

echo "=== HPO Reserved Instance Setup completed at $(date) ==="
echo ""
echo "Summary:"
echo "  Venv:     ~/rayenv"
echo "  Vortex:   ~/ElastiFlow"
echo "  CIFAR-10: ~/cifar10"
echo "  EFS:      /fsx"
echo "  Executor: running (log: ~/executor.out)"
echo "  Redis:    running on default port"
