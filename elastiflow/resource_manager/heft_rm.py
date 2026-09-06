import heapq
from typing import List
from elastiflow.config.constants import AVG_BUDGET, AVG_DEADLINE, AVG_TINYDA_ITERATIONS, AVG_WORKFLOW_ITERATIONS, BUDGET_FACTOR, DEADLINE_FACTOR, MIN_RUNTIME
from elastiflow.scripts.speedup import getRuntime
from elastiflow.utils.request import ExecutorRequest
from elastiflow.utils.resource import getEstimate
from elastiflow.resource_manager.resource_manager import ResourceManager
from elastiflow.execution.backend import backend_for

class HEFTResourceManager(ResourceManager):

    def __init__(self) -> None:
        # Max heap to store a sorted list of workflows
        self.workflow_heap = []
        self.resource_request_heap = []
        super().__init__()

    # Workflows are sorted based on computation and communication cost
    def processWorkflows(self, workflows: List[any]):
        for wf in workflows:
            wf_plan = eval(wf)
            # We need maxheap, but python gives minheap by default, so multiply -1
            if wf_plan['id'] == 'END':
                heapq.heappush(self.workflow_heap, (1, wf_plan['id'], wf_plan))
            else:
                # NOTE: We do not multiply per second cost since it will cancel out
                runtime = getRuntime(1, wf_plan['config']['mesh'], 'on-prem')
                exec_cost = (getEstimate(runtime, 1 + wf_plan['constraints']['tinydaIterations'], 1, chains = wf_plan['constraints']['chains'])
                + getEstimate(runtime, AVG_TINYDA_ITERATIONS, AVG_WORKFLOW_ITERATIONS-1, 4)) # Avg configs
                # In case of a tie, workflows are picked randomly, here with wf_id
                heapq.heappush(self.workflow_heap, (-exec_cost, wf_plan['id'], wf_plan))  

    # Workflows are sorted based on computation and communication cost
    def processResourceRequests(self, workflows: List[any]):
        for req in workflows:
            req = eval(req)
            if req['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                # We need maxheap, but python gives minheap by default, so multiply -1
                wf = self.getWorkflow(req['wf-id']) # (instances, budget, deadline, start_time, mesh)
                runtime = getRuntime(1, wf[4], wf[0][0][0].name) # instances = [(instance, alloc_n, ip_list)]
                exec_cost = (getEstimate(runtime, req['tinyda-iterations'], 1, chains = req['count'])
                + getEstimate(runtime, AVG_TINYDA_ITERATIONS, workflow_iterations = max(AVG_WORKFLOW_ITERATIONS - req['iteration'], 1)))
            else:
                exec_cost = 100000 # Free requests are always on top
            # In case of a tie, workflows are picked randomly, here with wf_id
            try:
                heapq.heappush(self.resource_request_heap, (-exec_cost, req['wf-id'], req))
            except:
                print('HEAP ERROR')
                print(self.resource_request_heap)

    def processWorkflowsByDeadline(self, workflows: List[any]):
        for wf in workflows:
            wf_plan = eval(wf)
            # We need a minheap with deadines
            if wf_plan['id'] == 'END':
                heapq.heappush(self.workflow_heap, (1000000, wf_plan['id'], wf_plan))
            else:
                deadline = wf_plan['submit_time'] + wf_plan['constraints']['deadline']
                # In case of a tie, workflows are picked randomly, here with wf_id
                heapq.heappush(self.workflow_heap, (deadline, wf_plan['id'], wf_plan))  

    def processResourceRequestsByDeadline(self, workflows: List[any]):
        for req in workflows:
            req = eval(req)
            if req['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                # Min heap with deadlines
                deadline = self.getWorkflow(req['wf-id'])[2] # (instances, budget, deadline, start_time, mesh)
            else:
                deadline = 0 # Free requests are always on top
            # In case of a tie, workflows are picked randomly, here with wf_id
            try:
                heapq.heappush(self.resource_request_heap, (deadline, req['wf-id'], req))
            except:
                print('HEAP ERROR')
                print(self.resource_request_heap)

    def processWorkflowsByPriority(self, workflows: List[any], sim):
        backend = backend_for(sim)
        for wf in workflows:
            wf_plan = eval(wf)
            # Priority rank = a * budget + b * deadline
            # Lower rank is executed first
            if wf_plan['id'] == 'END':
                heapq.heappush(self.workflow_heap, (1000000, wf_plan['id'], wf_plan))
            else:
                budget_factor = BUDGET_FACTOR / AVG_BUDGET[wf_plan['config']['mesh']]
                deadline_factor = DEADLINE_FACTOR / AVG_DEADLINE[wf_plan['config']['mesh']]
                priority = budget_factor * wf_plan['constraints']['budget'] + deadline_factor * (wf_plan['submit_time'] + wf_plan['constraints']['deadline'] - backend.now())
                # In case of a tie, workflows are picked randomly, here with wf_id
                heapq.heappush(self.workflow_heap, (priority, wf_plan['id'], wf_plan)) 
                # print(budget_factor * wf_plan['constraints']['budget'], deadline_factor * (wf_plan['submit_time'] + wf_plan['constraints']['deadline'] - backend.now()), priority)

    def processResourceRequestsByPriority(self, workflows: List[any], metrics_obj, sim):
        backend = backend_for(sim)
        for req in workflows:
            req = eval(req)
            if req['request'] == ExecutorRequest.REQUEST_RESOURCE.value:
                # lower priority ranks (low budget, low runtime) must be executed first - min heap
                wf = self.getWorkflow(req['wf-id']) # (instances, budget, deadline, start_time, mesh)
                used_budget = metrics_obj.computeCost(req['wf-id'], backend.now())
                available_budget = max(0, wf[1] - used_budget)
                available_time = max(0, wf[2] - backend.now())
                # Normalize budget and deadline
                budget_factor = BUDGET_FACTOR / AVG_BUDGET[wf[4]]
                deadline_factor = DEADLINE_FACTOR / AVG_DEADLINE[wf[4]]
                priority = budget_factor * available_budget + deadline_factor * available_time
            else:
                priority = 0 # Free requests are always on top
            # In case of a tie, workflows are picked randomly, here with wf_id
            try :
                heapq.heappush(self.resource_request_heap, (priority, req['wf-id'], req))
            except:
                print('HEAP ERROR')
                print(self.resource_request_heap)
    
    def peekWorkflow(self, heap):
        return heap and heap[0][2] 

    def popWorkflow(self, heap):
        try:
            heapq.heappop(heap) 
        except:
            print('HEAP ERROR')
            print(heap)         
        

