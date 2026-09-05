"""License-POV analysis of LA runs (static vs moldable).

Mines the per-run artifacts the simulator already writes:
  *_license_usage.csv : pool time series (1200s sim grid) -> token-seconds, util
  *_results.csv       : per-workflow license/hardware cost, completion, solver

Computes license-centric metrics and aggregates static {FCFS-ST,EDF-ST} vs
moldable {LAMF,EDF-LAMF,EDF-HSM} per N. No new simulator runs.
"""
import csv
import glob
import os
from collections import defaultdict
from statistics import mean

RUN_ROOT = os.environ.get('LA_RUN_ROOT', '/tmp/breadth_runs')
GRID_S = 1200.0  # license_usage.csv sampling grid (verified)
SID_NAME = {1: 'ANSYS', 2: 'ABAQUS', 3: 'LSDYNA'}

STATIC = ['FCFS-ST-LA', 'EDF-ST-LA']
MOLDABLE = ['LAMF', 'EDF-LAMF', 'EDF-HSM']
POLICIES = STATIC + MOLDABLE
NS = [int(x) for x in os.environ.get('LA_NS', '200,300').split(',')]
SEEDS = [int(x) for x in os.environ.get('LA_SEEDS', '7,107,207').split(',')]


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def analyze_usage(path):
    """Per-pool: token-seconds, active-window util, peak, saturation exposure."""
    rows = list(csv.DictReader(open(path)))
    by_pool = defaultdict(list)  # pool -> [(t, alloc, total)]
    for r in rows:
        t = _f(r['Timestamp'])
        if t > 1e6:          # drop the single trailing wall-clock row
            continue
        by_pool[r['Pool']].append(
            (t, _f(r['Allocated_Tokens']), _f(r['Total_Tokens'])))
    out = {}
    for pool, samp in by_pool.items():
        samp.sort()
        cap = samp[0][2] if samp else 0
        token_sec = sum(a * GRID_S for _, a, _ in samp)
        nz = [(t, a) for t, a, _ in samp if a > 0]
        if nz:
            active_s = (nz[-1][0] - nz[0][0]) + GRID_S
            # time-weighted util over active window
            active_alloc = [a for t, a, _ in samp if nz[0][0] <= t <= nz[-1][0]]
            tw_util = (mean(active_alloc) / cap * 100) if cap else 0
            peak = max(a for _, a, _ in samp)
            sat_frac = (sum(1 for a in active_alloc if a >= 0.8 * cap)
                        / len(active_alloc) * 100) if active_alloc else 0
        else:
            active_s = tw_util = peak = sat_frac = 0
        out[pool] = dict(cap=cap, token_sec=token_sec, active_s=active_s,
                         tw_util=tw_util, peak=peak,
                         peak_util=(peak / cap * 100 if cap else 0),
                         sat_frac=sat_frac)
    return out


def analyze_results(path):
    """Per-workflow license economics + per-solver split."""
    rows = list(csv.DictReader(open(path)))
    tot_lic = tot_hw = 0.0
    done_lic = done_n = 0.0
    waste_lic = miss_n = 0.0
    hold_times = []
    per_solver = defaultdict(lambda: dict(lic=0.0, n=0, done=0, ts_proxy=0.0))
    for r in rows:
        lic = _f(r['license_cost']); hw = _f(r['hardware_cost'])
        complete = str(r['complete']).strip().lower() == 'true'
        sid = int(_f(r['software_id'], 0))
        tot_lic += lic; tot_hw += hw
        ps = per_solver[SID_NAME.get(sid, f'sid{sid}')]
        ps['lic'] += lic; ps['n'] += 1
        if complete:
            done_lic += lic; done_n += 1; ps['done'] += 1
            st, fin = _f(r['exec_start_time']), _f(r['finish_time'])
            if fin > st:
                hold_times.append(fin - st)
        else:
            waste_lic += lic; miss_n += 1
    return dict(
        tot_lic=tot_lic, tot_hw=tot_hw,
        lic_per_done=(done_lic / done_n if done_n else None),
        waste_lic=waste_lic,
        waste_frac=(waste_lic / tot_lic * 100 if tot_lic else 0),
        overhead=(tot_lic / tot_hw * 100 if tot_hw else 0),
        mean_hold=(mean(hold_times) if hold_times else None),
        n_done=done_n, n_miss=miss_n,
        per_solver={k: dict(v) for k, v in per_solver.items()})


def run_dir(policy, N, seed):
    return os.path.join(RUN_ROOT, f'{policy}__N{N}__seed{seed}')


def collect():
    data = defaultdict(list)  # (policy,N) -> [merged per-seed dict]
    for pol in POLICIES:
        for N in NS:
            for sd in SEEDS:
                d = run_dir(pol, N, sd)
                ug = glob.glob(os.path.join(d, '*_license_usage.csv'))
                rg = glob.glob(os.path.join(d, '*_results.csv'))
                if not ug or not rg:
                    continue
                u = analyze_usage(ug[0])
                r = analyze_results(rg[0])
                r['_usage'] = u
                # aggregate pool-level into run-level token-seconds total
                r['token_sec_total'] = sum(p['token_sec'] for p in u.values())
                data[(pol, N)].append(r)
    return data


