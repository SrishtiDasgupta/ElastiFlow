import boto3
import paramiko
import subprocess
import re, json
import heapq
import time
from seissol_runner_base import SeisSolRunner
from scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5

class CloudRunnerHPO(SeisSolRunner):
    def __init__(self, request):
        self.request = request
        self.ec2_instance_id = 'i-00dfc41cc154695c3'
        self.key_file_path = '/fsx/Nisarg-HPC.pem'
        self.user = 'ubuntu'
        self.region = 'eu-north-1'
        self.ec2 = boto3.client('ec2', region_name=self.region)

    def numOfHosts(self, hosts):
        num = 0
        for host in hosts:
            num += len(hosts[host])
        return num

    def extractHosts(self, hosts, n):
        currently_acquired = 0
        host_types = []
        for key in hosts:
            to_be_used = min(n - currently_acquired, len(hosts[key]))
            if to_be_used:
                hosts[key] = hosts[key][to_be_used:]
                currently_acquired += to_be_used
                host_types.append(key)
            if currently_acquired == n:
                break
        return host_types

    def collectHostRuntimes(self, hosts, mesh):
        return {host: getRuntime(1, mesh, host) for host in hosts}

    

        return chain_groups

    def runReserved(self):
        request = self.request
        hosts = request['hosts']
        lead_private_dns = ""
        lead_private_ip = ""
        for host, _ in hosts.items():
            instance_info = self.ec2.describe_instances(Filters=[{
                'Name': 'private-ip-address',
                'Values': hosts[host]
                }])
            

        for reservation in instance_info['Reservations']:
            instance = reservation['Instances'][0]
            lead_private_dns = instance['PrivateDnsName']
            lead_private_ip = instance['PrivateIpAddress']
            # print(lead_private_dns)
            break # use first instance for SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        privkey = paramiko.RSAKey.from_private_key_file(self.key_file_path)
        ssh.connect(lead_private_dns, username=self.user, pkey=privkey)

        command = f'bash -c "/home/ubuntu/rayenv/bin/ray start --head --port=6380 --redis-password=\'1234\'"'
        stdin, stdout, stderr = ssh.exec_command(command)
        # print(stdout.read().decode())
        # print(stderr.read().decode())
        # print("Ray server started")
        for host, _ in hosts.items():
           
            

            instance_info = self.ec2.describe_instances(Filters=[{
            'Name': 'private-ip-address',
            'Values': hosts[host]
            }])
            

            for reservation in instance_info['Reservations']:
                
                for instance in reservation['Instances']:
                    private_dns = instance['PrivateDnsName']
                    cores = instance['CpuOptions']['CoreCount']
                    if lead_private_dns == private_dns: continue
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    privkey = paramiko.RSAKey.from_private_key_file(self.key_file_path)
                    ssh.connect(private_dns, username=self.user, pkey=privkey)
                    head_ip = lead_private_ip + ':6380'
                    command = f'bash -c "/home/ubuntu/rayenv/bin/ray start --address={head_ip} --redis-password=\'1234\'"'
                    
                    ssh.exec_command(command)

        return hosts

    def runClient(self, chain_groups):
        request = self.request
        hosts_num = self.numOfHosts(request['hosts'])
        remote_command_script = '/home/ubuntu/Vortex/service/run_client_HPO.sh'
        lead_private_dns = ""
        lead_private_ip = ""

        command = ['bash', remote_command_script, str(json.dumps(request["cohesion"])), str(hosts_num)]
        subprocess.run(command, capture_output=True, text=True)

        for host, _ in chain_groups.items():
           
            

            instance_info = self.ec2.describe_instances(Filters=[{
            'Name': 'private-ip-address',
            'Values': chain_groups[host]
            }])
            

            for reservation in instance_info['Reservations']:
                
                for instance in reservation['Instances']:
                    private_dns = instance['PrivateDnsName']
                    
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    privkey = paramiko.RSAKey.from_private_key_file(self.key_file_path)
                    ssh.connect(private_dns, username=self.user, pkey=privkey)
                    head_ip = lead_private_ip + ':6380'
                    command = f'bash -c "/home/ubuntu/rayenv/bin/ray stop"'
                    
                    ssh.exec_command(command)

        
    def fetchOutput(self):
        output = {}
        client_output_filepath = "/home/ubuntu/client.out"
        with open(client_output_filepath, 'r') as client_output:
            for line in client_output:
                if re.search('{"config":', line):
                    line = line.replace("array", "np.array")
                    line = eval(line)
                    output['config'] = line['config']
                    print(output)
                    break

    def run(self):
        chain_groups = self.runReserved()
        self.runClient(chain_groups)
        self.fetchOutput()
