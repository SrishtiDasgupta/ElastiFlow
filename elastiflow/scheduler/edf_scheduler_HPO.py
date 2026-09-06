"""
EDF_Scheduler_HPO: Static EDF Scheduler for HPO Workflows

Static (non-moldable) Earliest Deadline First scheduler for HPO.
Resources allocated once at workflow start - no scale-up/down.
Workflows processed in EDF order (earliest deadline first).

Baseline scheduler for comparison with:
- edf_optimized_HPO.py (Moldable EDF with deadline urgency)
- fcfs_optimized_HPO.py (Moldable FCFS)
"""

import threading
import time

from elastiflow.config.constants_HPO import COLD_START_TIME
from elastiflow.utils.request import sendRequest, getConfig
import os
from elastiflow.resource_manager.resource_manager import ResourceManager
from elastiflow.resource_manager.instance import CloudOnDemandInstance

_HPO_RESOURCES_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'resources_HPO.yaml')
from elastiflow.scheduler.scheduler import EDFOrderingMixin
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO_Static


class EDF_Scheduler_HPO(EDFOrderingMixin, Scheduler_HPO_Static):
    """
    Static EDF scheduler for HPO workflows.

    Workflows processed in EDF order (earliest deadline first).
    Resources allocated once at workflow start, no reallocation.
    """

    policy_label = 'EDF '

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost',
                 resource_config=None, file_prefix=None):
        self.resource_manager = ResourceManager(resource_config or _HPO_RESOURCES_DEFAULT)
        self.file_prefix = file_prefix or 'EDF_Static_HPO_'
        func = lambda x: x.cost if hasattr(x, 'cost') else 0
        self.resource_manager.sortResourcesByFunction(func)

        # EDF heap setup
        self.workflow_heap = []
        self.workflow_counter = 0

        super().__init__(queue, finish_queue, resource_request_queue)

    def printBanner(self, backend):
        print(f'Starting HPO EDF Static scheduler at {backend.now()}...')
        print(f'  - Non-moldable: Resources allocated once at workflow start')
        print(f'  - EDF ordering: Workflows prioritized by earliest deadline')

    def nextWorkflow(self, backend):
        # Collect new workflows and sort by deadline (EDF)
        workflows = backend.workflows.pop_many(None)
        if workflows:
            self.processWorkflowsByDeadline(workflows)
        # Process heap (even if no new workflows)
        return self.peekWorkflow(self.workflow_heap)

    def dropWorkflow(self, backend):
        self.popWorkflow(self.workflow_heap)
        backend.workflows.pop()

    def printAllocation(self, wf_plan, ips, backend, constraints=None):
        print(f"{wf_plan['id']} allocated at {backend.now()} (deadline={constraints['deadline']:.1f}s):", ips)

    # =========================================================================
    # EDF HEAP MANAGEMENT
    # =========================================================================

    # =========================================================================
    # HPO RESOURCE ALLOCATION (same as fcfs_scheduler_HPO.py)
    # =========================================================================