def agg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return mean(vals) if vals else None


def main():
    data = collect()

    print('=' * 96)
    print('LICENSE-POV SUMMARY  (seed-averaged; 3 seeds each)')
    print('=' * 96)
    hdr = (f'{"policy":11} {"N":>4} | {"lic/done€":>9} {"waste€":>8} '
           f'{"waste%":>6} {"ovhd%":>6} {"holds":>7} {"tok·Ms":>7} {"tok/done":>9}')
    print(hdr); print('-' * 96)
    for N in NS:
        for pol in POLICIES:
            rows = data.get((pol, N))
            if not rows:
                print(f'{pol:11} {N:>4} | (no data)'); continue
            lpd = agg(rows, 'lic_per_done')
            ts = agg(rows, 'token_sec_total')
            ndone = agg(rows, 'n_done')
            tok_per_done = (ts / ndone) if ts and ndone else None
            print(f'{pol:11} {N:>4} | {lpd:>9.1f} '
                  f'{agg(rows,"waste_lic"):>8.0f} {agg(rows,"waste_frac"):>6.1f} '
                  f'{agg(rows,"overhead"):>6.0f} '
                  f'{agg(rows,"mean_hold"):>7.0f} {ts/1e6:>7.1f} '
                  f'{tok_per_done:>9.0f}')
        print()

    # static-vs-moldable deltas
    print('=' * 96)
    print('MOLDABLE vs STATIC-BASELINE (avg of FCFS-ST/EDF-ST)   [neg = moldable better]')
    print('=' * 96)
    for N in NS:
        sref = {k: mean(agg(data[(p, N)], k) for p in STATIC)
                for k in ('lic_per_done', 'waste_frac', 'mean_hold', 'token_sec_total')}
        for pol in MOLDABLE:
            rows = data.get((pol, N))
            if not rows:
                continue
            d_lpd = (agg(rows, 'lic_per_done') / sref['lic_per_done'] - 1) * 100
            d_w = agg(rows, 'waste_frac') - sref['waste_frac']
            d_h = (agg(rows, 'mean_hold') / sref['mean_hold'] - 1) * 100
            d_ts = (agg(rows, 'token_sec_total') / sref['token_sec_total'] - 1) * 100
            print(f'  N{N} {pol:10} Δlic/done {d_lpd:+6.1f}%  '
                  f'Δwaste {d_w:+5.1f}pp  Δhold {d_h:+6.1f}%  Δtoken·s {d_ts:+6.1f}%')
        print()

    # per-pool utilisation (active-window, honest) — moldable vs static at N300
    print('=' * 96)
    print('PER-POOL ACTIVE-WINDOW UTILISATION  (N=max, seed-avg)  [tw=time-weighted]')
    print('=' * 96)
    print(f'{"policy":11} | ' + '  '.join(f'{p:>22}' for p in ('ANSYS', 'ABAQUS', 'LSDYNA')))
    print(f'{"":11} | ' + '  '.join(f'{"tw%/peak%/sat%":>22}' for _ in range(3)))
    print('-' * 96)
    for pol in POLICIES:
        rows = data.get((pol, max(NS)))
        if not rows:
            continue
        cells = []
        for pool in ('ANSYS', 'ABAQUS', 'LSDYNA'):
            tw = mean(r['_usage'][pool]['tw_util'] for r in rows if pool in r['_usage'])
            pk = mean(r['_usage'][pool]['peak_util'] for r in rows if pool in r['_usage'])
            sat = mean(r['_usage'][pool]['sat_frac'] for r in rows if pool in r['_usage'])
            cells.append(f'{tw:5.1f}/{pk:5.1f}/{sat:5.1f}')
        print(f'{pol:11} | ' + '  '.join(f'{c:>22}' for c in cells))

    # per-solver license cost split (N300)
    print()
    print('=' * 96)
    print('PER-SOLVER LICENSE COST SHARE & COMPLETION  (N=max, seed-avg)')
    print('=' * 96)
    print(f'{"policy":11} | {"":>26}'.replace('  ', ' '), end='')
    print('  '.join(f'{s:>16}' for s in ('ANSYS', 'ABAQUS', 'LSDYNA')))
    print(f'{"":11} |   ' + '  '.join(f'{"lic€/compl%":>16}' for _ in range(3)))
    print('-' * 96)
    for pol in POLICIES:
        rows = data.get((pol, max(NS)))
        if not rows:
            continue
        cells = []
        for s in ('ANSYS', 'ABAQUS', 'LSDYNA'):
            lic = mean(r['per_solver'].get(s, {}).get('lic', 0) for r in rows)
            n = mean(r['per_solver'].get(s, {}).get('n', 0) for r in rows)
            done = mean(r['per_solver'].get(s, {}).get('done', 0) for r in rows)
            compl = (done / n * 100) if n else 0
            cells.append(f'{lic:7.0f}/{compl:4.0f}')
        print(f'{pol:11} |   ' + '  '.join(f'{c:>16}' for c in cells))


if __name__ == '__main__':
    main()
