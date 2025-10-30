import pandas as pd
import time

from config.constants import RESOURCE_UTILIZATION_POLLING, TOTAL_RESOURCES, TOTAL_WORKFLOWS
from resource_manager.instance import CloudReservedInstance, OnPremInstance
from resource_manager.resource_manager import ResourceManager

class Metrics():

    def __init__(self):
        self.df = {}
        self.free_resources = []
        self.collectFlag = True

    def addToDataframe(self, id, wf, submit_time: float):
        instances = self.processInstances(wf[0], wf[3])
        data = {
            'instances': instances,
            'budget': wf[1],
            'deadline': wf[2],
            'sched_start_time': wf[3],
            'submit_time': submit_time
        }
        self.df[id] = data

    def updateDataframe(self, id, obj):
       data = self.df[id]
       for key in obj:
           data[key] = obj[key]
       self.df[id] = data

    def updateResources(self, id, instances, start_time, finish_time = None):
        new_instances = self.processInstances(instances, start_time, finish_time)
        # Make instances of the form {obj: [(), ()])}
        for instance in new_instances:
            self.df[id]['instances'][instance] = self.df[id]['instances'].get(instance, []) + new_instances[instance]
    
    # wf[0] = instances : [(instanceObj, count, ips)]
    # store instance in the form (count, start_time, finish_time)
    def processInstances(self, instances, start_time, finish_time = None):
        new_instances = {}
        for instance, count, ips in instances:
            new_instances[instance] = [(count, start_time, finish_time)]
        return new_instances

    def computeCost(self, id, wf_finish_time):
        cost = 0
        instances = self.df[id]['instances'] # {obj: [(), ()]}
        for instance in instances:
            instance_list = instances[instance]
            for count, start_time, finish_time in instance_list:
                if start_time: # Newly added resource
                    cost += instance.getCostPerSecond() * count * (wf_finish_time - start_time)
                if finish_time: # Freed resource
                    cost -= instance.getCostPerSecond() * count * (wf_finish_time - finish_time)
        return cost

    def computeMetrics(self):
        print('Computing metrics...')
        self.collectFlag = False
        resource_df = pd.DataFrame(self.free_resources, columns=['Timestamp', 'On-prem', 'Cloud'])
        resource_df.to_csv('resources.csv')
        makespan, waitTime, cost = 0, 0, 0
        budget_miss, deadline_miss, overall_miss = 0, 0, 0
        executed_workflows = 0
        wasted_cost, wasted_time = 0, 0

        for wf in self.df:
            if self.df[wf]['complete']:
                executed_workflows += 1
                makespan += self.df[wf]['finish_time'] - self.df[wf]['submit_time']
                waitTime += self.df[wf]['exec_start_time'] - self.df[wf]['submit_time']
                self.df[wf]['cost'] = self.computeCost(wf, self.df[wf]['finish_time'])
                cost += self.df[wf]['cost']
                budget = self.df[wf]['cost'] > self.df[wf]['budget']
                deadline = self.df[wf]['finish_time'] > self.df[wf]['deadline']
                budget_miss += budget
                deadline_miss += deadline
                overall_miss += (budget or deadline)
            else:
                wasted_cost += self.computeCost(wf, self.df[wf]['finish_time'])
                wasted_time += self.df[wf]['finish_time'] - self.df[wf]['exec_start_time']
        
        print(f'Total workflows = {TOTAL_WORKFLOWS}')
        print(f'Executed workflows = {executed_workflows}')
        
        print(f'Average Flowtime = {round(makespan/executed_workflows, 4)}')
        print(f'Average Cost = {round(cost/executed_workflows, 4)}')
        print(f'Average Wait Time = {round(waitTime/executed_workflows, 4)}')
        print(f'Average resourcs utilized = {round(self.computeResourceUtilization(), 4)}')
        print(f'Deadline miss rate = {round((deadline_miss + TOTAL_WORKFLOWS - executed_workflows)/TOTAL_WORKFLOWS, 4)}')
        print(f'Budget miss rate = {round(budget_miss/TOTAL_WORKFLOWS, 4)}')
        print(f'Overall miss rate = {round((overall_miss + TOTAL_WORKFLOWS - executed_workflows)/TOTAL_WORKFLOWS, 4)}')
        print(f'Time spent on incomplete workflows = {round(wasted_time / (60*60), 2)} hours')
        print(f'Wasted cost on incomplete workflows = {round(wasted_cost, 2)}')       

        df = pd.DataFrame(self.df).T
        df.to_csv('results.csv')
        exit()

    def collectResourceUtilization(self, sim, rm: ResourceManager):
        while self.collectFlag:
            resources = rm.getResources()
            onprem, cloud = 0, 0
            # Find current free resources
            for instance in resources:
                if isinstance(instance, OnPremInstance):
                    onprem += instance.getFreeSlots()
                elif isinstance(instance, CloudReservedInstance):
                    cloud += instance.getFreeSlots()
            self.free_resources.append(((sim and sim.now) or time.time(), onprem, cloud))
            (sim or time).sleep(RESOURCE_UTILIZATION_POLLING)

    def computeResourceUtilization(self):
        
        start, stop, n = 0, 0, len(self.free_resources)
        for i in range(n):
            if self.free_resources[i][1] + self.free_resources[i][2] < TOTAL_RESOURCES:
                start = i
                break
        for i in range(n-1, -1, -1):
            if self.free_resources[i][1] + self.free_resources[i][2] < TOTAL_RESOURCES:
                stop = i
                break
        
        free = 0
        for i in range(start, stop + 1):
            free += self.free_resources[i][1] + self.free_resources[i][2]
        
        return ((TOTAL_RESOURCES * (stop-start+1)) - free) / (stop-start+1)