# Phase B, step 1: the execution-backend interface

Status: PROPOSAL. Nothing applied. This is the first Phase B step because it is
what makes the three scheduler forks mergeable: today `sim` is threaded through
every scheduler signature and 314 call sites branch on it.

## What the code switches on today (complete inventory, 2026-09-06)

| operation | sites | simulated form | live form |
|---|---|---|---|
| clock: now | 155 | `sim.now` via `getTime(sim)` | `time.time()` |
| clock: sleep | 48 | `sim.sleep(s)` via `(sim or time).sleep` | `time.sleep(s)` |
| send on a channel (3 channels) | 8 | `sim.sync().send(sim, '<mailbox>', str(msg))` | `sendRequest(ip, port, msg)` (HTTP) into a Redis queue |
| receive from a channel | 3 helpers | simulus mailbox `peek/retrieve` | Redis `peek/pop` (`utils/sim.py` already abstracts this) |
| scheduler → executor: run workflow | 5 | `sim.process(executeWorklow, request, sim)` | HTTP to the executor node, port 8089 |
| scheduler → executor: resources changed | 8 | `sim.process(processNewResources, req)` | HTTP to the executor node, port 8089 |
| spawn the metrics sampler | 19 | `sim.process(metrics.collectResourceUtilization, sim, rm)` | a thread |
| provision instances | 3 | `sleep(COLD_START_TIME)` | boto3 `run_instances` |
| release instances | 4 | no-op | `terminateInstance` |
| run one iteration | 2 | service stub returns the modelled runtime; `sleep(min(runtime, remaining) + 7.7)` | subprocess runs the real service; parse its output |
| on-premise port lease | 4 | none | take/return a port from `config/ports.yaml` |
| remaining `if sim:` guards | 66 | (wrap the rows above) | |

Three channels: `wf_mb` (dispatcher → scheduler; live port 8080, queue
`wf-queue`), `completed_jobs_mb` (executor → scheduler; 8082,
`completed-jobs-queue`), `resource_request_mb` (workflow engine → scheduler;
8084, `resource-request-queue`).

Wiring today. Simulated: two simulus simulators (`dispatcher`, `scheduler`),
three mailboxes on the scheduler simulator, the scheduler's `run` and
`processJobCompletion` as processes, the executor as a process spawned per
workflow, `simulus.sync([...]).run()`. Live: five threads in `main.py` (three
HTTP servers feeding the three Redis queues, `sched.run`,
`sched.processJobCompletion`) and an executor process on each node.

## The interface

```python
# elastiflow/execution/backend.py

class Channel(Protocol):
    """One of the three message channels. Messages are the same dict payloads
    the code sends today; str() encoding stays inside the implementation."""
    def send(self, message: dict) -> None: ...
    def peek(self) -> dict | None: ...
    def pop(self) -> None: ...
    def pop_many(self, count: int | None) -> list[dict]: ...

class ExecutionBackend(Protocol):
    # clock
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    # channels (scheduler side)
    workflows: Channel            # wf_mb / wf-queue
    completions: Channel          # completed_jobs_mb / completed-jobs-queue
    resource_requests: Channel    # resource_request_mb / resource-request-queue
    # scheduler -> executor
    def start_workflow(self, request: dict, executor_ip: str) -> None: ...
    def notify_resources(self, request: dict, executor_ip: str) -> None: ...
    # concurrency
    def spawn(self, fn, *args, name: str | None = None) -> None: ...
    # resources
    def provision(self, instance_type: str, count: int) -> list[str]: ...   # ips
    def release(self, ips: list[str]) -> None: ...
    # execution of one iteration (called by the workflow engine)
    def run_iteration(self, wf_id: str, service: str, args: dict,
                      deadline: float) -> IterationResult: ...
    # on-premise port lease (live only; simulated returns None)
    def lease_port(self) -> int | None: ...
    def return_port(self, port: int) -> None: ...

@dataclass
class IterationResult:
    output: object          # what the service returned (next cohesion, hosts, ...)
    runtime: float          # seconds the iteration took (modelled or measured)
    completed: bool         # False if the deadline cut it short
```

