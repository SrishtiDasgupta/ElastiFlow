import threading
import time
from elastiflow.resource_manager.heft_rm import HEFTResourceManager
from elastiflow.scheduler.scheduler import Scheduler_Ordered

# Workflows are sorted based on computation and communication cost
# Workflow with the highest cost is allocated to fastest available instance
class HEFT_HEFT_REQ(Scheduler_Ordered):
    
    def __init__(self, queue, finish_queue, resource_request_queue):
        self.resource_manager = HEFTResourceManager()
        # For HEFT, we need the fastest nodes
        self.resource_manager.sortResources('runtime_per_iteration')
        super().__init__(queue, finish_queue, resource_request_queue)

    request_heap = True

    def orderWorkflows(self, workflows, backend):
        self.resource_manager.processWorkflows(workflows)

    def orderRequests(self, requests, backend):
        self.resource_manager.processResourceRequests(requests)


