#!/bin/bash
set -ex

# Log everything
exec > >(tee /var/log/user-data.log) 2>&1
echo "Starting user_data script at $(date)"

# Update system
apt-get update -y

# Install Lustre client
apt-get install -y lustre-client-modules-$(uname -r) lustre-client-modules-aws || {
    echo "Installing lustre-client-modules from amazon-linux-extras..."
    apt-get install -y linux-aws lustre-client-modules-aws
}

# Mount FSx Lustre
mkdir -p /fsx
mount -t lustre -o noatime,flock ${fsx_dns_name}@tcp:/${fsx_mount_name} /fsx

# Add to fstab for persistence
echo "${fsx_dns_name}@tcp:/${fsx_mount_name} /fsx lustre defaults,noatime,flock,_netdev 0 0" >> /etc/fstab

# Verify mount
df -h /fsx
echo "FSx Lustre mounted successfully"

# Install git if not present
apt-get install -y git

# Clone private repository
cd /home/ubuntu
git clone --branch ${git_branch} https://${git_token}@${replace(git_repo, "https://", "")} repo
chown -R ubuntu:ubuntu /home/ubuntu/repo
echo "Git repository cloned successfully"

# Install AWS CLI if not present
if ! command -v aws &> /dev/null; then
    apt-get install -y awscli
fi

# Download and run startup script from S3
aws s3 cp ${s3_script_path} /tmp/startup_script.py
chmod +x /tmp/startup_script.py
chown ubuntu:ubuntu /tmp/startup_script.py

# Run the script as ubuntu user
sudo -u ubuntu python3 /tmp/startup_script.py

echo "User data script completed at $(date)"
