#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

sudo bash -c 'echo "\$nrconf{restart} = '\''a'\'';" > /etc/needrestart/conf.d/99-restart.conf'

wget -O - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc | gpg --dearmor | sudo tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null

yes | sudo bash -c 'echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main" > /etc/apt/sources.list.d/fsxlustreclientrepo.list && yes | apt-get update >> ~/setup.out'

yes | sudo apt install -y lustre-client-modules-$(uname -r) >> ~/setup.out

sudo mkdir -p /fsx

sudo mount -t lustre -o relatime,flock fs-0577278416bdf1172.fsx.eu-north-1.amazonaws.com@tcp:/zyqr7bev /fsx

if ! command -v aws &> /dev/null; then
    cp /fsx/install_aws.sh ~

    ./install_aws.sh > ~/setup.out

else 
    echo "AWS CLI is already there"
fi

cp -r /fsx/.aws ~


#rm -f ~/.ssh/id_rsa ~/.ssh/id_rsa.pub #removes existing keys
ssh-keygen -t rsa -b 4096 -f /home/ubuntu/.ssh/id_rsa -N '' || true
cat .ssh/id_rsa.pub >> /fsx/global_authorized_hosts


cd /fsx/


./setup-HPC.sh >> ~/setup.out
echo "setup Seissol done"

cd ~

sudo cp -r /fsx/Vortex/ ~

cd Vortex/src/main

nohup python3 executor.py > ~/executor.out 2>&1 &

sleep 5

