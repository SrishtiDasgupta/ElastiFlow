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
from elastiflow.config.constants_HPO import COLD_START_TIME
from elastiflow.scripts import cold_start_log

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

# Per-instance-type AZ exclusions: AWS does not offer all G-instance families in every AZ.
# g5.xlarge in eu-north-1 is only available in 1b and 1c (not 1a).
INSTANCE_AZ_EXCLUSIONS = {
    'g5.xlarge':   {'eu-north-1a'},
    'g5.2xlarge':  {'eu-north-1a'},
    'g5.4xlarge':  {'eu-north-1a'},
}

def createExecutorInstance(instance_type: str = 'g4dn.2xlarge', backend=None) -> str:
    """
    Create a dedicated executor instance for HPO workflows
    Returns single IP address for the executor
    """
    print(f'Creating dedicated executor instance: {instance_type}')

    if backend is not None and backend.simulated:
        backend.sleep(COLD_START_TIME)
        # Generate simulated IP for executor
        digits = [10, 19] + random.choices(range(200, 255), k=2)
        executor_ip = '.'.join(map(str, digits))
        print(f'Simulated executor IP: {executor_ip}')
        return executor_ip
    else:
        ips = launchInstanceHPO(instance_type, 1, 'executor')
        return ips[0] if ips else None

def createWorkerInstances(instance_type: str, count: int, backend=None) -> List[str]:
    """
    Create homogeneous worker instances for HPO workflows
    Returns list of IP addresses for workers
    """
    if count < 1:
        return []

    print(f'Creating {count} worker instances of {instance_type}')

    if backend is None:                      # the CLI helpers below: live, no backend
        return launchInstanceHPO(instance_type, count, 'worker')
    ips = backend.provision(instance_type, count)
    if backend.simulated:
        print(f'Simulated worker IPs: {ips}')
    return ips

def createInstance(name: str, count: int = 1, backend=None) -> List[str]:
    """
    Backward compatibility function - routes to worker instance creation
    """
    return createWorkerInstances(name, count, backend)

def _terminate_failed_instance(instance, private_ip, reason):
    """Terminate an EC2 instance that failed setup to prevent leaking.
    Retries once after 5s if the first attempt fails."""
    print(f"[CLEANUP] Terminating failed instance {instance.id} ({private_ip}): {reason}")
    for attempt in range(2):
        try:
            instance.terminate()
            print(f"[CLEANUP] Instance {instance.id} terminated")
            return
        except Exception as e:
            print(f"[CLEANUP] Failed to terminate {instance.id} (attempt {attempt+1}/2): {e}")
            if attempt == 0:
                time.sleep(5)

def _setup_single_instance(instance, instance_role):
    """Wait for one instance to be running, SSH-ready, and set up. Returns IP or None."""
    instance.wait_until_running()
    instance.reload()

    private_ip = instance.private_ip_address
    private_dns = instance.private_dns_name

    cold_start_log.mark(
        instance.id, "t_running",
        instance_type=instance.instance_type,
        role=instance_role,
        az=instance.placement.get("AvailabilityZone", ""),
    )
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
                cold_start_log.mark(instance.id, "t_ssh")
                print(f"SSH ready on {private_ip}")
                break
        except Exception as e:
            print(f"SSH check error for {private_ip}: {e}")
        print(f"Waiting for SSH on {private_ip}... (attempt {attempt + 1})")
        time.sleep(10)
    else:
        print(f"Warning: SSH not ready after 20 attempts for {private_ip}")
        _terminate_failed_instance(instance, private_ip, "SSH timeout")
        return None

    # Setup
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(private_dns, username=user, key_filename=key_file_path)

        ok = setupInstanceHPO(ssh, private_ip)

        if ok:
            cold_start_log.mark(instance.id, "t_setup_done")
            # Verify executor is listening on port 8089 before returning
            print(f"Verifying executor on {private_ip}:8089 ...")
            executor_verified = False
            for attempt in range(6):
                _, stdout_chk, _ = ssh.exec_command(
                    f"ss -tlnp | grep 8089 || echo NOT_READY"
                )
                chk = stdout_chk.read().decode().strip()
                if 'NOT_READY' not in chk:
                    cold_start_log.mark(instance.id, "t_executor_listening")
                    print(f"Executor verified on {private_ip}:8089")
                    executor_verified = True
                    break
                if attempt == 5:
                    # Dump executor log for diagnostics
                    _, log_out, _ = ssh.exec_command("tail -30 ~/executor.out 2>/dev/null")
                    print(f"FATAL: Executor never started on {private_ip}:8089 after 30s")
                    print(f"  executor.out: {log_out.read().decode()}")
                print(f"  Waiting for executor... (attempt {attempt+1}/6)")
                time.sleep(5)

            ssh.close()
            if not executor_verified:
                cold_start_log.finalize(instance.id, "executor_timeout")
                _terminate_failed_instance(instance, private_ip, "executor not started")
                return None
            cold_start_log.finalize(instance.id, "ok")
            return private_ip

        else:
            cold_start_log.finalize(instance.id, "setup_failed")
            print(f"Setup FAILED for {instance_role} instance: {private_ip}")
            ssh.close()
            _terminate_failed_instance(instance, private_ip, "setup failed")
            return None

    except Exception as e:
        cold_start_log.finalize(instance.id, f"exception:{type(e).__name__}")
        print(f"Error setting up instance {private_ip}: {e}")
        _terminate_failed_instance(instance, private_ip, str(e))
        return None


