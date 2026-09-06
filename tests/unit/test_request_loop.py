"""The request-loop skeleton (B7.4): the SeisSol policies no longer carry a
`run` of their own; each is the base skeleton plus the hooks listed here."""
from elastiflow.scheduler.scheduler import Scheduler, Scheduler_Ordered
from elastiflow.scheduler.fcfs_scheduler import FCFS_Scheduler
from elastiflow.scheduler.fcfs_optimized import FCFS_Optimized
from elastiflow.scheduler.earliest_deadline_fcfs import EarliestDeadlineFCFS
from elastiflow.scheduler.priority_fcfs import PriorityFCFS
from elastiflow.scheduler.heft_fcfs_req import HEFT_FCFS_REQ
from elastiflow.scheduler.earliest_deadline_edf import EarliestDeadlineEDF
from elastiflow.scheduler.priority_priority import PriorityPriority
from elastiflow.scheduler.heft_heft_req import HEFT_HEFT_REQ

SEISSOL = (FCFS_Scheduler, FCFS_Optimized, EarliestDeadlineFCFS, PriorityFCFS, HEFT_FCFS_REQ, EarliestDeadlineEDF, PriorityPriority, HEFT_HEFT_REQ)


def test_seissol_policies_run_the_skeleton():
    for cls in SEISSOL:
        assert 'run' not in cls.__dict__ and cls.run is Scheduler.run, cls


def test_hooks_per_policy():
    # the two queue-order policies: the base defaults, with their own log lines and request phase
    assert 'printBanner' in FCFS_Scheduler.__dict__ and 'printAllocation' in FCFS_Scheduler.__dict__
    assert 'serviceResourceRequests' in FCFS_Optimized.__dict__ and FCFS_Optimized.admit is Scheduler.admit
    # the six heap-order policies
    for cls, req_heap, overhead in ((EarliestDeadlineFCFS, False, True), (PriorityFCFS, False, True), (HEFT_FCFS_REQ, False, True),
                                    (EarliestDeadlineEDF, True, True), (PriorityPriority, True, False), (HEFT_HEFT_REQ, True, True)):
        assert issubclass(cls, Scheduler_Ordered) and 'orderWorkflows' in cls.__dict__, cls
        assert (cls.request_heap, cls.scheduler_overhead) == (req_heap, overhead), cls
        assert ('orderRequests' in cls.__dict__) == req_heap, cls
        assert cls.admit is Scheduler_Ordered.admit and cls.nextWorkflow is Scheduler_Ordered.nextWorkflow, cls
