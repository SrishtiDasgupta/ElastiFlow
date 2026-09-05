kill -9 $(lsof -t -i :4242)
sudo cp -r /fsx/.aws ~/
sudo cp -r /fsx/ElastiFlow ~/
cd ~/ElastiFlow/elastiflow
python3 executor.py
