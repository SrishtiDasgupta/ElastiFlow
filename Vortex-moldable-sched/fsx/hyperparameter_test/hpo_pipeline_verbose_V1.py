import ray
from ray import tune, air
import os
import sys
import logging
from datetime import datetime
import torch

from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig, RunConfig
from ray.tune.integration.ray_train import TuneReportCallback
from ray.tune.schedulers import AsyncHyperBandScheduler
from train_cifar10_torch_ray_oomsafe import train_cifar10_torch  # Changed from Keras to PyTorch
import numpy as np
import argparse
import pandas as pd
import json

MIN_TRIALS = 3
os.environ["RAY_TRAIN_WORKER_GROUP_START_TIMEOUT_S"] = "180"

# Setup comprehensive logging
def setup_logging():
    """Setup detailed logging for debugging"""
    log_format = '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f'/fsx/workflow_debug_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        ]
    )

    # Set Ray logging to INFO level
    ray_logger = logging.getLogger("ray")
    ray_logger.setLevel(logging.INFO)

    return logging.getLogger(__name__)

logger = setup_logging()

class TunePipeline:
    def __init__(
        self,
        train_fn,
        initial_search,
        metric="accuracy",
        mode="max",
        target=0.99,
        max_rounds=5,
        num_samples=1,
        scheduler=AsyncHyperBandScheduler(max_t=20),
        improvement_threshold=1e-3,
        patience=2,
        numHosts=1,
        refine_pct=0.2
    ):
        logger.info("=" * 80)
        logger.info("INITIALIZING TunePipeline")
        logger.info("=" * 80)

        self.train_fn = train_fn
        self.search_space = initial_search,  # Note: this creates a tuple
        self.mode = mode
        self.metric = metric
        self.target = target
        self.max_rounds = max_rounds
        self.num_samples = num_samples
        self.scheduler = scheduler
        self.improvement_threshold = improvement_threshold
        self.patience = patience
        self.numHosts = numHosts
        self.host_alloc = [0] * num_samples

        logger.info(f"Training function: {train_fn.__name__}")
        logger.info(f"Search space: {self.search_space}")
        logger.info(f"Target metric: {metric} {mode} {target}")
        logger.info(f"Number of samples: {num_samples}")
        logger.info(f"Available hosts: {numHosts}")
        logger.info(f"Improvement threshold: {improvement_threshold}")

    def allocate_trials(self, num_trials, num_hosts):
        """Distribute available hosts across trials as evenly as possible"""
        logger.info(f"ALLOCATING RESOURCES: {num_hosts} hosts across {num_trials} trials")

        base = max(1, num_hosts // max(1, num_trials))
        rem = max(0, num_hosts - base * num_trials)
        allocation = [base + (1 if i < rem else 0) for i in range(num_trials)]

        logger.info(f"Base allocation per trial: {base}")
        logger.info(f"Remainder hosts: {rem}")
        logger.info(f"Final allocation: {allocation}")
        logger.info(f"Total allocated: {sum(allocation)}/{num_hosts}")

        return allocation

    def train_driver_fn(self, config: dict):
        """Ray Tune-compatible wrapper that creates distributed PyTorch trainer"""
        from ray.train.torch import TorchTrainer
        from ray.train import ScalingConfig, RunConfig
        import socket
        
        trial_id = tune.get_trial_id() if hasattr(tune, 'get_trial_id') else "unknown"
        num_workers = config.get("num_workers", 1)
        
        logger.info(f"TRIAL {trial_id} on {socket.gethostname()}: Starting with {num_workers} workers")
        logger.info(f"TRIAL {trial_id}: Config = {config}")

        trainer = TorchTrainer(
            train_loop_per_worker=self.train_fn,
            scaling_config=ScalingConfig(
                num_workers=num_workers,
                use_gpu=True,
            ),
            train_loop_config=config,
            run_config=RunConfig(
                name=f"train_{trial_id}",
                storage_path='/fsx/ray_results',
            )
        )
    
        result = trainer.fit()
    
        # Handle metrics properly
        accuracy = 0.0
        if result.metrics is not None:
            accuracy = result.metrics.get("best_accuracy") or result.metrics.get("accuracy") or 0.0
        else:
            mdf = getattr(result, "metrics_dataframe", None)
            if mdf is not None and not mdf.empty:
                if "best_accuracy" in mdf.columns:
                    accuracy = float(mdf["best_accuracy"].iloc[-1])
                elif "accuracy" in mdf.columns:
                    accuracy = float(mdf["accuracy"].iloc[-1])
        
        logger.info(f"TRIAL {trial_id}: Finished with accuracy={accuracy}")
        return {"accuracy": accuracy}

    def run(self, cohesion=None):
        """Execute the hyperparameter optimization pipeline"""
        logger.info("=" * 80)
        logger.info("STARTING HYPERPARAMETER OPTIMIZATION RUN")
        logger.info("=" * 80)

        try:
            # Initialize Ray
            logger.info("Initializing Ray...")
            ray.init(address="auto", ignore_reinit_error=True)
            logger.info(f"Ray cluster info: {ray.cluster_resources()}")

            best_metric = cohesion or 0.0
            logger.info(f"Baseline metric for comparison: {best_metric}")
            output = {}

            # Allocate hosts across trials
            logger.info("Allocating computational resources...")
            host_alloc = self.allocate_trials(self.num_samples, self.numHosts)
            it = iter(host_alloc)

            # Setup scheduler for early stopping and resource management
            logger.info("Setting up hyperparameter scheduler...")
            scheduler = AsyncHyperBandScheduler(
                max_t=200,
                metric=self.metric,
                mode=self.mode,
            )
            logger.info(f"Scheduler: {scheduler}")

            logger.info("=" * 60)
            logger.info("PRE-TUNER DIAGNOSTICS")
            logger.info("=" * 60)
            logger.info(f"Search space type: {type(self.search_space)}")
            logger.info(f"Search space[0]: {self.search_space[0]}")
            logger.info(f"train_driver_fn callable: {callable(self.train_driver_fn)}")
            logger.info(f"Cluster resources: {ray.cluster_resources()}")
            logger.info(f"Available resources: {ray.available_resources()}")

            # Test if train_driver_fn can be serialized
            import cloudpickle
            try:
                cloudpickle.dumps(self.train_driver_fn)
                logger.info("train_driver_fn serialization: OK")
            except Exception as e:
                logger.error(f"train_driver_fn serialization FAILED: {e}")


            # Add dynamic worker allocation to search space
            logger.info("Adding dynamic worker allocation to search space...")
            self.search_space[0]["num_workers"] = tune.sample_from(lambda _: next(it))
            logger.info(f"Updated search space: {self.search_space[0]}")

            # Create and configure Ray Tune experiment
            logger.info("Creating Ray Tune experiment...")
            # Allocate workers per trial
            host_alloc = self.allocate_trials(self.num_samples, self.numHosts)
            logger.info(f"Host allocation for trials: {host_alloc}")

            # Add num_workers to search space
            #self.search_space[0]["num_workers"] = tune.grid_search(host_alloc)
            self.search_space[0]["num_workers"] = tune.choice([1])

            # Wrap trainable with resource requirements
            # Each trial needs 1 CPU for driver + GPUs will be requested by TorchTrainer
            """ trainable = tune.with_resources(
                self.train_driver_fn,
                resources={"CPU": 1, "GPU": 1}  # 1 GPU per trial
            ) """

            num_workers = self.search_space[0].get("num_workers", 1)
            if isinstance(num_workers, int):
                self.search_space[0]["num_workers"] = tune.choice([num_workers])

            max_concurrent = self.search_space[0].pop("max_concurrent_trials", 2)

            tuner = tune.Tuner(
                self.train_driver_fn,  # No wrapper needed
                tune_config=tune.TuneConfig(
                    scheduler=scheduler,
                    num_samples=self.num_samples,
                    #max_concurrent_trials=2,  # Both GPUs
                    max_concurrent_trials=max_concurrent,

                ),
                run_config=tune.RunConfig(
                    name="pytorch_hpo_exp",
                    stop={self.metric: self.target},
                    storage_path='/fsx/ray_results',
                    verbose=2
                ),
                param_space=self.search_space[0]
            )
            logger.info("Ray Tune experiment configured")

            # Execute hyperparameter search
            logger.info("EXECUTING HYPERPARAMETER SEARCH...")
            logger.info("This may take a while depending on your configuration...")
            logger.info("=" * 60)
            logger.info("ABOUT TO CALL tuner.fit()")
            logger.info("=" * 60)
            try:
                results = tuner.fit()
                logger.info("tuner.fit() COMPLETED SUCCESSFULLY")
            except Exception as e:
                logger.error(f"tuner.fit() FAILED: {e}")
                import traceback
                logger.error(traceback.format_exc())
                raise
            results = tuner.fit()
            logger.info("Hyperparameter search completed!")

            # Analyze results and compute statistics
            logger.info("Analyzing results...")
            df = results.get_dataframe()
            logger.info(f"Results dataframe shape: {df.shape}")
            logger.info(f"Results columns: {df.columns.tolist()}")

            if not df.empty:
                logger.info(f"Results head:\n{df.head()}")
                results_file = 'pytorch_hpo_results.csv'
                df.to_csv(results_file)
                logger.info(f"Results saved to: {results_file}")
            else:
                logger.warning("Results dataframe is empty!")

            # Calculate success rate for adaptive trial estimation
            logger.info("Calculating success rate...")
            if self.metric in df.columns:
                accs = df[self.metric].values
                hits = (accs >= best_metric + self.improvement_threshold).sum()
                success_rate = hits / max(len(accs), 1)
                logger.info(f"Successful trials: {hits}/{len(accs)}")
                logger.info(f"Success rate: {success_rate:.3f}")
            else:
                logger.warning(f"Metric '{self.metric}' not found in results!")
                success_rate = 0.0

            # Get best configuration and performance
            logger.info("Extracting best result...")
            try:
                best = results.get_best_result(metric=self.metric, mode=self.mode)
                score = best.metrics[self.metric]
                configs = best.config.copy()  # Make a copy to avoid modification issues
                logger.info(f"Best score: {score}")
                logger.info(f"Best config: {configs}")
            except Exception as e:
                logger.error(f"Error getting best result: {e}")
                score = 0.0
                configs = {}

            # Prepare output with best config
            output['config'] = configs

            # Adaptive trial estimation using statistical model
            logger.info("Computing adaptive trial estimation...")
            if success_rate <= 0:
                # No successful trials, use minimum
                next_trials = MIN_TRIALS
                logger.info(f"No successful trials found, using minimum: {next_trials}")
            else:
                # Calculate trials needed: m = ceil(log(1-target)/log(1-q))
                try:
                    m = int(np.ceil(np.log(1 - self.target) / np.log(1 - success_rate)))
                    next_trials = max(MIN_TRIALS, min(m, 50))  # Cap at 50
                    logger.info(f"Statistical estimate: {m} trials")
                    logger.info(f"Capped estimate: {next_trials} trials")
                except Exception as e:
                    logger.error(f"Error in statistical calculation: {e}")
                    next_trials = MIN_TRIALS

            output["config"]["next_trials"] = next_trials

            # logger.info("=" * 60)
            # logger.info("OPTIMIZATION RESULTS SUMMARY")
            # logger.info("=" * 60)
            # logger.info(f"Best {self.metric}: {score:.4f}")
            # logger.info(f"Target {self.metric}: {self.target}")
            # logger.info(f"Success rate: {success_rate:.3f}")
            # logger.info(f"Next trials recommended: {next_trials}")

            # Check if target achieved
            if (self.mode == "max" and score >= self.target) or (self.mode == "min" and score <= self.target):
                logger.info("🎉 TARGET METRIC ACHIEVED! Stopping optimization.")
                output["config"]["next_trials"] = 0
            else:
                logger.info(f"Target not yet achieved. Continue with {next_trials} more trials.")

            return output

        except Exception as e:
            logger.error("=" * 80)
            logger.error("CRITICAL ERROR IN OPTIMIZATION PIPELINE")
            logger.error("=" * 80)
            logger.error(f"Error: {str(e)}")
            logger.error(f"Error type: {type(e)}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise


def refine_space(best_config):
    """Intelligently refine hyperparameter search space around best configuration"""
    logger.info("=" * 60)
    logger.info("REFINING SEARCH SPACE")
    logger.info("=" * 60)
    logger.info(f"Input config: {best_config}")

    new_space = {}

    for k, v in best_config.items():
        logger.info(f"Processing parameter: {k} = {v} (type: {type(v)})")

        if k == "next_trials":
            # Skip this metadata field
            logger.info(f"  Skipping metadata field: {k}")
            continue
        elif k == "hidden":
            # For CIFAR-10, this should always be 10 (number of classes)
            new_space[k] = tune.choice([10])
            logger.info(f"  Fixed classes: {new_space[k]}")
        elif k == "batch_size":
            # Keep batch_size FIXED (match instrumentation baseline)
            new_space[k] = tune.choice([v])
            logger.info(f"  Batch size: fixed at {v}")
        elif k == "image_size":
            # Keep image_size FIXED (match instrumentation baseline)
            new_space[k] = tune.choice([v])
            logger.info(f"  Image size: fixed at {v}")
        elif k == "epoch":
            # Limit epochs to reasonable range
            low = max(1, int(v * 0.8))
            high = min(10, int(v * 1.5))
            new_space[k] = tune.randint(low, high)
            logger.info(f"  Epoch range: {low} to {high}")
        elif isinstance(v, float):
            if k == "learning_rate":
                # Log-uniform distribution for learning rate
                low = max(1e-5, v * 0.1)
                high = min(1e-1, v * 10)
                new_space[k] = tune.loguniform(low, high)
                logger.info(f"  Learning rate (log): {low:.2e} to {high:.2e}")
            else:
                # Standard uniform for other floats (momentum, etc.)
                if k == "momentum":
                    low = max(0.1, v * 0.9)
                    high = min(0.99, v * 1.1)
                else:
                    low = max(0, v * 0.8)
                    high = v * 1.2
                new_space[k] = tune.uniform(low, high)
                logger.info(f"  Float {k}: {low:.4f} to {high:.4f}")
        elif isinstance(v, int) and k not in ["hidden", "batch_size", "image_size", "epoch", "amp", "train_backbone"]:
            # Handle other integer parameters (exclude amp/train_backbone - they are booleans)
            low = max(1, int(v * (1 - 0.5)))
            high = int(v * (1 + 0.5))
            if low >= high:  # Ensure valid range
                high = low + 1  # Adjust high to be greater than low
            new_space[k] = tune.randint(low,high)
            logger.info(f"  Integer {k}: {low} to {high}")
        elif isinstance(v, bool):
            # Keep boolean values as choices
            new_space[k] = tune.choice([v])
            logger.info(f"  Boolean {k}: fixed at {v}")
        else:
            # Keep other values as fixed choices (including model_name)
            new_space[k] = tune.choice([v])
            if k == "model_name":
                logger.info(f"  ⚠️  MODEL_NAME PARAMETER: fixed at {v}")
            else:
                logger.info(f"  Other {k}: fixed at {v}")

    # Ensure required PyTorch-specific parameters exist
    if "amp" not in new_space:
        new_space["amp"] = tune.choice([True])  # Keep AMP enabled
        logger.info("  Added missing parameter: amp = True")
    if "train_backbone" not in new_space:
        new_space["train_backbone"] = tune.choice([False])  # Keep backbone frozen
        logger.info("  Added missing parameter: train_backbone = False")
    if "data_dir" not in new_space:
        new_space["data_dir"] = tune.choice([os.path.expanduser("~/cifar10")])
        logger.info(f"  Added missing parameter: data_dir = {os.path.expanduser('~/cifar10')}")

    logger.info(f"Final refined search space: {new_space}")
    return new_space

def get_default_config():
    """Return a default configuration for testing"""
    return {
        "epoch": 3,
        "learning_rate": 0.01,
        "momentum": 0.9,
        "hidden": 10,
        "batch_size": 64,
        "image_size": 160,
        "amp": True,
        "train_backbone": False,
        "data_dir": os.path.expanduser("~/cifar10"),
        "next_trials": 2
    }

if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("PYTORCH HYPERPARAMETER OPTIMIZATION WORKFLOW (TEST MODE)")
    logger.info("=" * 80)

    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument("--hosts", type=int, default=2, help="Total number of available hosts")
        parser.add_argument("--config-file", type=str, help="JSON config file (optional)")
        parser.add_argument("--test", action="store_true", help="Use default test configuration")
        args = parser.parse_args()
        logger.info(f"Command line args: {args}")

        # Get configuration

        if args.test:
            logger.info("Using default test configuration...")
            request = get_default_config()
        elif args.config_file:
            logger.info(f"Reading configuration from file: {args.config_file}")
            with open(args.config_file, 'r') as f:
                request = json.load(f)
        else:
            logger.info("Reading JSON configuration from stdin...")
            try:
                request = json.load(sys.stdin)
            except Exception as e:
                logger.error(f"Failed to read from stdin: {e}")
                logger.info("Using default configuration as fallback...")
                request = get_default_config()

        logger.info(f"Input configuration: {json.dumps(request, indent=2)}")

        # Extract number of trials to run
        num_samples = request.get("next_trials", 3)
        logger.info(f"Number of trials to run: {num_samples}")

        # Refine search space around previous best configuration
        logger.info("Refining search space...")
        initial_space = refine_space(request)
        logger.info(f"⚠️  MODEL CHECK: model_name in initial_space = {initial_space.get('model_name', 'NOT FOUND')}")
        logger.info(f"⚠️  FULL SEARCH SPACE: {initial_space}")

        # Create and configure the optimization pipeline
        logger.info("Creating optimization pipeline...")
        pipeline = TunePipeline(
            train_fn=train_cifar10_torch,  # PyTorch training function
            initial_search=initial_space,
            metric="accuracy",
            target=0.99,
            max_rounds=5,
            num_samples=num_samples,
            improvement_threshold=1e-2,
            patience=2,
            numHosts=args.hosts
        )

        # Execute the optimization with baseline performance
        logger.info("Executing optimization pipeline...")
        best_result = pipeline.run(cohesion=0.85)  # Baseline accuracy for comparison

        # Output results for next iteration
        # logger.info("=" * 80)
        # logger.info("📋 FINAL OUTPUT FOR NEXT ITERATION")
        # logger.info("=" * 80)

        # Create a detailed output summary
        output_summary = {
            "iteration_summary": {
                "trials_completed": num_samples,
                "trials_next": best_result.get("config", {}).get("next_trials", 0),
                "best_accuracy": best_result.get("config", {}).get("accuracy", "N/A"),
                "adaptive_strategy": "expand" if best_result.get("config", {}).get("next_trials", 0) > num_samples else "converge"
            },
            "hyperparameter_evolution": "Check logs above for detailed parameter changes",
            "next_iteration_config": best_result
        }

        # logger.info("📊 ITERATION SUMMARY:")
        # logger.info(f"   Trials this round: {num_samples}")
        # logger.info(f"   Trials next round: {best_result.get('config', {}).get('next_trials', 0)}")
        # logger.info(f"   Evolution direction: {output_summary['iteration_summary']['adaptive_strategy']}")
        best_result["config"].pop("amp")
        best_result["config"].pop("train_backbone")
        print(json.dumps(best_result))

        # logger.info("🎯 WORKFLOW ITERATION COMPLETED SUCCESSFULLY!")
        # logger.info("💡 Key adaptations made:")
        # logger.info("   1. Hyperparameters refined around best performers")
        # logger.info("   2. Trial count adjusted based on success rate")
        # logger.info("   3. Resource allocation optimized for next iteration")
        # logger.info("=" * 80)

    except Exception as e:
        logger.error("=" * 80)
        logger.error("WORKFLOW FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        sys.exit(1)