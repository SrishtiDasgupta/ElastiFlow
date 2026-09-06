"""
Runtime model for TinyDA--SeisSol iterations (the simulated backend's source of
iteration times). The functions are the ones the service stub
`simulate-tinyda-seissol.py` ran in a subprocess per iteration until B4b; they
are pure (fitted speedup curves from `speedup.py`, no state), so calling them
in-process gives the same numbers without the process spawn and the
str()/eval() round trip.
"""
import heapq

from elastiflow.scripts.speedup import getRuntime


def collectHostRuntimes(hosts, mesh): # returns a list of runtimes for all the hosts
    return {host: getRuntime(1, mesh, host) for host in hosts}

def parallelSimulation(hosts, chains, tinyda_iterations, host_count, mesh):
    host_runtimes = collectHostRuntimes(hosts, mesh)
    heap = []
    # Use a max heap - assign additional node to the slowest runtime
    # initial alloc - push 1 node for each chain
    to_be_pushed = chains
    for host in hosts:
        n = min(len(hosts[host]), to_be_pushed)
        for i in range(n):
            heapq.heappush(heap, (-host_runtimes[host], host, 1))
        to_be_pushed -= n
        hosts[host] = [] if n == len(hosts[host]) else hosts[host][n:]
        if to_be_pushed == 0: break
    
    # For each available node, add it to the highest runtime
    for host in hosts:
        for inst in hosts[host]:
            runtime, name, nodes = heapq.heappop(heap)
            if host_runtimes[host] > host_runtimes[name]: name = host
            new_runtime = getRuntime(nodes + 1, mesh, name)
            heapq.heappush(heap, (-new_runtime, name, nodes+1))
        
    return (-heap[0][0] * tinyda_iterations)

def sequentialSimulation(hosts, chains, tinydaIterations, mesh):
    runtimes = collectHostRuntimes(hosts, mesh) # {name: runtime}
    heap = []
    hostLength = 0
    for host in hosts:
        n = len(hosts[host])
        hostLength += n
        for i in range(n):
            heapq.heappush(heap, (runtimes[host], host)) # initial runtimes
    # print(runtimes)
    # A fully starved iteration (0 hosts) cannot run. Previously this fell through
    # to heappop() on an empty heap and raised IndexError, which the executor caught
    # but left the workflow stalled (no sim.sleep, iterator not advanced) — a fragile,
    # clock-skewing path to what is really a deadline miss. Return an effectively
    # infinite (but finite, so it survives the str()->eval() runtime round-trip;
    # float('inf') would eval to a NameError) runtime so the caller's existing
    # min(runtime, deadline-now) logic sleeps to the deadline and cleanly kills
    # (misses) the workflow instead of crashing.
    if hostLength == 0:
        return 1e12  # ~31000 yr; dwarfs any deadline -> guaranteed clean miss
    extraChains = chains - hostLength
    while extraChains:
        runtime, name = heapq.heappop(heap)
        heapq.heappush(heap, (runtime + runtimes[name], name))
        extraChains = extraChains - 1

    return max(heap)[0] * tinydaIterations


def iteration_runtime(t) -> dict:
    """The modelled runtime of one TinyDA--SeisSol iteration for request `t`
    (chains, tinyda_iterations, hosts, mesh), plus the constant cohesion the
    stub has always returned. Verbatim from simulate-tinyda-seissol.py; the stub
    now prints this function's result.""" # two cases for each iteration, one is when there are enough resources for each chain which means all run in parallel, and second is when number of hosts is less than chains
    chains = t['chains']
    tinydaIterations = t['tinyda_iterations'] + 2
    hosts = t['hosts']
    mesh = t['mesh']
    count = 0
    for name in hosts:
        count += len(hosts[name])

    if count >= t['chains']:
        sleep_time = parallelSimulation(hosts, chains, tinydaIterations, count, mesh)
    else:
        sleep_time = sequentialSimulation(hosts, chains, tinydaIterations, mesh)
    return {"cohesion":2.13, "runtime":sleep_time}
    # return sleep_time # returning sleeptime to simulate the workflows manually
    # print(f'simulating a run for {sleep_time} secs')
    # simulationFunction(sleep_time)
    # print(f'simulating a run for {sleep_time} secs')
