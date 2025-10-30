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
- `src/main/config/licenses.yaml`: License pool management (ANSYS, ABAQUS, LSDYNA tokens)
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

## Testing

```bash
# Run simulation mode
python src/main/simulate_main.py

# Test workflows are located in:
# - src/main/workflow/
# - src/test/
```

## Development Notes

- The system uses Redis queues for inter-component communication
- Scheduler algorithms are pluggable (see `src/main/scheduler/` directory)
- Resource manager supports both reserved and on-demand cloud instances
- License management includes fairness policies and anti-starvation mechanisms
- SeisSol integration provides seismic simulation workload support