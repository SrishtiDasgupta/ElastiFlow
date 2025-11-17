from abc import ABC
from typing import List, Tuple

import yaml

from .instance import CloudOnDemandInstance, CloudReservedInstance, Instance, OnPremInstance

class ResourceManager(ABC):

    def __init__(self, path_to_resources = '/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/resources.yaml') -> None:
        with open(path_to_resources, 'r') as file:
            defined_resources = yaml.safe_load(file)

        self.resourcesAvailable = True
        self.node_resources = []

        # Update on-prem
        type = defined_resources['on-prem'][0]
        instance = OnPremInstance(
                    type.get('name'),
                    type.get('runtime'),
                    type.get('reserved-cost'),
                    type.get('slots'),
                    type.get('ip')
                    )
        self.node_resources.append(instance)
        
        # Update cloud
        for type in defined_resources['cloud']:
            reserved = CloudReservedInstance(
                type.get('name'),
                type.get('runtime'), 
                type.get('reserved-cost'), 
                ip=type.get('ip')
                )
            ondemand = CloudOnDemandInstance(
                type.get('name'),
                type.get('runtime'), 
                type.get('on-demand-cost'),
                slots=type.get('on-demand-slots')
            )
            self.node_resources.append(reserved)
            self.node_resources.append(ondemand)
        
        self.workflows = {} # Maintain budget, deadline and allocated resources information of each workflow
    
    def sortResources(self, key):
        self.node_resources.sort(key = lambda x: x.getValue(key))
    
    def sortResourcesByFunction(self, func):
        self.node_resources.sort(key = func)
        # for instance in self.node_resources:
        #     print(instance.name, instance.cost_per_iteration, instance.runtime_per_iteration)

    def getResources(self):
        return self.node_resources
    
    def setResourcesAvailable(self, flag):
        self.resourcesAvailable = flag
    
    def getResourcesAvailable(self) -> bool:
        return self.resourcesAvailable
    
    def addWorkflow(self, id, instances, budget, deadline, start_time, mesh):
        self.workflows[id] = (instances, budget, deadline, start_time, mesh) # instances = [(instance, alloc_n, ip_list)]
        return self.workflows[id]

    def updateWorkflowResources(self, id, new_instances):
        wf = list(self.workflows[id])
        # instances = [(obj, count, [ips])]
        instances = wf[0] + new_instances
        # Merge the old and new instances with a dict
        merged_dict = {}
        for instance, count, ips in instances:
            _, cur_count, cur_ips = merged_dict.get(instance, (instance, 0, []))
            merged_dict[instance] = (instance, cur_count + count, cur_ips + ips)
        
        wf[0] = list(merged_dict.values())
        self.workflows[id] = tuple(wf)
    
    def updateFreedResources(self, id, instances):
        wf = list(self.workflows[id])
        wf[0] = instances
        self.workflows[id] = tuple(wf)
    
    def getWorkflow(self, id):
        return self.workflows.get(id, None)
    
    # return ips in the form {'on-prem': {}, 'reserved': {name: (count, [ips])}, 'on-demand': {}}
    def allocateResources(self, instances: List[tuple[Instance, int]]) -> Tuple[dict, List[tuple[Instance, int, List]]]:
        ips = {'on-prem': {}, 'reserved': {}, 'on-demand': {}}
        for i in range(len(instances)):
            instance, count = instances[i]
            alloc_n, ip_list = instance.allocate(count) # (count, [ips])
            ips[instance.type][instance.name] = (alloc_n, ip_list)
            instances[i] = (instance, alloc_n, ip_list)    
        return (ips, instances)
          
    def returnResources(self, id, instances = []):
        instances = instances or self.workflows.pop(id)[0]
        for instance, count, ips  in instances:
            instance.freeResources(count, ips)
        self.setResourcesAvailable(True)
    