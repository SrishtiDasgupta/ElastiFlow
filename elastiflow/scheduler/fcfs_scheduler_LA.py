"""
License-Aware Static FCFS Scheduler

Static (non-moldable) First-Come-First-Served scheduler with license awareness.
Resources and licenses are allocated once at workflow start and remain fixed.
"""

import threading
import time

from elastiflow.resource_manager.resource_manager_LA import ResourceManager_LA
from elastiflow.scheduler.scheduler_LA import Scheduler_LA


class FCFS_Scheduler_LA(Scheduler_LA):
    """
    Static FCFS scheduler with license-awareness

    Workflows processed in FCFS order.
    Both compute resources and licenses allocated at workflow start.
    No dynamic reallocation (use fcfs_optimized_LA for moldable variant).
    """

    def __init__(self, queue, finish_queue, resource_request_queue, sort_key='cost_per_iteration'):
        # Use license-aware resource manager
        self.resource_manager = ResourceManager_LA()
        self.resource_manager.sortResources(sort_key)
        super().__init__(queue, finish_queue, resource_request_queue)

        # Use resource_manager's license_manager (shared instance)
        self.license_manager = self.resource_manager.license_manager

        # Baseline is non-moldable (static allocation only)
        self.is_moldable = False

    def printBanner(self, backend):
        print(f'Starting License-Aware Static FCFS Scheduler at {backend.now()}...')

