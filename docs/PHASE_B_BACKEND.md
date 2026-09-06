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
* **B2. Channels and messages.** The three `Channel`s replace the mailbox/queue
  pairs and the 8 send sites; `start_workflow` / `notify_resources` replace the
  13 `sim.process`-or-HTTP pairs; `spawn` replaces the 19 sampler starts.
* **B3. Provisioning.** `provision` / `release` replace `createInstance` /
  `terminateInstance` and their `SIMULATE` guards.
* **B4. Iteration execution.** `run_iteration` replaces the execute action's
  branch. First as a move that keeps the subprocess call to the service stub in
  the simulated implementation (so the numbers cannot move), then, as a separate
  gated step, the stub's runtime lookup becomes an in-process call.
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
