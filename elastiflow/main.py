import threading

from elastiflow.utils.request import getConfig
from elastiflow.server import server
from elastiflow.wf_queue.redis_queue import Redis_Queue
from elastiflow.scheduler.fcfs_scheduler import FCFS_Scheduler

if __name__ == "__main__":

    # Create a queue for communication
    queue = Redis_Queue(queue_name='wf-queue')
    finish_queue = Redis_Queue(queue_name='completed-jobs-queue')
    resource_request_queue = Redis_Queue(queue_name='resource-request-queue')
    from elastiflow.execution.backend import LiveBackend
    from elastiflow.scripts.create_instance import launchInstance, terminateInstance
    backend = LiveBackend(queue, finish_queue, resource_request_queue, launch=launchInstance, terminate=terminateInstance)
    sched = FCFS_Scheduler(queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration')

    # Create threads
    # Thread to listen to user jobs
    thread1 = threading.Thread(target=server.run, kwargs={'queue': queue})
    # Scheduler thread
    thread2 = threading.Thread(target=sched.run, args=[backend])
    # Thread to listen to the executor for job completion
    thread3 = threading.Thread(target=server.run, kwargs={'queue': finish_queue, 'port': getConfig('workflow-complete-port'), 'handler_class': server.FinishJobHandler}) 
    # Thread to process completed jobs
    thread4 = threading.Thread(target=sched.processJobCompletion, args=[backend])
    # Thread to listen to resource requests
    thread5 = threading.Thread(target=server.run, kwargs={'queue': resource_request_queue, 'port': getConfig('resource-request-port'), 'handler_class': server.ResourceRequestHandler}) 

    # Start the threads
    thread1.start()
    thread2.start()
    thread3.start()
    thread4.start()
    thread5.start()

    # Wait for all threads to complete
    thread1.join()
    thread2.join()
    thread3.join()
    thread4.join()
    thread5.join()
