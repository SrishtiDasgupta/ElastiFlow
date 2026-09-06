import threading
import time
from elastiflow.config.constants import SORT_COUNT, WORKFLOW_POLLING
from elastiflow.utils.request import ExecutorRequest
from elastiflow.utils.sim import getAllElements
from elastiflow.resource_manager.heft_rm import HEFTResourceManager
from elastiflow.utils.resource import getConstraintsFromWorkflow
from elastiflow.scheduler.scheduler import Scheduler
from elastiflow.execution.backend import backend_for

# Workflows are sorted based on computation and communication cost
# Workflow with the highest cost is allocated to fastest available instance
class HEFT_HEFT_REQ(Scheduler):
    
    def __init__(self, queue, finish_queue, resource_request_queue):
        self.resource_manager = HEFTResourceManager()
        # For HEFT, we need the fastest nodes
        self.resource_manager.sortResources('runtime_per_iteration')
        super().__init__(queue, finish_queue, resource_request_queue)


    def run(self, sim = None, wf_mb = None, resource_request_mb = None):
       
        backend = backend_for(sim)
        print(f'Starting scheduler...')

        # Start a thread to periodically compute resource utilization
        if sim:
            sim.process(self.metrics.collectResourceUtilization, sim, self.resource_manager)
        else:
            thread = threading.Thread(target=self.metrics.collectResourceUtilization, args=[sim, self.resource_manager])
            thread.start()

        while True:

            # Check queue for resource requests
            resource_requests = getAllElements(resource_request_mb, self.resource_request_queue, SORT_COUNT)
            # Sort and update resource requests list 
            self.resource_manager.processResourceRequests(resource_requests)
            resource_request = self.resource_manager.peekWorkflow(self.resource_manager.resource_request_heap)

            if resource_request:
                # Moldable scale-up / scale-down via rich base-class
                # negotiation (processFreeRequest decides internally).
                self.processFreeRequest(resource_request, sim)
                self.resource_manager.popWorkflow(self.resource_manager.resource_request_heap)
                sim and backend.sleep(0.2) # NOTE: scheduler overhead
                continue

            # Retrieve all new jobs in the queue
            workflows = getAllElements(wf_mb, self.queue, SORT_COUNT)
            # Sort and update workflow list 
            self.resource_manager.processWorkflows(workflows)

            # Check the processed queue for new jobs
            wf_plan = self.resource_manager.peekWorkflow(self.resource_manager.workflow_heap)

            if wf_plan:

                # End the simulation and compute metrics
                if wf_plan['id'] == 'END':
                    self.resource_manager.popWorkflow(self.resource_manager.workflow_heap)
                    self.metrics.computeMetrics()
                    break 

                # If workflow cannot be executed, pop it to prevent stagnation
                if self.purgeWorkflow(wf_plan, sim): 
                    self.resource_manager.popWorkflow(self.resource_manager.workflow_heap)
                    continue

                # Scheduling
                constraints = getConstraintsFromWorkflow(wf_plan)
                ips, alloc_resources = self.allocateResources(constraints)
                print(f"{wf_plan['id']} allocated: ", ips)
               
                # Remove the element if we found the resources needed.
                if ips:
                    self.resource_manager.popWorkflow(self.resource_manager.workflow_heap)
                    # NOTE: We start billing at this point
                    start_time = backend.now()
                    self.sendWorkflowForExecution(wf_plan, ips, sim, constraints['deadline'])
                    wf = self.resource_manager.addWorkflow(wf_plan['id'], alloc_resources, constraints['budget'], constraints['deadline'], start_time, constraints['mesh'])
                    self.metrics.addToDataframe(wf_plan['id'], wf, submit_time=wf_plan['submit_time'])

                else:
                    print('No resources to allocate, waiting...')

            backend.sleep(WORKFLOW_POLLING)

        