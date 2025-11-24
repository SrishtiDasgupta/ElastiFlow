#!/bin/sh
cd ~
echo $5 > hosts

cd /fsx
# ls
cd Seis-Bridge/tpv13

# echo $1
# echo $2
# echo $3
# echo $4

export PORT=4242
export RANKS=1
export MACHINE_FILE=~/hosts
export CORES=$4

echo "Starting server"
nohup python3 tpv13server.py $6 > /home/ubuntu/server.out&
echo $! > ~/save_pid.txt

cd ..

echo "Starting client..."
time python3 client/tinyda.py 4242 $1 $2 $3 

sudo kill -9 `cat ~/save_pid.txt`
sudo rm ~/save_pid.txt