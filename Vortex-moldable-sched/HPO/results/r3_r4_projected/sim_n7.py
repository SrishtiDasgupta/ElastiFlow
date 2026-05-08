import math, heapq, sys

# N=7 dispatch [9, 5, 7, 3, 8, 12, 1] — per campaign plan
# data1, data12 are vgg19 — use τ_vgg = 25.58 (proxy from data3/data8)
# Trajectories: chains follow next_trials growth (4→6→9 → ~12 if 4 iters)
# tinyda from observed vgg pattern (data3: 15,18,15; data8: 12,15,12)
# Submits: extend the seed=42 Poisson pattern; mean ≈ 90s. Use first 5 from
# observed dispatcher.log; sample next 2 at mean delay 90s.

WFS = {
  "data9":  dict(chains0=3, submit=0,   iters=[(3,20),(4,18),(6,17),(9,11),(10,13)], tau=20.16, deadline=5648.81, model="convnext"),
  "data5":  dict(chains0=3, submit=271, iters=[(3,18),(4,20),(6,24),(9,25),(10,25)], tau=35.90, deadline=4859.88, model="wide_resnet"),
  "data7":  dict(chains0=2, submit=390, iters=[(2,20),(3,25),(4,24),(6,14)],          tau=16.18, deadline=3837.20, model="wide_resnet"),
  "data3":  dict(chains0=4, submit=472, iters=[(4,15),(6,18),(9,15)],                  tau=25.58, deadline=3463.89, model="vgg19"),
  "data8":  dict(chains0=4, submit=487, iters=[(4,12),(6,15),(9,12)],                  tau=25.58, deadline=4107.07, model="vgg19"),
  "data12": dict(chains0=4, submit=577, iters=[(4,14),(6,16),(9,14),(13,12)],          tau=25.58, deadline=3819.14, model="vgg19"),
  "data1":  dict(chains0=4, submit=667, iters=[(4,15),(6,18),(9,15)],                  tau=25.58, deadline=3463.88, model="vgg19"),
}

POOL = 14
SETUP = 30
INTER_ITER = 30
COLD_START = 330
OD_PRICE = 0.526 / 3600

def simulate(mode):
    aon_used = 0
    wf = {}
    events = []
    od_log = []
    finishes = {}
    iter_log = []
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
                start = t + SETUP
            else:
                take = max(0, free); deficit = need - take
                aon_used += take
                wf[name] = dict(lanes=need, od=deficit)
                start = t + COLD_START + SETUP
                od_log.append([name, deficit, t, None])
            c, ti = d["iters"][0]
            rt = math.ceil(c / need) * ti * d["tau"]
            iter_log.append((name, 0, start, start+rt, need))
            heapq.heappush(events, (start+rt, seq, "iter_done", name, 0)); seq += 1
        elif kind == "iter_done":
            k = payload
            if k+1 >= len(d["iters"]):
                finishes[name] = t
                aon_used -= max(0, wf[name]["lanes"] - wf[name]["od"])
                for od in od_log:
                    if od[0]==name and od[3] is None: od[3] = t
                continue
            nk = k+1
            c, ti = d["iters"][nk]
            cur = wf[name]["lanes"]
            if mode == "moldable_cap10" and c > cur:
                deficit = c - cur
                free = POOL - aon_used
                grant = min(deficit, free)
                aon_used += grant
                wf[name]["lanes"] = cur + grant
            lanes = wf[name]["lanes"]
            rt = math.ceil(c/lanes) * ti * d["tau"]
            start = t + INTER_ITER
            iter_log.append((name, nk, start, start+rt, lanes))
            heapq.heappush(events, (start+rt, seq, "iter_done", name, nk)); seq += 1
    return finishes, od_log, iter_log

for mode in ("static", "moldable_cap10"):
    print(f"\n{'='*72}\nN=7  MODE = {mode}\n{'='*72}")
    f, od, ilog = simulate(mode)
    miss = 0
    print(f"  {'wf':7} {'submit':>6} {'finish':>7} {'mks':>6} {'×3 dl':>7} {'st':>4}")
    for n in WFS:
        ms = f[n] - WFS[n]["submit"]
        dl3 = WFS[n]["deadline"] * 1.5
        st = "MISS" if ms > dl3 else "HIT"
        if ms > dl3: miss += 1
        print(f"  {n:7} {WFS[n]['submit']:>6} {f[n]:>7.0f} {ms:>6.0f} {dl3:>7.0f} {st:>4}")
    print(f"\n  total makespan = {max(f.values()):.0f}s   ({max(f.values())/3600:.2f}h)")
    print(f"  deadline misses ×3 = {miss}/7")
    od_cost = sum(n_*(rel-req)*OD_PRICE for _,n_,req,rel in od if rel)
    od_summary = [(w, n_, int(req), int(rel)) for w,n_,req,rel in od]
    print(f"  on-demand intervals: {od_summary}")
    print(f"  on-demand cost: ${od_cost:.2f}")

    # data5 timeline (the headline wf)
    print("\n  data5 iter timeline:")
    for r in [r for r in ilog if r[0]=="data5"]:
        _, k, s, e, l = r
        print(f"    iter{k}  lanes={l}  [{s:>5.0f}–{e:>5.0f}]  {e-s:>5.0f}s")
