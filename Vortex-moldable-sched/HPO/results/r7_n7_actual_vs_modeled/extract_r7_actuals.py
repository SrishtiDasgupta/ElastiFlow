"""Extract per-iter actuals from R7 results.jsonl files.

For each wf, walk the LAST N entries of results.jsonl (where N = iter count
inferred from file existence in hpo_logs/) and record:
  - iter idx
  - end timestamp (ts in jsonl)
  - epoch used (from output config — applied in NEXT iter's input)
  - next_trials (from output config — applied in NEXT iter's chains)
  - mode (cloud / on-prem)

Output: r7_actual_per_iter.json
"""
import json
import os
import glob
from collections import defaultdict

WFS = {
    'data8':  ('hpo-60bf3eb4', 1778413716, 1778419876, 'vgg19'),
    'data9':  ('hpo-b03a4442', 1778413987, 1778422460, 'convnext_large'),
    'data5':  ('hpo-df19b440', 1778414106, 1778421395, 'wide_resnet101_2'),
    'data7':  ('hpo-73120874', 1778414188, 1778419943, 'wide_resnet101_2'),
    'data3':  ('hpo-a87b8123', 1778414203, 1778419398, 'vgg19'),
    'data12': ('hpo-ccb43739', 1778414218, 1778419947, 'vgg19'),
    'data1':  ('hpo-417bb2bd', 1778414224, 1778419419, 'vgg19'),
}
HPO_LOGS = '/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/HPO/results/r7_n7_edf_mold/hpo_logs'

# How many R7 iters per wf? Count iter files (skip FAILED/ERROR/attempt2).
def count_iters(full_id):
    files = sorted(glob.glob(f'{HPO_LOGS}/{full_id}_iter*_attempt1.log'))
    files = [f for f in files if 'FAILED' not in f and 'ERROR' not in f and 'attempt2' not in f]
    iters = set()
    for f in files:
        # extract iterN from path
        base = os.path.basename(f)
        # hpo-XXX_iterN_attempt1.log
        token = base.split('_iter')[1].split('_')[0]
        iters.add(int(token))
    return sorted(iters)

# Walk results.jsonl and find R7 entries (ts within R7 window: 1778413000 - 1778431000)
def parse_results_jsonl(full_id, n_iters_expected):
    path = f'{HPO_LOGS}/{full_id}_results.jsonl'
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [json.loads(l) for l in f if l.strip()]
    # Filter to R7 ts window
    r7 = [l for l in lines if 1778413000 <= l['ts'] <= 1778431000]
    return r7


print("=" * 80)
print(f"{'wf':6}  {'iters':>5}  {'iter ends → durations (min)'}")
print("=" * 80)

actuals = {}
for wf, (full_id, submit, deadline, model) in WFS.items():
    iter_idxs = count_iters(full_id)
    n = len(iter_idxs)
    r7_results = parse_results_jsonl(full_id, n)

    # Each r7_results entry corresponds to an iter. Ordered by iter idx.
    # Note: for wfs with more iters in R7 than entries (e.g., due to retry attempts),
    # we may need to handle carefully. For now, take last n entries.
    last_n = r7_results[-n:] if n > 0 else []

    iter_data = []
    prev_ts = submit
    for i, entry in enumerate(last_n):
        ts = entry['ts']
        cfg = entry['result']['config']
        # epoch field name varies: "epoch" (HPO output) or "epochs" (initial yaml)
        epoch = cfg.get('epoch', cfg.get('epochs', None))
        next_trials = cfg.get('next_trials', None)
        accuracy = cfg.get('accuracy', None)
        duration_s = ts - prev_ts
        iter_data.append({
            'iter': i,
            'ts_end': ts,
            'duration_s': duration_s,
            'duration_min': round(duration_s / 60, 1),
            'epoch_output': epoch,
            'next_trials_output': next_trials,
            'accuracy': accuracy,
        })
        prev_ts = ts

    actuals[wf] = {
        'full_id': full_id,
        'submit': submit,
        'deadline': deadline,
        'deadline_min_from_submit': round((deadline - submit)/60, 1),
        'model': model,
        'n_iters': n,
        'iter_data': iter_data,
    }
    durs = [d['duration_min'] for d in iter_data]
    total = sum(durs) if durs else 0
    print(f"{wf:6}  {n:>5}  {durs}  total={total:.1f} min  vs deadline {actuals[wf]['deadline_min_from_submit']:.1f}")

# Save
out_path = '/Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/HPO/results/r7_n7_actual_vs_modeled/r7_actual_per_iter.json'
with open(out_path, 'w') as f:
    json.dump(actuals, f, indent=2, default=str)
print(f"\nSaved → {out_path}")
