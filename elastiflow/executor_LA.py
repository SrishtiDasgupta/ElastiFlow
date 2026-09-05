"""
License-Aware Executor

Extends the base executor to handle license-aware workflow execution.
Accepts and logs license hold information but delegates license management to scheduler.
"""

import threading
from elastiflow.utils.sim import getTime
from elastiflow.utils.exec_sched import setNewResources
from elastiflow.scripts.create_instance import deleteInstanceFromIp
from elastiflow.utils.request import getConfig, sendRequest
from elastiflow.workflow.steep_workflow import Steep_Workflow
from elastiflow.server import server
from elastiflow.wf_queue.redis_queue import Redis_Queue
from elastiflow.config.constants import SIMULATE


def processQueueData(queue):
    """
    Process incoming workflow execution requests

    Handles both initial allocations and resource updates (moldable scheduling).
    """
    while True:
        data = queue.peek()
        if data:
            data = eval(data)
            if data["initial-alloc"]:
                # New workflow - create execution thread
                thread = threading.Thread(target=executeWorkflowLA, args=[data])
                thread.start()
            else:
                # Resource update (moldable reallocation)
                processNewResourcesLA(data)
            queue.pop()


def executeWorkflowLA(data, sim=None):
    """
    Execute license-aware workflow using Steep workflow engine

    License management notes:
    - License holds are allocated by scheduler BEFORE execution starts
    - License holds are passed in data['license-holds'] for tracking
    - Licenses remain held throughout workflow execution
    - Licenses are released by scheduler on workflow completion or failure
    - Executor only logs license info; scheduler manages license lifecycle

    Args:
        data: Dict with keys:
            - wf-plan: Workflow YAML plan
            - hosts: Allocated compute resources
            - deadline: Workflow deadline
            - license-holds: List of license hold IDs (optional)
        sim: SimPy environment (None for real execution)
    """
    workflow_plan = data.get('wf-plan')
    hosts = data.get('hosts')
    deadline = data.get('deadline')
    license_holds = data.get('license-holds', [])

    # Create Steep workflow
    workflow = Steep_Workflow(workflow_plan, sim, deadline)
    start_time = getTime(sim)

    print(f'Executing workflow {workflow.id} at {start_time}')
    if license_holds:
        print(f'  with {len(license_holds)} license hold(s): {license_holds}')

    # Execute workflow - this blocks until all iterations complete
    new_hosts, isComplete = workflow.execute(hosts)

    # Executor overhead
    if SIMULATE:
        sim.sleep(7.7)

    # Report completion to scheduler
    request = {
        "wf-id": workflow.id,
        "hosts": new_hosts,
        "start-time": start_time,
        "finish-time": getTime(sim),
        "complete": isComplete,
        "license-holds": license_holds  # Pass back for scheduler cleanup
    }

    print(f"Workflow {workflow.id} complete at {request['finish-time']}")
    if license_holds:
        print(f"  (scheduler will release {len(license_holds)} license hold(s))")

    if sim:
        sim.sync().send(sim, 'completed_jobs_mb', str(request))
    else:
        sendRequest(getConfig('scheduler'), getConfig('workflow-complete-port'), request)

    # Kill newly created on-demand instances
    for node in new_hosts.get('on-demand', {}):
        if isinstance(new_hosts['on-demand'][node], tuple):
            deleteInstanceFromIp(new_hosts['on-demand'][node][1])


def processNewResourcesLA(data):
    """
    Process new resource allocations for moldable workflows

    For scale-up operations, new licenses may be allocated by scheduler.
    For scale-down operations, licenses are released by scheduler.
    Executor simply updates workflow config with new resources.

    Args:
        data: Dict with keys:
            - wf-id: Workflow ID
            - request: Request type (REQUEST_RESOURCE or FREE_RESOURCE)
            - hosts: New/freed compute resources
            - license-holds: New license holds (if scale-up) (optional)
    """
    wf_id = data.get('wf-id')
    new_license_holds = data.get('license-holds', [])

    # Update workflow config with new compute resources
    setNewResources(wf_id, (data.get('request'), data.get('hosts')))

    print(f"Updated resources for workflow {wf_id}")
    if new_license_holds:
        print(f"  with {len(new_license_holds)} new license hold(s)")


if __name__ == "__main__":
    print('=' * 60)
    print('LICENSE-AWARE EXECUTOR (LAMF)')
    print('=' * 60)
    print(f'Port: {getConfig("executor-incoming-port")} (8089)')
    print('')
    print('Features:')
    print('  - Accepts license hold information from scheduler')
    print('  - Logs license tracking for debugging')
    print('  - License lifecycle managed by scheduler (not executor)')
    print('  - Compatible with moldable resource adjustments')
    print('=' * 60)

    # Setup executor queue
    queue = Redis_Queue(queue_name='exec-queue')

    # Start HTTP server thread
    server_thread = threading.Thread(
        target=server.run,
        kwargs={'queue': queue, 'port': getConfig('executor-incoming-port')}
    )
    server_thread.start()

    # Start queue listener daemon thread
    queue_listener = threading.Thread(target=processQueueData, args=[queue])
    queue_listener.daemon = True
    queue_listener.start()

    print(f'License-Aware Executor started successfully!')
    print(f'Listening for workflows on port {getConfig("executor-incoming-port")}...')
    print('Press Ctrl+C to stop')
    print('')

    try:
        server_thread.join()
        queue_listener.join()
    except KeyboardInterrupt:
        print("\n\nExecutor shutting down...")
