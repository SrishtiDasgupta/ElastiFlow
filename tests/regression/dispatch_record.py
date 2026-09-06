"""
The arrival processes of the three dispatchers, recorded offline (B7.6).

Each dispatcher is run against a stub backend whose clock advances by the
requested sleeps and whose workflows channel records what was sent, so the
record is the sequence of (workflow id, submit time) each use case's arrival
process produces for a fixed seed: SeisSol seeds itself from constants.SEED,
the licence and HPO generators draw from numpy's global state, seeded here as
the runners do. record_dispatch.py writes baseline_dispatch.json;
test_dispatch_baseline.py compares the current tree with it exactly.
"""
from __future__ import annotations

import contextlib
import io

import numpy as np


class Clock:
    """Simulated time and a recording workflows channel."""
    simulated = True

    def __init__(self):
        self.t, self.sent, self.workflows = 0.0, [], self

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += float(seconds)

    def send(self, workflow):
        self.sent.append([workflow['id'], self.t, workflow.get('submit_time')])


def run(fn, *args, seed=None, **kwargs):
    if seed is not None:
        np.random.seed(seed)
    clock = Clock()
    with contextlib.redirect_stdout(io.StringIO()):
        fn(clock, *args, **kwargs)
    return clock.sent


def compute() -> dict:
    from elastiflow.scripts import dispatcher, dispatcher_HPO, dispatcher_LA
    return {
        'seissol': run(dispatcher.dispatcher, dispatcher.SEISSOL),                    # seeds itself (constants.SEED)
        'licence': run(dispatcher.dispatcher, dispatcher_LA.LICENCE, seed=42),        # simulate_main_LA.py's default seed
        'hpo/handcrafted': run(dispatcher_HPO.dispatcher),
        'hpo/fixed': run(dispatcher_HPO.dispatcher, num_workflows=15, use_generated=True),
        'hpo/poisson': run(dispatcher_HPO.dispatcher, num_workflows=15, use_generated=True, poisson=True, seed=7),
    }
