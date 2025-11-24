yes | sudo apt-get update

yes | sudo apt install python3.10-venv

python3 -m venv ~/rayenv
source ~/rayenv/bin/activate

pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.com/whl/cu128
pip install "ray[default]"
pip install "ray[tune]"
pip install numpy pandas filelock boto3 paramiko pyyaml redis

yes | sudo apt-get install -y lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release) main" | sudo tee /etc/apt/sources.list.d/redis.list
yes | sudo apt-get update
yes | sudo apt-get install -y redis

yes | sudo systemctl enable redis-server
yes | sudo systemctl start redis-server
