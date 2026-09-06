"""One scheduler base (B7.1): the licence and HPO layers subclass `Scheduler`,
the methods they used to duplicate resolve to the base's, and their
per-family bindings are the two class attributes."""
from elastiflow.policies import POLICIES
from elastiflow.scheduler.scheduler import Scheduler
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO
from elastiflow.scheduler.scheduler_LA import Scheduler_LA
from elastiflow.utils.metrics import Metrics
from elastiflow.utils.metrics_HPO import MetricsHPO
from elastiflow.utils.metrics_LA import MetricsLA


def test_layers_subclass_the_base():
    assert issubclass(Scheduler_LA, Scheduler) and issubclass(Scheduler_HPO, Scheduler)
    assert (Scheduler.metrics_class, Scheduler_LA.metrics_class, Scheduler_HPO.metrics_class) == (Metrics, MetricsLA, MetricsHPO)
    assert (Scheduler.log_prefix, Scheduler_LA.log_prefix, Scheduler_HPO.log_prefix) == ('', '', 'HPO ')


def test_shared_methods_are_the_base_methods():
    for name in ('run', 'allocateResources', 'checkResources', 'purgeWorkflow'):
        assert getattr(Scheduler_LA, name) is getattr(Scheduler, name), name
    for name in ('__init__', 'run', 'allocateResources', 'sendWorkflowForExecution', 'sendNewResources',
                 'sendFreedResources', 'checkResources', 'purgeWorkflow'):
        assert getattr(Scheduler_HPO, name) is getattr(Scheduler, name), name


def test_layers_keep_their_own_versions_of_what_differs():
    for name in ('sendWorkflowForExecution', 'sendNewResources', 'sendFreedResources', 'allocateNewResources',
                 'freeResources', 'checkNewResources', 'checkCloseness', 'processJobCompletion'):
        assert getattr(Scheduler_LA, name) is not getattr(Scheduler, name), name
    for name in ('allocateNewResources', 'freeResources', 'checkNewResources', 'processJobCompletion'):
        assert getattr(Scheduler_HPO, name) is not getattr(Scheduler, name), name


def test_every_policy_class_is_a_scheduler():
    for p in POLICIES:
        assert issubclass(p.load(), Scheduler), p.name


# --- B7.2: the layers inside each family ------------------------------------------
from elastiflow.scheduler.scheduler import EDFOrderingMixin  # noqa: E402
from elastiflow.scheduler.scheduler_HPO import Scheduler_HPO_Elastic, Scheduler_HPO_Static  # noqa: E402
from elastiflow.scheduler.scheduler_LA import Scheduler_LA_Elastic  # noqa: E402
from elastiflow.scheduler.edf_optimized_LA import EDF_Optimized_LA  # noqa: E402
from elastiflow.scheduler.edf_hsm_LA import EDF_HSM_LA  # noqa: E402
from elastiflow.scheduler.edf_scheduler_LA import EDF_Scheduler_LA  # noqa: E402
from elastiflow.scheduler.fcfs_optimized_LA import FCFS_Optimized_LA  # noqa: E402
from elastiflow.scheduler.edf_optimized_HPO import EDF_Optimized_HPO  # noqa: E402
from elastiflow.scheduler.edf_scheduler_HPO import EDF_Scheduler_HPO  # noqa: E402
from elastiflow.scheduler.fcfs_optimized_HPO import FCFS_Optimized_HPO  # noqa: E402
from elastiflow.scheduler.fcfs_scheduler_HPO import FCFS_Scheduler_HPO  # noqa: E402


def test_edf_policies_share_the_ordering_helpers():
    for cls in (EDF_Scheduler_LA, EDF_Optimized_LA, EDF_HSM_LA, EDF_Scheduler_HPO, EDF_Optimized_HPO):
        assert issubclass(cls, EDFOrderingMixin), cls
        for name in ('peekWorkflow', 'popWorkflow', 'processWorkflowsByDeadline'):
            assert getattr(cls, name) is getattr(EDFOrderingMixin, name), (cls, name)
    # the resource-request ordering differs per policy and stays where it was
    assert EDF_Scheduler_LA.processResourceRequestsByDeadline is not EDF_Optimized_LA.processResourceRequestsByDeadline
    assert EDF_HSM_LA.processResourceRequestsByDeadline is EDF_Optimized_LA.processResourceRequestsByDeadline
    assert EDF_Optimized_HPO.processResourceRequestsByDeadline is not EDF_Optimized_LA.processResourceRequestsByDeadline


def test_hsm_is_edf_lamf_plus_its_gate():
    assert issubclass(EDF_HSM_LA, EDF_Optimized_LA)
    for name in ('checkNewResourcesWithLicenses', 'findLicenseFeasibleAllocation', 'isWorkflowImpossible', 'freeResourcesWithLicenses'):
        assert getattr(EDF_HSM_LA, name) is getattr(EDF_Optimized_LA, name), name
    for name in ('run', 'processFreeRequestWithLicenses', '_hsm_in_static_phase', '__init__'):
        assert name in EDF_HSM_LA.__dict__, name


def test_elastic_licence_layer():
    for cls in (FCFS_Optimized_LA, EDF_Optimized_LA):
        assert issubclass(cls, Scheduler_LA_Elastic)
        assert cls.freeResourcesWithLicenses is Scheduler_LA_Elastic.freeResourcesWithLicenses
    assert 'checkNewResourcesWithLicenses' in FCFS_Optimized_LA.__dict__ and 'checkNewResourcesWithLicenses' in EDF_Optimized_LA.__dict__


def test_hpo_static_and_elastic_layers():
    assert issubclass(FCFS_Scheduler_HPO, Scheduler_HPO_Static) and issubclass(EDF_Scheduler_HPO, Scheduler_HPO_Static)
    assert issubclass(FCFS_Optimized_HPO, Scheduler_HPO_Elastic) and issubclass(EDF_Optimized_HPO, Scheduler_HPO_Elastic)
    assert Scheduler_HPO_Static.createOnDemandWorkers is not Scheduler_HPO_Elastic.createOnDemandWorkers
    for cls in (FCFS_Scheduler_HPO, EDF_Scheduler_HPO, FCFS_Optimized_HPO, EDF_Optimized_HPO):
        assert cls.getInstanceTypeForHPO is Scheduler_HPO.getInstanceTypeForHPO
    for name in ('_syncOnDemandIPs', 'freeResources', 'getHPOInstanceCost', 'createOnDemandWorkers'):
        assert FCFS_Optimized_HPO.__dict__.get(name) is None and getattr(FCFS_Optimized_HPO, name) is getattr(Scheduler_HPO_Elastic, name), name
