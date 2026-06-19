import math, heapq

# Per-wf trajectories from R1 (chains, tinyda); data5 iter 4 + iter 3 finish projected.
# τ values from R1 lanes=2 fits.
WFS = {
  "data9": dict(chains0=3, submit=0,   iters=[(3,20),(4,18),(6,17),(9,11),(10,13)], tau=20.16, deadline=5648.81, model="convnext"),
  "data7": dict(chains0=2, submit=271, iters=[(2,20),(3,25),(4,24),(6,14)],          tau=16.18, deadline=3837.20, model="wide_resnet"),
  "data5": dict(chains0=3, submit=390, iters=[(3,18),(4,20),(6,24),(9,25),(10,25)],  tau=35.90, deadline=4859.88, model="wide_resnet"),
  "data3": dict(chains0=4, submit=472, iters=[(4,15),(6,18),(9,15)],                  tau=25.58, deadline=3463.89, model="vgg19"),
  "data8": dict(chains0=4, submit=487, iters=[(4,12),(6,15),(9,12)],                  tau=25.58, deadline=4107.07, model="vgg19"),
}
POOL = 14
SETUP = 30
INTER_ITER = 30
COLD_START = 330
OD_PRICE = 0.526 / 3600  # g4dn.xlarge $/sec

def simulate(mode):
    """mode in {'static', 'moldable_cap10'}"""
    aon_used = 0                 # always-on lanes in use
    wf = {}                      # name -> {lanes, od, current_iter}
    events = []
    od_log = []                  # (wf, lanes, request_t, release_t)
    finishes = {}
    iter_log = []                # (wf, iter, start, end, lanes)

    for name, d in WFS.items():
        heapq.heappush(events, (d["submit"], 0, "submit", name, None))
    seq = 1

    while events:
        t, _, kind, name, payload = heapq.heappop(events)
        d = WFS[name]
        if kind == "submit":
            need = d["chains0"]
            free = POOL - aon_used
            if free >= need:
                aon_used += need
                wf[name] = dict(lanes=need, od=0)
                iter0_start = t + SETUP
            else:
                take = max(0, free)
                deficit = need - take
                aon_used += take
                wf[name] = dict(lanes=need, od=deficit)
                iter0_start = t + COLD_START + SETUP
                od_log.append([name, deficit, t, None])
            chains_k, tinyda_k = d["iters"][0]
            rt = math.ceil(chains_k / need) * tinyda_k * d["tau"]
            iter_log.append((name, 0, iter0_start, iter0_start + rt, need))
            heapq.heappush(events, (iter0_start + rt, seq, "iter_done", name, 0)); seq += 1

        elif kind == "iter_done":
            k = payload
            if k + 1 >= len(d["iters"]):
                finishes[name] = t
                aon_used -= max(0, wf[name]["lanes"] - wf[name]["od"])
                for od in od_log:
                    if od[0] == name and od[3] is None:
                        od[3] = t
                continue

            nk = k + 1
            chains_k, tinyda_k = d["iters"][nk]
            cur = wf[name]["lanes"]

            if mode == "moldable_cap10" and chains_k > cur:
                deficit = chains_k - cur
                free = POOL - aon_used
                grant = min(deficit, free)
                aon_used += grant
                wf[name]["lanes"] = cur + grant   # capped by available always-on
                # do not spawn on-demand for scale-up (conservative)

            lanes = wf[name]["lanes"]
            rt = math.ceil(chains_k / lanes) * tinyda_k * d["tau"]
            start = t + INTER_ITER
            iter_log.append((name, nk, start, start + rt, lanes))
            heapq.heappush(events, (start + rt, seq, "iter_done", name, nk)); seq += 1

    return finishes, od_log, iter_log

# Run both
for mode in ("static", "moldable_cap10"):
    print(f"\n{'='*72}\nMODE = {mode}\n{'='*72}")
    finishes, od_log, iter_log = simulate(mode)
    print(f"\nPer-wf iter timeline (start, end, lanes):")
    for wfname in WFS:
        rows = [r for r in iter_log if r[0] == wfname]
        for name, k, s, e, l in rows:
            print(f"  {name} iter{k}  lanes={l}  [{s:>5.0f}–{e:>5.0f}]  {e-s:>5.0f}s")

    # Makespan and deadline checks
    print(f"\nResults vs ×3 deadline:")
    print(f"  {'wf':6} {'finish':>7} {'×3 dl':>7} {'status':>6}  {'submit':>6}  makespan")
    miss = 0
    for name in WFS:
        f = finishes[name]
        ms = f - WFS[name]["submit"]
        dl3 = WFS[name]["deadline"] * 1.5
        st = "MISS" if ms > dl3 else "HIT"
        if ms > dl3: miss += 1
        print(f"  {name:6} {f:>7.0f} {dl3:>7.0f} {st:>6}  {WFS[name]['submit']:>6}  {ms:>5.0f}s")
    print(f"\n  total makespan = {max(finishes.values()):.0f}s")
    print(f"  deadline misses ×3 = {miss}/5")

    # Cost
    od_cost = sum(n * (rel - req) * OD_PRICE for _, n, req, rel in od_log if rel)
    print(f"\n  on-demand intervals: {[(w, n, int(req), int(rel)) for w, n, req, rel in od_log]}")
    print(f"  on-demand cost: ${od_cost:.2f}")
