#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

sudo bash -c 'echo "\$nrconf{restart} = '\''a'\''; > /etc/needrestart/conf.d/99-restart.conf'

# Mount EFS (replaces FSx Lustre)
sudo apt-get install -y nfs-common >> ~/setup.out 2>&1

sudo mkdir -p /fsx

sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576 \
    fs-0c7ed8d283368b734.efs.eu-north-1.amazonaws.com:/ /fsx

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

sudo cp -r /fsx/Vortex-mid/Vortex-moldable-sched/ ~

cd ElastiFlow/elastiflow

nohup python3 executor.py > ~/executor.out 2>&1 &

sleep 5