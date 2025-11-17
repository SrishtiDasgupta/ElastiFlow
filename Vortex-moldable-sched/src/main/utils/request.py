from enum import Enum
import requests
import yaml

from scripts.create_instance import createInstance

class ExecutorRequest(Enum):
    REQUEST_RESOURCE = 1
    FREE_RESOURCE = 2

def getConfig(key, config = '/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/resources.yaml') -> str:
    with open(config, 'r') as file:
        config = yaml.safe_load(file)
    return config[key]

def sendRequest(ip: str, port, data):
    url = f"http://{ip}:{port}"
    proxies = {
    "http": None,
    "https": None
    }
    response = requests.post(url, json=data, proxies=proxies, timeout=15)
    if response.status_code == 200:
        print("Data sent successfully!")
    else:
        print(f"Error sending data: {response.status_code} - {response.text}")

# NOTE: Right now, return a random executor node for simulation
# script to create one executor instance and retrieve its ip. Creation of other instances must be offlloaded to the executor
def getExecutor(ips, sim) -> str:

    # e = ['10.3.14.60', '10.3.14.41']
    # return e[0], None

    for cluster in ['on-prem', 'reserved']:
        for instance in ips[cluster]:
            return ips[cluster][instance][1][0], None # Return first ip in the list
        
    # Only on-demand instances are allocated - create 1 instance and return its ip
    instance_type = None
    for instance in ips['on-demand']:
        instance_type = instance
        break
    
    ip = createInstance(instance_type, 1, sim)[0]
    return ip, instance_type
