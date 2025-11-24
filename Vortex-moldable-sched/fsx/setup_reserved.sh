kill -9 $(lsof -t -i :4242)
sudo cp -r /fsx/.aws ~/
sudo cp -r /fsx/Vortex ~/
cd ~/Vortex/src/main
python3 executor.py
