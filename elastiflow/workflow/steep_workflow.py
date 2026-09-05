from elastiflow.utils.exec_sched import removeWorkflowConfig, setWorkflowConfig
from .workflow import Workflow
from .steep.steep_actions import *
from .steep.steep_variables  import *
from .steep.steep_parser import *

class Steep_Workflow(Workflow):

    def __init__(self, wf_plan, sim, deadline):
        super().__init__(wf_plan)
        setWorkflowConfig(self.id, self.plan, sim, deadline)
        self.vars, self.actions  = Steep_Parser(self.plan).getTuple() # Tuple[List[Variable], List[Action]]
            
    def execute(self, hosts):
        new_hosts = {}
        for action in self.actions:
            new_hosts = action.execute(hosts)
        isComplete = removeWorkflowConfig(self.id)['complete']
        return new_hosts, isComplete

        


