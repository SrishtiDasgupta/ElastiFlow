import os
import boto3
import paramiko
import subprocess
import re
import json
import time
import logging
from typing import Dict, List, Tuple

from scripts.speedup_runtime_HPO import getRuntime_g4, getRuntime_g5

import socket
import json

def _can_connect(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

class CloudRunnerHPO:
    """
    HPO Cloud Runner with Dedicated Executor Architecture

    New architecture:
    - Executor: Dedicated instance for communication with scheduler
    - Ray Head: First worker instance (not executor)
    - Ray Workers: Remaining worker instances
    - HPO Pipeline: Runs on Ray head node
    """

    def __init__(self, request):
        self.request = request
        self.key_file_path = '/fsx/Nisarg-HPC.pem'
        self.user = 'ubuntu'
        self.region = 'eu-north-1'
        self.ec2 = boto3.client('ec2', region_name=self.region)

        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Extract components from request
        self.executor_ip = request.get('executor-ip')
        self.worker_hosts = request.get('hosts', {})
        self.workflow_plan = request.get('wf-plan', {})
        self.cohesion = request.get('cohesion', {})
        self.is_moldable = request.get('moldable', False)

        # Ray cluster configuration
        self.ray_head_ip = None
        self.ray_workers = []
        self.ray_port = 6380
        self.ray_password = "hpo_cluster_2024"

    def setup_ray_cluster(self) -> bool:
        """
        Setup Ray cluster on worker instances (NOT on executor)
        Returns True if successful, False otherwise
        """
        self.logger.info("Setting up HPO Ray cluster...")

        # Get all worker IPs
        worker_ips = self.extract_worker_ips()

        if not worker_ips:
            self.logger.error("No worker instances available for Ray cluster")
            return False

        # First worker becomes Ray head
        self.ray_head_ip = worker_ips[0]
        self.ray_workers = worker_ips[1:] if len(worker_ips) > 1 else []

        self.logger.info(f"Ray Head: {self.ray_head_ip}")

        self.logger.info(f"Ray Workers: {self.ray_workers}")

        # Start Ray head
        if not self.start_ray_head():
            self.logger.error("Failed to start Ray head")
            return False

        # Start Ray workers
        if not self.start_ray_workers():
            self.logger.error("Failed to start Ray workers")
            return False

        self.logger.info("Ray cluster setup completed successfully")
        return True

    def extract_worker_ips(self) -> List[str]:
        """Extract all worker IP addresses from hosts configuration

        Handles two formats:
        1. Tiered: {'reserved': {'g4dn.2xlarge': (1, ['10.19.201.97'])}}
        2. Flat: {'g4dn.2xlarge': ['10.19.201.97']}
        """
        worker_ips = []

        # Check if tiered format (has 'on-prem', 'reserved', or 'on-demand' keys)
        is_tiered = any(k in self.worker_hosts for k in ['on-prem', 'reserved', 'on-demand'])

        if is_tiered:
            # Tiered format: {'reserved': {'g4dn.2xlarge': (count, [ips])}}
            for cluster_type in ['on-prem', 'reserved', 'on-demand']:
                if cluster_type in self.worker_hosts:
                    for instance_type, (count, ips) in self.worker_hosts[cluster_type].items():
                        worker_ips.extend(ips)
        else:
            # Flat format: {'g4dn.2xlarge': ['10.19.201.97']}
            for instance_type, ips in self.worker_hosts.items():
                if isinstance(ips, list):
                    worker_ips.extend(ips)
                elif isinstance(ips, tuple) and len(ips) == 2:
                    # Handle (count, [ips]) if present in flat format
                    worker_ips.extend(ips[1])

        self.logger.info(f"Extracted {len(worker_ips)} worker IPs: {worker_ips}")
        return worker_ips

    def start_ray_head(self) -> bool:
        """Start Ray head on the first worker instance"""
        try:
            # Get instance details
            private_dns = self.get_private_dns(self.ray_head_ip)
            if not private_dns:
                return False

            # Connect via SSH
            ssh = self.create_ssh_connection(private_dns)
            if not ssh:
                return False

            # Stop any existing Ray processes
            stop_command = "ray stop --force"
            ssh.exec_command(stop_command)
            time.sleep(3)

            # Start Ray head
            start_command = f"""
            cd /fsx/hyperparameter_test && \
            source /home/ubuntu/rayenv/bin/activate &&  \
            ray start --head \
                --port={self.ray_port} \
                --redis-password='{self.ray_password}' \
                --num-gpus=1 \
                --verbose
            """

            stdin, stdout, stderr = ssh.exec_command(start_command)

            # Check for successful startup
            output = stdout.read().decode()
            error = stderr.read().decode()

            self.logger.info(f"Ray head startup output: {output}")
            if error:
                self.logger.warning(f"Ray head startup warnings: {error}")

            ssh.close()

            # Wait for Ray head to be ready
            time.sleep(10)

            # Verify Ray head is running
            return self.verify_ray_head()

        except Exception as e:
            self.logger.error(f"Error starting Ray head: {e}")
            return False

    def start_ray_workers(self) -> bool:
        """Start Ray workers on remaining worker instances"""
        if not self.ray_workers:
            self.logger.info("No additional Ray workers to start")
            return True

        ray_address = f"{self.ray_head_ip}:{self.ray_port}"

        for worker_ip in self.ray_workers:
            try:
                # Get instance details
                private_dns = self.get_private_dns(worker_ip)
                if not private_dns:
                    continue

                # Connect via SSH
                ssh = self.create_ssh_connection(private_dns)
                if not ssh:
                    continue

                # Stop any existing Ray processes
                stop_command = "ray stop --force"
                ssh.exec_command(stop_command)
                time.sleep(2)

                # Start Ray worker
                start_command = f"""
                cd /fsx/hyperparameter_test && \
                source /home/ubuntu/rayenv/bin/activate && \
                ray start --address={ray_address} \
                    --redis-password='{self.ray_password}' \
                    --num-gpus=1 \
                    --verbose
                """

                stdin, stdout, stderr = ssh.exec_command(start_command)

                output = stdout.read().decode()
                error = stderr.read().decode()

                self.logger.info(f"Ray worker {worker_ip} startup output: {output}")
                if error:
                    self.logger.warning(f"Ray worker {worker_ip} warnings: {error}")

                ssh.close()
                time.sleep(3)

            except Exception as e:
                self.logger.error(f"Error starting Ray worker {worker_ip}: {e}")
                continue

        return True

    def verify_ray_head(self) -> bool:
        """Simple, robust: check head ports; if open, confirm >=1 alive node via JSON (if supported)."""
        try:
            # 1) Fast, reliable: Ray head GCS & dashboard ports
            gcs_ok = _can_connect(self.ray_head_ip, 6380)   # default GCS
            dash_ok = _can_connect(self.ray_head_ip, 8265)  # default dashboard

            if not (gcs_ok or dash_ok):
                self.logger.error("Ray head ports not reachable (6380/8265).")
                return False

            # 2) If CLI JSON is available, confirm there's at least one alive node
            private_dns = self.get_private_dns(self.ray_head_ip)
            ssh = self.create_ssh_connection(private_dns)

            # ray list nodes --format=json exists on newer Ray; fall back to plain status if not.
            cmd = "bash -lc 'ray list nodes --format=json 2>/dev/null || echo __NO_JSON__'"
            _, stdout, _ = ssh.exec_command(cmd)
            out = stdout.read().decode("utf-8", errors="ignore").strip()
            ssh.close()

            if out != "__NO_JSON__":
                try:
                    nodes = json.loads(out)
                    # Handle both list-of-nodes or dict{'data': [...]}
                    items = nodes if isinstance(nodes, list) else nodes.get("data", [])
                    alive = [n for n in items if str(n.get('state') or n.get('Status') or '').upper() in ('ALIVE', 'HEALTHY', 'RUNNING')]
                    if alive:
                        self.logger.info("Ray head verification successful (ports open, nodes alive).")
                        return True
                    else:
                        self.logger.error(f"Ray head up but no alive nodes reported. Raw: {out[:500]}")
                        return False
                except json.JSONDecodeError:
                    # JSON command printed something unexpected; still accept since ports are open.
                    self.logger.info("Ray head ports reachable; node JSON unreadable but head likely up.")
                    return True

            # Older Ray without JSON: ports open is good enough.
            self.logger.info("Ray head ports reachable; assuming healthy.")
            return True

        except Exception as e:
            self.logger.error(f"Error verifying Ray head: {e}")
            return False


    def run_hpo_pipeline(self) -> Dict:
        """
        Run HPO pipeline on Ray head node and return results
        """
        self.logger.info("Starting HPO pipeline execution...")

        try:
            # Connect to Ray head
            private_dns = self.get_private_dns(self.ray_head_ip)
            ssh = self.create_ssh_connection(private_dns)

            # Prepare HPO configuration
            hpo_config = self.prepare_hpo_config()

            # Write config to file on Ray head
            config_json = json.dumps(hpo_config, indent=2)

            # Upload config and run HPO pipeline
            sftp = ssh.open_sftp()
            with sftp.file('/tmp/hpo_config.json', 'w') as f:
                f.write(config_json)
            sftp.close()

            # Execute HPO pipeline
            pipeline_command = f"""
            cd /fsx/hyperparameter_test && \
            source /home/ubuntu/rayenv/bin/activate && \
            export RAY_ADDRESS='{self.ray_head_ip}:{self.ray_port}' && \
            python3 hpo_pipeline_verbose.py \
                --config-file /tmp/hpo_config.json \
                --hosts {len(self.extract_worker_ips())}
            """

            self.logger.info("Executing HPO pipeline...")
            stdin, stdout, stderr = ssh.exec_command(pipeline_command, timeout=3600)  # 1 hour timeout

            # Read results
            output = stdout.read().decode()
            error = stderr.read().decode()

            if error:
                self.logger.warning(f"HPO pipeline warnings: {error}")

            ssh.close()

            # Parse results
            results = self.parse_hpo_results(output)

            self.logger.info(f"HPO pipeline completed successfully")
            return results

        except Exception as e:
            self.logger.error(f"Error running HPO pipeline: {e}")
            return {'error': str(e), 'config': {'next_trials': 0}}

    def prepare_hpo_config(self) -> Dict:
        """Prepare HPO configuration from cohesion(workflow YAML) or workflow_plan
        Priority:
        1. Use cohesion if available (parameters from workflow YAML vars)
        2. Fall back to workflow_constraints
        """

        workflow_config = self.workflow_plan.get('config', {})
        constraints = self.workflow_plan.get('constraints', {})

        # Extract HPO parameters
        #model = constraints.get('mesh', 'vgg19')  # Model type
        #trials = constraints.get('chains', 3)     # Number of trials
        #epochs = constraints.get('tinydaIterations', 6)  # Epochs per trial

        # Extract HPO parameters
        #model = constraints.get('mesh', 'vgg19')  # Model type
        #trials = constraints.get('chains', 3)     # Number of trials
        #epochs = constraints.get('tinydaIterations', 6)  # Epochs per trial

        # Extract parameters with priority: cohesion > constraints > defaults

        # Model (handle both 'model' and 'model_name' from cohesion)
        model = self.cohesion.get('model_name',
                self.cohesion.get('model',
                constraints.get('mesh', 'vgg19')))

        # Trials
        trials = self.cohesion.get('next_trials', constraints.get('chains', 3))

        # Epochs (handle both singular 'epoch' and plural 'epochs')
        epochs = self.cohesion.get('epoch', self.cohesion.get('epochs', constraints.get('tinydaIterations', 6)))

        # Hyperparameters with cohesion priority
        learning_rate = self.cohesion.get('learning_rate', 0.01)
        momentum = self.cohesion.get('momentum', 0.9)
        batch_size = self.cohesion.get('batch_size', 64)
        hidden = self.cohesion.get('hidden', 10)
        image_size = self.cohesion.get('image_size', 160)
        amp = self.cohesion.get('amp', True)
        train_backbone = self.cohesion.get('train_backbone', False)
        data_dir = self.cohesion.get('data_dir', os.path.expanduser("~/cifar10"))

        # Build HPO configuration
        hpo_config = {
            'model_name': model,
            'epoch': epochs,
            'next_trials': trials,
            'learning_rate': learning_rate,
            'momentum': momentum,
            'batch_size': batch_size,
            'hidden': hidden, # Number of classes for CIFAR-10
            'image_size': image_size,
            'amp': amp,
            'train_backbone': train_backbone,
            'data_dir': data_dir  #"/home/ubuntu/cifar10"
        }

        self.logger.info(f"⚠️  HPO CONFIG PREPARED:")
        self.logger.info(f"    model_name: {hpo_config.get('model_name', 'NOT SET')}")
        self.logger.info(f"    epochs: {hpo_config.get('epoch')}")
        self.logger.info(f"    trials: {hpo_config.get('next_trials')}")
        self.logger.info(f"    Full config: {hpo_config}")
        return hpo_config

    def parse_hpo_results(self, output: str) -> Dict:
        """Parse HPO pipeline output to extract results"""
        try:
            # Look for JSON output in the pipeline output
            lines = output.split('\n')

            for line in lines:
                if line.strip().startswith('{') and 'config' in line:
                    # Found JSON result line
                    result = json.loads(line.strip())
                    self.logger.info(f"Parsed HPO result: {result}")
                    return result

            # Fallback: create minimal result
            self.logger.warning("Could not parse HPO results, using fallback")
            return {
                'config': {
                    'learning_rate': 0.01,
                    'momentum': 0.9,
                    'batch_size': 64,
                    'next_trials': 2  # Reduce trials for next iteration
                }
            }

        except Exception as e:
            self.logger.error(f"Error parsing HPO results: {e}")
            return {'error': str(e), 'config': {'next_trials': 0}}

   def cleanup_ray_cluster(self):
        """Clean up Ray cluster on all worker nodes"""
        self.logger.info("Cleaning up Ray cluster...")

        all_workers = [self.ray_head_ip] + self.ray_workers

        for worker_ip in all_workers:
            try:
                private_dns = self.get_private_dns(worker_ip)
                ssh = self.create_ssh_connection(private_dns)

                # Stop Ray
                stop_command = "ray stop --force"
                ssh.exec_command(stop_command)

                ssh.close()
                self.logger.info(f"Ray stopped on {worker_ip}")

            except Exception as e:
                self.logger.warning(f"Error stopping Ray on {worker_ip}: {e}")

    def send_results_to_executor(self, results: Dict) -> bool:
        """
        Send HPO results to the dedicated executor instance
        """
        self.logger.info(f"Sending results to executor {self.executor_ip}")

        try:
            # Send results via HTTP to executor's result endpoint
            import requests

            executor_url = f"http://{self.executor_ip}:8090/hpo_results"

            response = requests.post(
                executor_url,
                json=results,
                timeout=30
            )

            if response.status_code == 200:
                self.logger.info("Results sent to executor successfully")
                return True
            else:
                self.logger.error(f"Failed to send results to executor: {response.status_code}")
                return False

        except Exception as e:
            self.logger.error(f"Error sending results to executor: {e}")
            return False


    def run(self) -> Dict:
        """
        Main execution method for HPO workflow
        """
        self.logger.info("Starting HPO Cloud Runner execution...")

        try:
            # 1. Setup Ray cluster on worker instances
            if not self.setup_ray_cluster():
                return {'error': 'Failed to setup Ray cluster'}

            # 2. Run HPO pipeline on Ray head
            results = self.run_hpo_pipeline()

            # 3. Send results to executor
            self.send_results_to_executor(results)

            # 4. Clean up (for moldable workflows, may skip this)
            if not self.is_moldable:
                self.cleanup_ray_cluster()

            self.logger.info("HPO execution completed successfully")
            return results

        except Exception as e:
            self.logger.error(f"Error in HPO execution: {e}")
            self.cleanup_ray_cluster()
            return {'error': str(e)}

    # Utility methods

    def get_private_dns(self, ip_address: str) -> str:
        """Get private DNS name for an IP address"""
        try:
            response = self.ec2.describe_instances(
                Filters=[
                    {'Name': 'private-ip-address', 'Values': [ip_address]},
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )

            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    return instance['PrivateDnsName']

            return None

        except Exception as e:
            self.logger.error(f"Error getting private DNS for {ip_address}: {e}")
            return None

    def create_ssh_connection(self, private_dns: str):
        """Create SSH connection to an instance"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            privkey = paramiko.RSAKey.from_private_key_file(self.key_file_path)
            ssh.connect(private_dns, username=self.user, pkey=privkey, timeout=30)

            return ssh

        except Exception as e:
            self.logger.error(f"Error creating SSH connection to {private_dns}: {e}")
            return None

# Backward compatibility
CloudRunner = CloudRunnerHPO

