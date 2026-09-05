# cloud_runner.py

import boto3
import paramiko
import subprocess
import re
from seissol_runner_base import SeisSolRunner

class CloudRunner(SeisSolRunner):
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

    def runReserved(self):
        request = self.request
        hosts = request['hosts']
        chains = request['chains']
        num_of_hosts = self.numOfHosts(hosts)
        hosts_per_chain = num_of_hosts // chains if num_of_hosts > chains else 1
        mesh = request['mesh']
        used_hosts_global = []
        used_hosts = []
        extra_hosts = num_of_hosts - chains if num_of_hosts > chains else 0

        for host in hosts:
            num_of_hosts_used = (
                hosts_per_chain + 1
                if extra_hosts > 0 and hosts_per_chain == 1
                else hosts_per_chain
            )
            instance_info = self.ec2.describe_instances(Filters=[{
                'Name': 'private-ip-address',
                'Values': hosts[host]
            }])

            for ec2_host in instance_info['Reservations']:
                private_ip = ec2_host['Instances'][0]['PrivateIpAddress']
                used_hosts_global.append(private_ip)
                if len(used_hosts_global) % num_of_hosts_used != 0:
                    continue

                used_hosts.append(private_ip)
                private_dns = ec2_host['Instances'][0]['PrivateDnsName']
                cores = ec2_host['Instances'][0]['CpuOptions']['CoreCount']
                host_file_ips = "\\n".join(
                ip for ip in used_hosts_global[-num_of_hosts_used:]
                )

                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                privkey = paramiko.RSAKey.from_private_key_file(self.key_file_path)
                ssh.connect(private_dns, username=self.user, pkey=privkey)

                ssh.exec_command(
                'grep -v $(hostname -I) /fsx/global_authorized_hosts > .ssh/authorized_keys'
                )
                remote_command_script = '/fsx/run_server_script.sh'
                command = f'sh {remote_command_script} "{host_file_ips}" {mesh} {cores} {num_of_hosts_used}'
                ssh.exec_command(command)

                extra_hosts -= 1

            return used_hosts

    def runClient(self, used_hosts):
        request = self.request
        chains = request['chains']
        tinyDa = request['tinyda_iterations']
        cohesion = request['cohesion']
        client_ips = " ".join(ip + ":4242" for ip in used_hosts)

        remote_command_script = '/fsx/run_client_script.sh'
        command = ['bash', remote_command_script, str(chains), str(tinyDa), str(cohesion), client_ips]
        subprocess.run(command, capture_output=True, text=True)

    def fetchOutput(self):
        client_output_filepath = "/home/ubuntu/client.out"
        with open(client_output_filepath, 'r') as client_output:
            for line in client_output:
                if re.search("{'likelihood':", line):
                    line = line.replace("array", "np.array")
                    print(line)
                    output = eval(line)
                    output['cohesion'] = output['input_parameters'][0]
                    output.pop('input_parameters', None)
                    print(output)

    def run(self):
        used_hosts = self.runReserved()
        self.runClient(used_hosts)
        self.fetchOutput()