VCPU_RETRY_WAIT = 45  # seconds to wait for old instances to finish terminating
VCPU_MAX_RETRIES = 8  # max retries on VcpuLimitExceeded (8x45s=360s covers ~6min shutdown)

def launchInstanceHPO(instanceName: str, count: int, instance_role: str):
    """
    Enhanced instance launch for HPO with role-specific setup
    instance_role: 'executor' or 'worker'
    """
    instance_details = []

    excluded_azs = INSTANCE_AZ_EXCLUSIONS.get(instanceName, set())
    candidate_azs = [(az, sn) for az, sn in AZ_SUBNETS if az not in excluded_azs]
    if excluded_azs:
        skipped = [az for az, _ in AZ_SUBNETS if az in excluded_azs]
        print(f"[AZ-FILTER] {instanceName} not offered in {skipped}; trying {[az for az,_ in candidate_azs]}")

    # Try each AZ in fallback order until instances launch
    for az, subnet_id in candidate_azs:
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

                for inst in instances:
                    cold_start_log.mark(inst.id, "t_request",
                                        instance_type=instanceName, role=instance_role, az=az)

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

        # Get output and check exit code
        stdout_output = stdout.read().decode()
        stderr_output = stderr.read().decode()
        exit_status = stdout.channel.recv_exit_status()

        print(f"Setup output for {instance_ip}:")
        if stdout_output:
            print(f"STDOUT: {stdout_output}")
        if stderr_output:
            print(f"STDERR: {stderr_output}")

        if exit_status != 0:
            print(f"SETUP FAILED for {instance_ip} (exit code {exit_status})")
            return False

        return True

    except Exception as e:
        print(f"Error in setupInstanceHPO for {instance_ip}: {e}")
        return False

def simulated_worker_ip() -> str:
    """A synthetic private IP for a simulated HPO worker (the same draw as before B3)."""
    digits = [10, 19] + random.choices(range(100, 199), k=2)
    return '.'.join(map(str, digits))


def launch_workers(instance_type: str, count: int) -> List[str]:
    """The live provisioning path, for LiveBackend.provision."""
    return launchInstanceHPO(instance_type, count, 'worker')


def deleteInstanceFromIp(instances: List[str], backend=None):
    """
    Terminate instances by IP addresses
    """
    print(f'Terminating HPO instances: {instances}')

    if backend is not None:
        return backend.release(instances)

    try:
        filters = [
            {'Name': 'private-ip-address', 'Values': instances},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping']}
        ]

        instances_to_terminate = list(ec2.instances.filter(Filters=filters))

        if instances_to_terminate:
            instance_ids = [inst.id for inst in instances_to_terminate]
            print(f"Terminating instances: {instance_ids}")

            for instance in instances_to_terminate:
                instance.terminate()

            # Wait for instances to reach 'terminated' state so AWS releases vCPUs.
            # Without this, new launches hit VcpuLimitExceeded during the ~6min
            # shutting-down window.
            print(f"Waiting for {len(instance_ids)} instances to reach terminated state...")
            for instance in instances_to_terminate:
                try:
                    instance.wait_until_terminated(
                        WaiterConfig={'Delay': 15, 'MaxAttempts': 30}  # up to 7.5 min
                    )
                    print(f"  {instance.id} terminated")
                except Exception as e:
                    print(f"  [WARN] Timeout waiting for {instance.id} to terminate: {e}")

            print(f"Terminated {len(instance_ids)} instances (vCPUs released)")
        else:
            print(f"No running instances found for IPs: {instances}")

    except Exception as e:
        print(f"Error terminating instances: {e}")

def terminate_live(instance_ips: List[str]):
    """The live termination path, for LiveBackend.release."""
    return deleteInstanceFromIp(instance_ips)


def terminateInstance(instance_ips: List[str], backend=None):
    """
    Backward compatibility function
    """
    deleteInstanceFromIp(instance_ips, backend)

# Additional HPO-specific utility functions

def getInstanceRole(instance_ip: str) -> str:
    """
    Get the role of an instance by its IP address
    """
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