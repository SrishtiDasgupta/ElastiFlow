#!/bin/bash
set -ex

exec > >(tee /var/log/user-data.log) 2>&1
echo "Starting user_data script at $(date)"

apt-get update -y

# Install Lustre client (FSx repo method)
wget -O - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc | gpg --dearmor | tee /usr/share/keyrings/fsx-ubuntu-public-key.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/fsx-ubuntu-public-key.gpg] https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu $(lsb_release -cs) main" > /etc/apt/sources.list.d/fsxlustreclientrepo.list
apt-get update -y
apt-get install -y lustre-client-modules-$(uname -r)

# Mount FSx Lustre
mkdir -p /fsx
mount -t lustre -o noatime,flock ${fsx_dns_name}@tcp:/${fsx_mount_name} /fsx
echo "${fsx_dns_name}@tcp:/${fsx_mount_name} /fsx lustre defaults,noatime,flock,_netdev 0 0" >> /etc/fstab

df -h /fsx
echo "FSx Lustre mounted successfully"

apt-get install -y git

cd /home/ubuntu
git clone --branch ${git_branch} https://${git_token}@${replace(git_repo, "https://", "")} repo
chown -R ubuntu:ubuntu /home/ubuntu/repo
echo "Git repository cloned successfully"

if ! command -v aws &> /dev/null; then
    apt-get install -y awscli
fi

aws s3 cp ${s3_script_path} /tmp/startup_script.py
chmod +x /tmp/startup_script.py
chown ubuntu:ubuntu /tmp/startup_script.py

sudo -u ubuntu bash /tmp/startup_script.py

echo "User data script completed at $(date)"