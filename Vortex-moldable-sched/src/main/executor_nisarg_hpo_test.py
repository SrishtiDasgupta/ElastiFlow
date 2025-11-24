import threading
from utils.sim import getTime
from utils.exec_sched import setNewResources
from scripts.create_instance import deleteInstanceFromIp
from utils.request import getConfig, sendRequest
from workflow.steep_workflow import Steep_Workflow
from server import server 
from wf_queue.redis_queue import Redis_Queue
from config.constants import SIMULATE

def processQueueData(queue):
    while True:
        data = queue.peek()
        if data:
            data = eval(data)
            if data["initial-alloc"]:
                # NOTE: For every request to the executor, we create a new thread - not needed for actual run
                thread = threading.Thread(target=executeWorklow, args=[data])
                thread.start()
            else:
                processNewResources(data)
            queue.pop()


def executeWorklow(data=None, sim=None):
    data = {
        'initial-alloc': True, 
        'wf-plan': {
            'actions': [
                {
                    'actions': [
                        {
                            'inputs': [
                                {
                                    'id': 'config', 
                                    'var': 'i'
                                }
                            ], 
                            'outputs': [
                                {
                                    'id': 'config_out', 
                                    'var': 'output_config'
                                },
                            ], 
                            'service': '/home/ubuntu/Vortex/service/run_seissol.py',
                            'type': 'execute'
                        }
                    ], 
                    'enumerator': 'i', 
                    'input': 'input_config', 
                    'type': 'for', 
                    'yieldToInput': 'output_config'
                }
            ], 
            'api': '4.7.0', 
            'config': {
                'mesh': 500, 
                'workflowConfig': [
                    {
                        'chains': 1, 
                        'tinydaIterations': 8
                    }, 
                    
                ], 
                "workflowIterations": 4
                
            }, 
            'constraints': {
                'budget': 348.79181189868143,
                'chains': 1, 
                'deadline': 145735.06830571947, 
                'tinydaIterations': 8
            }, 
            'id': 'test-d9ac8ddb-7ef6-4781-973f-7965575e5c7b', 
            'vars': [{'id': 'num_trials', 'value': '3'}, {'id': 'input_config', 'value': {"learning_rate": 0.05134898953358611, "momentum": 0.21166724781648533, "hidden": 75, "threads": 2, "epoch": 10, "next_trials":1}}], 
            'submit_time': 1
        }, 
        'hosts': {'on-prem': {}, 'reserved': {'g5.2xlarge': (2, ['10.19.236.153', '10.19.236.57'])}, 'on-demand': {}}, 
        'deadline': 145736.06830571947
    } 
    print(data)
    workflow, hosts = Steep_Workflow(data.get('wf-plan'), sim, data.get('deadline')), data.get('hosts')
    start_time = getTime(sim)
    print(f'Executing workflow {workflow.id} at {start_time}')
    new_hosts, isComplete = workflow.execute(hosts)
    # Tell scheduler workflow execution is complete
    if SIMULATE: sim.sleep(7.7) #Executor overhead
    request = {
        "wf-id": workflow.id,
        "hosts": new_hosts,
        "start-time": start_time,
        "finish-time": getTime(sim),
        "complete": isComplete
    }
    print(f"Workflow {workflow.id} complete at {request['finish-time']}")
    if sim:
        sim.sync().send(sim, 'completed_jobs_mb', str(request))
    else:
        sendRequest(getConfig('scheduler'), getConfig('workflow-complete-port'), request)

    # kill newly created on-demand instances
    for node in new_hosts['on-demand']:
        deleteInstanceFromIp(new_hosts['on-demand'][node][1])

def processNewResources(data):
    # Update workflow config
    setNewResources(data['wf-id'], (data['request'], data['hosts']))
            
if __name__ == "__main__":
    # queue = Redis_Queue(queue_name='exec-queue')
    # server_thread = threading.Thread(target=server.run, kwargs={'queue': queue, 'port': getConfig('executor-incoming-port')})
    # server_thread.start()
    # # Queue listener
    # queue_listener = threading.Thread(target=processQueueData, args=[queue])
    # queue_listener.daemon = True
    # queue_listener.start()
    # print('Started the executor...')

    # server_thread.join()
    # queue_listener.join()
    executeWorklow()