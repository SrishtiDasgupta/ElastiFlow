#!/usr/bin/env python3
# Minimal HPO pipeline with just the three metrics requested.

import os
from pathlib import Path

import argparse, json, os, sys, time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import ray
from ray import tune
from ray.tune import Callback
from ray.tune.schedulers import ASHAScheduler
from ray import air

# ---------- tiny helpers ----------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def to_ts(iso_s: str) -> float:
    return datetime.fromisoformat(iso_s).timestamp()

class CSV:
    def __init__(self, path: Path, header: list[str]):
        self.path = path
        self.header = header
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(",".join(header) + "\n")

    def append(self, row: Dict[str, Any]):
        vals = ["" if row.get(h) is None else str(row.get(h)) for h in self.header]
        with self.path.open("a", encoding="utf-8") as f:
            f.write(",".join(vals) + "\n")

# ---------- the only callback we need ----------

class MinimalLifecycleCallback(Callback):
    """
    Captures:
      - trial submitted/running/first_result/last_result/finished timestamps
      - sums 'pure_train_sec' from trial results if provided by the trainable
    """
    def __init__(self):
        self.trials: Dict[str, Dict[str, Any]] = {}

    def _s(self, trial):
        tid = trial.trial_id
        if tid not in self.trials:
            self.trials[tid] = {
                "trial_id": tid,
                "submitted_at": None,
                "running_at": None,
                "first_result_at": None,
                "last_result_at": None,
                "finished_at": None,
                "pure_train_sec_sum": 0.0,
            }
        return self.trials[tid]

    def on_trial_add(self, iteration, trials, trial, **info):
        self._s(trial)["submitted_at"] = now_iso()

    def on_trial_start(self, iteration, trials, trial, **info):
        self._s(trial)["running_at"] = now_iso()

    def on_trial_result(self, iteration, trials, trial, result, **info):
        s = self._s(trial)
        if s["first_result_at"] is None:
            s["first_result_at"] = now_iso()
        s["last_result_at"] = now_iso()
        # accumulate pure training time if the trainable reports it
        v = result.get("pure_training_time_s")
        if isinstance(v, (int, float)):
            s["pure_train_sec_sum"] += float(v)

    def on_trial_complete(self, iteration, trials, trial, **info):
        self._s(trial)["finished_at"] = now_iso()

    def on_trial_error(self, iteration, trials, trial, **info):
        self._s(trial)["finished_at"] = now_iso()

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-samples", type=int, default=4)
    ap.add_argument("--gpus-per-trial", type=float, default=1.0)
    ap.add_argument("--cpus-per-trial", type=float, default=2.0)
    ap.add_argument("--trainable", type=str,
                    default="train_cifar10_torch_ray_oomsafe_instrumented:train_driver_fn",
                    help="Format: module.path:callable_name")
    ap.add_argument("--metrics-dir", type=str, default="")
    ap.add_argument("--smoke-test", action="store_true")
    args = ap.parse_args()


    if os.getenv("MEASURE", "").strip().lower() in ("1", "true", "yes", "on"):
        args.num_samples=1

    # run dir
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(args.metrics_dir) if args.metrics_dir else Path("runs") / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    storage_uri=f"file://{(run_dir / 'tune').resolve()}"

    # import trainable
    if ":" not in args.trainable:
        raise ValueError("--trainable must be like 'module:callable'")
    mod, fn = args.trainable.split(":", 1)
    train_fn = getattr(__import__(mod, fromlist=[fn]), fn)

    # env vars
    env_forward = {k: os.environ[k] for k in ["MEASURE","EPOCHS","HOSTS","MODEL_NAME"] if k in os.environ}

    # connect to ray
    #ray.init(ignore_reinit_error=True)
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, runtime_env={"env_vars": env_forward})

    # create trainable
    trainable = tune.with_resources(train_fn, {"cpu": args.cpus_per_trial, "gpu": 0})

    # simple search space
    #space = {"lr": tune.loguniform(1e-4, 1e-2), "batch_size": tune.choice([64, 160])}
    pace = {
            "epoch": int(os.environ.get("EPOCHS", "3")),
            "learning_rate": 5e-3,
            "momentum": 0.9,
            "hidden": 10,
            "batch_size": 64,
            "image_size": 160,
            "amp": True,
            "train_backbone": False, 
            "data_dir": os.path.expanduser("~/cifar10"),
            # num workers is overridden inside the train script from HOSTS anyway
            "num_workers": int(os.environ.get("HOSTS", "1")),
            }
    #space = { "num_workers": int(os.environ.get("HOSTS", "1"))}
    if args.smoke_test:
        space = {"lr": tune.grid_search([1e-3]), "batch_size": tune.grid_search([64])}
        args.num_samples = 1

    scheduler = ASHAScheduler(time_attr="training_iteration", max_t=10 if args.smoke_test else 50,
                              grace_period=1, reduction_factor=2)

    cb = MinimalLifecycleCallback()

    start_ts = time.time()
    """tuner = tune.Tuner(
            tune.with_resources(train_fn, {"cpu": args.cpus_per_trial, "gpu": args.gpus_per_trial}),
        tune_config=tune.TuneConfig(
            metric="val_accuracy", mode="max",
            num_samples=args.num_samples, scheduler=scheduler
        ),
        run_config=tune.RunConfig(
            storage_path=storage_uri,
            runtime_env={"env_vars": env_forward},
            callbacks=[cb], 
            verbose=1
        ),
        param_space=space,
    )"""
    tuner = tune.Tuner(
            trainable,
            tune_config=tune.TuneConfig(
                metric="accuracy", mode="max",
                num_samples=args.num_samples, scheduler=scheduler, max_concurrent_trials=1,
            ),
            run_config=air.RunConfig(
                name="round0",
                storage_path=storage_uri,
                callbacks=[cb],
                verbose=1,
            ),
            param_space=space,
        )

    result = tuner.fit()
    end_ts = time.time()

    # write per-trial CSV
    per_trial = CSV(run_dir / "per_trial.csv",
                    ["trial_id", "setup_sec", "teardown_sec", "pure_train_sec"])
    total_pure_train = 0.0
    for s in cb.trials.values():
        # setup = running -> first_result
        setup = ""
        if s["running_at"] and s["first_result_at"]:
            setup = f"{to_ts(s['first_result_at']) - to_ts(s['running_at']):.3f}"
        # teardown = finished -> last_result (finished occurs after last_result)
        teardown = ""
        if s["finished_at"] and s["last_result_at"]:
            teardown = f"{to_ts(s['finished_at']) - to_ts(s['last_result_at']):.3f}"
        pure = s["pure_train_sec_sum"] if s["pure_train_sec_sum"] else ""
        if isinstance(pure, float):
            total_pure_train += pure
            pure = f"{pure:.3f}"
        per_trial.append({
            "trial_id": s["trial_id"],
            "setup_sec": setup,
            "teardown_sec": teardown,
            "pure_train_sec": pure,
        })

    # coordination overhead:
    # = total wall clock - sum(pure model training time reported by trials)
    total_wall = end_ts - start_ts
    coordination_overhead = None
    if total_pure_train > 0:
        coordination_overhead = max(0.0, total_wall - total_pure_train)

    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump({
            "started_at": datetime.fromtimestamp(start_ts).isoformat(timespec="seconds"),
            "finished_at": datetime.fromtimestamp(end_ts).isoformat(timespec="seconds"),
            "total_wall_clock_sec": round(total_wall, 3),
            "sum_pure_train_sec": round(total_pure_train, 3),
            "coordination_overhead_sec": (round(coordination_overhead, 3)
                                          if coordination_overhead is not None else None),
            "note": "coordination_overhead = total_wall - sum(pure_train_sec) "
                    "(requires trainable to report 'pure_train_sec')"
        }, f, indent=2)

    print(f"\nmetrics saved in: {run_dir}")
    print(" - per_trial.csv (setup_sec, teardown_sec, pure_train_sec)")
    print(" - summary.json   (coordination_overhead_sec)")

if __name__ == "__main__":
    main()
