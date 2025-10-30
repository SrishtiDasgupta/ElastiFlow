# This is the initial setup for every instance to run the workflow program  

# Create hosts file
cd ~
sudo touch hosts
sudo chmod 777 hosts
cd /


# Install pip
yes | sudo apt-get update
yes | sudo apt-get install python3-pip -y

# Install dependencies
cd /fsx
pip install numpy scipy pandas umbridge tinyDA ray waitress flask paramiko boto3 redis 

# Install MPI
yes | sudo apt-get update \
&& yes | sudo apt-get install -y \
autoconf \
autotools-dev \
bison \
cmake \
flex \
g++ \
gcc \
gfortran \
git \
gnupg \
libibverbs-dev \
libnuma-dev \
libnuma1 \
libomp-dev \
libreadline-dev \
libtool \
libyaml-cpp-dev \
lsb-release \
make \
pkg-config \
python3 \
python3-numpy \
python3-pip \
software-properties-common \
vim \
wget \
wget \
&& sudo rm -rf /var/lib/apt/lists/*

sudo mkdir -p /home/tools/src
export PATH=/home/tools/bin:$PATH
cd /home/tools/src
#
ls -la 

sudo wget --progress=bar:force:noscroll https://download.open-mpi.org/release/open-mpi/v4.1/openmpi-4.1.6.tar.bz2
sudo tar -xf ./openmpi-4.1.6.tar.bz2 && cd ./openmpi-4.1.6
mkdir -p  ./build && cd ./build
../configure --with-memory-manager=none --enable-static=yes --enable-shared --enable-mpirun-prefix-by-default
 yes| sudo make -j $(nproc) && sudo make install && cd /home/tools/src

sudo ldconfig

# Installing apptainer
yes | sudo apt update
yes | sudo apt install -y wget
cd /tmp
wget https://github.com/apptainer/apptainer/releases/download/v1.3.6/apptainer_1.3.6_amd64.deb
yes | sudo apt install -y ./apptainer_1.3.6_amd64.deb

# Replace the tinyDA/chain.py file
cd /fsx
sudo cp -f chain.py /home/ubuntu/.local/lib/python3.10/site-packages/tinyDA/chain.py
#sudo cp -f chain.py /home/ubuntu/tinyda-seissol/lib/python3.12/site-packages/tinyDA/chain.py

# Setup for starting the server

export PORT=4242
export RANKS=1
export MACHINE_FILE=~/hosts

yes | sudo apt-get install -y lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
yes | sudo apt-get update
yes | sudo apt-get install -y redis

yes | sudo systemctl enable redis-server
yes | sudo systemctl start redis-server

