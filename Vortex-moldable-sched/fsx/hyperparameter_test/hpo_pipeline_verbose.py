import ray
from ray import tune
import os
import sys
import logging
from datetime import datetime
import torch
from typing import List, Tuple, Dict, Any
import pandas as pd

# Set timeout for worker startup BEFORE importing ray.train
os.environ["RAY_TRAIN_WORKER_GROUP_START_TIMEOUT_S"] = "180"

from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig, RunConfig
from ray.tune.schedulers import AsyncHyperBandScheduler
from train_cifar10_torch_ray_oomsafe import train_cifar10_torch
import numpy as np
import argparse
import json

MIN_TRIALS = 3

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

    ray_logger = logging.getLogger("ray")
    ray_logger.setLevel(logging.INFO)

    return logging.getLogger(__name__)

logger = setup_logging()


# =============================================================================
# HYBRID BATCHING RESOURCE ALLOCATION ALGORITHM
# =============================================================================
#
# This algorithm optimizes GPU utilization for HPO by using a hybrid approach:
# 1. Run full parallel batches (1 GPU per trial) for maximum exploration
# 2. Handle remaining trials optimally based on divisibility and efficiency
#
# PROFILING DATA (VGG19 on G4/G5 instances):
# ┌─────────┬───────────┬──────────┬────────────┐
# │ Workers │ Time/Epoch│ Speedup  │ Efficiency │
# ├─────────┼───────────┼──────────┼────────────┤
# │ 1 GPU   │ 82.0s     │ 1.00x    │ 100%       │
# │ 2 GPUs  │ 42.0s     │ 1.95x    │ 97.6%      │
# │ 4 GPUs  │ 21.4s     │ 3.84x    │ 96.0%      │
# └─────────┴───────────┴──────────┴────────────┘
#
# KEY INSIGHT: With high scaling efficiency (>90%), we should:
# - Use all GPUs even for remainder trials (no idle GPUs)
# - Balance between parallel exploration and distributed speedup
#
# DECISION MATRIX (4 hosts example):
# ┌────────┬─────────────────────────────────────────────────────────┐
# │ Trials │ Strategy                                                │
# ├────────┼─────────────────────────────────────────────────────────┤
# │ 1      │ 1 trial × 4 GPUs (distributed)                         │
# │ 2      │ 2 trials × 2 GPUs each (balanced, concurrent)          │
# │ 3      │ 3 trials × 4 GPUs each (distributed, sequential)       │
# │ 4      │ 4 trials × 1 GPU each (parallel, concurrent)           │
# │ 5      │ Batch1: 4×1 GPU (parallel) + Batch2: 1×4 GPUs (dist)   │
# │ 6      │ Batch1: 4×1 GPU (parallel) + Batch2: 2×2 GPUs (bal)    │
# │ 7      │ Batch1: 4×1 GPU + Batch2: 2×2 GPU + Batch3: 1×4 GPU    │
# │ 8      │ 2 batches of 4×1 GPU each (parallel)                   │
# └────────┴─────────────────────────────────────────────────────────┘
# =============================================================================


