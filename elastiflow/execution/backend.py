"""
The execution backend: the one interface through which the orchestration code
(Gateway, Scheduler, Resource Manager, Workflow Engine, Load Balancer) reaches
execution, in either mode (dissertation Fig. 7.1).

Phase B introduces it operation by operation; see docs/PHASE_B_BACKEND.md.
B1 covers the clock. `backend_for(sim)` is the bridge while the simulator object
is still threaded through the signatures: it returns the simulated backend for a
simulus simulator and the live backend for None.
"""
from __future__ import annotations

import time
from typing import Protocol


class ExecutionBackend(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class LiveBackend:
    """Wall-clock time."""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class SimulatedBackend:
    """Simulated time on a simulus simulator; `sleep` advances the calling
    process without consuming wall-clock time."""

    def __init__(self, sim):
        self.sim = sim

    def now(self) -> float:
        return self.sim.now

    def sleep(self, seconds: float) -> None:
        self.sim.sleep(seconds)


_LIVE = LiveBackend()
_SIMULATED: dict[int, SimulatedBackend] = {}


def backend_for(sim) -> ExecutionBackend:
    """The backend for a simulator object (None means live). One backend per
    simulator, so that identity is stable across calls."""
    if sim is None:
        return _LIVE
    b = _SIMULATED.get(id(sim))
    if b is None or b.sim is not sim:
        b = _SIMULATED[id(sim)] = SimulatedBackend(sim)
    return b
