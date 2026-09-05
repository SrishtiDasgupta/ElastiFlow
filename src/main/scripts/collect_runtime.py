import boto3
import paramiko
import sys
# AWS configuration
key_file_path = '/home/ubuntu/Nisarg-HPC.pem'
user = 'ubuntu'  # or 'ubuntu' for Ubuntu instances
region= 'eu-north-1' #change to eu-north-1 when taking runtime of stockholm instances
# Create an EC2 client
ec2 = boto3.client('ec2', region_name=region)
ec2 = boto3.client('ec2', region_name=region)

def runReserved(request):
    hosts = request['hosts']
    chains = request['chains']
    tinyDa = request['tinyda_iterations']
    mesh = request['mesh']
    cohesion = request['cohesion']
    for host in hosts:
        print(hosts[host])
        instance_info = ec2.describe_instances(Filters=[{
                    'Name': 'private-ip-address',
                    'Values': hosts[host]
                }
                ])
        # print(instance_info['Reservations'][0]['Instances'][0]['CpuOptions'])
        ranks = len(hosts[host])
        host_file_ips = str("\\n".join(ip for ip in hosts[host]))
        ranks = len(hosts[host])
        host_file_ips = str("\\n".join(ip for ip in hosts[host]))
        private_dns = instance_info['Reservations'][0]['Instances'][0]['PrivateDnsName']
        print(private_dns)
        cores = instance_info['Reservations'][0]['Instances'][0]['CpuOptions']['CoreCount']
    # # Connect to the EC2 instance using Paramiko
    # # Connect to the EC2 instance using Paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        privkey = paramiko.RSAKey.from_private_key_file(key_file_path)
        ssh.connect(private_dns, username=user, pkey=privkey)
        # Execute a command (for example, updating the package list)
        remote_command_script = '/fsx/record_runtime.sh'
        print("running command")
        command = f'sh {remote_command_script} {chains} {tinyDa} {cohesion} {cores} "{host_file_ips}" {mesh} {ranks}'  # Change this command as needed
        command = f'sh {remote_command_script} {chains} {tinyDa} {cohesion} {cores} "{host_file_ips}" {mesh} {ranks}'  # Change this command as needed
        stdin, stdout, stderr = ssh.exec_command(command)
        print(stdout.read().decode())




if __name__ == "__main__":
    request = {'cohesion': 3e10, 'hosts': {'c6id.32xlarge': ['10.19.210.207', '10.19.192.45']}, 'chains': 1, 'tinyda_iterations': 1, 'mesh': 500}
    runReserved(request)

