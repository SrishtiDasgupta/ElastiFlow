#!/bin/bash
# =============================================================================
# Slurm Head Node Setup (ParallelCluster OnNodeConfigured)
# Role: Executor — orchestrates HPO workflows, does NOT train
# ParallelCluster provides: EFS mount at /fsx, AWS CLI, Slurm
# =============================================================================
set -ex

exec > >(tee /var/log/hpo-head-setup.log) 2>&1
echo "=== HPO Slurm Head Setup starting at $(date) ==="

export DEBIAN_FRONTEND=noninteractive

# Disable restart prompts
mkdir -p /etc/needrestart/conf.d/
echo "\$nrconf{restart} = 'a';" | tee /etc/needrestart/conf.d/99-restart.conf > /dev/null

# Wait for unattended-upgrades / dpkg lock to release before any apt calls
echo "Waiting for dpkg lock..."
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
    echo "  dpkg lock held by unattended-upgrades, waiting 5s..."
    sleep 5
done
echo "dpkg lock free"

# --- Verify EFS is mounted (ParallelCluster should have done this) ---
if ! mountpoint -q /fsx; then
    echo "ERROR: /fsx is not mounted. ParallelCluster should mount this."
    exit 1
fi
echo "EFS verified at /fsx"

# --- Python venv (control-plane only, no PyTorch/Ray) ---
apt-get update -y
apt-get install -y python3.10-venv

sudo -u ubuntu bash <<'USEREOF'
set -ex
python3 -m venv ~/rayenv
source ~/rayenv/bin/activate
pip install --upgrade pip
pip install boto3 paramiko redis PyYAML pandas scikit-learn
USEREOF

echo "Python control-plane venv created"

# --- Redis server (required for executor queue) ---
apt-get install -y lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -sc) main" \
  | tee /etc/apt/sources.list.d/redis.list
apt-get update -y
apt-get install -y redis-server
systemctl enable redis-server
systemctl start redis-server

# Verify Redis
sleep 2
if systemctl is-active --quiet redis-server; then
    echo "Redis server running"
else
    echo "WARNING: Redis not started, attempting manual start"
    redis-server --daemonize yes
fi

# Verify Redis is actually accepting connections
echo "Verifying Redis is accepting connections..."
for attempt in $(seq 1 10); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
        echo "Redis ready (attempt $attempt)"
        break
    fi
    if [ "$attempt" -eq 10 ]; then
        echo "ERROR: Redis not responding after 10 attempts, trying manual start..."
        redis-server --daemonize yes
        sleep 2
    fi
    sleep 2
done

# --- Copy Vortex codebase from FSx ---
sudo -u ubuntu bash <<'USEREOF'
set -ex
cp -r /fsx/Vortex ~/Vortex
echo "Vortex codebase copied to ~/Vortex"
USEREOF

# --- Copy AWS credentials if available on FSx ---
if [ -d /fsx/.aws ]; then
    sudo -u ubuntu cp -r /fsx/.aws /home/ubuntu/
    echo "AWS credentials copied"
fi

# --- Start executor ---
sudo -u ubuntu bash <<'USEREOF'
set -ex
source ~/rayenv/bin/activate
cd ~/Vortex/src/main
EXECUTOR_IP=$(hostname -I | awk '{print $1}')
nohup python3 executor_HPO.py --ip ${EXECUTOR_IP} > ~/executor.out 2>&1 &
echo "HPO_EXECUTOR_READY:$(date)" > ~/hpo_executor_ready.marker
echo "Executor started on ${EXECUTOR_IP}"
USEREOF

echo "=== HPO Slurm Head Setup completed at $(date) ==="
