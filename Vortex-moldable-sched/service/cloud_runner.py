import boto3
import paramiko
import subprocess
import re
import heapq
from seissol_runner_base import SeisSolRunner
from speedup import getRuntime # import runtime model

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

    def collectHostRuntimes(self, hosts, mesh):
        return {host: getRuntime(1, mesh, host) for host in hosts}

    def parallelAllocation(self, hosts, chains, mesh):
        hosts = {k: v[:] for k, v in hosts.items()} # deep copy
        host_runtimes = self.collectHostRuntimes(hosts, mesh)
        heap = []
        bottleneck_type = ""
        to_be_pushed = chains
        chain_groups = []
        
        # Step 1: assign one node per chain
        for host in hosts:
            n = min(len(hosts[host]), to_be_pushed)
            for _ in range(n):
                ip = hosts[host].pop(0)
                heapq.heappush(heap, (-host_runtimes[host], host, 1, [ip]))
                to_be_pushed -= 1
            if to_be_pushed == 0:
                break
        
        #print(hosts)

        
        # Step 2: distribute remaining nodes
        for host in hosts:
            for ip in hosts[host]:
                runtime, bottleneck_type, nodes, ip_list = heapq.heappop(heap)
                if host_runtimes[host] > host_runtimes[bottleneck_type]:
                    bottleneck_type = host
                new_runtime = getRuntime(nodes + 1, mesh, bottleneck_type)
                ip_list.append(ip)
                heapq.heappush(heap, (-new_runtime, bottleneck_type, nodes + 1, ip_list))
                # while hosts[host]:
                #     ip = hosts[host].pop(0)
                #     runtime, bottleneck_type, nodes, ip_list = heapq.heappop(heap)
                # if host_runtimes[host] > host_runtimes[bottleneck_type]:
                #     bottleneck_type = host
                # new_runtime = getRuntime(nodes + 1, mesh, bottleneck_type)
                # ip_list.append(ip)
                # heapq.heappush(heap, (-new_runtime, bottleneck_type, nodes + 1, ip_list))

        while heap:
            _, _, _, ip_list = heapq.heappop(heap)
            chain_groups.append(ip_list)

        return chain_groups

    def runReserved(self):
        request = self.request
        hosts = request['hosts']
        chains = request['chains']
        mesh = request['mesh']

        chain_groups = self.parallelAllocation(hosts, chains, mesh)
        for group in chain_groups:
            leader_ip = group[0]
            num_nodes = len(group)

            instance_info = self.ec2.describe_instances(Filters=[{
            'Name': 'private-ip-address',
            'Values': group
            }])
            

            for reservation in instance_info['Reservations']:
                instance = reservation['Instances'][0]
                private_dns = instance['PrivateDnsName']
                cores = instance['CpuOptions']['CoreCount']
                break # use first instance for SSH

            host_file_ips = "\\n".join(ip for ip in group)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            privkey = paramiko.RSAKey.from_private_key_file(self.key_file_path)
            ssh.connect(private_dns, username=self.user, pkey=privkey)

            remote_command_script = '/fsx/run_server_script.sh'
            command = f'sh {remote_command_script} "{host_file_ips}" {mesh} {cores} {num_nodes}'
            ssh.exec_command(command)

        return chain_groups

    def runClient(self, chain_groups):
        request = self.request
        chains = request['chains']
        tinyDa = request['tinyda_iterations']
        cohesion = request['cohesion']

        client_ips = " ".join(group[0] + ":4242" for group in chain_groups)
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
                    print(output)

    def run(self):
        chain_groups = self.runReserved()
        self.runClient(chain_groups)
        self.fetchOutput()
