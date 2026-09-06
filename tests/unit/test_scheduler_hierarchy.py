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
                 'checkNewResources', 'checkCloseness', 'processJobCompletion'):
        assert getattr(Scheduler_LA, name) is not getattr(Scheduler, name), name
    for name in ('freeResources', 'checkNewResources', 'processJobCompletion'):
        assert getattr(Scheduler_HPO, name) is not getattr(Scheduler, name), name
    # B7.3: the licence release and the HPO scale-up request are the base's, with the
    # timeout and the tuple width as the only differences
    assert Scheduler_LA.freeResources is Scheduler.freeResources
    assert Scheduler_HPO.allocateNewResources is Scheduler.allocateNewResources
    assert (Scheduler.request_timeout, Scheduler_LA.request_timeout, Scheduler_HPO.request_timeout) == (180, 180, 720)


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
    for name in ('run', '_hsm_in_static_phase', '_holdAllocation', '__init__'):
        assert name in EDF_HSM_LA.__dict__, name
    assert EDF_HSM_LA.processFreeRequestWithLicenses is EDF_Optimized_LA.processFreeRequestWithLicenses   # B7.3: the gate is a hook


def test_elastic_licence_layer():
    for cls in (FCFS_Optimized_LA, EDF_Optimized_LA):
        assert issubclass(cls, Scheduler_LA_Elastic)
        assert cls.freeResourcesWithLicenses is Scheduler_LA_Elastic.freeResourcesWithLicenses
    for name in ('checkNewResourcesWithLicenses', 'findLicenseFeasibleAllocation'):      # shared since B7.3, differing by _feasibilityChains
        assert FCFS_Optimized_LA.__dict__.get(name) is None and EDF_Optimized_LA.__dict__.get(name) is None
        assert getattr(FCFS_Optimized_LA, name) is getattr(Scheduler_LA_Elastic, name)


def test_hpo_static_and_elastic_layers():
    assert issubclass(FCFS_Scheduler_HPO, Scheduler_HPO_Static) and issubclass(EDF_Scheduler_HPO, Scheduler_HPO_Static)
    assert issubclass(FCFS_Optimized_HPO, Scheduler_HPO_Elastic) and issubclass(EDF_Optimized_HPO, Scheduler_HPO_Elastic)
    assert Scheduler_HPO_Static.createOnDemandWorkers is not Scheduler_HPO_Elastic.createOnDemandWorkers
    for cls in (FCFS_Scheduler_HPO, EDF_Scheduler_HPO, FCFS_Optimized_HPO, EDF_Optimized_HPO):
        assert cls.getInstanceTypeForHPO is Scheduler_HPO.getInstanceTypeForHPO
    for name in ('_syncOnDemandIPs', 'freeResources', 'getHPOInstanceCost', 'createOnDemandWorkers'):
        assert FCFS_Optimized_HPO.__dict__.get(name) is None and getattr(FCFS_Optimized_HPO, name) is getattr(Scheduler_HPO_Elastic, name), name


# --- B7.3: the near-identical methods, merged behind explicit per-policy hooks ------
from elastiflow.scheduler.fcfs_optimized import FCFS_Optimized  # noqa: E402
from elastiflow.scripts.speedup_HPO_runtime import getRuntime_g4, getRuntime_g5  # noqa: E402


def test_seissol_elastic_fcfs_keeps_only_its_planner():
    assert 'checkNewResourcesMoldable' in FCFS_Optimized.__dict__
    assert 'processFreeRequest' not in FCFS_Optimized.__dict__ and 'freeResources' not in FCFS_Optimized.__dict__
    assert FCFS_Optimized.processFreeRequest is Scheduler.processFreeRequest


def test_licence_policy_hooks():
    assert FCFS_Optimized_LA._feasibilityChains(None, {'chains': 4}) == 1
    assert EDF_Optimized_LA._feasibilityChains(None, {'chains': 4}) == 4
    assert EDF_HSM_LA._feasibilityChains is EDF_Optimized_LA._feasibilityChains
    assert EDF_Optimized_LA._holdAllocation(None, *([None] * 9)) is False
    assert 'processFreeRequestWithLicenses' not in EDF_HSM_LA.__dict__ and '_holdAllocation' in EDF_HSM_LA.__dict__
    assert (EDF_Optimized_LA.negotiation_label, EDF_HSM_LA.negotiation_label) == ('EDF-LAMF', 'HSM MOLDABLE-PHASE')
    assert (EDF_Optimized_LA.phase_note, EDF_HSM_LA.phase_note) == ('', ' (MOLDABLE)')


def test_hpo_policy_hooks_and_labels():
    assert FCFS_Optimized_HPO._runtimeFunctionFor(None, 'on-prem') is getRuntime_g5
    assert EDF_Optimized_HPO._runtimeFunctionFor(None, 'on-prem') is getRuntime_g4
    for cls in (FCFS_Optimized_HPO, EDF_Optimized_HPO):
        assert cls._runtimeFunctionFor(None, 'g4dn.xlarge') is getRuntime_g4 and cls._runtimeFunctionFor(None, 'g5.xlarge') is getRuntime_g5
    assert (Scheduler_HPO.policy_label, FCFS_Scheduler_HPO.policy_label, EDF_Scheduler_HPO.policy_label, EDF_Optimized_HPO.policy_label) == ('', '', 'EDF ', 'EDF ')
    assert (Scheduler_HPO_Static.mode_label, Scheduler_HPO_Elastic.mode_label) == ('', 'Moldable ')
    assert Scheduler_HPO_Elastic.moldable_request and not Scheduler_HPO_Static.moldable_request
    for cls in (FCFS_Scheduler_HPO, EDF_Scheduler_HPO, FCFS_Optimized_HPO, EDF_Optimized_HPO):
        assert cls.sendWorkflowForExecutionHPO is Scheduler_HPO.sendWorkflowForExecutionHPO
        assert cls.selectOptimalInstanceType.__qualname__.split('.')[0] in ('Scheduler_HPO_Static', 'Scheduler_HPO_Elastic')
    for name in ('allocateResourcesMoldableHPO', 'checkNewResourcesHPO', 'sendNewResources', 'sendFreedResources'):
        assert getattr(FCFS_Optimized_HPO, name) is getattr(Scheduler_HPO_Elastic, name) is getattr(EDF_Optimized_HPO, name), name