def calculate_optimal_batches(
    num_trials: int, 
    num_hosts: int, 
    efficiency: float = 0.92
) -> List[Dict[str, Any]]:
    """
    Calculate the optimal batch execution plan for HPO trials.
    
    This function implements a hybrid batching strategy that:
    1. Maximizes parallel exploration when possible
    2. Minimizes idle GPU time for remainder trials
    3. Uses distributed training when it provides speedup benefits
    
    Algorithm Overview:
    ==================
    
    Step 1: Calculate full parallel batches
    ---------------------------------------
    When trials >= hosts, we can run full parallel batches where each
    trial uses 1 GPU. This maximizes exploration of hyperparameter space.
    
        full_batches = num_trials // num_hosts
        remainder = num_trials % num_hosts
    
    Step 2: Handle remainder trials optimally
    -----------------------------------------
    For remainder trials (when remainder > 0), we have several options:
    
    a) If remainder == 0: Done, no leftover trials
    
    b) If remainder == 1: Use ALL GPUs for single trial (distributed)
       - Time: T_h (one trial with h GPUs)
       - Better than: T_1 with (h-1) idle GPUs
    
    c) If num_hosts % remainder == 0: Use BALANCED allocation
       - Each trial gets (num_hosts / remainder) GPUs
       - All remainder trials run concurrently
       - Example: 4 hosts, 2 remaining → 2 trials × 2 GPUs each
    
    d) Otherwise: Compare DISTRIBUTED vs PARALLEL+IDLE
       - Distributed: remainder trials × T_h (sequential, all GPUs)
       - Parallel: 1 batch × T_1 (concurrent, some GPUs idle)
       - Choose whichever is faster based on efficiency
    
    Time Complexity Analysis:
    ========================
    Let T_n = time for 1 trial with n GPUs
    With efficiency η: T_n ≈ T_1 / (n × η)
    
    For 4 hosts, η = 0.92:
    - T_1 = 1.00 (baseline)
    - T_2 = 1/(2×0.92) = 0.54
    - T_4 = 1/(4×0.92) = 0.27
    
    Args:
        num_trials: Total number of HPO trials to run
        num_hosts: Number of available GPUs
        efficiency: Scaling efficiency from profiling (default 0.92)
        
    Returns:
        List of batch configurations, each containing:
        - workers_per_trial: GPUs per trial in this batch
        - concurrent_trials: Trials running in parallel
        - num_trials: Number of trials in this batch
        - mode: Description of the batch strategy
        
    Example:
        >>> calculate_optimal_batches(7, 4, 0.92)
        [
            {'workers': 1, 'concurrent': 4, 'trials': 4, 'mode': 'parallel'},
            {'workers': 2, 'concurrent': 2, 'trials': 2, 'mode': 'balanced'},
            {'workers': 4, 'concurrent': 1, 'trials': 1, 'mode': 'distributed'}
        ]
    """
    
    batches = []
    remaining_trials = num_trials
    
    logger.info("=" * 70)
    logger.info("CALCULATING OPTIMAL BATCH EXECUTION PLAN")
    logger.info("=" * 70)
    logger.info(f"Input: {num_trials} trials, {num_hosts} GPUs, efficiency={efficiency}")
    
    # =========================================================================
    # STEP 1: Fill complete parallel batches
    # =========================================================================
    # When we have more trials than GPUs, run them in parallel batches
    # Each trial gets 1 GPU, maximizing hyperparameter exploration
    # =========================================================================
    
    if remaining_trials >= num_hosts:
        full_parallel_batches = remaining_trials // num_hosts
        trials_in_parallel = full_parallel_batches * num_hosts
        
        batches.append({
            'workers_per_trial': 1,
            'concurrent_trials': num_hosts,
            'num_trials': trials_in_parallel,
            'num_batches': full_parallel_batches,
            'mode': 'parallel'
        })
        
        remaining_trials = remaining_trials % num_hosts
        
        logger.info(f"Step 1: PARALLEL BATCHES")
        logger.info(f"  - {full_parallel_batches} batch(es) of {num_hosts} trials each")
        logger.info(f"  - Total: {trials_in_parallel} trials (1 GPU per trial)")
        logger.info(f"  - Remaining: {remaining_trials} trials")
    
    # =========================================================================
    # STEP 2: Handle remainder trials optimally
    # =========================================================================
    # For leftover trials, choose the fastest strategy:
    # - Single trial: use all GPUs (distributed)
    # - Perfect division: balanced allocation
    # - Otherwise: compare distributed vs parallel
    # =========================================================================
    
    if remaining_trials == 0:
        logger.info(f"Step 2: No remainder trials - done!")
        
    elif remaining_trials == 1:
        # ---------------------------------------------------------------------
        # Case 2a: Single remaining trial
        # ---------------------------------------------------------------------
        # Use ALL GPUs for this one trial (distributed training)
        # This is always better than running 1 trial with idle GPUs
        #
        # Time comparison:
        #   Distributed: T_h = T_1 / (h × η)
        #   Parallel:    T_1 (with h-1 idle GPUs)
        #
        # For h=4, η=0.92: T_4 = 0.27 < T_1 = 1.00 ✓
        # ---------------------------------------------------------------------
        
        batches.append({
            'workers_per_trial': num_hosts,
            'concurrent_trials': 1,
            'num_trials': 1,
            'num_batches': 1,
            'mode': 'distributed'
        })
        
        logger.info(f"Step 2: SINGLE REMAINDER → DISTRIBUTED")
        logger.info(f"  - 1 trial using all {num_hosts} GPUs")
        logger.info(f"  - Speedup: ~{num_hosts * efficiency:.1f}x vs single GPU")
        
        remaining_trials = 0
        
    elif num_hosts % remaining_trials == 0:
        # ---------------------------------------------------------------------
        # Case 2b: Perfect division (balanced allocation)
        # ---------------------------------------------------------------------
        # GPUs divide evenly among remaining trials
        # All trials run concurrently with multi-GPU each
        #
        # Example: 4 hosts, 2 remaining
        #   → 2 trials × 2 GPUs each, running concurrently
        #   → Time: T_2 = 0.54 (both finish together)
        #
        # This is optimal because:
        #   - No idle GPUs
        #   - All trials complete simultaneously
        #   - Benefits from distributed training speedup
        # ---------------------------------------------------------------------
        
        workers = num_hosts // remaining_trials
        
        batches.append({
            'workers_per_trial': workers,
            'concurrent_trials': remaining_trials,
            'num_trials': remaining_trials,
            'num_batches': 1,
            'mode': 'balanced'
        })
        
        logger.info(f"Step 2: PERFECT DIVISION → BALANCED")
        logger.info(f"  - {remaining_trials} trials × {workers} GPUs each (concurrent)")
        logger.info(f"  - All trials complete simultaneously")
        
        remaining_trials = 0
        
    else:
        # ---------------------------------------------------------------------
        # Case 2c: Uneven remainder (compare strategies)
        # ---------------------------------------------------------------------
        # GPUs don't divide evenly. Compare two strategies:
        #
        # Strategy A - Distributed Sequential:
        #   Run each trial with ALL GPUs, one after another
        #   Time: remaining × T_h = remaining × T_1 / (h × η)
        #
        # Strategy B - Parallel with Idle GPUs:
        #   Run all trials at once, 1 GPU each, some GPUs idle
        #   Time: T_1 (single batch, but GPUs wasted)
        #
        # Decision: Distributed wins when:
        #   remaining × T_h < T_1
        #   remaining / (h × η) < 1
        #   remaining < h × η
        # ---------------------------------------------------------------------
        
        # Calculate time for each strategy (normalized to T_1 = 1.0)
        distributed_time = remaining_trials / (num_hosts * efficiency)
        parallel_time = 1.0  # One batch with idle GPUs
        
        logger.info(f"Step 2: UNEVEN REMAINDER → COMPARING STRATEGIES")
        logger.info(f"  - Remaining trials: {remaining_trials}")
        logger.info(f"  - Strategy A (distributed sequential): {remaining_trials} × T_{num_hosts}")
        logger.info(f"    Time: {remaining_trials} / ({num_hosts} × {efficiency}) = {distributed_time:.3f} × T_1")
        logger.info(f"  - Strategy B (parallel + idle): 1 batch, {num_hosts - remaining_trials} GPUs idle")
        logger.info(f"    Time: {parallel_time:.3f} × T_1")
        
        if distributed_time < parallel_time:
            # Distributed sequential is faster
            batches.append({
                'workers_per_trial': num_hosts,
                'concurrent_trials': 1,
                'num_trials': remaining_trials,
                'num_batches': remaining_trials,
                'mode': 'distributed_sequential'
            })
            
            logger.info(f"  → Winner: DISTRIBUTED SEQUENTIAL")
            logger.info(f"    {remaining_trials} trials × {num_hosts} GPUs each (sequential)")
            
        else:
            # Parallel with idle GPUs is faster
            idle_gpus = num_hosts - remaining_trials
            
            batches.append({
                'workers_per_trial': 1,
                'concurrent_trials': remaining_trials,
                'num_trials': remaining_trials,
                'num_batches': 1,
                'mode': 'parallel_idle'
            })
            
            logger.info(f"  → Winner: PARALLEL + IDLE")
            logger.info(f"    {remaining_trials} trials × 1 GPU each, {idle_gpus} GPUs idle")
        
        remaining_trials = 0
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    
    logger.info("=" * 70)
    logger.info("BATCH EXECUTION PLAN SUMMARY")
    logger.info("=" * 70)
    
    total_time_units = 0.0
    for i, batch in enumerate(batches):
        workers = batch['workers_per_trial']
        concurrent = batch['concurrent_trials']
        trials = batch['num_trials']
        num_batches = batch['num_batches']
        mode = batch['mode']
        
        # Calculate time for this batch group (in T_1 units)
        time_per_trial = 1.0 / (workers * efficiency) if workers > 1 else 1.0
        if mode in ['parallel', 'balanced', 'parallel_idle']:
            batch_time = time_per_trial * num_batches
        else:  # distributed_sequential
            batch_time = time_per_trial * trials
        
        total_time_units += batch_time
        
        logger.info(f"Batch Group {i+1}: {mode.upper()}")
        logger.info(f"  - Trials: {trials} ({num_batches} batch(es) of {concurrent})")
        logger.info(f"  - Config: {workers} GPU(s)/trial, {concurrent} concurrent")
        logger.info(f"  - Time: {batch_time:.3f} × T_1")
    
    logger.info("-" * 70)
    logger.info(f"TOTAL ESTIMATED TIME: {total_time_units:.3f} × T_1")
    
    # Compare to naive parallel (all 1 GPU, no optimization)
    naive_batches = (num_trials + num_hosts - 1) // num_hosts
    naive_time = naive_batches * 1.0
    savings = (naive_time - total_time_units) / naive_time * 100
    
    logger.info(f"NAIVE PARALLEL TIME:  {naive_time:.3f} × T_1")
    logger.info(f"TIME SAVINGS:         {savings:.1f}%")
    logger.info("=" * 70)
    
    return batches


