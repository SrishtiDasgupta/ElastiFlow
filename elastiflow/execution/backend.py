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

import subprocess
import sys

import numpy as np   # the runtime-model stub prints numpy scalars (np.float64(...)); eval needs the name, as the engine had it
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass
class IterationResult:
    """What one workflow iteration produced: the service's output (the object the
    engine used to receive as `result`), the iteration's runtime in seconds
    (modelled or measured; None where the live service does not report it), and
    whether it ran to completion (False when the deadline cut it short)."""
    output: object
    runtime: float | None
    completed: bool


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
    def provision(self, instance_type: str, count: int) -> list: ...   # returns instance ips
    def release(self, ips) -> None: ...
    def run_iteration(self, wf_id: str, service: str, args: dict, deadline: float, iteration: int) -> IterationResult: ...
    def lease_port(self) -> int: ...          # on-premise service port for an iteration
    def return_port(self, port: int) -> None: ...


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
                 on_resources: Callable | None = None, cold_start: float = 0.0,
                 fake_ip: Callable | None = None, release_message: str | None = None,
                 executor_overhead: float = 7.7, runtime_model: Callable | None = None):
        self.sim = sim
        self._cold_start, self._fake_ip, self._release_message = cold_start, fake_ip, release_message
        self._executor_overhead = executor_overhead
        self._runtime_model = runtime_model      # request -> {'runtime': s, ...}; None falls back to the service stub
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

    def provision(self, instance_type: str, count: int) -> list:
        """Cloud provisioning as a simulated delay of the measured cold start,
        then synthetic private IPs from the use case's generator (the draws are
        the same calls in the same order as before B3)."""
        self.sleep(self._cold_start)
        return [self._fake_ip() for _ in range(count)]

    def release(self, ips) -> None:
        if self._release_message:
            print(self._release_message.format(ips=ips))

    def run_iteration(self, wf_id: str, service: str, args: dict, deadline: float, iteration: int) -> IterationResult:
        """One iteration in simulated time: the service (the runtime-model stub)
        reports the modelled runtime; the process sleeps for it, capped at the
        deadline, plus the measured executor overhead. With a registered runtime
        model the lookup is in-process (B4b); otherwise the service stub runs."""
        if self._runtime_model is not None:
            result = self._runtime_model(args)
        else:
            cp = subprocess.run([sys.executable, service, str(args)], check=True, capture_output=True, text=True)
            result = eval(cp.stdout, {'np': np})
        runtime = float(result['runtime'])
        sleep_time = min(runtime, max(deadline - self.now(), 0))
        self.sleep(sleep_time + self._executor_overhead)
        return IterationResult(result, runtime, sleep_time >= runtime)

    def lease_port(self) -> int:
        return 4242            # no on-premise dispatch in simulation; the constant the engine used

    def return_port(self, port: int) -> None:
        pass


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

    def __init__(self, queue=None, finish_queue=None, resource_request_queue=None,
                 launch: Callable | None = None, terminate: Callable | None = None):
        self._queues = (queue, finish_queue, resource_request_queue)
        self._channels = None
        self._launch, self._terminate = launch, terminate

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

    def provision(self, instance_type: str, count: int) -> list:
        if self._launch is None:
            from elastiflow.scripts.create_instance import launchInstance
            self._launch = launchInstance
        return self._launch(instance_type, count)

    def release(self, ips) -> None:
        if self._terminate is None:
            from elastiflow.scripts.create_instance import terminateInstance
            self._terminate = terminateInstance
        return self._terminate(ips)

    def run_iteration(self, wf_id: str, service: str, args: dict, deadline: float, iteration: int) -> IterationResult:
        """One iteration for real: run the service and take the first line of its
        output as the value for the next iteration (as the engine did)."""
        cp = subprocess.run([sys.executable, service, str(args)], check=True, capture_output=True, text=True)
        print(f"{wf_id} Workflow iteration {iteration} started at {time.time()}")
        input_value = cp.stdout.splitlines()[0]
        print(input_value)
        return IterationResult(eval(input_value, {'np': np}), None, True)

    def lease_port(self) -> int:
        from elastiflow.utils.exec_sched import getWorkflowOnpremPort
        return getWorkflowOnpremPort()

    def return_port(self, port: int) -> None:
        import yaml
        from elastiflow.config.paths import PACKAGE_DIR
        with open(f"{PACKAGE_DIR}/config/ports.yaml", "r") as f:
            data = yaml.safe_load(f)
        ports = data.get("onprem_ports", [])
        ports.append(port)
        data["onprem_ports"] = ports
        with open(f"{PACKAGE_DIR}/config/ports.yaml", "w") as f:
            yaml.safe_dump(data, f)


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