`SimulatedBackend(sim, runtime_model, overheads, cold_start)` holds the simulus
simulator and the three mailboxes; `provision` sleeps `cold_start`;
`run_iteration` asks `runtime_model` for the iteration time and sleeps
`min(runtime, remaining) + overheads.executor`; `spawn` is `sim.process`;
channels are mailboxes. `LiveBackend(config)` holds the Redis queues and the
ports; `provision` is boto3; `run_iteration` runs the service as a subprocess;
`spawn` is a thread; `start_workflow` and `notify_resources` are HTTP posts.

What a use case contributes to the simulated backend, via the use-case protocol
of `docs/REORGANISATION.md`: `runtime_model` (SeisSol and licence:
`speedup.getRuntime` over the fitted tables; HPO: none, live only) and
`overheads` (executor 7.7 s; cold start 400.5 s SeisSol and licence, 530 s HPO).

## Extraction order, each step gated

Gate for every step: `pytest` (3 tagged cells exact) and the new B0 test below,
then `pytest -m smoke`. From B5 on, also the default suite with Redis stopped.

* **B0. Widen the reference** (done 2026-09-06: `tests/regression/record_baseline.py`,
  `tests/smoke/test_all_policies_match_baseline.py`; the cell runners are shared
  with the regression tests through `conftest.py`).** Record every parsed field of all 16 simulated
  policies at one (N, seed) each from the current tree into
  `tests/regression/baseline_all_policies.json`, with a test that compares them
  exactly. The current tree reproduces the tagged cells, so this baseline is the
  tag's behaviour for every policy, not just three. No framework change.
* **B1. Clock** (done 2026-09-06). `elastiflow/execution/backend.py` defines
  `ExecutionBackend` (clock operations for now), `SimulatedBackend(sim)`,
  `LiveBackend`, and the bridge `backend_for(sim)`. All 192 code sites (plus 3
  in comments) in 59 functions across 32 files now read `backend.now()` /
  `backend.sleep()`, where `backend = backend_for(sim)` is a local bound from the
  function's `sim` parameter, or, in the three functions that fetch `sim` from
  the workflow registry, right after that binding. `utils/sim.getTime` is
  retired; a missed site fails at import. The licence manager's clock arrives
  as `backend.now()` from its five callers (`set_sim_time`); owning a backend
  reference comes with injection in B6. Gate: regression 11/11, smoke against
  the B0 baseline.
