"""Negotiation-latency instrumentation. No-op unless NEGOTIATION_LOG_DIR is set.

Writes one row per call to a per-role CSV under $NEGOTIATION_LOG_DIR. Used to measure
engine-scheduler negotiation latency on real infra and validate the simulator's model
of polling-cycle floors.

Roles produce these CSVs (merged post-hoc by (wf_id, iter_idx)):
  - executor:        wf_id,iter_idx,request_type,requested_count,
                     t_engine_request_sent,t_engine_reply_received,outcome
  - scheduler:       wf_id,iter_idx,request_type,granted_count,
                     t_scheduler_request_observed,t_scheduler_reply_sent
  - iteration:       wf_id,iter_idx,attempt,t_iteration_started

Safe across processes via fcntl.flock. Auto-creates dir.
"""
import csv
import fcntl
import os
import threading

_LOCK = threading.Lock()  # in-process serialization (flock handles cross-process)

_HEADERS = {
    'executor': ['wf_id', 'iter_idx', 'request_type', 'requested_count',
                 't_engine_request_sent', 't_engine_reply_received', 'outcome'],
    'scheduler': ['wf_id', 'iter_idx', 'request_type', 'granted_count',
                  't_scheduler_request_observed', 't_scheduler_reply_sent'],
    'iteration': ['wf_id', 'iter_idx', 'attempt', 't_iteration_started'],
}


def _path(role):
    base = os.environ.get('NEGOTIATION_LOG_DIR')
    if not base:
        return None
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f'negotiation_{role}.csv')


def log(role, **fields):
    """Append one row to negotiation_<role>.csv. No-op if env var unset."""
    path = _path(role)
    if path is None:
        return
    header = _HEADERS.get(role)
    if header is None:
        return
    row = [fields.get(col, '') for col in header]
    try:
        with _LOCK:
            need_header = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, 'a', newline='') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    w = csv.writer(f)
                    if need_header:
                        w.writerow(header)
                    w.writerow(row)
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        # Never break the run because of instrumentation
        print(f'[negotiation_log] WARN: failed to write {role} row: {e}')


def count_hosts(ips_dict):
    """Sum host counts across clusters in a {cluster: {instance: (count, [ips])}} dict."""
    if not isinstance(ips_dict, dict):
        return 0
    n = 0
    for cluster in ('on-prem', 'reserved', 'on-demand'):
        for _, v in ips_dict.get(cluster, {}).items():
            if isinstance(v, (tuple, list)) and len(v) >= 1:
                try:
                    n += int(v[0])
                except (TypeError, ValueError):
                    pass
    return n
