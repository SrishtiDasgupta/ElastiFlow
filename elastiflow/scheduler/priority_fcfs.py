import threading
import time

from elastiflow.config.constants import BUDGET_FACTOR, DEADLINE_FACTOR
from elastiflow.resource_manager.heft_rm import HEFTResourceManager
from elastiflow.scheduler.scheduler import Scheduler_Ordered

# Workflows are sorted based on priorities. Priorities are computed based on deadline and budget. Lower deadline and lower budget have lower rank and are executed first
class PriorityFCFS(Scheduler_Ordered):

    def __init__(self, queue, finish_queue, resource_request_queue):
        self.resource_manager = HEFTResourceManager()
        func = lambda x: (BUDGET_FACTOR/2.8) * x.getValue('cost_per_iteration') + (DEADLINE_FACTOR/2281) * x.getValue('runtime_per_iteration')
        # self.resource_manager.sortResources(sort_key)
        self.resource_manager.sortResourcesByFunction(func)
        super().__init__(queue, finish_queue, resource_request_queue)

    def orderWorkflows(self, workflows, backend):
        self.resource_manager.processWorkflowsByPriority(workflows, backend)

