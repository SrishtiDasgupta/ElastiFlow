#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

sudo bash -c 'echo "\$nrconf{restart} = '\''a'\''; > /etc/needrestart/conf.d/99-restart.conf'

wget -o - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc | gpg --dearmor | sudo tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null

yes | sudo bash -c 'echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main" > /etc/apt/sources.list.d/fsxlustreclientrepo.list && yes | apt-get update >> ~/setup.out'

yes | sudo apt install -y lustre-client-modules-$(uname -r) >> ~/setup.out

sudo mkdir -p /fsx

sudo mount -t lustre -o relatime,flock fs-04a4223998940b3ef.fsx.eu-north-1.amazonaws.com@tcp:/5ynhnbev /fsx

if ! command -v aws &> /dev/null; then
    cp /fsx/install_aws.sh ~

    ./install_aws.sh   
else 
    echo "AWS CLI is already there"
fi

cp -r /fsx/.aws ~

cd /fsx

./instance_setup_hpo.sh >> ~/setup.out
# ./setup-HPC.sh >> ~/setup.out
echo "Setup complete"

cd ~

sudo cp -r /fsx/Vortex/ ~

cd Vortex/src/main

nohup python3 executor.py > ~/executor.out 2>&1 &

sleep 5