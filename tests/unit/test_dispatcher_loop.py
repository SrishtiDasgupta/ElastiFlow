"""The shared arrival loop (B7.6): delays are slept on the backend's clock in
order, every loadable plan is stamped and sent, an unloadable one is skipped,
and END follows the profile's wait."""
from elastiflow.scripts.dispatcher import Arrivals, dispatcher


class Clock:
    simulated = True

    def __init__(self):
        self.t, self.sent, self.workflows, self.log = 0.0, [], self, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.log.append(('sleep', s))

    def send(self, wf):
        self.sent.append((wf['id'], wf.get('submit_time')))


def test_loop_order():
    plans = {0: {'id': 'a'}, 1: None, 2: {'id': 'c'}}
    arrivals = Arrivals('t', 3, lambda n: [5, 10, 20][:n], plans.get, end_delay=100,
                        banner=lambda n, b: None, announce=lambda i, b: None, missing=lambda i: None)
    import elastiflow.scripts.dispatcher as d
    d.end_workflow, saved = (lambda: {'id': 'END'}), d.end_workflow
    try:
        clock = Clock()
        dispatcher(clock, arrivals)
    finally:
        d.end_workflow = saved
    assert clock.log == [('sleep', 5), ('sleep', 10), ('sleep', 20), ('sleep', 100)]
    assert clock.sent == [('a', 5), ('c', 35), ('END', None)]
