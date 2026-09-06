"""
Main entry point for License-Aware Moldable FCFS (LAMF) Scheduler

Runs the LAMF scheduler with dual-resource (compute + license) constraints.
"""

import threading

from elastiflow.utils.request import getConfig
from elastiflow.server import server
from elastiflow.wf_queue.redis_queue import Redis_Queue
from elastiflow.scheduler.fcfs_optimized_LA import FCFS_Optimized_LA


if __name__ == "__main__":

    print('=' * 70)
    print('VORTEX - LICENSE-AWARE MOLDABLE FCFS (LAMF) SCHEDULER')
    print('=' * 70)
    print('Configuration:')
    print('  - Scheduler: LAMF (License-Aware Moldable FCFS)')
    print('  - Resource Management: Hybrid (On-Prem + Cloud)')
    print('  - License Pools: ANSYS, ABAQUS, LSDYNA')
    print('  - Moldable: Dynamic scale-up/scale-down between iterations')
    print('  - Iteration-weighted: OPTIM_FCFS_BFACTOR/DFACTOR allocation')
    print('=' * 70)
    print('')

    # Create Redis queues for communication
    queue = Redis_Queue(queue_name='wf-queue')
    finish_queue = Redis_Queue(queue_name='completed-jobs-queue')
    resource_request_queue = Redis_Queue(queue_name='resource-request-queue')
    from elastiflow.execution.backend import LiveBackend, register
    from elastiflow.scripts.create_instance import launchInstance, terminateInstance
    register(None, LiveBackend(queue, finish_queue, resource_request_queue, launch=launchInstance, terminate=terminateInstance))

    # Initialize LAMF scheduler
    sched = FCFS_Optimized_LA(
        queue,
        finish_queue,
        resource_request_queue,
        sort_key='cost_per_iteration'
    )

    print('Creating threads...')

    # Create threads
    # Thread 1: Listen to user job submissions (port 8080)
    thread1 = threading.Thread(
        target=server.run,
        kwargs={'queue': queue},
        name='JobSubmissionServer'
    )

    # Thread 2: LAMF scheduler (main scheduling loop)
    thread2 = threading.Thread(
        target=sched.run,
        name='LAMFScheduler'
    )

    # Thread 3: Listen to workflow completion notifications (port 8082)
    thread3 = threading.Thread(
        target=server.run,
        kwargs={
            'queue': finish_queue,
            'port': getConfig('workflow-complete-port'),
            'handler_class': server.FinishJobHandler
        },
        name='CompletionServer'
    )

    # Thread 4: Process completed workflows (release compute + licenses)
    thread4 = threading.Thread(
        target=sched.processJobCompletion,
        name='CompletionProcessor'
    )

    # Thread 5: Listen to resource requests from executors (port 8084)
    thread5 = threading.Thread(
        target=server.run,
        kwargs={
            'queue': resource_request_queue,
            'port': getConfig('resource-request-port'),
            'handler_class': server.ResourceRequestHandler
        },
        name='ResourceRequestServer'
    )

    # Start all threads
    print('Starting threads...')
    thread1.start()
    print('  [✓] Job submission server (port 8080)')
    thread2.start()
    print('  [✓] LAMF scheduler')
    thread3.start()
    print('  [✓] Completion server (port 8082)')
    thread4.start()
    print('  [✓] Completion processor')
    thread5.start()
    print('  [✓] Resource request server (port 8084)')

    print('')
    print('=' * 70)
    print('LAMF SCHEDULER RUNNING')
    print('=' * 70)
    print('Submit workflows via: POST http://<scheduler-ip>:8080')
    print('Press Ctrl+C to stop')
    print('=' * 70)
    print('')

    # Wait for all threads to complete
    try:
        thread1.join()
        thread2.join()
        thread3.join()
        thread4.join()
        thread5.join()
    except KeyboardInterrupt:
        print('\n\nShutting down LAMF scheduler...')
        print('Goodbye!')
