import threading
import time

from elastiflow.config.constants_HPO import WORKFLOW_POLLING, COLD_START_TIME
from elastiflow.utils.request import ExecutorRequest, sendRequest, getConfig
import os
from elastiflow.resource_manager.resource_manager import ResourceManager
from elastiflow.resource_manager.instance import CloudOnDemandInstance

_HPO_RESOURCES_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from elastiflow.utils.resource import getConstraintsFromWorkflow
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO_Static

# HPO-specific FCFS Scheduler with Dedicated Executor Design
# Static version - no moldable resource allocation
class FCFS_Scheduler_HPO(Scheduler_HPO_Static):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost',
                 resource_config=None, file_prefix=None):
        self.resource_manager = ResourceManager(resource_config or _HPO_RESOURCES_DEFAULT)
        self.file_prefix = file_prefix or 'FCFS_Static_HPO_'
        # Sort by base cost (hourly rate) - cost_per_trial calculated during allocation
        func = lambda x: x.cost if hasattr(x, 'cost') else 0
        self.resource_manager.sortResourcesByFunction(func)
        super().__init__(queue, finish_queue, resource_request_queue)

    def run(self, backend):
        print(f'Starting HPO FCFS scheduler at {backend.now()}...')

        # Start a thread to periodically compute resource utilization
        backend.spawn(self.metrics.collectResourceUtilization, backend, self.resource_manager)

        while True:

            # Check queue for resource requests (minimal for static version)
            resource_request = backend.resource_requests.peek()

            if resource_request:
                # Static scheduler - limited resource request handling
                resource_request = eval(resource_request)
                print(f"HPO Static Scheduler: Resource request received but ignored (static mode)")
                backend.resource_requests.pop()
                continue

            # Check the queue for new jobs
            workflow_plan = backend.workflows.peek()

            if workflow_plan:
                wf_plan = eval(workflow_plan) # Convert string back to dictionary

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    backend.workflows.pop()
                    self.metrics.computeMetrics(file_prefix=self.file_prefix)
                    break

                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResourcesHPO(constraints, backend)
                    print(f"{wf_plan['id']} allocated at {backend.now()}:", ips)

                    # Remove the element if we found the resources needed.
                    if ips:
                        backend.workflows.pop()
                        # NOTE: We start billing at this point
                        start_time = backend.now()
                        self.sendWorkflowForExecutionHPO(wf_plan, ips, backend, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        print('No HPO resources to allocate, waiting...')

            backend.sleep(WORKFLOW_POLLING)