def get_simple_allocation(
    num_trials: int, 
    num_hosts: int, 
    efficiency: float = 0.92
) -> Tuple[int, int, str]:
    """
    Simplified allocation for single-phase execution (Ray Tune compatibility).
    
    Since Ray Tune doesn't natively support multi-phase execution with different
    resource configurations, this function returns a single allocation that works
    for the entire HPO run. It chooses the best single-phase strategy.
    
    For true optimal execution, use calculate_optimal_batches() with manual
    multi-phase orchestration.
    
    Decision Logic:
    1. trials >= hosts: PARALLEL (1 GPU/trial, maximize exploration)
    2. hosts % trials == 0: BALANCED (perfect distribution)
    3. trials == 1: DISTRIBUTED (use all GPUs)
    4. Otherwise: Compare and choose best single strategy
    
    Args:
        num_trials: Number of HPO trials
        num_hosts: Number of available GPUs
        efficiency: Scaling efficiency (default 0.92)
        
    Returns:
        Tuple of (workers_per_trial, max_concurrent_trials, mode_string)
    """
    
    logger.info(f"Simple allocation: {num_trials} trials, {num_hosts} GPUs")
    
    if num_trials >= num_hosts:
        # More trials than GPUs - can only do 1 GPU per trial
        mode = "parallel"
        workers = 1
        concurrent = num_hosts
        logger.info(f"  → PARALLEL: {workers} GPU/trial, {concurrent} concurrent")
        
    elif num_hosts % num_trials == 0:
        # Perfect division - balanced is optimal
        mode = "balanced"
        workers = num_hosts // num_trials
        concurrent = num_trials
        logger.info(f"  → BALANCED: {workers} GPUs/trial, {concurrent} concurrent")
        
    elif num_trials == 1:
        # Single trial - use all GPUs
        mode = "distributed"
        workers = num_hosts
        concurrent = 1
        logger.info(f"  → DISTRIBUTED: {workers} GPUs/trial, {concurrent} concurrent")
        
    else:
        # Uneven - compare strategies
        distributed_time = num_trials / (num_hosts * efficiency)
        
        if distributed_time < 1.0:
            mode = "distributed_sequential"
            workers = num_hosts
            concurrent = 1
            logger.info(f"  → DISTRIBUTED SEQ: {workers} GPUs/trial, sequential")
        else:
            mode = "parallel_idle"
            workers = 1
            concurrent = num_trials
            idle = num_hosts - num_trials
            logger.info(f"  → PARALLEL+IDLE: {workers} GPU/trial, {idle} idle")
    
    return workers, concurrent, mode


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
        refine_pct=0.2,
        scaling_efficiency=0.92  # From profiling data
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
        self.scaling_efficiency = scaling_efficiency

        logger.info(f"Training function: {train_fn.__name__}")
        logger.info(f"Search space: {self.search_space}")
        logger.info(f"Target metric: {metric} {mode} {target}")
        logger.info(f"Number of samples (trials): {num_samples}")
        logger.info(f"Available hosts (GPUs): {numHosts}")
        logger.info(f"Scaling efficiency: {scaling_efficiency}")
        logger.info(f"Improvement threshold: {improvement_threshold}")

    def train_driver_fn(self, config: dict):
        """Ray Tune-compatible wrapper that creates distributed PyTorch trainer"""
        import time
        import socket
        
        # Get trial info
        trial_context = ray.tune.get_context()
        trial_id = trial_context.get_trial_id() if trial_context else "unknown"
        trial_name = trial_context.get_trial_name() if trial_context else "unknown"
        num_workers = config.get("num_workers", 1)
        
        # Get current node info
        current_node = socket.gethostname()
        current_ip = socket.gethostbyname(current_node)
        
        # Record start time
        start_time = time.time()
        start_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        logger.info("=" * 70)
        logger.info(f"TRIAL START")
        logger.info("=" * 70)
        logger.info(f"  Trial ID:       {trial_id}")
        logger.info(f"  Trial Name:     {trial_name}")
        logger.info(f"  Start Time:     {start_timestamp}")
        logger.info(f"  Driver Node:    {current_node} ({current_ip})")
        logger.info(f"  Workers (GPUs): {num_workers}")
        logger.info(f"  Config:         {json.dumps({k: str(v) for k, v in config.items()}, indent=2)}")
        logger.info("-" * 70)

        try:
            # Create TorchTrainer for distributed PyTorch training
            # Note: Do NOT specify resources_per_worker here - let Ray auto-detect
            logger.info(f"[{trial_id}] Creating TorchTrainer with {num_workers} workers...")
            trainer = TorchTrainer(
                train_loop_per_worker=self.train_fn,
                scaling_config=ScalingConfig(
                    num_workers=num_workers,
                    use_gpu=True
                ),
                train_loop_config=config,
                run_config=RunConfig(
                    name=f"train-trial_id={trial_id}",
                    storage_path='/fsx/ray_results'
                )
            )
            trainer_created_time = time.time()
            logger.info(f"[{trial_id}] TorchTrainer created in {trainer_created_time - start_time:.2f}s")

            # Execute training
            logger.info(f"[{trial_id}] Starting distributed training...")
            training_start_time = time.time()
            result = trainer.fit()
            training_end_time = time.time()
            training_duration = training_end_time - training_start_time
            
            logger.info(f"[{trial_id}] Training completed in {training_duration:.2f}s")

            # DEBUG: Write to /fsx/ file (logger goes to separate process, unreachable)
            debug_path = f'/fsx/trial_debug_{trial_id}.txt'
            try:
                with open(debug_path, 'w') as _dbg:
                    _dbg.write(f"trial_id: {trial_id}\n")
                    _dbg.write(f"training_duration: {training_duration:.2f}s\n")
                    _dbg.write(f"result type: {type(result)}\n")
                    _dbg.write(f"result.metrics: {result.metrics}\n")
                    _dbg.write(f"result.metrics type: {type(result.metrics)}\n")
                    if hasattr(result, 'metrics_dataframe') and result.metrics_dataframe is not None:
                        _dbg.write(f"metrics_dataframe columns: {list(result.metrics_dataframe.columns)}\n")
                        _dbg.write(f"metrics_dataframe tail:\n{result.metrics_dataframe.tail(3)}\n")
                    if hasattr(result, 'error'):
                        _dbg.write(f"result.error: {result.error}\n")
            except Exception:
                pass

            # Extract accuracy from training result
            final_accuracy = 0.0
            if result.metrics:
                final_accuracy = result.metrics.get('accuracy', 0.0)
                if 'best_accuracy' in result.metrics:
                    final_accuracy = max(final_accuracy, result.metrics['best_accuracy'])

            # Also check metrics_dataframe for the last reported accuracy
            if hasattr(result, 'metrics_dataframe') and result.metrics_dataframe is not None:
                df = result.metrics_dataframe
                if 'accuracy' in df.columns and len(df) > 0:
                    final_accuracy = max(final_accuracy, df['accuracy'].iloc[-1])

            logger.info(f"[{trial_id}] FINAL extracted accuracy: {final_accuracy}")

            # Record end time
            end_time = time.time()
            end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            total_duration = end_time - start_time

            logger.info("=" * 70)
            logger.info(f"TRIAL COMPLETE")
            logger.info("=" * 70)
            logger.info(f"  Trial ID:       {trial_id}")
            logger.info(f"  End Time:       {end_timestamp}")
            logger.info(f"  Driver Node:    {current_node} ({current_ip})")
            logger.info(f"  Workers Used:   {num_workers}")
            logger.info(f"  Training Time:  {training_duration:.2f}s ({training_duration/60:.2f} min)")
            logger.info(f"  Total Time:     {total_duration:.2f}s ({total_duration/60:.2f} min)")
            logger.info(f"  Final Accuracy: {final_accuracy:.4f}")
            logger.info("=" * 70)

            # Return metrics (don't use tune.report in nested TorchTrainer)
            return {"accuracy": final_accuracy}

        except Exception as e:
            end_time = time.time()
            end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            total_duration = end_time - start_time
            
            logger.error("=" * 70)
            logger.error(f"TRIAL FAILED")
            logger.error("=" * 70)
            logger.error(f"  Trial ID:       {trial_id}")
            logger.error(f"  End Time:       {end_timestamp}")
            logger.error(f"  Driver Node:    {current_node} ({current_ip})")
            logger.error(f"  Duration:       {total_duration:.2f}s")
            logger.error(f"  Error:          {str(e)}")
            import traceback
            logger.error(f"  Traceback:\n{traceback.format_exc()}")
            logger.error("=" * 70)
            return {"accuracy": 0.0, "error": str(e)}

    def run(self, cohesion=None):
        """Execute the hyperparameter optimization pipeline"""
        import time
        import socket
        
        hpo_start_time = time.time()
        hpo_start_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        logger.info("=" * 80)
        logger.info("HPO RUN STARTED")
        logger.info("=" * 80)
        logger.info(f"  Start Time:     {hpo_start_timestamp}")
        logger.info(f"  Total Trials:   {self.num_samples}")
        logger.info(f"  Available GPUs: {self.numHosts}")
        logger.info(f"  Efficiency:     {self.scaling_efficiency}")
        logger.info("=" * 80)

        try:
            # Initialize Ray
            logger.info("Initializing Ray...")
            ray.init(address="auto", ignore_reinit_error=True)
            
            cluster_resources = ray.cluster_resources()
            available_nodes = ray.nodes()
            
            logger.info("-" * 80)
            logger.info("CLUSTER INFO")
            logger.info("-" * 80)
            logger.info(f"  Total Resources: {cluster_resources}")
            logger.info(f"  Available GPUs:  {cluster_resources.get('GPU', 0)}")
            logger.info(f"  Number of Nodes: {len(available_nodes)}")
            for i, node in enumerate(available_nodes):
                node_ip = node.get('NodeManagerAddress', 'unknown')
                node_resources = node.get('Resources', {})
                node_gpus = node_resources.get('GPU', 0)
                node_alive = node.get('Alive', False)
                logger.info(f"    Node {i+1}: {node_ip} | GPUs: {node_gpus} | Alive: {node_alive}")
            logger.info("-" * 80)

            best_metric = cohesion or 0.0
            logger.info(f"Baseline metric for comparison: {best_metric}")

            # Calculate optimal batch plan for multi-phase execution
            batches = calculate_optimal_batches(
                self.num_samples, 
                self.numHosts, 
                self.scaling_efficiency
            )
            
            # Log multi-phase execution plan
            logger.info("-" * 80)
            logger.info("MULTI-PHASE EXECUTION PLAN")
            logger.info("-" * 80)
            logger.info(f"  Total Phases: {len(batches)}")
            for i, batch in enumerate(batches):
                logger.info(f"  Phase {i+1}: {batch['num_trials']} trial(s), {batch['workers_per_trial']} GPU(s)/trial, {batch['concurrent_trials']} concurrent ({batch['mode']})")
            logger.info("-" * 80)

            # Pre-generate all hyperparameter configs upfront
            logger.info("Pre-generating hyperparameter configurations...")
            base_space = self.search_space[0].copy()
            all_configs = []
            for i in range(self.num_samples):
                config = {}
                for key, value in base_space.items():
                    if key == "num_workers":
                        continue  # Will be set per-phase
                    if hasattr(value, 'sample'):
                        config[key] = value.sample()
                    elif hasattr(value, 'categories'):
                        config[key] = np.random.choice(value.categories)
                    else:
                        config[key] = value
                all_configs.append(config)
                logger.info(f"  Config {i+1}: {config}")
            
            # Track results across all phases
            all_results_dfs = []
            best_score = 0.0
            best_config = None
            total_trials_run = 0
            early_stopped = False
            
            # Execute each phase
            config_idx = 0
            for phase_num, batch in enumerate(batches):
                phase_start_time = time.time()
                phase_start_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                
                workers = batch['workers_per_trial']
                concurrent = batch['concurrent_trials']
                num_trials_this_phase = batch['num_trials']
                mode = batch['mode']
                
                logger.info("=" * 80)
                logger.info(f"PHASE {phase_num + 1}/{len(batches)} STARTED")
                logger.info("=" * 80)
                logger.info(f"  Start Time:     {phase_start_timestamp}")
                logger.info(f"  Trials:         {num_trials_this_phase}")
                logger.info(f"  Workers/Trial:  {workers} GPU(s)")
                logger.info(f"  Max Concurrent: {concurrent}")
                logger.info(f"  Mode:           {mode}")
                logger.info("-" * 80)
                
                # Get configs for this phase
                phase_configs = all_configs[config_idx:config_idx + num_trials_this_phase]
                config_idx += num_trials_this_phase
                
                # Add num_workers to each config
                for cfg in phase_configs:
                    cfg['num_workers'] = workers
                
                logger.info(f"  Configs for this phase:")
                for i, cfg in enumerate(phase_configs):
                    logger.info(f"    Trial {total_trials_run + i + 1}: {cfg}")
                
                # Create search space that samples from pre-generated configs
                phase_space = base_space.copy()
                phase_space['num_workers'] = tune.choice([workers])
                
                # Setup scheduler for this phase
                scheduler = AsyncHyperBandScheduler(
                    max_t=200,
                    metric=self.metric,
                    mode=self.mode,
                )
                
                # Create and run Tuner for this phase
                tuner = tune.Tuner(
                    self.train_driver_fn,
                    tune_config=tune.TuneConfig(
                        scheduler=scheduler,
                        num_samples=num_trials_this_phase,
                        max_concurrent_trials=concurrent,
                    ),
                    run_config=tune.RunConfig(
                        name=f"pytorch_hpo_phase{phase_num + 1}",
                        stop={self.metric: self.target},
                        storage_path='/fsx/ray_results',
                        verbose=2
                    ),
                    param_space=phase_space
                )
                
                logger.info(f"  Executing phase {phase_num + 1}...")
                results = tuner.fit()
                
                phase_end_time = time.time()
                phase_end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                phase_duration = phase_end_time - phase_start_time
                
                # Collect results from this phase
                df = results.get_dataframe()
                if not df.empty:
                    df['phase'] = phase_num + 1
                    df['workers_per_trial'] = workers
                    all_results_dfs.append(df)
                    
                    # Check for best result in this phase
                    if self.metric in df.columns:
                        phase_best_idx = df[self.metric].idxmax() if self.mode == 'max' else df[self.metric].idxmin()
                        phase_best_score = df.loc[phase_best_idx, self.metric]
                        if phase_best_score >= best_score:
                            best_score = phase_best_score
                            best_config = df.loc[phase_best_idx].to_dict()
                
                total_trials_run += num_trials_this_phase
                
                logger.info("=" * 80)
                logger.info(f"PHASE {phase_num + 1}/{len(batches)} COMPLETED")
                logger.info("=" * 80)
                logger.info(f"  End Time:       {phase_end_timestamp}")
                logger.info(f"  Duration:       {phase_duration:.2f}s ({phase_duration/60:.2f} min)")
                logger.info(f"  Trials Run:     {num_trials_this_phase}")
                logger.info(f"  Best Score:     {best_score:.4f}")
                logger.info("=" * 80)
                
                # Early stopping: if we hit target, skip remaining phases
                if best_score >= self.target:
                    logger.info(f"TARGET REACHED ({best_score:.4f} >= {self.target}) - Skipping remaining phases")
                    early_stopped = True
                    break
            
            # Merge all results
            logger.info("Merging results from all phases...")
            if all_results_dfs:
                final_df = pd.concat(all_results_dfs, ignore_index=True)
            else:
                final_df = pd.DataFrame()
            
            logger.info(f"Combined results shape: {final_df.shape}")
            
            if not final_df.empty:
                results_file = 'pytorch_hpo_results.csv'
                final_df.to_csv(results_file, index=False)
                logger.info(f"Results saved to: {results_file}")
                logger.info(f"Results preview:\n{final_df.head()}")

            # Calculate success rate
            if self.metric in final_df.columns:
                accs = final_df[self.metric].values
                hits = (accs >= best_metric + self.improvement_threshold).sum()
                success_rate = hits / max(len(accs), 1)
                logger.info(f"Success rate: {hits}/{len(accs)} = {success_rate:.3f}")
            else:
                success_rate = 0.0

            # Determine next iteration trial count
            if success_rate > 0.5:
                next_trials = max(MIN_TRIALS, int(self.num_samples * 0.5))
            else:
                next_trials = min(10, int(self.num_samples * 1.5))

            logger.info(f"Next iteration trials: {next_trials} (based on success_rate={success_rate:.3f})")

            # Build output
            output = {
                "config": {
                    **{k: v for k, v in (best_config or {}).items() 
                       if k not in ['num_workers', 'phase', 'workers_per_trial']},
                    "accuracy": best_score,
                    "next_trials": next_trials
                },
                "phases_executed": phase_num + 1,
                "total_trials_run": total_trials_run,
                "early_stopped": early_stopped,
                "batch_plan": batches
            }

            # Final summary
            hpo_end_time = time.time()
            hpo_end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            hpo_duration = hpo_end_time - hpo_start_time
            
            logger.info("=" * 80)
            logger.info("HPO RUN COMPLETE - SUMMARY")
            logger.info("=" * 80)
            logger.info(f"  Start Time:       {hpo_start_timestamp}")
            logger.info(f"  End Time:         {hpo_end_timestamp}")
            logger.info(f"  Total Duration:   {hpo_duration:.2f}s ({hpo_duration/60:.2f} min)")
            logger.info(f"  Phases Executed:  {phase_num + 1}/{len(batches)}")
            logger.info(f"  Trials Executed:  {total_trials_run}/{self.num_samples}")
            logger.info(f"  Early Stopped:    {early_stopped}")
            logger.info(f"  Best Accuracy:    {best_score:.4f}")
            logger.info(f"  Success Rate:     {success_rate:.3f}")
            logger.info("=" * 80)

            logger.info(f"Pipeline output: {output}")
            return output

        except Exception as e:
            hpo_end_time = time.time()
            hpo_end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            hpo_duration = hpo_end_time - hpo_start_time
            
            logger.error("=" * 80)
            logger.error("HPO RUN FAILED")
            logger.error("=" * 80)
            logger.error(f"  Start Time:     {hpo_start_timestamp}")
            logger.error(f"  End Time:       {hpo_end_timestamp}")
            logger.error(f"  Duration:       {hpo_duration:.2f}s")
            logger.error(f"  Error:          {str(e)}")
            import traceback
            logger.error(f"  Traceback:\n{traceback.format_exc()}")
            logger.error("=" * 80)
            raise


