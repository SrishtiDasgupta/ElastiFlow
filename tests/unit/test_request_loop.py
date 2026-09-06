"""The request-loop skeleton (B7.4): the SeisSol and licence policies no longer
carry a `run` of their own; each is the base skeleton plus the hooks listed here."""
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


# --- the licence family --------------------------------------------------------------
from elastiflow.scheduler.scheduler_LA import Scheduler_LA  # noqa: E402
from elastiflow.scheduler.fcfs_scheduler_LA import FCFS_Scheduler_LA  # noqa: E402
from elastiflow.scheduler.edf_scheduler_LA import EDF_Scheduler_LA  # noqa: E402
from elastiflow.scheduler.fcfs_optimized_LA import FCFS_Optimized_LA  # noqa: E402
from elastiflow.scheduler.edf_optimized_LA import EDF_Optimized_LA  # noqa: E402
from elastiflow.scheduler.edf_hsm_LA import EDF_HSM_LA  # noqa: E402

LICENCE = (FCFS_Scheduler_LA, EDF_Scheduler_LA, FCFS_Optimized_LA, EDF_Optimized_LA, EDF_HSM_LA)


def test_licence_policies_run_the_skeleton():
    for cls in LICENCE:
        assert 'run' not in cls.__dict__ and cls.run is Scheduler.run and cls.admit is Scheduler_LA.admit, cls
    assert [c.metrics_prefix for c in LICENCE] == ['Baseline_', 'EDF_Static_', 'LAMF_', 'EDF_', 'EDF_HSM_']
    assert [c.drop_rejected_now for c in LICENCE] == [False, True, False, False, False]


def test_licence_hooks_per_policy():
    # FCFS-ST-LA: the layer's defaults (requests by type, queue order, block on shortage)
    assert FCFS_Scheduler_LA.serviceResourceRequests is Scheduler.serviceResourceRequests
    assert FCFS_Scheduler_LA.nextWorkflow is Scheduler.nextWorkflow and FCFS_Scheduler_LA.wait is Scheduler_LA.wait
    # EDF-ST-LA: warns on scale-up requests, heap order, its own rejection check
    for name in ('handleRequest', 'nextWorkflow', 'dropWorkflow', 'rejectIfImpossible', 'wait'):
        assert name in EDF_Scheduler_LA.__dict__, name
    # FCFS-LAMF: timeout-dropping request phase, initial-allocation bookkeeping, licence-shortage wait
    for name in ('serviceResourceRequests', 'computeFinalMetrics', 'afterAdmission', 'wait'):
        assert name in FCFS_Optimized_LA.__dict__, name
    # EDF-LAMF: idle termination in beginCycle, heap-served requests, impossibility by isWorkflowImpossible
    for name in ('beforeLoop', 'beginCycle', 'serviceResourceRequests', 'nextWorkflow', 'whenRefused', 'waitForResources', 'waitNoResources', 'whenUnavailable'):
        assert name in EDF_Optimized_LA.__dict__, name
    # HSM: EDF-LAMF's loop with its own banner, prefix and non-blocking waits
    assert set(EDF_HSM_LA.__dict__) & {'serviceResourceRequests', 'nextWorkflow', 'whenRefused', 'beginCycle'} == set()
    assert 'waitForResources' in EDF_HSM_LA.__dict__ and 'waitNoResources' in EDF_HSM_LA.__dict__
