import threading
from elastiflow.utils.exec_sched import setNewResources
from elastiflow.scripts.create_instance import deleteInstanceFromIp
from elastiflow.utils.request import getConfig, sendRequest
from elastiflow.workflow.steep_workflow import Steep_Workflow
from elastiflow.server import server 
from elastiflow.wf_queue.redis_queue import Redis_Queue

def processQueueData(queue, backend, execute=None, on_resources=None):
    """The executor node's loop (B7.6: one for every use case): a start request
    runs the use case's execute function in a thread, a resource update goes to
    its on_resources function. Defaults are SeisSol's."""
    execute = execute or executeWorklow
    on_resources = on_resources or processNewResources
    while True:
        data = queue.peek()
        if data:
            data = eval(data)
            if data["initial-alloc"]:
                # NOTE: For every request to the executor, we create a new thread - not needed for actual run
                thread = threading.Thread(target=execute, args=[data, backend])
                thread.start()
            else:
                on_resources(data)
            queue.pop()


def serve(backend, execute=None, on_resources=None, started=('Started the executor...',)):
    """Run an executor node: the Gateway's HTTP server for the scheduler's
    messages on the executor port, and the queue loop above."""
    queue = Redis_Queue(queue_name='exec-queue')
    server_thread = threading.Thread(target=server.run, kwargs={'queue': queue, 'port': getConfig('executor-incoming-port')})
    server_thread.start()
    # Queue listener
    queue_listener = threading.Thread(target=processQueueData, args=[queue, backend, execute, on_resources])
    queue_listener.daemon = True
    queue_listener.start()
    for line in started:
        print(line)
    try:
        server_thread.join()
        queue_listener.join()
    except KeyboardInterrupt:
        print("\n\nExecutor shutting down...")


def executeWorklow(data, backend):
    workflow, hosts = Steep_Workflow(data.get('wf-plan'), backend, data.get('deadline')), data.get('hosts')
    start_time = backend.now()
    print(f'Executing workflow {workflow.id} at {start_time}')
    new_hosts, isComplete = workflow.execute(hosts)
    # Tell scheduler workflow execution is complete
    if backend.simulated: backend.sleep(7.7) #Executor overhead
    request = {
        "wf-id": workflow.id,
        "hosts": new_hosts,
        "start-time": start_time,
        "finish-time": backend.now(),
        "complete": isComplete
    }
    print(f"Workflow {workflow.id} complete at {request['finish-time']}")
    backend.completions.send(request)

    # kill newly created on-demand instances
    for node in new_hosts['on-demand']:
        deleteInstanceFromIp(new_hosts['on-demand'][node][1], backend)

def processNewResources(data):
    # Update workflow config
    setNewResources(data['wf-id'], (data['request'], data['hosts']))
            
if __name__ == "__main__":
    from elastiflow.execution.backend import LiveBackend
    serve(LiveBackend())   # the executor node: wall clock, HTTP to the scheduler, boto3 release