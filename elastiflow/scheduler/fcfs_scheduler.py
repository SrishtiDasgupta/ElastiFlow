import threading
import time

from elastiflow.config.constants import WORKFLOW_POLLING
from elastiflow.utils.request import ExecutorRequest
from elastiflow.resource_manager.resource_manager import ResourceManager
from elastiflow.utils.resource import getConstraintsFromWorkflow
from elastiflow.scheduler.scheduler import Scheduler
from elastiflow.execution.backend import backend_for

# If requested resources are available, they are granted. Else the workflow waits
class FCFS_Scheduler(Scheduler):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = ResourceManager()
        self.resource_manager.sortResources(sort_key)
        super().__init__(queue, finish_queue, resource_request_queue)


    def run(self, sim = None, wf_mb = None, resource_request_mb = None):
        
        backend = backend_for(sim)
        print(f'Starting scheduler at {backend.now()}...')

        # Start a thread to periodically compute resource utilization
        backend.spawn(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        
        while True:

            # Check queue for resource requests
            resource_request = backend.resource_requests.peek()

            if resource_request:
                # Allocate new resources
                resource_request = eval(resource_request)
                if resource_request['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                    self.allocateNewResources(resource_request, sim)
                else:
                    self.freeResources(resource_request, sim)
                backend.resource_requests.pop()
                sim and backend.sleep(0.2) # NOTE: scheduler overhead
                continue
            
            # Check the queue for new jobs
            workflow_plan = backend.workflows.peek()

            if workflow_plan:
                wf_plan = eval(workflow_plan) # Convert string back to dictionary

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    backend.workflows.pop()
                    self.metrics.computeMetrics()
                    break 

                # If workflow cannot be executed, pop it to prevent stagnation
                # if self.purgeWorkflow(wf_plan, sim):
                #     backend.workflows.pop()
                #     self.resource_manager.setResourcesAvailable(True)
                #     continue
            
                # Scheduling
                if self.resource_manager.getResourcesAvailable():

                    constraints = getConstraintsFromWorkflow(wf_plan)
                    ips, alloc_resources = self.allocateResources(constraints)
                    print(f"{wf_plan['id']} allocated at {backend.now()}:", ips)              

                    # Remove the element if we found the resources needed.
                    if ips:
                        backend.workflows.pop()
                        # NOTE: We start billing at this point
                        start_time = backend.now()
                        self.sendWorkflowForExecution(wf_plan, ips, sim, constraints['deadline'])
                        wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                        self.metrics.addToDataframe(wf_plan['id'], wf, wf_plan['submit_time'])
                    else:
                        # Wait until resources become available
                        self.resource_manager.setResourcesAvailable(False)
                        # NOTE: Can also suspend process and resume when resources are available again 
                        print('No resources to allocate, waiting...')
            
            backend.sleep(WORKFLOW_POLLING)


