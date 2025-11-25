yes | sudo apt-get update

yes | sudo apt install python3.10-venv

python3 -m venv ~/rayenv
source ~/rayenv/bin/activate

pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install "ray[default]"
pip install "ray[tune]"
pip install numpy pandas filelock boto3 paramiko pyyaml redis

# Install deps
yes | sudo apt-get install -y lsb-release curl gpg

# Add Redis GPG key
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg

# Add Redis apt repo (FIXED)
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -sc) main" \
  | sudo tee /etc/apt/sources.list.d/redis.list

# Update package lists
yes | sudo apt-get update

# Install Redis server
yes | sudo apt-get install -y redis-server

# Enable and start Redis (correct unit name)
sudo systemctl enable redis-server
sudo systemctl start redis-server
