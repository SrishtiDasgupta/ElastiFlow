from elastiflow.utils.exec_sched import getWorkflowConfig, removeWorkflowConfig, setWorkflowConfig
from .workflow import Workflow
from .steep.steep_parser import Steep_Parser


class Steep_Workflow(Workflow):
    """The Steep engine of every use case (B7.7): the plan's use case
    (elastiflow/usecase.py) supplies the action classes, so HPO's execute
    action with its own iteration runner needs no second engine."""

    def __init__(self, wf_plan, backend, deadline):
        super().__init__(wf_plan)
        setWorkflowConfig(self.id, self.plan, backend, deadline)
        foreach_action, execute_action = getWorkflowConfig(self.id)['use_case'].engine_actions()
        self.vars, self.actions = Steep_Parser(self.plan, foreach_action, execute_action).getTuple() # Tuple[List[Variable], List[Action]]

    def execute(self, hosts):
        new_hosts = {}
        for action in self.actions:
            new_hosts = action.execute(hosts)
        isComplete = removeWorkflowConfig(self.id)['complete']
        return new_hosts, isComplete
