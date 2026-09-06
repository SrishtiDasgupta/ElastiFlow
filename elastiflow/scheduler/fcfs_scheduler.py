import threading
import time

from elastiflow.resource_manager.resource_manager import ResourceManager
from elastiflow.scheduler.scheduler import Scheduler

# If requested resources are available, they are granted. Else the workflow waits
class FCFS_Scheduler(Scheduler):

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        self.resource_manager = ResourceManager()
        self.resource_manager.sortResources(sort_key)
        super().__init__(queue, finish_queue, resource_request_queue)

    def printBanner(self, backend):
        print(f'Starting scheduler at {backend.now()}...')

    def printAllocation(self, wf_plan, ips, backend):
        print(f"{wf_plan['id']} allocated at {backend.now()}:", ips)


