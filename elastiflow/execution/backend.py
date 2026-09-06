"""
The execution backend: the one interface through which the orchestration code
(Gateway, Scheduler, Resource Manager, Workflow Engine, Load Balancer) reaches
execution, in either mode (dissertation Fig. 7.1). See docs/PHASE_B_BACKEND.md.

B1: clock. B2: the three message channels, the scheduler-to-executor messages,
and process spawning. Messages keep today's encoding (str(dict) on the wire,
eval at the consumer); the channels move strings, exactly as the mailboxes and
Redis queues did.

`backend_for(sim)` is the bridge while the simulator object is still threaded
through the signatures: runners register the fully wired backend for their
simulator (and the live entry points for None); anything not registered gets a
bare backend that supports the clock only.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Protocol


class Channel(Protocol):
    """One of the three message channels between the components."""
    def send(self, message) -> None: ...
    def peek(self): ...
    def pop(self) -> None: ...
    def pop_many(self, count): ...


class ExecutionBackend(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    workflows: Channel            # dispatcher -> scheduler
    completions: Channel          # executor -> scheduler
    resource_requests: Channel    # workflow engine -> scheduler
    def start_workflow(self, request: dict, executor_ip) -> None: ...
    def notify_resources(self, request: dict, executor_ip): ...
    def spawn(self, fn: Callable, *args, name=None) -> None: ...


# --- simulated ---------------------------------------------------------------

class SimulatedChannel:
    """A named simulus mailbox. Sending goes through the sync group by name, from
    the sender's own simulator, with the mailbox's minimum delay (as
    sim.sync().send did); receiving reads the mailbox object, which only the
    simulator that owns it holds."""

    def __init__(self, sim, name: str, mailbox=None):
        self.sim, self.name, self.mailbox = sim, name, mailbox

    def send(self, message) -> None:
        self.sim.sync().send(self.sim, self.name, str(message))

    def peek(self):
        mb = self.mailbox
        return mb.peek() and mb.peek()[0]

    def pop(self) -> None:
        self.mailbox.retrieve(isall=False)

    def pop_many(self, count):
        mb = self.mailbox
        all_elements = mb.peek()
        n = len(all_elements)
        if not count or n <= count:
            return mb.retrieve(isall=True)
        out = mb.peek()[:count]
        for _ in range(count):
            mb.retrieve(isall=False)
        return out


class SimulatedBackend:
    """Simulated time and messaging on a simulus simulator."""

    def __init__(self, sim, mailboxes: dict | None = None, execute: Callable | None = None,
                 on_resources: Callable | None = None):
        self.sim = sim
        mailboxes = mailboxes or {}
        self.workflows = SimulatedChannel(sim, 'wf_mb', mailboxes.get('wf_mb'))
        self.completions = SimulatedChannel(sim, 'completed_jobs_mb', mailboxes.get('completed_jobs_mb'))
        self.resource_requests = SimulatedChannel(sim, 'resource_request_mb', mailboxes.get('resource_request_mb'))
        self._execute, self._on_resources = execute, on_resources

    def now(self) -> float:
        return self.sim.now

    def sleep(self, seconds: float) -> None:
        self.sim.sleep(seconds)

    def start_workflow(self, request: dict, executor_ip) -> None:
        self.sim.process(self._execute, request, self.sim)

    def notify_resources(self, request: dict, executor_ip):
        self.sim.process(self._on_resources, request)
        return True

    def spawn(self, fn: Callable, *args, name=None) -> None:
        self.sim.process(fn, *args)


# --- live --------------------------------------------------------------------

class LiveChannel:
    """A Redis queue fed by an HTTP endpoint of the Gateway: sending posts to
    the endpoint (whose handler pushes str(message) onto the queue), receiving
    reads the queue."""

    def __init__(self, queue, ip=None, port=None):
        self.queue, self.ip, self.port = queue, ip, port

    def send(self, message) -> None:
        from elastiflow.utils.request import sendRequest
        sendRequest(self.ip, self.port, message)

    def peek(self):
        return self.queue.peek()

    def pop(self) -> None:
        self.queue.pop()

    def pop_many(self, count):
        return self.queue.pop(count or self.queue.getLength())


class LiveBackend:
    """Wall-clock time, Redis channels, HTTP to the executor nodes, threads."""

    def __init__(self, queue=None, finish_queue=None, resource_request_queue=None):
        self._queues = (queue, finish_queue, resource_request_queue)
        self._channels = None

    def _build(self):
        from elastiflow.utils.request import getConfig
        from elastiflow.wf_queue.redis_queue import Redis_Queue
        q, fq, rq = self._queues
        scheduler = getConfig('scheduler')
        self.workflows = LiveChannel(q or Redis_Queue(queue_name='wf-queue'), scheduler, getConfig('user-request-port'))
        self.completions = LiveChannel(fq or Redis_Queue(queue_name='completed-jobs-queue'), scheduler, getConfig('workflow-complete-port'))
        self.resource_requests = LiveChannel(rq or Redis_Queue(queue_name='resource-request-queue'), scheduler, getConfig('resource-request-port'))
        self._channels = True

    def __getattr__(self, name):
        if name in ('workflows', 'completions', 'resource_requests') and self.__dict__.get('_channels') is None:
            self._build()
            return self.__dict__[name]
        raise AttributeError(name)

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def start_workflow(self, request: dict, executor_ip) -> None:
        from elastiflow.utils.request import getConfig, sendRequest
        sendRequest(executor_ip, getConfig('executor-incoming-port'), request)

    def notify_resources(self, request: dict, executor_ip):
        from elastiflow.utils.request import getConfig, sendRequest
        return sendRequest(executor_ip, getConfig('executor-incoming-port'), request)

    def spawn(self, fn: Callable, *args, name=None) -> None:
        threading.Thread(target=fn, args=list(args), name=name).start()


# --- registry ----------------------------------------------------------------

_LIVE = LiveBackend()
_REGISTERED: dict[int, object] = {}
_BARE: dict[int, SimulatedBackend] = {}


def register(sim, backend) -> None:
    """Bind a fully wired backend to a simulator (or to None for live mode)."""
    _REGISTERED[id(sim)] = backend


def backend_for(sim):
    """The backend for a simulator object (None means live). One backend per
    simulator: the registered one, else a bare one that supports the clock."""
    b = _REGISTERED.get(id(sim))
    if b is not None:
        return b
    if sim is None:
        return _LIVE
    b = _BARE.get(id(sim))
    if b is None or b.sim is not sim:
        b = _BARE[id(sim)] = SimulatedBackend(sim)
    return b
