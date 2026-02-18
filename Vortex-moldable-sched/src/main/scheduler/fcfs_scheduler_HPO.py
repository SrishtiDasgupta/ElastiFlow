import threading
import time
import math

from config.constants_HPO import WORKFLOW_POLLING, SIMULATE, COLD_START_TIME
from scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5
from scripts.create_instance_HPO import createExecutorInstance, createWorkerInstances
from utils.request import ExecutorRequest, sendRequest, getConfig
import os
from resource_manager.resource_manager import ResourceManager
from resource_manager.instance import CloudOnDemandInstance, OnPremInstance

_HPO_RESOURCES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from utils.sim import getTime, peekElement, removeElement
from utils.resource import getConstraintsFromWorkflow
from scheduler.scheduler_HPO import Scheduler_HPO

# HPO-specific FCFS Scheduler with Dedicated Executor Design
# Static version - no moldable resource allocation
class FCFS_Scheduler_HPO(Scheduler_HPO):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost'):
        self.resource_manager = ResourceManager(_HPO_RESOURCES)
        # Sort by base cost (hourly rate) - cost_per_trial calculated during allocation
        func = lambda x: x.cost if hasattr(x, 'cost') else 0
        self.resource_manager.sortResourcesByFunction(func)
        super().__init__(queue, finish_queue, resource_request_queue)

    def run(self, sim = None, wf_mb = None, resource_request_mb = None):

        print(f'Starting HPO FCFS scheduler at {getTime(sim)}...')

        # Start a thread to periodically compute resource utilization
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(target=self.metrics.collectResourceUtilization, args=[sim, self.resource_manager])
            thread.start()

        while True:

            # Check queue for resource requests (minimal for static version)
            resource_request = peekElement(resource_request_mb, self.resource_request_queue)

            if resource_request:
                # Static scheduler - limited resource request handling
                resource_request = eval(resource_request)
                print(f"HPO Static Scheduler: Resource request received but ignored (static mode)")
                removeElement(resource_request_mb, self.resource_request_queue)
                continue

            # Check the queue for new jobs
            workflow_plan = peekElement(wf_mb, self.queue)

            if workflow_plan:
                wf_plan = eval(workflow_plan) # Convert string back to dictionary

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    removeElement(wf_mb, self.queue)
                    self.metrics.computeMetrics()
                    break

                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesHPO(constraints, sim)
                    print(f"{wf_plan['id']} allocated at {getTime(sim)}:", ips)

                    # Remove the element if we found the resources needed.
                    if ips:
                        removeElement(wf_mb, self.queue)
                        # NOTE: We start billing at this point
                        start_time = getTime(sim)
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO resources to allocate, waiting...')

            (sim or time).sleep(WORKFLOW_POLLING)

    def allocateResourcesHPO(self, constraints, sim=None):
        """
        HPO-specific resource allocation with resilient fallback
        Tries to allocate optimal number of hosts, degrades gracefully if unavailable
        """
        model = constraints['mesh']
        budget = constraints['budget']
        deadline_duration = constraints['deadline_duration']  # Duration in seconds (not absolute timestamp)
        trials = constraints['chains']
        epochs = constraints['tinydaIterations']

        # Get optimal instance type AND number of hosts
        optimal_type, num_hosts_requested = self.selectOptimalInstanceType(
            budget, deadline_duration, model, trials, epochs
        )

        instances = self.resource_manager.getResources()
        selected_instances = []
        remaining = num_hosts_requested

        # PRIORITY 1: On-premise (if type matches)
        for instance in instances:
            if isinstance(instance, OnPremInstance) and \
               self.getInstanceTypeForHPO(instance.name) == optimal_type:
                slots_available = instance.getFreeSlots()
                if slots_available >= remaining:
                    # Can fully satisfy with on-prem
                    selected_instances = [(instance, remaining)]
                    remaining = 0
                    print(f"Allocated {num_hosts_requested} on-prem {instance.name} (workflow locked to on-prem)")
                    break
                elif slots_available > 0:
                    # Partial on-prem allocation (resilient: take what's available)
                    selected_instances = [(instance, slots_available)]
                    remaining -= slots_available
                    print(f"Allocated {slots_available} on-prem {instance.name} (degraded from {num_hosts_requested})")
                    break

        # PRIORITY 2: Cloud reserved
        if remaining > 0:
            for instance in instances:
                if instance.type == 'reserved' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        selected_instances.append((instance, slots_available))
                        remaining -= slots_available
                        if remaining == 0:
                            break

        # PRIORITY 3: Cloud on-demand (with budget check)
        if remaining > 0:
            runtime_func = getRuntime_g5 if optimal_type == 'g5' else getRuntime_g4

            # Calculate runtime for cost check (based on what we'll actually get)
            allocated_so_far = num_hosts_requested - remaining
            total_hosts = allocated_so_far + remaining

            if total_hosts >= trials:
                workers_per_trial = total_hosts // trials
                runtime = runtime_func(workers_per_trial, model, epochs)
            else:
                batches = math.ceil(trials / total_hosts)
                runtime = batches * runtime_func(1, model, epochs)

            for instance in instances:
                if instance.type == 'on-demand' and \
                   self.getInstanceTypeForHPO(instance.name) == optimal_type:
                    slots_available = min(remaining, instance.getFreeSlots())
                    if slots_available > 0:
                        # Calculate cost with cold start
                        cost = (runtime / 3600) * instance.cost_per_second * slots_available
                        if cost < budget:
                            selected_instances.append((instance, slots_available))
                            remaining -= slots_available
                            print(f"Allocated {slots_available} on-demand {optimal_type} (cost: ${cost:.2f})")
                            if remaining == 0:
                                break

        # RESILIENT: Always proceed with what we got (never fail)
        allocated_hosts = num_hosts_requested - remaining
        if remaining > 0:
            print(f"⚠️  Degraded allocation: requested {num_hosts_requested}, got {allocated_hosts} hosts")
            print(f"   Workflow will run with reduced parallelism (some trials sequential)")

        if allocated_hosts == 0:
            print(f"❌ Failed to allocate any hosts of type {optimal_type}")
            return None, None

        # Allocate and create instances
        ips, alloc_resources = self.resource_manager.allocateResources(selected_instances)
        ips = self.createOnDemandWorkers(ips, sim)

        return ips, alloc_resources

    def selectOptimalInstanceType(self, budget, deadline, model, trials, epochs):
        """
        Select optimal instance type AND number of hosts
        Returns: (instance_type, num_hosts)

        Considers ALL available instance types (on-prem, g4, g5) with actual costs
        Explores different host counts to find best cost/performance tradeoff
        No instance type switching - stays within one type for entire workflow
        """
        instances = self.resource_manager.getResources()

        # Build list of unique instance types to evaluate
        instance_types = {}
        for instance in instances:
            inst_type = self.getInstanceTypeForHPO(instance.name)
            if inst_type not in instance_types:
                # Store cheapest cost for this type (prioritize reserved/on-prem over on-demand)
                cost = instance.cost_per_second
                runtime_func = getRuntime_g5 if inst_type == 'g5' else getRuntime_g4
                instance_types[inst_type] = {
                    'cost_per_second': cost,
                    'runtime_func': runtime_func,
                    'name': instance.name
                }
            else:
                # Update if we found a cheaper instance of same type
                if instance.cost_per_second < instance_types[inst_type]['cost_per_second']:
                    instance_types[inst_type]['cost_per_second'] = instance.cost_per_second
                    instance_types[inst_type]['name'] = instance.name

        best_instance_type = None
        best_num_hosts = 1
        best_score = float('inf')
        best_cost = 0
        best_runtime = 0

        # Evaluate each instance type
        for inst_type, info in instance_types.items():
            runtime_func = info['runtime_func']
            cost_per_second = info['cost_per_second']
            cost_per_hour = cost_per_second * 3600

            # Try different num_hosts for this instance type
            for num_hosts in range(1, trials + 1):
                # Calculate actual runtime based on execution pattern
                if num_hosts >= trials:
                    # Parallel execution: each trial gets (num_hosts // trials) workers
                    workers_per_trial = num_hosts // trials
                    runtime = runtime_func(workers_per_trial, model, epochs)
                else:
                    # Sequential batches: some trials must wait
                    batches = math.ceil(trials / num_hosts)
                    runtime = batches * runtime_func(1, model, epochs)

                # Calculate cost for this configuration (use cost_per_second directly)
                cost = runtime * cost_per_second * num_hosts

                # Check if meets constraints
                if runtime <= deadline and cost <= budget:
                    # Score: minimize cost with slight preference for faster completion
                    score = cost + (runtime / deadline) * 0.1
                    if score < best_score:
                        best_score = score
                        best_num_hosts = num_hosts
                        best_instance_type = inst_type
                        best_cost = cost
                        best_runtime = runtime

        if best_instance_type is None:
            # No solution found within constraints - return cheapest option
            cheapest_type = min(instance_types.items(), key=lambda x: x[1]['cost_per_second'])[0]
            print(f"⚠️  No configuration meets constraints, defaulting to cheapest: 1 × {cheapest_type}")
            return cheapest_type, 1

        print(f"Selected: {best_num_hosts} × {best_instance_type} for {trials} trials (cost: ${best_cost:.2f}, runtime: {best_runtime:.0f}s)")
        return (best_instance_type, best_num_hosts)

    def getInstanceTypeForHPO(self, instance_name):
        """Map instance names to HPO instance types"""
        if 'g4dn' in instance_name:
            return 'g4'
        elif 'g5' in instance_name:
            return 'g5'
        elif 'on-prem' in instance_name:
            return 'g4'  # On-prem uses g4dn.xlarge instances
        else:
            return 'unknown'

    def createOnDemandWorkers(self, ips, sim):
        """
        Create actual on-demand worker instances for allocated virtual slots
        ResourceManager.allocateResources() returns empty IP lists for on-demand,
        this method creates the actual instances and updates the IP lists
        """
        for instance_type, (count, ip_list) in ips.get('on-demand', {}).items():
            if count > 0 and len(ip_list) == 0:
                print(f"Creating {count} on-demand {instance_type} worker instances...")
                worker_ips = createWorkerInstances(instance_type, count, sim)
                ips['on-demand'][instance_type] = (count, worker_ips)
                print(f"Created {count} on-demand {instance_type} workers: {worker_ips}")
        return ips

    def sendWorkflowForExecutionHPO(self, wf_plan, ips, sim, deadline):
        """
        HPO-specific workflow execution with dedicated executor design
        Routes to on-prem executor OR creates cloud executor based on worker allocation
        """
        # Determine executor based on worker allocation
        # If on-prem workers → use on-prem executor (manually started)
        # If cloud workers → create dedicated cloud executor

        # ips['on-prem'] is a dict: {'on-prem': (count, [ip_list])}
        on_prem_hosts = ips.get('on-prem', {})
        on_prem_ips = []
        for name, (count, ip_list) in on_prem_hosts.items():
            on_prem_ips.extend(ip_list)

        if on_prem_ips:
            executor_ip = on_prem_ips[0]  # Head node IP
            print(f"HPO Workflow {wf_plan['id']}: Using on-prem executor at {executor_ip}")
        else:
            # Cloud workflow: Create dedicated executor instance
            executor_ip = createExecutorInstance('g4dn.2xlarge', sim)

            if not executor_ip:
                print(f"Error: Could not create dedicated executor instance for {wf_plan['id']}")
                return

            print(f"HPO Workflow {wf_plan['id']}: Created cloud executor at {executor_ip}")

        # Prepare request with separated executor and worker instances
        request = {
            "initial-alloc": True,
            "wf-plan": wf_plan,
            "hosts": ips,  # Worker instances only
            "executor-ip": executor_ip,  # Dedicated executor (on-prem or cloud)
            "deadline": deadline
        }

        print(f"  Workers: {ips}")

        # Send to executor
        if sim:
            # In simulation mode, process directly
            from executor_HPO import executeWorkflowHPO
            sim.process(executeWorkflowHPO, request, sim)
        else:
            # Send to executor instance
            sendRequest(executor_ip, getConfig('executor-incoming-port'), request)