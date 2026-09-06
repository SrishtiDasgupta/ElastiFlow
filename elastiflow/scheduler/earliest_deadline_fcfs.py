import threading
import time

from elastiflow.resource_manager.heft_rm import HEFTResourceManager
from elastiflow.scheduler.scheduler import Scheduler_Ordered

# Workflows are sorted based on their deadline
class EarliestDeadlineFCFS(Scheduler_Ordered):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = HEFTResourceManager()
        self.resource_manager.sortResources(sort_key)
        super().__init__(queue, finish_queue, resource_request_queue)

    def orderWorkflows(self, workflows, backend):
        self.resource_manager.processWorkflowsByDeadline(workflows)

