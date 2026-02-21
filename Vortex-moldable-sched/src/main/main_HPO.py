import threading
import sys
import argparse

from utils.request import getConfig
from server import server
from wf_queue.redis_queue import Redis_Queue

# Import HPO-specific schedulers
from scheduler.fcfs_scheduler_HPO import FCFS_Scheduler_HPO
from scheduler.fcfs_optimized_HPO import FCFS_Optimized_HPO
from scheduler.edf_scheduler_HPO import EDF_Scheduler_HPO
from scheduler.edf_optimized_HPO import EDF_Optimized_HPO

def main(scheduler_type='moldable', algo='fcfs'):
    """
    Main entry point for HPO scheduling system

    Args:
        scheduler_type: 'static' or 'moldable' (default: moldable)
        algo: 'fcfs' or 'edf' (default: fcfs)
    """

    print(f"Starting HPO Scheduling System")
    print(f"Algorithm: {algo.upper()}, Mode: {scheduler_type.upper()}")
    print("=" * 50)

    # Create queues for communication
    queue = Redis_Queue(queue_name='wf-queue')
    finish_queue = Redis_Queue(queue_name='completed-jobs-queue')
    resource_request_queue = Redis_Queue(queue_name='resource-request-queue')

    # Select scheduler based on algo + mode
    if algo == 'fcfs' and scheduler_type == 'static':
        print("Using Static HPO FCFS Scheduler")
        sched = FCFS_Scheduler_HPO(queue, finish_queue, resource_request_queue, sort_key='cost_per_trial')
    elif algo == 'fcfs' and scheduler_type == 'moldable':
        print("Using Moldable HPO FCFS Scheduler")
        sched = FCFS_Optimized_HPO(queue, finish_queue, resource_request_queue, sort_key='cost_per_trial')
    elif algo == 'edf' and scheduler_type == 'static':
        print("Using Static HPO EDF Scheduler")
        sched = EDF_Scheduler_HPO(queue, finish_queue, resource_request_queue, sort_key='cost_per_trial')
    elif algo == 'edf' and scheduler_type == 'moldable':
        print("Using Moldable HPO EDF Scheduler")
        sched = EDF_Optimized_HPO(queue, finish_queue, resource_request_queue, sort_key='cost_per_trial')

    print(f"Scheduler initialized: {sched.__class__.__name__}")

    # Create threads
    # Thread to listen to user jobs
    thread1 = threading.Thread(
        target=server.run,
        kwargs={'queue': queue},
        name="JobListener"
    )

    # Scheduler thread
    thread2 = threading.Thread(
        target=sched.run,
        name="Scheduler"
    )

    # Thread to listen to the executor for job completion
    thread3 = threading.Thread(
        target=server.run,
        kwargs={
            'queue': finish_queue,
            'port': getConfig('workflow-complete-port'),
            'handler_class': server.FinishJobHandler
        },
        name="CompletionListener"
    )

    # Thread to process completed jobs
    thread4 = threading.Thread(
        target=sched.processJobCompletion,
        name="CompletionProcessor"
    )

    # Thread to listen to resource requests (important for moldable)
    thread5 = threading.Thread(
        target=server.run,
        kwargs={
            'queue': resource_request_queue,
            'port': getConfig('resource-request-port'),
            'handler_class': server.ResourceRequestHandler
        },
        name="ResourceListener"
    )

    print("\nStarting scheduler threads:")
    print("  1. Job Listener (port 8080)")
    print("  2. HPO Scheduler")
    print("  3. Completion Listener (port 8082)")
    print("  4. Completion Processor")
    print("  5. Resource Request Listener (port 8084)")

    # Start all threads
    thread1.start()
    print("  ✅ Job Listener started")

    thread2.start()
    print("  ✅ HPO Scheduler started")

    thread3.start()
    print("  ✅ Completion Listener started")

    thread4.start()
    print("  ✅ Completion Processor started")

    thread5.start()
    print("  ✅ Resource Request Listener started")

    print("\n" + "=" * 50)
    print("HPO Scheduling System Ready!")
    print(f"Algorithm: {algo.upper()}, Mode: {scheduler_type.upper()}")
    print("Waiting for HPO workflows...")
    print("=" * 50)

    if scheduler_type == 'moldable':
        print("\n📌 Moldable Features Enabled:")
        print("  • Dynamic instance type switching")
        print("  • Elastic resource scaling")
        print("  • Cross-type optimization (g4dn ↔ g5)")
        print("  • Automatic executor creation per workflow")
    else:
        print("\n📌 Static Mode:")
        print("  • Fixed resource allocation")
        print("  • No mid-execution adjustments")
        print("  • Automatic executor creation per workflow")

    print("\n⚠️  Important: Cloud executors will be created automatically")
    print("No manual executor start needed on reserved instances!")
    print("\nPress Ctrl+C to stop the scheduler")

    try:
        # Wait for all threads to complete
        thread1.join()
        thread2.join()
        thread3.join()
        thread4.join()
        thread5.join()
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down HPO Scheduler...")
        sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='HPO Scheduling System')
    parser.add_argument(
        '--mode',
        choices=['static', 'moldable'],
        default='moldable',
        help='Scheduler mode: static or moldable (default: moldable)'
    )
    parser.add_argument(
        '--algo',
        choices=['fcfs', 'edf'],
        default='fcfs',
        help='Scheduling algorithm: fcfs or edf (default: fcfs)'
    )

    args = parser.parse_args()

    # Run main with selected scheduler type and algorithm
    main(scheduler_type=args.mode, algo=args.algo)