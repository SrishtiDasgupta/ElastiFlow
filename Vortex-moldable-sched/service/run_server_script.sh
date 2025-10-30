#!/bin/sh
cd ~
echo $1 > hosts
export PORT=4242
export RANKS=1
export MACHINE_FILE=~/hosts
export CORES=$3

grep -v $(hostname -I) /fsx/global_authorized_hosts >> .ssh/authorized_keys

cd /fsx
# ls:
cd Seis-Bridge/tpv13

kill -9 $(lsof -t -i :4242)

# echo "Starting server"
nohup /home/ubuntu/.local/bin/waitress-serve --host=0.0.0.0 --port=4242 --threads=4 tpv13server_wsgi_safe:app > ~/server.out 2>&1 &
