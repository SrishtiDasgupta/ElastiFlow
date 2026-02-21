#!/bin/bash
# HPO Cloud Instance Setup Script
# Sets up any cloud instance as executor-capable (Redis + executor process)

WORKER_IP=$1

export DEBIAN_FRONTEND=noninteractive

echo "Starting HPO Worker setup on ${WORKER_IP}..."

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

# Run HPO-specific instance setup (includes GPU drivers, PyTorch, Ray, etc.)
./instance_setup_hpo.sh >> ~/setup.out

echo "HPO instance setup completed"

cd ~

# Copy Vortex codebase
sudo cp -r /fsx/Vortex-mid/Vortex-moldable-sched/ ~

cd Vortex-moldable-sched/src/main

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

# Install Python dependencies for HPO Executor
echo "Installing Python dependencies for HPO..."
pip3 install --upgrade pip >> ~/setup.out 2>&1

# Install required packages (executor + worker libraries)
pip3 install torch torchvision ray boto3 paramiko redis PyYAML pandas scikit-learn >> ~/setup.out 2>&1

echo "Python dependencies installed"

# Copy HPO-specific scripts and data
sudo cp -r /fsx/hyperparametr_test/ ~/

# Setup CIFAR-10 dataset if not exists
if [ ! -d "~/cifar10" ]; then
    echo "Setting up CIFAR-10 dataset..."
    cd ~/hyperparametr_test
    python3 -c "
import torchvision
import torchvision.transforms as transforms
import os

# Download CIFAR-10 to standard location
transform = transforms.Compose([transforms.ToTensor()])
dataset = torchvision.datasets.CIFAR10(root=os.path.expanduser('~/cifar10'), train=True, download=True, transform=transform)
print('CIFAR-10 dataset downloaded successfully')
"
    cd ~
fi

echo "Starting HPO Executor on ${WORKER_IP}..."

# Start the HPO-specific executor
cd ~/Vortex-moldable-sched/src/main
nohup python3 executor_HPO.py --ip ${WORKER_IP} > ~/executor.out 2>&1 &

echo "HPO Executor started successfully"

# Create marker file to indicate setup completion
echo "HPO_READY:$(date)" > ~/hpo_ready.marker

sleep 5

echo "HPO cloud instance setup on ${WORKER_IP} completed"