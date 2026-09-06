import threading
import time

from elastiflow.config.constants_HPO import COLD_START_TIME
from elastiflow.utils.request import sendRequest, getConfig
import os
from elastiflow.resource_manager.resource_manager import ResourceManager
from elastiflow.resource_manager.instance import CloudOnDemandInstance

_HPO_RESOURCES_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
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

    def printBanner(self, backend):
        print(f'Starting HPO FCFS scheduler at {backend.now()}...')

