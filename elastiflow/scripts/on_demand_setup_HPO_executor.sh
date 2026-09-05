#!/bin/bash
# HPO Executor Instance Setup Script
# This script sets up a dedicated executor instance for HPO workflows

EXECUTOR_IP=$1

set -e  # Exit immediately on any command failure

export DEBIAN_FRONTEND=noninteractive

echo "Starting HPO Executor setup on ${EXECUTOR_IP}..."

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

# Mount EFS (replaces FSx Lustre — no Lustre client needed)
sudo apt-get update -y >> ~/setup.out 2>&1
sudo apt-get install -y nfs-common >> ~/setup.out 2>&1

sudo mkdir -p /fsx

sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576 \
    fs-0c7ed8d283368b734.efs.eu-north-1.amazonaws.com:/ /fsx

# Verify EFS mount succeeded
if ! mountpoint -q /fsx; then
    echo "FATAL: EFS mount failed"
    exit 1
fi

# Install AWS CLI if not present
if ! command -v aws &> /dev/null; then
    cp /fsx/install_aws.sh ~
    ./install_aws.sh
else
    echo "AWS CLI is already installed"
fi

# Copy AWS credentials
cp -r /fsx/.aws ~

cd /fsx

# Run HPO-specific instance setup
./instance_setup_hpo.sh >> ~/setup.out

echo "HPO instance setup completed"

cd ~

# Copy Vortex codebase
sudo cp -r /fsx/ElastiFlow/ ~

# Verify codebase exists
if [ ! -f ~/ElastiFlow/elastiflow/executor_HPO.py ]; then
    echo "FATAL: Vortex codebase copy failed"
    exit 1
fi

cd ElastiFlow/elastiflow

# Install and start Redis server (required for executor queue)
echo "Installing Redis server..."
sudo apt-get update >> ~/setup.out 2>&1
sudo apt-get install -y redis-server >> ~/setup.out 2>&1

# Verify Redis is actually installed
if ! command -v redis-server &>/dev/null; then
    echo "FATAL: redis-server not installed"
    exit 1
fi

# Start Redis service (try both possible service names)
echo "Starting Redis server..."
if sudo systemctl list-unit-files | grep -q "redis-server.service"; then
    REDIS_SERVICE="redis-server"
elif sudo systemctl list-unit-files | grep -q "redis.service"; then
    REDIS_SERVICE="redis"
else
    echo "WARNING: Could not find Redis service name, trying 'redis'"
    REDIS_SERVICE="redis"
fi

echo "Using Redis service name: ${REDIS_SERVICE}"
sudo systemctl enable ${REDIS_SERVICE} >> ~/setup.out 2>&1
sudo systemctl start ${REDIS_SERVICE} >> ~/setup.out 2>&1

# Verify Redis is running
sleep 2
if sudo systemctl is-active --quiet ${REDIS_SERVICE}; then
    echo "Redis server started successfully"
else
    echo "WARNING: Redis server may not have started properly"
    echo "Attempting to start Redis manually..."
    sudo redis-server --daemonize yes >> ~/setup.out 2>&1
fi

# Verify Redis is actually accepting connections
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

# Install Python dependencies for executor
echo "Installing Python dependencies for HPO Executor..."
pip3 install --upgrade pip >> ~/setup.out 2>&1

# Install required packages (executor doesn't need PyTorch/Ray, just control plane libraries)
pip3 install boto3 paramiko redis PyYAML pandas >> ~/setup.out 2>&1

echo "Python dependencies installed"

echo "Starting HPO Executor on ${EXECUTOR_IP}..."

# Start the HPO-specific executor (simplified - no role parameter needed)
nohup python3 executor_HPO.py --ip ${EXECUTOR_IP} > ~/executor.out 2>&1 &

echo "HPO Executor started successfully"

# Only write marker if executor process is running
sleep 2
if pgrep -f "executor_HPO.py" > /dev/null; then
    echo "HPO_EXECUTOR_READY:$(date)" > ~/hpo_executor_ready.marker
else
    echo "FATAL: executor_HPO.py process not running"
    exit 1
fi

sleep 5

echo "HPO Executor setup script completed"