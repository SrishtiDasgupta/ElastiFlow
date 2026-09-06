"""
The HPO schedulers' allocation logic, computed offline.

HPO has no simulated cell (its driver ran live services), but all four HPO
schedulers construct against config/resources_HPO.yaml and their allocation
decisions live in pure methods. This module exercises them deterministically:

* selectOptimalInstanceType over a grid of (model, budget, deadline, trials,
  epochs) around the campaign's averages in constants_HPO;
* the admission allocation (allocateResourcesHPO / allocateResourcesMoldableHPO)
  for each of the 20 workflows of workflow/sample_workflows_HPO, once on a
  fresh scheduler and once in sequence on one scheduler without releases, so
  the on-prem, reserved and on-demand paths are all reached;
* for the elastic classes, one scale-up probe (checkNewResourcesHPO) after the
  fresh allocation.

The schedulers provision on-demand workers from a thread pool, which cannot
sleep on a simulus clock from another thread, so the harness hands them a stub
backend: simulated, clock fixed at 0, provisioning immediate with IPs derived
from the instance type and count (deterministic across threads).
record_hpo_allocation.py writes the result to baseline_hpo_allocation.json and
test_hpo_allocation_baseline.py compares the current tree with it exactly.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / 'elastiflow' / 'workflow' / 'sample_workflows_HPO').glob('data*.yaml'),
                   key=lambda p: int(p.stem[4:]))
HPO_POLICIES = ('FCFS-ST', 'Elastic-FCFS', 'EDF-ST', 'Elastic-EDF')
TRIALS = (1, 2, 4)
EPOCHS = (5, 20)
SCALES = (0.5, 1.0, 2.0)


def _ser(obj):
    """JSON-able view: instances become (name, type), tuples become lists."""
    if isinstance(obj, (list, tuple)):
        return [_ser(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _ser(v) for k, v in obj.items()}
    if hasattr(obj, 'name') and hasattr(obj, 'type') and hasattr(obj, 'getFreeSlots'):
        return f'{obj.name}/{obj.type}'
    return obj


def _fresh(cls):
    from elastiflow.wf_queue.redis_queue import Redis_Queue
    q = [Redis_Queue(queue_name=f'hpo-allocation-baseline-{n}') for n in ('wf', 'done', 'rr')]
    return cls(*q)


class ProbeBackend:
    """The execution backend as the allocation methods see it: simulated, time 0,
    provisioning immediate. IPs come from the instance type and count only, so
    the thread pool in createOnDemandWorkers cannot make them order-dependent."""
    simulated = True

    def now(self):
        return 0.0

    def sleep(self, seconds):
        pass

    def provision(self, instance_type, count):
        tag = sum(ord(ch) for ch in instance_type) % 200
        return [f'10.19.{tag}.{n + 1}' for n in range(count)]

    def release(self, ips):
        pass


def _allocate(sched, wf_plan: dict, probe: bool) -> dict:
    from elastiflow.utils.resource import getConstraintsFromWorkflow
    backend = ProbeBackend()
    out = {}
    c = getConstraintsFromWorkflow(wf_plan)
    allocate = getattr(sched, 'allocateResourcesMoldableHPO', None) or sched.allocateResourcesHPO
    ips, alloc = allocate(c, backend)
    out['ips'], out['alloc'] = _ser(ips), _ser(alloc)
    out['free_slots'] = {f'{i.name}/{i.type}': i.getFreeSlots() for i in sched.resource_manager.getResources()}
    if probe and alloc:
        request = {'wf-id': wf_plan['id'], 'chains': c['chains'], 'count': c['chains'],
                   'tinyda-iterations': c['tinydaIterations'], 'iteration': 1}
        out['scale_up'] = _ser(sched.checkNewResourcesHPO(
            sched.resource_manager.getResources(), alloc, c['budget'], c['deadline_duration'],
            request, c['mesh'], alloc[0][0].name, backend))
    return out


def compute() -> dict:
    """Flat record: key -> JSON-able value, deterministic for a given tree."""
    from elastiflow.config.constants_HPO import AVG_BUDGET, AVG_DEADLINE
    from elastiflow.policies import get
    plans = []
    for p in WORKFLOWS:
        wf = yaml.safe_load(p.read_text())
        wf['submit_time'] = 0
        plans.append(wf)
    rec = {}
    with contextlib.redirect_stdout(io.StringIO()):
        for name in HPO_POLICIES:
            cls = get('hpo', name).load()
            elastic = get('hpo', name).elastic
            s = _fresh(cls)
            for model in sorted(AVG_BUDGET):
                for bs in SCALES:
                    for ds in SCALES:
                        for t in TRIALS:
                            for e in EPOCHS:
                                b, d = AVG_BUDGET[model] * bs, AVG_DEADLINE[model] * ds
                                rec[f'{name}/select/{model}/b{bs:g}/d{ds:g}/t{t}/e{e}'] = _ser(
                                    s.selectOptimalInstanceType(b, d, model, t, e))
            for wf in plans:
                rec[f'{name}/fresh/{wf["id"]}'] = _allocate(_fresh(cls), wf, probe=elastic)
            s = _fresh(cls)
            for wf in plans:
                rec[f'{name}/sequence/{wf["id"]}'] = _allocate(s, wf, probe=False)
    return rec
