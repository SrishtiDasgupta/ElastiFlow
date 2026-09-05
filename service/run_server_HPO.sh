#!/bin/sh
cd ~
echo $1 > hosts
export PORT=4242
export RANKS=1
export MACHINE_FILE=~/hosts
export CORES=$3

grep -v $(hostname -I) /fsx/global_authorized_hosts >> .ssh/authorized_keys

cd /home/ubuntu/hyperparameter_test

ray start --head --port=6379 --redis-password="1234"