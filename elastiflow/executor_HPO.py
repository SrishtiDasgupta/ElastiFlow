import argparse
from elastiflow.utils.exec_sched import setNewResources, isResourceRequestPending
from elastiflow.scripts.create_instance_HPO import deleteInstanceFromIp
from elastiflow.utils.request import getConfig, sendRequest
from elastiflow.workflow.steep_workflow_HPO import Steep_Workflow_HPO

def executeWorkflowHPO(data, backend):
    """
    Execute HPO workflow using Steep workflow engine

    Key differences from SeisSol:
    1. Executor runs on dedicated instance (doesn't participate in computation)
    2. Workers are separate homogeneous instances (no GPU straggling)
    3. Service script (run_hpo.py) routes to PlclRunnerHPO or CloudRunnerHPO

    Flow:
    Scheduler → Dedicated Executor → Steep Workflow → run_hpo.py → Runner (on workers)
    """
    print(f"[DEBUG] executeWorkflowHPO called with data: {data.keys() if data else 'None'}")

    workflow_plan = data.get('wf-plan')
    hosts = data.get('hosts')
    deadline = data.get('deadline')

    print(f"[DEBUG] Workflow plan ID: {workflow_plan.get('id') if workflow_plan else 'None'}")
    print(f"[DEBUG] Hosts: {hosts}")
    print(f"[DEBUG] Deadline: {deadline}")

    # Create Steep workflow (same as SeisSol)
    try:
        workflow = Steep_Workflow_HPO(workflow_plan, backend, deadline)
        start_time = backend.now()

        print(f'Executing HPO workflow {workflow.id} at {start_time}')
        print(f'  Workflow will use Steep engine to call service script')
        print(f'  Service script will route to appropriate runner (on-prem/cloud)')
        print(f'[DEBUG] About to call workflow.execute()')

    except Exception as e:
        print(f"[ERROR] Failed to create workflow: {e}")
        import traceback
        traceback.print_exc()
        raise

    # Execute workflow - this blocks until all iterations complete
    # Steep engine will:
    #   1. Parse YAML workflow
    #   2. Execute ForEach actions (workflow iterations)
    #   3. Call service script (run_hpo.py) via subprocess
    #   4. Service script instantiates PlclRunnerHPO or CloudRunnerHPO
    #   5. Runner sets up Ray cluster on WORKERS (not executor)
    #   6. Training happens on workers
    #   7. Results return through call stack
    try:
        new_hosts, isComplete = workflow.execute(hosts)
        print(f"[DEBUG] workflow.execute() returned: new_hosts={new_hosts}, isComplete={isComplete}")
    except Exception as e:
        print(f"[ERROR] Workflow execution failed: {e}")
        import traceback
        traceback.print_exc()
        new_hosts = hosts  # Use original hosts for cleanup
        isComplete = False

    # Tell scheduler workflow execution is complete
    try:
        if backend.simulated:
            backend.sleep(7.7)  # Executor overhead (simulation only)

        request = {
            "wf-id": workflow.id,
            "hosts": new_hosts,
            "start-time": start_time,
            "finish-time": backend.now(),
            "complete": isComplete
        }

        print(f"HPO Workflow {workflow.id} complete at {request['finish-time']}")

        if backend.simulated:
            backend.completions.send(request)
        else:
            # Retry completion notification up to 3 times.
            # A lost notification means the scheduler never frees this
            # workflow's resources, hanging the entire experiment.
            for attempt in range(3):
                success = sendRequest(getConfig('scheduler'), getConfig('workflow-complete-port'), request)
                if success:
                    break
                print(f"[RETRY] Completion notification for {workflow.id} failed (attempt {attempt+1}/3)")
                import time as _time
                _time.sleep(5)
            else:
                print(f"[ERROR] All 3 completion notification attempts failed for {workflow.id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify scheduler: {e}")
    finally:
        # ALWAYS clean up on-demand instances, even if notification failed
        # Note: JSON serialization converts tuples to lists, so check both
        if not backend.simulated:
            for node in new_hosts.get('on-demand', {}):
                val = new_hosts['on-demand'][node]
                if isinstance(val, (tuple, list)) and len(val) >= 2:
                    ips = val[1]
                    print(f"[CLEANUP] Terminating on-demand instances: {ips}")
                    try:
                        deleteInstanceFromIp(ips, backend)
                    except Exception as e:
                        print(f"[ERROR] Termination failed for {ips}: {e}")

    print(f"HPO Workflow {workflow.id} thread exiting")


def processNewResourcesHPO(data):
    """
    Process new resource allocations for moldable HPO workflows
    Updates workflow config with new worker instances
    """
    wf_id = data['wf-id']
    # Only accept if executor is still waiting for this response.
    # Late responses (after executor timeout) are discarded to prevent
    # stale resources being picked up by the next iteration.
    if not isResourceRequestPending(wf_id):
        print(f"[DISCARD] Late resource response for {wf_id} (executor already timed out)")
        return
    setNewResources(wf_id, (data.get('request'), data.get('hosts')))
    print(f"Updated resources for HPO workflow {wf_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='HPO Executor with Dedicated Architecture')
    parser.add_argument('--ip', required=False, help='Executor IP address (optional, auto-detected if not provided)')

    args = parser.parse_args()

    executor_ip = args.ip if args.ip else "auto-detected"

    print('=' * 60)
    print('HPO EXECUTOR - Dedicated Architecture')
    print('=' * 60)
    print(f'Executor IP: {executor_ip}')
    print(f'Port: {getConfig("executor-incoming-port")} (8089)')
    print('')
    print('Architecture:')
    print('  - Executor: Runs on this dedicated instance')
    print('  - Workers: Separate instances (homogeneous, no straggling)')
    print('  - Ray Cluster: Runs on WORKERS (not executor)')
    print('  - Steep Engine: Handles workflow orchestration')
    print('  - Service Script: Routes to appropriate runner')
    print('')
    print('Workflow Flow:')
    print('  Scheduler → Executor → Steep → run_hpo.py → Runner → Workers')
    print('=' * 60)

    from elastiflow.execution.backend import LiveBackend
    from elastiflow.executor import serve
    from elastiflow.scripts.create_instance_HPO import launch_workers, terminate_live
    serve(LiveBackend(launch=launch_workers, terminate=terminate_live), executeWorkflowHPO, processNewResourcesHPO,   # the executor node: HPO's own provisioning
          started=('HPO Executor started successfully!',
                   f'Listening for workflows on port {getConfig("executor-incoming-port")}...',
                   'Press Ctrl+C to stop', ''))
