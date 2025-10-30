  Let me walk through the complete moldable logic step-by-step with a concrete example.

  ---
  Scenario: 3 HPO Workflows Competing for Resources

  Available Resources:

  Reserved:
    - g4dn.2xlarge: 2 instances
    - g5.2xlarge: 2 instances

  On-Demand (limits):
    - g4dn.2xlarge: 2 instances max
    - g5.2xlarge: 2 instances max

  Three Workflows:

  WF1: Budget=$50, Deadline=3600s, Model=vgg19
    - Round 0: 8 trials × 10 epochs
    - Round 1: 6 trials × 12 epochs
    - Round 2: 4 trials × 15 epochs

  WF2: Budget=$30, Deadline=1800s, Model=convnext_large
    - Round 0: 4 trials × 8 epochs
    - Round 1: 4 trials × 10 epochs

  WF3: Budget=$40, Deadline=2400s, Model=wide_resnet101_2
    - Round 0: 6 trials × 12 epochs
    - Round 1: 8 trials × 10 epochs

  ---
  Phase 1: Initial Allocation (Static Selection)

  WF1 Arrives First (t=0)

  # In allocateResourcesHPO()
  constraints = {
      'budget': 50,
      'deadline': 3600,
      'mesh': 'vgg19',
      'chains': 8,  # 8 trials
      'tinydaIterations': 10  # 10 epochs
  }

  # Step 1: Select optimal instance type
  optimal_type = selectOptimalInstanceType(50, 3600, 'vgg19', 8, 10)
  # Calculates:
  # - g4dn runtime: ~800s, cost: ~$4.50
  # - g5 runtime: ~600s, cost: ~$5.50
  # Decision: deadline=3600s is comfortable, choose g4dn (cheaper)
  # Result: 'g4'

  # Step 2: Allocate homogeneous g4dn instances
  # Try reserved first:
  selected = [
      (g4dn.2xlarge_reserved, 2),  # Takes both reserved g4dn
  ]
  trials_remaining = 8 - 2 = 6

  # Try on-demand:
  # Budget check: 6 instances × $4.50 = $27 < $50 ✓
  # Deadline check: 600s × 10 epochs = 6000s / 6 parallel = 1000s < 3600s ✓
  selected.append(
      (g4dn.2xlarge_ondemand, 2)  # Takes both on-demand slots (limit reached!)
  )
  trials_remaining = 6 - 2 = 4

  # ERROR: Still need 4 more instances but on-demand limit reached!
  # Fallback: Reduce trials or fail workflow
  # Let's say we allocate what we can: 4 g4dn total

  Result:
  WF1 allocated: 4 × g4dn.2xlarge (2 reserved + 2 on-demand)
  Status: Underprovisioned (needs 8, got 4)
  Resources left:
    - g4dn.2xlarge: 0 on-demand, 0 reserved
    - g5.2xlarge: 2 on-demand, 2 reserved

  ---
  WF2 Arrives (t=10)

  # WF2 analysis
  optimal_type = selectOptimalInstanceType(30, 1800, 'convnext_large', 4, 8)
  # Tight deadline (1800s), choose g5 (faster)
  # Result: 'g5'

  # Allocate g5 instances:
  selected = [
      (g5.2xlarge_reserved, 2),  # Takes both reserved g5
  ]
  trials_remaining = 4 - 2 = 2

  # On-demand g5:
  # Budget check: 2 × $6 = $12 < $30 ✓
  # Deadline check: 400s × 8 epochs = 3200s / 4 parallel = 800s < 1800s ✓
  selected.append(
      (g5.2xlarge_ondemand, 2)  # Takes both on-demand g5 slots
  )

  Result:
  WF2 allocated: 4 × g5.2xlarge (2 reserved + 2 on-demand)
  Status: Fully provisioned ✓
  Resources left:
    - g4dn.2xlarge: 0 on-demand, 0 reserved
    - g5.2xlarge: 0 on-demand, 0 reserved

  ---
  WF3 Arrives (t=20)

  # WF3 analysis
  optimal_type = selectOptimalInstanceType(40, 2400, 'wide_resnet101_2', 6, 12)
  # Result: 'g5' (needs speed for deadline)

  # Try to allocate g5:
  # NO g5 AVAILABLE! All reserved and on-demand taken.
  # WF3 WAITS in queue ⏳

  Result:
  WF3: WAITING (no resources available)

  ---
  Phase 2: Moldable Reallocation (Round Transitions)

  WF1 Round 0 → Round 1 Transition (t=650)

  WF1 completes Round 0 (8 trials × 10 epochs). Now needs Round 1 (6 trials × 12 epochs).

  # Executor sends resource request to scheduler
  request = {
      'wf-id': 'WF1',
      'iteration': 1,  # Round 1
      'chains': 6,     # Need 6 trials now (down from 8)
      'tinyda-iterations': 12,  # 12 epochs now
      'request-time': 650
  }

  # In processFreeRequest()
  (instances, budget, deadline, start_time, model) = getWorkflow('WF1')
  # instances = [(g4dn_reserved, 2, [ip1, ip2]), (g4dn_ondemand, 2, [ip3, ip4])]
  # budget = 50, deadline = 3600, start_time = 0, model = 'vgg19'

  # 1. Calculate constraints
  ind = 1  # iteration 1
  used_time = 650
  available_time = (3600 - 650) * OPTIM_FCFS_DFACTOR[1]
  # OPTIM_FCFS_DFACTOR[1] = 0.7 (70% of remaining time for this iteration)
  # available_time = 2950 × 0.7 = 2065s

  used_budget = computeCost('WF1', 650)
  # Spent: 4 instances × 650s × $0.0002/s = $0.52
  available_budget = (50 - 0.52) * OPTIM_FCFS_BFACTOR[1]
  # OPTIM_FCFS_BFACTOR[1] = 0.6 (60% of remaining budget for this iteration)
  # available_budget = 49.48 × 0.6 = $29.69

  # 2. Current allocation
  current_trials = 4 (4 instances allocated)
  current_instance_type = 'g4dn.2xlarge'

  # 3. Check if can reduce resources
  trials_per_instance = 3
  runtime_per_trial = getRuntime_g4(1, 'vgg19', 12) = ~100s

  while trials_per_instance > 0:
      runtime = 3 × 100s = 300s
      if 300s < 2065s:  # ✓ Can complete with 3 trials per instance
          min_needed_instances = 6 trials / 3 per instance = 2 instances
          if current_trials (4) > min_needed_instances (2):
              # CAN FREE 2 INSTANCES! 🎉
              request['count'] = 4 - 2 = 2
              freeResources(instances, request, sim)
              return

  # FREE 2 ON-DEMAND INSTANCES
  # Free the last 2 instances: (g4dn_ondemand, 2, [ip3, ip4])
  # Return to pool: g4dn on-demand slots: 0 → 2 ✓

  Result:
  WF1 now using: 2 × g4dn.2xlarge (2 reserved only)
  WF1 freed: 2 × g4dn.2xlarge on-demand
  Resources available now:
    - g4dn.2xlarge: 2 on-demand ✓ (freed from WF1)
    - g5.2xlarge: 0 on-demand, 0 reserved

  ---
  WF3 Can Now Start! (t=652)

  # Scheduler checks queue, finds WF3 waiting
  # WF3 needs: 6 trials, model=wide_resnet101_2
  optimal_type = 'g5'  # Prefers g5

  # But only g4dn available (2 on-demand)
  # Fallback: Allocate what's available
  selected = [(g4dn.2xlarge_ondemand, 2)]

  # Create on-demand instances
  ips = createOnDemandWorkers(ips, sim)
  # Creates: [new_ip5, new_ip6]

  Result:
  WF3 allocated: 2 × g4dn.2xlarge (on-demand)
  Status: Underprovisioned (needs 6, got 2)
  Resources left:
    - g4dn.2xlarge: 0 on-demand, 0 reserved
    - g5.2xlarge: 0 on-demand, 0 reserved

  ---
  WF2 Round 0 → Round 1 Transition (t=850)

  WF2 completes Round 0 (4 trials × 8 epochs). Needs Round 1 (4 trials × 10 epochs - same trial count).

  request = {
      'wf-id': 'WF2',
      'iteration': 1,
      'chains': 4,  # Still need 4 trials
      'tinyda-iterations': 10
  }

  # In processFreeRequest()
  used_time = 850
  available_time = (1800 - 850) * OPTIM_FCFS_DFACTOR[1] = 950 × 0.7 = 665s

  used_budget = computeCost('WF2', 850) = ~$1.50
  available_budget = (30 - 1.50) * OPTIM_FCFS_BFACTOR[1] = 28.5 × 0.6 = $17.10

  current_trials = 4 (4 g5 instances)
  trials_per_instance = 3

  runtime_per_trial = getRuntime_g5(1, 'convnext_large', 10) = ~60s

  # Check if can reduce:
  runtime = 3 × 60s = 180s
  if 180s < 665s:  # ✓
      min_needed = 4 trials / 3 per instance = 2 instances (rounded up)
      if 4 > 2:
          # CAN FREE 2 INSTANCES!
          request['count'] = 2
          freeResources(...)

  # FREE 2 ON-DEMAND G5 INSTANCES

  Result:
  WF2 now using: 2 × g5.2xlarge (2 reserved only)
  WF2 freed: 2 × g5.2xlarge on-demand
  Resources available:
    - g4dn.2xlarge: 0 on-demand
    - g5.2xlarge: 2 on-demand ✓ (freed from WF2)

  ---
  WF3 Can Scale Up! (t=900)

  WF3 is running with only 2 instances but needs 6. Now that g5 on-demand freed up, WF3 can scale up.

  But wait - WF3 is using g4dn, and g5 is available. Can we switch? NO - we decided no instance type switching due
   to cold start penalty.

  Instead, WF3 continues with 2 g4dn instances (underprovisioned) until WF1 frees more g4dn.

  ---
  WF1 Round 1 → Round 2 Transition (t=1200)

  WF1 completes Round 1 (6 trials × 12 epochs with 2 g4dn). Needs Round 2 (4 trials × 15 epochs).

  request = {
      'wf-id': 'WF1',
      'iteration': 2,
      'chains': 4,  # Need only 4 trials now
      'tinyda-iterations': 15
  }

  available_time = (3600 - 1200) * OPTIM_FCFS_DFACTOR[2] = 2400 × 0.5 = 1200s
  used_budget = ~$2.00
  available_budget = (50 - 2) × OPTIM_FCFS_BFACTOR[2] = 48 × 0.4 = $19.20

  current_trials = 2 (2 g4dn reserved)
  runtime_per_trial = getRuntime_g4(1, 'vgg19', 15) = ~120s

  # Check if need more resources:
  trials_per_instance = 3
  runtime = 3 × 120s = 360s
  min_needed = 4 trials / 3 per instance = 2 instances (rounded up)

  # Currently have 2, need 2 → NO CHANGE NEEDED
  # But deadline is tight, let's check speedup

  # Can we benefit from 1 more instance?
  current_runtime = 120s × (4 trials / 2 instances) = 240s
  new_runtime = 120s × (4 trials / 3 instances) = 160s
  speedup = 240 / 160 = 1.5

  # Check if should allocate:
  if speedup > SPEEDUP_THRESHOLD (1.2):  # ✓ 1.5 > 1.2
      # Check budget:
      cost = 120s × 15 epochs × $0.0002/s = $0.36
      if $0.36 < $19.20:  # ✓
          # ALLOCATE 1 MORE INSTANCE
          alloc_instances = checkNewResourcesHPO(...)
          # Finds: g4dn on-demand available? NO (WF3 using them)
          # Cannot allocate - no g4dn available

  Result:
  WF1 continues with: 2 × g4dn.2xlarge (cannot scale up)

  ---
  Key Moldable Logic Points

  1. Iteration-Weighted Constraints

  # As workflow progresses, reduce available budget/time for each iteration
  available_time = remaining_time × OPTIM_FCFS_DFACTOR[iteration]
  available_budget = remaining_budget × OPTIM_FCFS_BFACTOR[iteration]

  # Example factors:
  OPTIM_FCFS_DFACTOR = [1.0, 0.7, 0.5, 0.3, 0.2]  # Decreasing time allocation
  OPTIM_FCFS_BFACTOR = [1.0, 0.6, 0.4, 0.3, 0.2]  # Decreasing budget allocation

  Rationale: Later iterations get smaller resource shares to ensure workflow completes within constraints.

  ---
  2. Scale Down Decision

  # Can we free resources without missing deadline?
  trials_per_instance = 3  # Start high
  while trials_per_instance > 0:
      estimated_runtime = trials_per_instance × runtime_per_trial × epochs
      if estimated_runtime < available_time:
          min_needed_instances = ceil(total_trials / trials_per_instance)
          if current_instances > min_needed_instances:
              free_count = current_instances - min_needed_instances
              freeResources(free_count)
              return
      trials_per_instance -= 1

  Rationale: Free resources when deadline pressure is low, making them available for other workflows.

  ---
  3. Scale Up Decision

  # Should we request more resources?
  if deadline_tight and budget_available:
      # Calculate speedup from adding 1 instance
      current_runtime = estimate_runtime(current_instances)
      new_runtime = estimate_runtime(current_instances + 1)
      speedup = current_runtime / new_runtime

      if speedup > SPEEDUP_THRESHOLD:  # Must justify cost with speedup
          cost_of_new_instance = calculate_cost(...)
          if cost_of_new_instance < available_budget:
              # Allocate!
              allocateAdditionalResources(...)

  Rationale: Only scale up if speedup justifies cost and deadline requires it.

  ---
  4. Priority: Reserved → On-Demand

  # When allocating additional resources:
  available_instances = []
  for inst in resources:
      if inst.name == current_instance_type:  # Same type only (homogeneous)
          priority = 0 if inst.type == 'reserved' else 1
          available_instances.append((priority, inst))

  available_instances.sort(key=lambda x: x[0])  # Reserved first

  Rationale: Reserved instances are cheaper (no cold start), prefer them over on-demand.

  ---
  5. Homogeneous Enforcement

  # NEVER mix instance types in same workflow
  current_instance_type = instances[0][0].name  # Lock to first allocated type

  # When allocating more:
  for inst in resources:
      if inst.name == current_instance_type:  # Must match!
          # Can allocate

  Rationale: GPU straggling eliminated by enforcing homogeneity.

  ---
  Why Moldable Wins

  Static Allocation (WF1 example):

  Round 0: 4 instances × 650s = 2600 instance-seconds
  Round 1: 4 instances × 550s = 2200 instance-seconds  (over-provisioned!)
  Round 2: 4 instances × 480s = 1920 instance-seconds  (over-provisioned!)
  Total: 6720 instance-seconds

  Moldable Allocation (WF1 example):

  Round 0: 4 instances × 650s = 2600 instance-seconds
  Round 1: 2 instances × 700s = 1400 instance-seconds  (freed 2!)
  Round 2: 2 instances × 600s = 1200 instance-seconds  (freed 2!)
  Total: 5200 instance-seconds

  Savings: (6720 - 5200) / 6720 = 22.6% cost reduction!

  Plus:
  - WF3 started earlier (t=652 vs waiting until WF1/WF2 finish)
  - Better resource utilization (freed instances used by others)
  - More workflows completed in same time window

  ---
  Ready to implement this logic?