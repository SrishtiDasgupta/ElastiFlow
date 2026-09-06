import threading
import time
from elastiflow.scripts.speedup import getRuntime
from elastiflow.resource_manager.heft_rm import HEFTResourceManager
from elastiflow.scheduler.scheduler import Scheduler_Ordered

# Workflows are sorted based on computation and communication cost
# Workflow with the highest cost is allocated to fastest available instance
class HEFT_FCFS_REQ(Scheduler_Ordered):
    
    def __init__(self, queue, finish_queue, resource_request_queue):
        self.resource_manager = HEFTResourceManager()
        # For HEFT, we need the fastest nodes
        self.resource_manager.sortResources('runtime_per_iteration')
        super().__init__(queue, finish_queue, resource_request_queue)
        # Maintain sorted lists based on mesh size
        self.node_resources_1000 = sorted(self.resource_manager.getResources(), key = lambda x: getRuntime(1, 1000, x.getValue('name')))
        self.node_resources_750 = sorted(self.resource_manager.getResources(), key = lambda x: getRuntime(1, 1000, x.getValue('name')))

    def orderWorkflows(self, workflows, backend):
        self.resource_manager.processWorkflows(workflows)

    def allocateResources(self, constraints):
        ips, alloc_resources = {}, []
        match constraints['mesh']:
            case 1000:
                instances = self.node_resources_1000
            case 750:
                instances = self.node_resources_750
            case 500:
                instances = self.resource_manager.getResources()
        count, instances = self.checkResources(instances, constraints['min_instances'])
        if count == constraints['min_instances']:
            ips, alloc_resources = self.resource_manager.allocateResources(instances)
        return ips, alloc_resources
    
