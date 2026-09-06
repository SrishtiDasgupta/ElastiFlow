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
