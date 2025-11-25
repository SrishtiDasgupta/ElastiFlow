from abc import ABC, abstractmethod
from typing import List, Tuple

from config.constants import COLD_START_TIME
from utils.request import getConfig

class Instance(ABC):
    def __init__(self, name, runtime, cost, cores):
        self.runtime_per_iteration = runtime # 500 mesh
        self.cost_per_iteration = cost * runtime # 500 mesh
        self.cost_per_second = cost
        self.name = name
        self.cores = cores
       
    def getValue(self, key) -> any:
        return vars(self).get(key)
    
    def toString(self) -> str:
        return f"{self.name}: {self.free_slots}"
    
    @abstractmethod
    def allocate(self, n: int) -> Tuple[int, List[str]]:
        pass

    @abstractmethod
    def getFreeSlots(self) -> int:
        pass

    def getCostPerSecond(self) -> float:
        return self.cost_per_second

    @abstractmethod
    def freeResources(self, count: int, ips: List[str]):
        pass

class OnPremInstance(Instance):
    def __init__(self, name, runtime, cost, slots, ip, cores):
        self.free_slots = slots
        self.ip = ip
        self.type = 'on-prem'
        self.cold_start_cost = 0
        super().__init__(name, runtime, cost, cores)

    def allocate(self, n) -> List[str]:
        self.free_slots -= n
        return (n, self.ip)
    
    def getFreeSlots(self) -> int:
        return self.free_slots
    
    def freeResources(self, count, ips):
        self.free_slots += count
        
class CloudReservedInstance(Instance):
    def __init__(self, name, runtime, cost, ip, cores):
        self.free_slots = ip
        self.allocated_slots = []
        self.type = 'reserved'
        self.cold_start_cost = 0
        super().__init__(name, runtime, cost + getConfig('fsx-cost'), cores)
    
    # Update slots and return IPs
    def allocate(self, n: int) -> List[str]:
        ips = self.free_slots[:n]
        self.allocated_slots += ips
        self.free_slots = self.free_slots[n:]
        return (n, ips)

    def getFreeSlots(self) -> int:
        return len(self.free_slots)
    
    def freeResources(self, count, ips):
        self.free_slots += ips
        self.allocated_slots = list(set(self.allocated_slots) - set(ips))

class CloudOnDemandInstance(Instance):
    def __init__(self, name, runtime, cost, slots, cores):
        self.free_slots = slots
        self.type = 'on-demand'
        self.cold_start_cost = COLD_START_TIME * cost
        super().__init__(name, runtime, cost + getConfig('fsx-cost'), cores)

    def allocate(self, n: int) -> List[str]:
        self.free_slots -= n
        return (n, [])
    
    def getFreeSlots(self) -> int:
        return self.free_slots
    
    def freeResources(self, count, ips):
            self.free_slots += count
        