* **B2. Channels and messages** (done 2026-09-06). `Channel` (send, peek, pop,
  pop_many) with `SimulatedChannel` (a named simulus mailbox; sending goes
  through the sync group by name from the sender's own simulator, so the
  mailbox's minimum delay and cross-simulator delivery are exactly as before)
  and `LiveChannel` (a Redis queue fed by a Gateway HTTP endpoint). The backend
  gained `workflows`, `completions`, `resource_requests`, `start_workflow`,
  `notify_resources` and `spawn`; `register(sim, backend)` lets each runner bind
  a fully wired backend to its simulator (mailbox objects, executor callables)
  and the live entry points bind `LiveBackend(queues)` to `None`. All 45 event
  sites and all receive calls now go through the backend; `utils/sim.py` is
  retired; the scheduler modules no longer import the executors (the backend
  holds the callables), which removes the scheduler-executor import cycle.
  Messages keep the `str(dict)` encoding and the consumers' `eval`; decoding
  moves into the channels in a later step. The HPO executor's live completion
  path (a three-attempt retry loop) is kept verbatim under its `if sim:` guard,
  since B2 must not change live behaviour. Gate: regression 11/11, unit 90/90,
  smoke against the B0 baseline.
* **B3. Provisioning** (done 2026-09-06). `provision(instance_type, count)` and
  `release(ips)` on the backend. `SimulatedBackend` takes `cold_start` and a
  `fake_ip` generator at registration (SeisSol and licence: 400.5 s and the
  `1.x.x.x` draw from `create_instance.simulated_ip`; HPO: 530 s and the
  `10.19.x.x` draw from `create_instance_HPO.simulated_worker_ip`), so the
  cold-start sleep and the random draws happen in the same order as before;
  `LiveBackend` takes `launch` and `terminate` (`launchInstance` /
  `terminateInstance`, or the HPO `launch_workers` / `terminate_live`).
  `createInstance`, `createWorkerInstances` and `deleteInstanceFromIp` stay as
  thin wrappers that print what they printed and call the backend;
  `deleteInstanceFromIp` gained a `sim` parameter, passed at its eleven call
  sites, because the branch is now decided by the backend rather than by the
  `SIMULATE` constant. The SeisSol module no longer imports `SIMULATE` or
  `COLD_START_TIME`; the HPO module keeps them for its CLI-only helpers
  (`createExecutorInstance`, `getInstanceRole`, `listHPOInstances`). Gate:
  regression 11/11, smoke against the B0 baseline.
* **B4. Iteration execution**, first half (done 2026-09-06). `run_iteration(wf_id,
  service, args, deadline, iteration) -> IterationResult(output, runtime,
  completed)` on the backend. `SimulatedBackend` keeps the subprocess call to the
  runtime-model stub, sleeps `min(runtime, remaining) + executor_overhead`
  (7.7 s, now a registration parameter) and reports `completed`; `LiveBackend`
  runs the service and takes the first output line. The SeisSol/licence execute
  action in `steep_actions.py` calls it and no longer branches on the mode; the
  on-premise port lease is `lease_port` / `return_port` (simulated: the constant
  4242 and a no-op, as before; live: `ports.yaml`). `SIMULATE` is gone from the
  workflow engine and from `utils/exec_sched.py`. One thing the move exposed: the
  engine's `eval` of the stub's output relied on `numpy` being imported in the
  engine module (the licence runtime model prints `np.float64(...)`); the backend
  passes the name explicitly. Deferred: `steep_actions_HPO.py` keeps its own
  iteration runner (a retry loop and JSON parsing around the live service, no
  overhead injection); it is the HPO driver and folds in with the use-case
  protocol. The second half (B4b, 2026-09-06): the stub's functions moved verbatim into
  `elastiflow/scripts/tinyda_runtime.py` (`iteration_runtime(request) ->
  {'cohesion', 'runtime'}`, pure functions over the fitted speedup curves);
  `SimulatedBackend` takes `runtime_model` at registration and calls it
  in-process, so a simulated run no longer spawns a subprocess per iteration or
  round-trips the request and the result through `str()`/`eval()`. The stub
  remains as a CLI over the same functions (live mode, and the equivalence test
  `tests/unit/test_runtime_model_inprocess.py`, which compares both paths on a
  grid of requests). Gate: regression, smoke against the B0 baseline.
* **B5. In-memory channels for simulated mode.** Simulated runs stop needing
  Redis. Gate: the default suite with Redis stopped.
* **B6. One switch.** The `SIMULATE` constants and the `sim` parameters go;
  `python -m elastiflow run --mode simulated|live --use-case ... --policy ...`
  builds the backend once and hands it to the scheduler, engine and dispatcher.
* **B7. Merge the scheduler forks.** With `sim` out of every signature, the
  three abstract bases differ only in their licence and HPO extension points;
  that merge is planned as its own document once B6 is in.

## What does not change

Policy logic, sort keys, factors and thresholds; the runtime-model tables and
the overhead values; the dispatcher's arrival replay and its seeds; dataset
schemas; policy names. Any step that changes a number in the B0 baseline is
reverted, not tuned.

## Open decisions

1. Whether the live backend keeps the per-node executor as a separate process
   (today) or becomes a thread pool in the scheduler process. Proposal: keep it;
   Phase B is about the interface, not the deployment model.
2. Whether `LicenseManager` takes the clock from the backend or keeps stamping
   `sim_now` itself. Proposal: from the backend, in B1.
3. Whether the HPO fork receives a `SimulatedBackend` at all. Proposal: no;
   HPO is live-only by the author's decision, and its scheduler gets the live
   backend only, which also removes the misleading `simulate_main_HPO.py` name
   in B6 (it becomes the live HPO driver).
