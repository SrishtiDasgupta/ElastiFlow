from typing import List
import os
import boto3
from botocore.exceptions import ClientError
import paramiko
import time
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import HPO-specific constants
from config.constants_HPO import SIMULATE, COLD_START_TIME

region = 'eu-north-1'  # Stockholm region for HPO testing

# Create an EC2 client
session = boto3.Session(region_name=region)
ec2 = session.resource('ec2')
user = 'ubuntu'
key_file_path = os.path.expanduser('~/.ssh/hpo-exp.pem')

# Subnet fallback order: eu-north-1b (Slurm cluster AZ) → 1c → 1a
AZ_SUBNETS = [
    ('eu-north-1b', 'subnet-098f73921fcc5fbe1'),
    ('eu-north-1c', 'subnet-016c0e4a31d8955d7'),
    ('eu-north-1a', 'subnet-05ea82169d51876b4'),
]

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

def _setup_single_instance(instance, instance_role):
    """Wait for one instance to be running, SSH-ready, and set up. Returns IP or None."""
    instance.wait_until_running()
    instance.reload()

    private_ip = instance.private_ip_address
    private_dns = instance.private_dns_name

    print(f"Instance {instance.id} running: {private_ip} ({instance_role})")

    # Poll SSH
    for attempt in range(20):
        try:
            script = f"""if nc -zv {private_ip} 22 2>&1 | grep -q succeeded;
            then
                echo True
            else
                echo False
            fi"""

            out = subprocess.run(script, shell=True, capture_output=True, text=True)
            if 'True' in out.stdout:
                print(f"SSH ready on {private_ip}")
                break
        except Exception as e:
            print(f"SSH check error for {private_ip}: {e}")
        print(f"Waiting for SSH on {private_ip}... (attempt {attempt + 1})")
        time.sleep(10)
    else:
        print(f"Warning: SSH not ready after 20 attempts for {private_ip}")
        return None

    # Setup
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(private_dns, username=user, key_filename=key_file_path)

        ok = setupInstanceHPO(ssh, private_ip)
        ssh.close()

        if ok:
            print(f"Successfully set up {instance_role} instance: {private_ip}")
        else:
            print(f"Warning: Setup may have failed for {instance_role} instance: {private_ip}")
        return private_ip  # Return IP even if setup had warnings — non-fatal

    except Exception as e:
        print(f"Error setting up instance {private_ip}: {e}")
        return None


VCPU_RETRY_WAIT = 45  # seconds to wait for old instances to finish terminating
VCPU_MAX_RETRIES = 4  # max retries on VcpuLimitExceeded

def launchInstanceHPO(instanceName: str, count: int, instance_role: str):
    """
    Enhanced instance launch for HPO with role-specific setup
    instance_role: 'executor' or 'worker'
    """
    instance_details = []

    # Try each AZ in fallback order until instances launch
    for az, subnet_id in AZ_SUBNETS:
        for vcpu_retry in range(VCPU_MAX_RETRIES + 1):
            try:
                print(f"Trying {instanceName} x{count} in {az} ({subnet_id})...")
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
                    SubnetId=subnet_id
                )

                # Wait for all instances in parallel (SSH-wait + setup are independent per instance)
                with ThreadPoolExecutor(max_workers=len(instances)) as pool:
                    futures = {
                        pool.submit(_setup_single_instance, inst, instance_role): inst
                        for inst in instances
                    }
                    for future in as_completed(futures):
                        ip = future.result()
                        if ip:
                            instance_details.append(ip)

                print(f"Created {len(instance_details)} {instance_role} instances in {az}: {instance_details}")
                return instance_details

            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == 'InsufficientInstanceCapacity':
                    print(f"No {instanceName} capacity in {az}, trying next AZ...")
                    break  # break retry loop, continue to next AZ
                elif error_code == 'VcpuLimitExceeded' and vcpu_retry < VCPU_MAX_RETRIES:
                    wait_start = time.time()
                    print(f"[VCPU_RACE] vCPU limit hit — old instances likely still terminating. "
                          f"Retry {vcpu_retry + 1}/{VCPU_MAX_RETRIES}, waiting {VCPU_RETRY_WAIT}s...")
                    time.sleep(VCPU_RETRY_WAIT)
                    wait_overhead = time.time() - wait_start
                    print(f"[VCPU_RACE] Waited {wait_overhead:.1f}s (race-condition overhead)")
                    continue  # retry same AZ
                else:
                    print(f"Error creating instances in {az}: {e}")
                    return []
            except Exception as e:
                print(f"Error creating instances in {az}: {e}")
                return []

    print(f"No {instanceName} capacity in any AZ")
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