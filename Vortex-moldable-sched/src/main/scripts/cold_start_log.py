import csv
import os
import threading
import time

_LOG_PATH = os.environ.get("COLD_START_LOG", "/fsx/cold_start_log.csv")
_FIELDS = [
    "instance_id", "instance_type", "role", "az",
    "t_request", "t_running", "t_ssh", "t_setup_done", "t_executor_listening",
    "outcome",
]
_lock = threading.Lock()
_pending = {}  # instance_id -> dict of timestamps


def _ensure_header():
    if os.path.exists(_LOG_PATH):
        return
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    with open(_LOG_PATH, "w", newline="") as f:
        csv.writer(f).writerow(_FIELDS)


def mark(instance_id: str, event: str, **meta):
    """Record a single event timestamp. Events: t_request, t_running, t_ssh, t_setup_done, t_executor_listening."""
    with _lock:
        rec = _pending.setdefault(instance_id, {"instance_id": instance_id})
        rec[event] = time.time()
        for k, v in meta.items():
            rec[k] = v


def finalize(instance_id: str, outcome: str):
    """Write the row and clear pending state."""
    with _lock:
        rec = _pending.pop(instance_id, None)
        if rec is None:
            return
        rec["outcome"] = outcome
        _ensure_header()
        with open(_LOG_PATH, "a", newline="") as f:
            csv.writer(f).writerow([rec.get(k, "") for k in _FIELDS])
