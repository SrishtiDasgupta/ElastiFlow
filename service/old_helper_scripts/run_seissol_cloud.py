import boto3
import paramiko
import subprocess
import re
import sys

# AWS configuration
ec2_instance_id = 'i-00dfc41cc154695c3'
key_file_path = '/fsx/Nisarg-HPC.pem'
user = 'ubuntu'  # or 'ubuntu' for Ubuntu instances

region= 'eu-north-1'
# region= 'eu-central-1'  #CHANGE THIS TO EU-NORTH-1 FOR STOCKHOLM INSTANCES
# Create an EC2 client
ec2 = boto3.client('ec2', region_name=region)
unused_hosts = []

def numOfHosts(hosts):
    num = 0
    for host in hosts:
        num += len(hosts[host])
    return num

def extractHosts(hosts, n):
    # Extract first n hosts from hosts
    currently_acquired = 0
    host_types = []
    for key in hosts:
        to_be_used = min(n-currently_acquired, len(hosts[key]))
        if to_be_used:
            hosts[key] = hosts[key][to_be_used:]
            currently_acquired += to_be_used
            host_types.append(key)
        if currently_acquired == n:
            break
    return host_types

def runReserved(request):
    hosts = request['hosts']
    chains = request['chains']
    num_of_hosts = numOfHosts(hosts)
    hosts_per_chain = num_of_hosts // chains if num_of_hosts > chains else 1
    mesh = request['mesh']
    used_hosts_global = []
    used_hosts = []
    extra_hosts = num_of_hosts - chains if num_of_hosts > chains else 0
    for host in hosts: # we only create server for the number of instances equal to the num of chains
        num_of_hosts_used = hosts_per_chain + 1 if extra_hosts > 0 and hosts_per_chain == 1 else hosts_per_chain
        instance_info = ec2.describe_instances(Filters=[{
                    'Name': 'private-ip-address',
                    'Values': hosts[host]
                }
                ])
        # print(instance_info['Reservations'][0]['Instances'][0])
        
        for ec2_host in instance_info['Reservations']:
            private_ip = ec2_host['Instances'][0]['PrivateIpAddress']
            # print(private_ip)
            used_hosts_global.append(private_ip)
            if len(used_hosts_global) % (num_of_hosts_used) != 0:
                continue
            # instance type
            used_hosts.append(private_ip)
            private_dns = ec2_host['Instances'][0]['PrivateDnsName']
            cores = ec2_host['Instances'][0]['CpuOptions']['CoreCount']
            host_file_ips = str("\\n".join(ip for ip in used_hosts_global[-num_of_hosts_used:]))
            # print(f"list of ips: {host_file_ips}")
        # Connect to the EC2 instance using Paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            privkey = paramiko.RSAKey.from_private_key_file(key_file_path)
            ssh.connect(private_dns, username=user, pkey=privkey)
            stdin, stdout, stderr = ssh.exec_command('grep -v $(hostname -I) /fsx/global_authorized_hosts > .ssh/authorized_keys') #global_authorized_hosts should always have the aws key
            # Execute a command (for example, updating the package list)
            remote_command_script = '/fsx/run_server_script.sh'
            # print("running command server")
            command = f'sh {remote_command_script} "{host_file_ips}" {mesh} {cores} {num_of_hosts_used}'  # Change this command as needed
            stdin, stdout, stderr = ssh.exec_command(command)
            # print(stdout.read().decode()) not required because we have logs in the instance and this gets read as an output by steep actions
            extra_hosts -= 1
            # chains -= 1
    return used_hosts

def runClient(request, used_hosts):
    chains = request['chains']
    tinyDa = request['tinyda_iterations']
    cohesion = request['cohesion']
    client_ips = str(" ".join(ip+":4242" for ip in used_hosts))
    # print(client_ips)
# # Connect to the EC2 instance using Paramiko
    
    # Execute a command (for example, updating the package list)
    remote_command_script = '/fsx/run_client_script.sh'
    # print("running command client")
    command = ['bash', remote_command_script, str(chains), str(tinyDa), str(cohesion), client_ips]   # Change this command as needed
    result = subprocess.run(command, capture_output=True, text=True)
    # print(result.stdout)

def fetchOutput(): #read the output of the client log and do the calculations
    output_object = {"cohesion": 0}
    client_output_filepath = "/home/ubuntu/client.out"
    client_output = open(client_output_filepath, 'r')
    for line in client_output:
        if re.search("{'likelihood':", line):
            line = line.replace("array", "np.array")
            print(line)
            output = eval(line)
            output['cohesion'] = output['input_parameters'][0]
            output.pop('input_parameters', None)
            print(output)
    # print(output_object)

if __name__ == "__main__":
    val = sys.argv[1:] # ["{'cohesion': 3, 'hosts': {'hpc6id.32xlarge': []}, 'chains': 3, 'tinyda_iterations': 12}"]
    request = eval(val[0])
    # request = {'cohesion': 3e10, 'hosts': {'c7i.24xlarge': ['10.3.14.43']}, 'chains': 1, 'tinyda_iterations': 1, 'mesh': 1000}
    used_hosts= runReserved(request)
    # used_hosts = ['10.3.14.38']
    runClient(request, used_hosts)
    fetchOutput()