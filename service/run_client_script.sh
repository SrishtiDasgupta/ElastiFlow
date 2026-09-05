#!/bin/sh
cd /fsx
# ls
cd Seis-Bridge

# echo $1
# echo $2
# echo $3
echo $4


python3 client/tinyda_client.py $4 --chains $1 --iterations $2 --cohesion $3 > ~/client.out
