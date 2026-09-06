import threading
import time

from elastiflow.resource_manager.heft_rm import HEFTResourceManager
from elastiflow.scheduler.scheduler import Scheduler_Ordered

# If requested resources are available, they are granted. Else the workflow waits
class EarliestDeadlineEDF(Scheduler_Ordered):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = HEFTResourceManager()
        self.resource_manager.sortResources(sort_key)
        super().__init__(queue, finish_queue, resource_request_queue)

    request_heap = True

    def orderWorkflows(self, workflows, backend):
        self.resource_manager.processWorkflowsByDeadline(workflows)

    def orderRequests(self, requests, backend):
        self.resource_manager.processResourceRequestsByDeadline(requests)

