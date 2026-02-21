#!/bin/bash
# HPO Executor Instance Setup Script
# This script sets up a dedicated executor instance for HPO workflows

EXECUTOR_IP=$1

export DEBIAN_FRONTEND=noninteractive

echo "Starting HPO Executor setup on ${EXECUTOR_IP}..."

# Disable restart prompts
sudo mkdir -p /etc/needrestart/conf.d/
echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/conf.d/99-restart.conf > /dev/null

# Setup FSx Lustre client
wget -o - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc | gpg --dearmor | sudo tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null

yes | sudo bash -c 'echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main" > /etc/apt/sources.list.d/fsxlustreclientrepo.list && yes | apt-get update >> ~/setup.out'

yes | sudo apt install -y lustre-client-modules-$(uname -r) >> ~/setup.out

sudo mkdir -p /fsx

# Mount FSx file system
sudo mount -t lustre -o relatime,flock fs-04a4223998940b3ef.fsx.eu-north-1.amazonaws.com@tcp:/5ynhnbev /fsx

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
sudo cp -r /fsx/Vortex/ ~

cd Vortex/src/main

# Install and start Redis server (required for executor queue)
echo "Installing Redis server..."
sudo apt-get update >> ~/setup.out 2>&1
sudo apt-get install -y redis-server >> ~/setup.out 2>&1

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
    # Try starting manually if systemd fails
    echo "Attempting to start Redis manually..."
    sudo redis-server --daemonize yes >> ~/setup.out 2>&1
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

# Create marker file to indicate setup completion
echo "HPO_EXECUTOR_READY:$(date)" > ~/hpo_executor_ready.marker

sleep 5

echo "HPO Executor setup script completed"