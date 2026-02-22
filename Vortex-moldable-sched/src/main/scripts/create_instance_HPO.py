from typing import List
import os
import boto3
from botocore.exceptions import ClientError
import paramiko
import time
import random
import subprocess

# Import HPO-specific constants
from config.constants_HPO import SIMULATE, COLD_START_TIME

region = 'eu-north-1'  # Stockholm region for HPO testing

# Create an EC2 client
session = boto3.Session(region_name=region)
ec2 = session.resource('ec2')
user = 'ubuntu'
key_file_path = os.path.expanduser('~/.ssh/hpo-exp.pem')

def createExecutorInstance(instance_type: str = 'g4dn.2xlarge', sim=None) -> str:
    """
    Create a dedicated executor instance for HPO workflows
    Returns single IP address for the executor
    """
    print(f'Creating dedicated executor instance: {instance_type}')

    if sim or SIMULATE:
        (sim or time).sleep(COLD_START_TIME)
        # Generate simulated IP for executor
        digits = [10, 19] + random.choices(range(200, 255), k=2)
        executor_ip = '.'.join(map(str, digits))
        print(f'Simulated executor IP: {executor_ip}')
        return executor_ip
    else:
        ips = launchInstanceHPO(instance_type, 1, 'executor')
        return ips[0] if ips else None

def createWorkerInstances(instance_type: str, count: int, sim=None) -> List[str]:
    """
    Create homogeneous worker instances for HPO workflows
    Returns list of IP addresses for workers
    """
    if count < 1:
        return []

    print(f'Creating {count} worker instances of {instance_type}')

    if sim or SIMULATE:
        (sim or time).sleep(COLD_START_TIME)
        ips = []
        for i in range(count):
            # Generate simulated IPs for workers
            digits = [10, 19] + random.choices(range(100, 199), k=2)
            ips.append('.'.join(map(str, digits)))
        print(f'Simulated worker IPs: {ips}')
        return ips
    else:
        return launchInstanceHPO(instance_type, count, 'worker')

def createInstance(name: str, count: int = 1, sim=None) -> List[str]:
    """
    Backward compatibility function - routes to worker instance creation
    """
    return createWorkerInstances(name, count, sim)

def launchInstanceHPO(instanceName: str, count: int, instance_role: str):
    """
    Enhanced instance launch for HPO with role-specific setup
    instance_role: 'executor' or 'worker'
    """
    instance_details = []

    try:
        # Create instances with appropriate tags and 64 GB root volume
        instances = ec2.create_instances(
            ImageId='ami-0f7f72d078ea0a900',  # HPO-optimized AMI
            BlockDeviceMappings=[
                {
                    'DeviceName': '/dev/sda1',  # Root device for Ubuntu AMIs
                    'Ebs': {
                        'VolumeSize': 64,  # Increase from 40 GB to 64 GB
                        'VolumeType': 'gp3',  # General Purpose SSD v3 (faster than gp2)
                        'DeleteOnTermination': True,  # Clean up on instance termination
                        'Iops': 3000,  # Base IOPS for gp3
                        'Throughput': 125  # Base throughput in MiB/s for gp3
                    }
                }
            ],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {
                            'Key': 'Name',
                            'Value': f'{instanceName}-hpo-{instance_role}'
                        },
                        {
                            'Key': 'Role',
                            'Value': instance_role
                        },
                        {
                            'Key': 'Project',
                            'Value': 'Vortex-HPO'
                        }
                    ]
                }
            ],
            InstanceType=instanceName,
            MinCount=count,
            MaxCount=count,
            KeyName='hpo-exp',
            SecurityGroupIds=['sg-0d61f6325a433891b'],
            SubnetId='subnet-016c0e4a31d8955d7'
        )

        # Wait for instances to be running and setup
        for instance in instances:
            instance.wait_until_running()
            instance.reload()

            print(f"Instance ID: {instance.id}")
            print(f"Private IP: {instance.private_ip_address}")
            print(f"Role: {instance_role}")

            private_ip = instance.private_ip_address
            private_dns = instance.private_dns_name
            instance_details.append(private_ip)

            # Wait for SSH to be available
            ssh_ready = False
            max_attempts = 20
            attempt = 0

            while not ssh_ready and attempt < max_attempts:
                try:
                    script = f"""if nc -zv {private_ip} 22 2>&1 | grep -q succeeded;
                    then
                        echo True
                    else
                        echo False
                    fi"""

                    out = subprocess.run(script, shell=True, capture_output=True, text=True)
                    ssh_ready = 'True' in out.stdout

                    if not ssh_ready:
                        print(f"Waiting for SSH on {private_ip}... (attempt {attempt + 1})")
                        time.sleep(10)
                        attempt += 1
                    else:
                        print(f"SSH ready on {private_ip}")

                except Exception as e:
                    print(f"SSH check error: {e}")
                    time.sleep(10)
                    attempt += 1

            if not ssh_ready:
                print(f"Warning: SSH not ready after {max_attempts} attempts for {private_ip}")
                continue

            # Setup instance based on role
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(private_dns, username=user, key_filename=key_file_path)

                # Upload and execute setup script (all instances are executor-capable)
                setup_script = setupInstanceHPO(ssh, private_ip)

                if setup_script:
                    print(f"Successfully set up {instance_role} instance: {private_ip}")
                else:
                    print(f"Warning: Setup may have failed for {instance_role} instance: {private_ip}")

                ssh.close()

            except Exception as e:
                print(f"Error setting up instance {private_ip}: {e}")
                continue

        print(f"Created {len(instance_details)} {instance_role} instances: {instance_details}")
        return instance_details

    except Exception as e:
        print(f"Error creating instances: {e}")
        return []

