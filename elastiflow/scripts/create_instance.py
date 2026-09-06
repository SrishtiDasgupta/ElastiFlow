from typing import List
import boto3
from botocore.exceptions import ClientError
import paramiko
import time
import random
import subprocess

# SIMULATE = False
# COLD_START_TIME = 0.00
from elastiflow.config.constants import SIMULATE, COLD_START_TIME
from elastiflow.execution.backend import backend_for
region= 'eu-north-1' #change to eu-north-1 when taking runtime of stockholm instances

# Create an EC2 client
session = boto3.Session(region_name=region)
ec2 = session.resource('ec2')
# ec2 = session.client('ec2')
user = 'ubuntu'
key_file_path = '/fsx/Nisarg-HPC.pem'

# NOTE: Cold start time will be added here
def createInstance(name: str, count: int = 1, sim = None) -> List[str]:
    backend = backend_for(sim)
    if count < 1:
        return []
    print(f'Creating {count} instances of {name}')
    if sim or SIMULATE:
        backend.sleep(COLD_START_TIME)
        ips = []
        for i in range(count):
            digits = [1] + random.choices(range(255), k=3)
            ips.append('.'.join(map(str, digits)))
        return ips
    else: 
        return launchInstance(name, count)

def deleteInstanceFromIp(instances: List[str]):
    print('Terminating instances', instances)
    if not SIMULATE:
         return terminateInstance(instances)
    if not SIMULATE:
         return terminateInstance(instances)

def launchInstance(instanceName: str, count):
    instance_details = []
    ssh_check_flag = False
    # filters = [
    #     {'Name': 'instance-type', 'Values': [instanceName]},
    #     {'Name': 'instance-state-name', 'Values': ['stopped']}
    # ]

    # paginator = ec2.get_paginator('describe_instances')
    # stopped_ids = []

    # for page in paginator.paginate(Filters=filters):
    #     for res in page.get('Reservations', []):
    #         for inst in res.get('Instances', []):
    #             stopped_ids.append(inst['InstanceId'])

    #             if len(stopped_ids) >=count:
    #                 break
    #         if len(stopped_ids) >=count:
    #                 break
    #     if len(stopped_ids) >=count:
    #                 break
    # if not stopped_ids:
    #     print(f"No instances of type {instanceName}")
    #     return

    # to_start = stopped_ids[:count]
    # try:
    #     response = ec2.start_instances(InstanceIds=to_start)
    # except ClientError as e:
    #     print(f"{e}")
    #     return [None]
    
    # waiter = ec2.get_waiter('instance_running')
    # waiter.wait(InstanceIds=to_start)
    to_start = []
    instances = ec2.create_instances(
        ImageId='ami-04542995864e26699',
        TagSpecifications=[
            {
                'ResourceType': 'instance',
                'Tags': [
                    {
                        'Key': 'Name',
                        'Value': instanceName + '-compute'  # Replace with your desired instance name
                    }
                ]
            }
        ],
        InstanceType=instanceName,
        MinCount=count,
        MaxCount=count,
        KeyName= 'Nisarg-HPC',
        SecurityGroupIds=['sg-00da4b839f250187d'], # change for stockholm
        SubnetId='subnet-03e7330f6288164e3'  # change for stockholm
    )
    instance = instances[0]
    for instance in instances:
        instance.wait_until_running()
        instance.reload()
        print("Instance ID:", instance.id)
        to_start.append(instance.id)
        print("Private IP:", instance.private_ip_address)
        private_dns = instance.private_dns_name
        
        instance_details.append(instance.private_ip_address)

        script = f"""if nc -zv {instance.private_ip_address} 22 2>&1 | grep -q succeeded;
        then
            echo True
        else
            echo False
        fi"""

        while not ssh_check_flag:
            out = subprocess.run(script, shell=True, capture_output= True, text=True)
            ssh_check_flag = out.stdout.split('\n')[0]
            time.sleep(5)

        print(f"Connecting to host{instance.private_ip_address}")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        privkey = paramiko.RSAKey.from_private_key_file(key_file_path)
        ssh.connect(private_dns, username=user, pkey=privkey)
        sftp = ssh.open_sftp()
        print(f"Setting up on-demand")
        remote_command_script = '/home/ubuntu/on_demand_setup.sh'
        local_script = '/home/ubuntu/ElastiFlow/elastiflow/scripts/on_demand_setup.sh'
        sftp.put(local_script, remote_command_script)
        sftp.chmod(remote_command_script, 0o700)
        
        sftp.close()

        # Execute a command (for example, updating the package list)
        
        # print("running command server")
        command = f'bash {remote_command_script}'  # Change this command as needed
        stdin, stdout, stderr = ssh.exec_command(command)
        print(stdout.read().decode(), stderr.read().decode)

    # instance_info = ec2.describe_instances(InstanceIds = to_start)

    # for ec2_host in instance_info['Reservations']:
    #     private_dns = ec2_host['Instances'][0]['PrivateDnsName']
    #     private_ip = ec2_host['Instances'][0]['PrivateIpAddress']
    #     instance_details.append(private_ip)
        # script = f"""if nc -zv {private_ip} 22 2>&1 | grep -q succeeded;
        # then
        #     echo True
        # else
        #     echo False
        # fi"""

        # while not ssh_check_flag:
        #     out = subprocess.run(script, shell=True, capture_output= True, text=True)
        #     ssh_check_flag = out.stdout.split('\n')[0]
        #     time.sleep(5)

        # print(f"Connecting to host{private_ip}")
        # ssh = paramiko.SSHClient()
        # ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # privkey = paramiko.RSAKey.from_private_key_file(key_file_path)
        # ssh.connect(private_dns, username=user, pkey=privkey)
        # sftp = ssh.open_sftp()
        # print(f"Setting up on-demand")
        # remote_command_script = '/home/ubuntu/on_demand_setup.sh'
        # local_script = 'elastiflow/scripts/on_demand_setup.sh'
        # sftp.put(local_script, remote_command_script)
        # sftp.chmod(remote_command_script, 0o700)
        
        # sftp.close()

        # # Execute a command (for example, updating the package list)
        
        # # print("running command server")
        # command = f'bash {remote_command_script}'  # Change this command as needed
        # stdin, stdout, stderr = ssh.exec_command(command)
        # print(stdout.read().decode(), stderr.read().decode)
    
    print(instance_details)

    return instance_details

def terminateInstance(instance_ips):
    
    instance_ids = []

    filters = [
        {'Name': 'private-ip-address', 'Values': instance_ips}
    ]

    response = ec2.instances.filter(Filters = filters).terminate()
    print(response)
    # paginator = ec2.get_paginator('describe_instances')
    # for page in paginator.paginate(Filters=filters):
    #     for res in page.get('Reservations', []):
    #         for inst in res.get('Instances', []):
    #             instance_ids.append(inst['InstanceId'])

    # if not instance_ids:
    #     print(f"No instances of the ips: {instance_ips}")
    #     return

    # stop_resp = ec2.stop_instances(InstanceIds = instance_ids)
    print(f"{instance_ips} stopped.")

if __name__ == "__main__":
    instance_details = launchInstance("c6i.16xlarge", 1)
    # instance_ids = [instance['Instance Id'] for instance in instance_details]
    # print(instance_ids)
    # time.sleep(10)
    # terminateInstance(['10.19.208.207'])