def refine_space(best_config):
    """Create a refined search space around the best configuration"""
    logger.info("=" * 80)
    logger.info("REFINING SEARCH SPACE")
    logger.info("=" * 80)
    logger.info(f"Best config to refine: {best_config}")

    new_space = {}

    for k, v in best_config.items():
        logger.info(f"Processing parameter: {k} = {v} (type: {type(v)})")

        if k == "next_trials":
            logger.info(f"  Skipping metadata field: {k}")
            continue
        elif k == "hidden":
            new_space[k] = tune.choice([10])
            logger.info(f"  Fixed classes: 10")
        elif k == "batch_size":
            new_space[k] = tune.choice([v])
            logger.info(f"  Batch size: fixed at {v}")
        elif k == "image_size":
            new_space[k] = tune.choice([v])
            logger.info(f"  Image size: fixed at {v}")
        elif k == "epoch":
            low = max(1, int(v * 0.8))
            high = min(10, int(v * 1.5))
            new_space[k] = tune.randint(low, high)
            logger.info(f"  Epoch range: {low} to {high}")
        elif isinstance(v, float):
            if k == "learning_rate":
                low = max(1e-5, v * 0.1)
                high = min(1e-1, v * 10)
                new_space[k] = tune.loguniform(low, high)
                logger.info(f"  Learning rate (log): {low:.2e} to {high:.2e}")
            else:
                if k == "momentum":
                    low = max(0.1, v * 0.9)
                    high = min(0.99, v * 1.1)
                else:
                    low = max(0, v * 0.8)
                    high = v * 1.2
                new_space[k] = tune.uniform(low, high)
                logger.info(f"  Float {k}: {low:.4f} to {high:.4f}")
        elif isinstance(v, int) and k not in ["hidden", "batch_size", "image_size", "epoch", "amp", "train_backbone"]:
            low = max(1, int(v * 0.5))
            high = int(v * 1.5)
            if low >= high:
                high = low + 1
            new_space[k] = tune.randint(low, high)
            logger.info(f"  Integer {k}: {low} to {high}")
        elif isinstance(v, bool):
            new_space[k] = tune.choice([v])
            logger.info(f"  Boolean {k}: fixed at {v}")
        else:
            new_space[k] = tune.choice([v])
            logger.info(f"  Other {k}: fixed at {v}")

    # Ensure required parameters exist
    if "amp" not in new_space:
        new_space["amp"] = tune.choice([True])
    if "train_backbone" not in new_space:
        new_space["train_backbone"] = tune.choice([False])
    if "data_dir" not in new_space:
        new_space["data_dir"] = tune.choice([os.path.expanduser("~/cifar10")])

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