def setupInstanceHPO(ssh, instance_ip: str):
    """
    Setup cloud instance as executor-capable (Redis + executor process).
    All cloud instances use the same setup script.
    """
    try:
        sftp = ssh.open_sftp()

        remote_script = '/home/ubuntu/hpo_setup.sh'
        local_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'on_demand_setup_HPO_worker.sh')

        # Upload setup script
        sftp.put(local_script, remote_script)
        sftp.chmod(remote_script, 0o700)
        sftp.close()

        # Execute setup script
        command = f'bash {remote_script} {instance_ip}'
        stdin, stdout, stderr = ssh.exec_command(command)

        # Get output
        stdout_output = stdout.read().decode()
        stderr_output = stderr.read().decode()

        print(f"Setup output for {instance_ip}:")
        if stdout_output:
            print(f"STDOUT: {stdout_output}")
        if stderr_output:
            print(f"STDERR: {stderr_output}")

        return True

    except Exception as e:
        print(f"Error in setupInstanceHPO for {instance_ip}: {e}")
        return False

def deleteInstanceFromIp(instances: List[str]):
    """
    Terminate instances by IP addresses
    """
    print(f'Terminating HPO instances: {instances}')

    if SIMULATE:
        print(f'Simulated termination of {instances}')
        return

    try:
        filters = [
            {'Name': 'private-ip-address', 'Values': instances},
            {'Name': 'instance-state-name', 'Values': ['running', 'stopping']}
        ]

        instances_to_terminate = list(ec2.instances.filter(Filters=filters))

        if instances_to_terminate:
            instance_ids = [inst.id for inst in instances_to_terminate]
            print(f"Terminating instances: {instance_ids}")

            for instance in instances_to_terminate:
                instance.terminate()

            print(f"Terminated {len(instance_ids)} instances")
        else:
            print(f"No running instances found for IPs: {instances}")

    except Exception as e:
        print(f"Error terminating instances: {e}")

def terminateInstance(instance_ips: List[str]):
    """
    Backward compatibility function
    """
    deleteInstanceFromIp(instance_ips)

# Additional HPO-specific utility functions

def getInstanceRole(instance_ip: str) -> str:
    """
    Get the role of an instance by its IP address
    """
    if SIMULATE:
        # In simulation, determine role by IP pattern
        if instance_ip.startswith('10.19.2'):
            return 'executor'
        else:
            return 'worker'

    try:
        filters = [
            {'Name': 'private-ip-address', 'Values': [instance_ip]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ]

        instances = list(ec2.instances.filter(Filters=filters))

        if instances:
            for tag in instances[0].tags or []:
                if tag['Key'] == 'Role':
                    return tag['Value']

        return 'unknown'

    except Exception as e:
        print(f"Error getting instance role for {instance_ip}: {e}")
        return 'unknown'

def listHPOInstances():
    """
    List all HPO instances with their roles and states
    """
    if SIMULATE:
        print("Simulation mode - no real instances to list")
        return

    try:
        filters = [
            {'Name': 'tag:Project', 'Values': ['Vortex-HPO']},
            {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping']}
        ]

        instances = list(ec2.instances.filter(Filters=filters))

        print(f"Found {len(instances)} HPO instances:")

        for instance in instances:
            role = 'unknown'
            name = 'unknown'

            for tag in instance.tags or []:
                if tag['Key'] == 'Role':
                    role = tag['Value']
                elif tag['Key'] == 'Name':
                    name = tag['Value']

            print(f"  {instance.id}: {instance.private_ip_address} - {role} - {instance.state['Name']} - {name}")

    except Exception as e:
        print(f"Error listing HPO instances: {e}")

if __name__ == "__main__":
    # Test the HPO instance creation
    print("Testing HPO instance creation...")

    # Test executor creation
    executor_ip = createExecutorInstance('g4dn.2xlarge')
    print(f"Created executor: {executor_ip}")

    # Test worker creation
    worker_ips = createWorkerInstances('g5.2xlarge', 2)
    print(f"Created workers: {worker_ips}")

    # List all instances
    listHPOInstances()