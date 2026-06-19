"""Analyze engine-scheduler negotiation latency.

Reads three CSVs from a run dir (executor + scheduler + iteration) and computes
four sub-intervals per (wf_id, iter_idx, request_type):

  1. request_handoff_s   = t_scheduler_request_observed - t_engine_request_sent
  2. decision_s          = t_scheduler_reply_sent       - t_scheduler_request_observed
  3. reply_handoff_s     = t_engine_reply_received      - t_scheduler_reply_sent
  4. wait_loop_plus_reconfig_s = t_iteration_started   - t_engine_reply_received

Hypothesis: simulator's polling-cycle floors capture real infra to within
quantization; residual non-polling overhead < 1 s.

Usage:
    NEGOTIATION_LOG_DIR=/fsx/HPO/results/r7_n7_edf_mold python analyze_negotiation.py
or
    python analyze_negotiation.py /path/to/run_dir
"""
import os
import sys
import pandas as pd
import numpy as np


def load(run_dir):
    def _read(name):
        path = os.path.join(run_dir, f'negotiation_{name}.csv')
        if not os.path.exists(path):
            print(f'[warn] missing {path}')
            return pd.DataFrame()
        return pd.read_csv(path)

    ex = _read('executor')
    sc = _read('scheduler')
    it = _read('iteration')

    # Scheduler emits two rows per request (observed + reply_sent). Collapse.
    if not sc.empty:
        sc = (sc.groupby(['wf_id', 'iter_idx', 'request_type'], as_index=False)
                .agg({'granted_count': 'max',
                      't_scheduler_request_observed': 'max',
                      't_scheduler_reply_sent': 'max'}))
    if not it.empty:
        it = (it[it['attempt'] == 1]
              .groupby(['wf_id', 'iter_idx'], as_index=False)
              .agg({'t_iteration_started': 'min'}))

    df = ex.merge(sc, on=['wf_id', 'iter_idx', 'request_type'], how='left')
    df = df.merge(it, on=['wf_id', 'iter_idx'], how='left')

    for c in ('t_engine_request_sent', 't_engine_reply_received',
              't_scheduler_request_observed', 't_scheduler_reply_sent',
              't_iteration_started'):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    df['request_handoff_s']      = df['t_scheduler_request_observed'] - df['t_engine_request_sent']
    df['decision_s']             = df['t_scheduler_reply_sent']       - df['t_scheduler_request_observed']
    df['reply_handoff_s']        = df['t_engine_reply_received']      - df['t_scheduler_reply_sent']
    df['wait_loop_plus_reconfig_s'] = df['t_iteration_started']        - df['t_engine_reply_received']
    return df


def summary(df):
    cols = ['request_handoff_s', 'decision_s', 'reply_handoff_s', 'wait_loop_plus_reconfig_s']
    print('\n=== Per-interval stats (seconds) ===')
    for c in cols:
        s = df[c].dropna()
        if s.empty:
            print(f'  {c}: no data'); continue
        print(f'  {c}: min={s.min():.2f}  median={s.median():.2f}  '
              f'mean={s.mean():.2f}  p95={np.percentile(s, 95):.2f}  max={s.max():.2f}  n={len(s)}')

    n_to = (df['outcome'] == 'timed_out').sum() if 'outcome' in df.columns else 0
    print(f'\n  Timed-out requests: {n_to}')

    # Hypothesis check: residual non-polling overhead
    # request_handoff floor = 30 s (scheduler poll), reply floor ~ 0 s
    rh = df['request_handoff_s'].dropna()
    if not rh.empty:
        residual = rh - 30
        residual = residual[residual > 0]
        if not residual.empty:
            print(f'\n  Residual (request_handoff - 30s polling floor): '
                  f'median={residual.median():.2f}s  p95={np.percentile(residual, 95):.2f}s')


def plots(df, out_dir):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print('matplotlib unavailable, skipping plots'); return

    os.makedirs(out_dir, exist_ok=True)

    fig, ax = plt.subplots()
    df['request_handoff_s'].dropna().hist(bins=30, ax=ax)
    ax.axvline(30, color='r', linestyle='--', label='30s scheduler-poll floor')
    ax.set_xlabel('request_handoff_s'); ax.set_ylabel('count'); ax.legend()
    fig.savefig(os.path.join(out_dir, 'request_handoff_hist.png'), bbox_inches='tight')

    fig, ax = plt.subplots()
    df['reply_handoff_s'].dropna().hist(bins=30, ax=ax)
    ax.axvline(0, color='r', linestyle='--', label='0s reply floor')
    ax.set_xlabel('reply_handoff_s'); ax.set_ylabel('count'); ax.legend()
    fig.savefig(os.path.join(out_dir, 'reply_handoff_hist.png'), bbox_inches='tight')

    print(f'plots written to {out_dir}')


if __name__ == '__main__':
    run_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('NEGOTIATION_LOG_DIR')
    if not run_dir:
        print('USAGE: analyze_negotiation.py <run_dir>  OR  set NEGOTIATION_LOG_DIR'); sys.exit(1)
    df = load(run_dir)
    if df.empty:
        print('no data loaded'); sys.exit(1)
    out = os.path.join(run_dir, 'negotiation_merged.csv')
    df.to_csv(out, index=False)
    print(f'merged → {out}  (n={len(df)} rows)')
    summary(df)
    plots(df, os.path.join(run_dir, 'negotiation_plots'))