def demo_allocation_logic():
    """
    Demonstrate the allocation logic for various configurations.
    Run with: python hpo_pipeline_verbose.py --demo
    """
    print("\n" + "=" * 80)
    print("HYBRID BATCHING RESOURCE ALLOCATION - DEMONSTRATION")
    print("=" * 80)
    
    test_cases = [
        (4, 1), (4, 2), (4, 3), (4, 4),
        (4, 5), (4, 6), (4, 7), (4, 8),
        (2, 1), (2, 2), (2, 3), (2, 4), (2, 5),
        (8, 3), (8, 5), (8, 7),
    ]
    
    print(f"\n{'Hosts':<6} {'Trials':<7} {'Mode':<25} {'Workers':<8} {'Concurrent':<10} {'Est. Time'}")
    print("-" * 80)
    
    for num_hosts, num_trials in test_cases:
        workers, concurrent, mode = get_simple_allocation(num_trials, num_hosts, 0.92)
        
        # Estimate time in T_1 units
        if mode in ['parallel', 'parallel_idle']:
            batches = (num_trials + concurrent - 1) // concurrent
            time = batches * 1.0
        elif mode == 'balanced':
            time = 1.0 / (workers * 0.92)
        elif mode in ['distributed', 'distributed_sequential']:
            time = num_trials / (num_hosts * 0.92)
        else:
            time = 1.0
            
        print(f"{num_hosts:<6} {num_trials:<7} {mode:<25} {workers:<8} {concurrent:<10} {time:.3f} × T₁")
    
    print("\n" + "=" * 80)
    print("DETAILED BATCH PLANS")
    print("=" * 80)
    
    # Show detailed batch plans for a few interesting cases
    for num_hosts, num_trials in [(4, 5), (4, 7), (8, 5)]:
        print(f"\n>>> {num_hosts} hosts, {num_trials} trials:")
        calculate_optimal_batches(num_trials, num_hosts, 0.92)


