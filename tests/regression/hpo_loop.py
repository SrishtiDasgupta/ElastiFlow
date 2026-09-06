"""
The HPO schedulers' request loops, driven offline (B7.4).

HPO has no simulated cell, so the loop of each HPO policy is run against a
stub backend: three of the sample plans and the END sentinel wait on the
workflows channel; one grow request for the first admitted workflow appears
on the resource-requests channel once that workflow has been started; the
clock advances by the requested sleeps; every channel operation, sleep,
message and provisioning call is logged in order, and the run is cut off
after a fixed number of sleeps. record_hpo_loop.py writes the logs to
baseline_hpo_loop.json; test_hpo_loop_baseline.py compares the current tree
with them exactly.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import yaml

from hpo_allocation import WORKFLOWS, _fresh, _ser

MAX_SLEEPS = 60
END = {'id': 'END'}


class Cutoff(Exception):
    pass


def _head(message):
    if message is None:
        return None
    d = eval(message)
    return d.get('id', d.get('wf-id'))


class Channel:
    def __init__(self, backend, tag, items=()):
        self.backend, self.tag, self.items = backend, tag, list(items)

    def peek(self):
        self.backend.inject()
        v = self.items[0] if self.items else None
        self.backend.log.append([f'peek_{self.tag}', _head(v)])
        return v

    def pop(self):
        v = self.items.pop(0) if self.items else None
        self.backend.log.append([f'pop_{self.tag}', _head(v)])
        return v

    def pop_many(self, count):
        self.backend.inject()
        n = len(self.items) if count is None else min(count, len(self.items))
        out, self.items = self.items[:n], self.items[n:]
        self.backend.log.append([f'drain_{self.tag}', [_head(v) for v in out]])
        return out

    def send(self, message):
        self.backend.log.append([f'send_{self.tag}', _ser(message)])


class LoopBackend:
    """Simulated clock, recording channels, immediate provisioning."""
    simulated = True

    def __init__(self, plans, request_for):
        self.t, self.log, self.started, self.sleeps = 0.0, [], [], 0
        self.workflows = Channel(self, 'wf', [str(p) for p in plans] + [str(END)])
        self.resource_requests = Channel(self, 'req')
        self.request_for, self.injected = request_for, False

    def inject(self):
        """The grow request of the first admitted workflow, once it has started."""
        if self.started and not self.injected:
            self.injected = True
            self.resource_requests.items.append(str(dict(self.request_for(self.started[0]), **{'request-time': self.t})))

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += float(seconds)
        self.log.append(['sleep', float(seconds)])
        self.sleeps += 1
        if self.sleeps >= MAX_SLEEPS:
            raise Cutoff()

    def spawn(self, fn, *args, name=None):
        self.log.append(['spawn', fn.__name__])

    def start_workflow(self, request, executor_ip):
        self.log.append(['start', executor_ip, _brief(request)])
        self.started.append(request['wf-plan']['id'])

    def notify_resources(self, request, executor_ip):
        self.log.append(['notify', executor_ip, _brief(request)])
        return True

    def provision(self, instance_type, count):
        tag = sum(ord(ch) for ch in instance_type) % 200
        self.log.append(['provision', instance_type, count])
        return [f'10.19.{tag}.{n + 1}' for n in range(count)]

    def release(self, ips):
        self.log.append(['release', list(ips)])


def _brief(request):
    r = dict(request)
    if isinstance(r.get('wf-plan'), dict):
        r['wf-plan'] = r['wf-plan'].get('id')
    return _ser(r)


def run_loop(policy_name: str) -> list:
    from elastiflow.policies import get
    from elastiflow.utils.request import ExecutorRequest
    plans = []
    for p in WORKFLOWS[:3]:
        wf = yaml.safe_load(p.read_text()); wf['submit_time'] = 0
        plans.append(wf)
    by_id = {p['id']: p for p in plans}

    def request_for(wf_id):
        c = by_id[wf_id]['constraints']
        return {'request': ExecutorRequest.REQUEST_RESOURCE.value, 'wf-id': wf_id, 'count': 2, 'iteration': 1,
                'tinyda-iterations': c['tinydaIterations'], 'chains': c['chains'], 'client-ip': '10.0.0.9'}
    backend = LoopBackend(plans, request_for)
    sched = _fresh(get('hpo', policy_name).load())
    sched.metrics.computeMetrics = lambda **kw: backend.log.append(['computeMetrics', _ser(kw)])
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            sched.run(backend)
            backend.log.append(['ended'])
        except Cutoff:
            backend.log.append(['cutoff', MAX_SLEEPS])
    return backend.log


def compute() -> dict:
    return {name: run_loop(name) for name in ('FCFS-ST', 'EDF-ST', 'Elastic-FCFS', 'Elastic-EDF')}
