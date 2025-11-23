# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Vortex is a moldable scheduling system for workflow execution that manages computational resources across on-premises and cloud environments. The system implements various scheduling algorithms with license-aware resource allocation and supports seismic simulation workloads (SeisSol).

## Architecture

The main application (`src/main/main.py`) orchestrates five concurrent threads:
- **Job Server**: Listens for user workflow submissions (port 8080)
- **Scheduler**: Executes scheduling algorithms (FCFS, EDF, Priority-based)
- **Completion Server**: Handles job completion notifications (port 8082)
- **Completion Processor**: Processes finished jobs
- **Resource Server**: Manages resource allocation requests (port 8084)

Key components:
- `src/main/scheduler/`: Contains scheduling algorithm implementations (FCFS, EDF, Priority)
- `src/main/resource_manager/`: Handles cloud and on-premises resource provisioning
- `src/main/workflow/`: Workflow parsing and execution (Steep API compatible)
- `src/main/wf_queue/`: Redis-based job queuing system
- `Seis-Bridge/`: SeisSol simulation server and client components
- `service/`: Helper scripts for running distributed components

## Development Commands

### Main Application
```bash
# Run the main scheduler (from project root)
python src/main/main.py

# Run with VS Code debugger
# Use "Python: Vortex" configuration in .vscode/launch.json
```

### Dependencies
```bash
# Install Python dependencies
pip install -r src/main/requirements.txt

# Required dependencies:
# - asarPy==1.0.1
# - PyYAML==6.0.2
# - redis==5.0.8
# - scikit-learn==1.6.1
```

### Redis Setup (Required)
```bash
# Ubuntu/Debian installation
sudo apt-get install lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
sudo chmod 644 /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update
sudo apt-get install redis

# Start Redis service
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

### Service Scripts
```bash
# Run SeisSol server (sets up port 4242)
./service/run_server_script.sh <host_ip> <unused> <cores>

# Run SeisSol client
./service/run_client_script.sh <chains> <iterations> <cohesion> <server_host>

# Multiple client execution
./service/run_client_script_mult.sh
```

## Configuration

### Core Configuration Files
- `src/main/config/resources.yaml`: Defines compute resources (on-prem/cloud instances, runtimes, costs)
- `src/main//Users/srishtidasgupta/PhD/PhD/PhD_Codebase/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml`: License pool management (ANSYS, ABAQUS, LSDYNA tokens)
- `src/main/config/ports.yaml`: Service port mappings
- `src/main/config/constants.py`: Application constants

### Key Configuration Points
- Scheduler IP: `10.19.224.232` (defined in resources.yaml)
- Service ports: 8080 (jobs), 8082 (completion), 8084 (resources), 8089 (executor)
- License accounting supports per-core, per-chain, and custom strategies
- Resource definitions include runtime benchmarks and cost models

## Workflow System

The system implements a subset of the Steep workflow API (version 4.7.0):
- Execute actions with service paths, inputs/outputs
- For-each actions with enumerators
- Variable substitution and basic dependency handling
- Workflow submission via HTTP POST to scheduler:8080

### Three Workflow Types

The system supports three distinct workflow types with different input handling:

#### 1. Plain SeisSol Workflows
- **Entry point**: `simulate_main.py`
- **Workflows**: `sample_workflows/data0.yaml`
- **Input format**: Simple numeric cohesion value (e.g., `3`)
- **Configuration**: Uses `workflowConfig` array for per-iteration chains/tinydaIterations
- **Detection**: Presence of `workflowConfig` array in config
- **Use case**: Basic seismic simulations without license management

#### 2. License-Aware (LA) SeisSol Workflows
- **Entry point**: `simulate_main_LA.py`
- **Workflows**: `workflow/sample_workflows_LA/data*.yaml`
- **Input format**: Simple numeric cohesion value (e.g., `3`)
- **Configuration**: Uses `constraints` for static chains/tinydaIterations
- **License fields**: `license_pool` (ANSYS/ABAQUS/LSDYNA), `software_id` (1/2/3)
- **Detection**: Presence of `license_pool` or `software_id` fields
- **Use case**: Seismic simulations with commercial software license tracking

#### 3. HPO (Hyperparameter Optimization) Workflows
- **Entry point**: `simulate_main_HPO.py`
- **Workflows**: `workflow/sample_workflows_HPO/data*.yaml`
- **Input format**: Dictionary with hyperparameters (`learning_rate`, `epochs`, `next_trials`, etc.)
- **Configuration**: Mesh is string (model name like "vgg19", "convnext_large")
- **Detection**: Mesh field is string type
- **Use case**: Machine learning hyperparameter tuning on CIFAR-10

### Workflow Type Detection

The `utils/exec_sched.py` module implements automatic workflow type detection:

```python
detectWorkflowType(wf_id)  # Returns 'PLAIN', 'LA', or 'HPO'
```

Detection hierarchy:
1. Check for license fields (`license_pool`, `software_id`) → LA
2. Check if mesh is string → HPO
3. Check if `workflowConfig` array exists → PLAIN
4. Check if mesh is integer → PLAIN (fallback)

Workflow type is detected once at initialization and cached in `workflow_config` for efficient routing.

### Input Handler Functions

- `getClientInputs_Plain()`: Reads chains/iterations from `workflowConfig[ind]` array
- `getClientInputs_LA()`: Reads chains/iterations from `constraints` (static)
- `getClientInputs_HPO()`: Reads chains/iterations from input dict (dynamic)
- `getClientInputs()`: Dispatcher that routes to appropriate handler

## Testing

```bash
# Run Plain SeisSol workflows
python src/main/simulate_main.py

# Run License-Aware workflows
python src/main/simulate_main_LA.py

# Run HPO workflows
python src/main/simulate_main_HPO.py

# Test workflows are located in:
# - Plain: src/main/sample_workflows/
# - LA: src/main/workflow/sample_workflows_LA/
# - HPO: src/main/workflow/sample_workflows_HPO/
# - Unit tests: src/test/
```

**Note**: `simulate_main_LA.py` uses `enable_smp=False` for Simulus to avoid Python 3.13 multiprocessing pickle errors on macOS.

## Development Notes

- The system uses Redis queues for inter-component communication
- Scheduler algorithms are pluggable (see `src/main/scheduler/` directory)
- Resource manager supports both reserved and on-demand cloud instances
- License management includes fairness policies and anti-starvation mechanisms
- SeisSol integration provides seismic simulation workload support
- **Workflow input handling**: Three workflow types (Plain, LA, HPO) have separate input handlers in `utils/exec_sched.py` - see `context_7_workflow_type_separation.log` for details

## Context Files

Development history and debugging sessions are documented in `contexts/`:
- `context_1.log`: Initial setup and architecture
- `context_2_ondemand_implementation.log`: On-demand cloud instance provisioning
- `context_3_runtime_calculation_fix.log`: Runtime estimation fixes
- `context_4_debugging_executor_communication.log`: Executor-scheduler communication
- `context_5_workflow_execution_fixes.log`: Workflow execution issues
- `context_6_hpo_runtime_fix.log`: HPO 3x slowdown debugging
- `context_7_workflow_type_separation.log`: Workflow type input handler separation (Nov 2025)
- `context_8_LA_workflow_debugging.log`: License-aware workflow debugging, deadline/budget fixes (Nov 2025)