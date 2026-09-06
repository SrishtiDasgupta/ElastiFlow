import threading
from elastiflow.utils.exec_sched import setNewResources
from elastiflow.scripts.create_instance import deleteInstanceFromIp
from elastiflow.utils.request import getConfig, sendRequest
from elastiflow.workflow.steep_workflow import Steep_Workflow
from elastiflow.server import server 
from elastiflow.wf_queue.redis_queue import Redis_Queue

def processQueueData(queue, backend):
    while True:
        data = queue.peek()
        if data:
            data = eval(data)
            if data["initial-alloc"]:
                # NOTE: For every request to the executor, we create a new thread - not needed for actual run
                thread = threading.Thread(target=executeWorklow, args=[data, backend])
                thread.start()
            else:
                processNewResources(data)
            queue.pop()


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
    queue = Redis_Queue(queue_name='exec-queue')
    from elastiflow.execution.backend import LiveBackend
    backend = LiveBackend()   # the executor node: wall clock, HTTP to the scheduler, boto3 release
    server_thread = threading.Thread(target=server.run, kwargs={'queue': queue, 'port': getConfig('executor-incoming-port')})
    server_thread.start()
    # Queue listener
    queue_listener = threading.Thread(target=processQueueData, args=[queue, backend])
    queue_listener.daemon = True
    queue_listener.start()
    print('Started the executor...')

    server_thread.join()
    queue_listener.join()