if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("PYTORCH HPO WORKFLOW WITH SMART RESOURCE ALLOCATION")
    logger.info("=" * 80)

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--hosts", type=int, default=2, 
                          help="Total number of available GPUs")
        parser.add_argument("--config-file", type=str, 
                          help="JSON config file (optional)")
        parser.add_argument("--test", action="store_true", 
                          help="Use default test configuration")
        parser.add_argument("--efficiency", type=float, default=0.92,
                          help="Scaling efficiency from profiling (default: 0.92)")
        parser.add_argument("--demo", action="store_true",
                          help="Run demo showing allocation logic for various configs")
        args = parser.parse_args()
        
        logger.info(f"Command line args: {args}")

        # Demo mode - show allocation logic
        if args.demo:
            demo_allocation_logic()
            sys.exit(0)

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
                request = get_default_config()

        logger.info(f"Input configuration: {json.dumps(request, indent=2)}")

        # Extract trial count
        num_samples = request.get("next_trials", 3)
        logger.info(f"Number of trials to run: {num_samples}")

        # Refine search space
        initial_space = refine_space(request)

        # Create pipeline with smart allocation
        pipeline = TunePipeline(
            train_fn=train_cifar10_torch,
            initial_search=initial_space,
            metric="accuracy",
            target=0.99,
            max_rounds=5,
            num_samples=num_samples,
            improvement_threshold=1e-2,
            patience=2,
            numHosts=args.hosts,
            scaling_efficiency=args.efficiency
        )

        # Execute
        best_result = pipeline.run(cohesion=0.85)

        # Output for next iteration — strip config/ prefix, filter to HPO keys
        output_config = best_result.get("config", {})
        # Tune dataframe uses "config/learning_rate" etc — strip the prefix
        cleaned = {}
        for k, v in output_config.items():
            clean_key = k.replace("config/", "") if k.startswith("config/") else k
            cleaned[clean_key] = v
        hpo_keys = {"learning_rate", "momentum", "batch_size", "image_size",
                     "epoch", "epochs", "hidden", "accuracy", "next_trials", "model_name"}
        output_config = {k: v for k, v in cleaned.items()
                         if k in hpo_keys and v is not None and not (isinstance(v, float) and np.isnan(v))}

        print(json.dumps({"config": output_config}))

    except Exception as e:
        logger.error("=" * 80)
        logger.error("WORKFLOW FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1) 