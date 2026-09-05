#!/bin/bash
# wget -O - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc | gpg --dearmor | sudo tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null

# sudo bash -c 'echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main" > /etc/apt/sources.list.d/fsxlustreclientrepo.list && apt-get update'

sudo apt install -y lustre-client-modules-$(uname -r)

sudo mkdir -p /fsx

sudo mount -t lustre -o relatime,flock fs-0577278416bdf1172.fsx.eu-north-1.amazonaws.com@tcp:/zyqr7bev /fsx

# if ! command -v aws &> /dev/null; then
#     cp /fsx/install_aws.sh ~

#     ./install_aws.sh

    
# else 
#     echo "AWS CLI is already there"
# fi

cp -r /fsx/.aws ~


# rm -f ~/.ssh/id_rsa ~/.ssh/id_rsa.pub #removes existing keys
ssh-keygen -t rsa -b 4096 -f /home/ubuntu/.ssh/id_rsa -N '' || true
cat .ssh/id_rsa.pub >> /fsx/global_authorized_hosts


cd /fsx/

sleep 384.136
# ./setup-HPC.sh

cd ~

sudo cp -r /fsx/ElastiFlow/ ~

cd ElastiFlow/elastiflow

nohup python3 executor.py > ~/executor.out 2>&1 &