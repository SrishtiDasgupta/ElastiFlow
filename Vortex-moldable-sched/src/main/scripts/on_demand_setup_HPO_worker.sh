#!/bin/bash
# HPO Worker Instance Setup Script
# This script sets up worker instances for HPO Ray clusters

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
sudo mount -t lustre -o relatime,flock fs-0577278416bdf1172.fsx.eu-north-1.amazonaws.com@tcp:/zyqr7bev /fsx

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

echo "HPO worker instance setup completed"

cd ~

# Copy Vortex codebase
sudo cp -r /fsx/Vortex/ ~

# Copy HPO-specific scripts and data
sudo cp -r /fsx/hyperparametr_test/ ~/

# Install Python dependencies for HPO worker (if not already installed)
echo "Installing/verifying Python dependencies for HPO Worker..."
pip3 install --upgrade pip >> ~/setup.out 2>&1

# Core dependencies (should be in instance_setup_hpo.sh, but verify)
pip3 install torch torchvision ray boto3 paramiko PyYAML pandas scikit-learn >> ~/setup.out 2>&1

echo "Python dependencies verified"

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

echo "HPO Worker setup on ${WORKER_IP} completed successfully"

# Create marker file to indicate setup completion
echo "HPO_WORKER_READY:$(date)" > ~/hpo_worker_ready.marker

# Worker instances don't start services automatically
# They will be controlled by the cloud_runner_HPO.py
echo "HPO Worker ${WORKER_IP} ready for Ray cluster participation"

sleep 2

echo "HPO Worker setup script completed"