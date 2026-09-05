"""
License-Aware Resource Manager

Extends the standard resource manager with license tracking capabilities.
Maintains workflow state including both compute resources and license holds.
"""

from typing import List, Tuple, Optional

from .resource_manager import ResourceManager
from .license.manager import LicenseManager
from .license.exceptions import LicenseError
from elastiflow.config.paths import PACKAGE_DIR


class ResourceManager_LA(ResourceManager):
    """
    License-Aware Resource Manager

    Extends ResourceManager to track license allocations alongside compute resources.
    Maintains license holds for each workflow and coordinates dual-resource lifecycle.
    """

    def __init__(self, path_to_resources=f'{PACKAGE_DIR}/config/resources.yaml'):
        super().__init__(path_to_resources)

        # Initialize license manager
        self.license_manager = LicenseManager()

        # Track license holds per workflow: {wf_id: [hold_ids]}
        self.workflow_licenses = {}

    def addWorkflow(self, id, instances, budget, deadline, start_time, mesh,
                    software_id=0, license_holds=None):
        """
        Add workflow with license tracking

        Extended tuple format:
        (instances, budget, deadline, start_time, mesh, software_id, license_holds)

        Args:
            id: Workflow identifier
            instances: List of (Instance, count, ips)
            budget: Budget constraint
            deadline: Deadline constraint
            start_time: Workflow start time
            mesh: Mesh configuration
            software_id: Software identifier (0 = unlicensed)
            license_holds: List of license hold IDs

        Returns:
            Extended workflow tuple
        """
        license_holds = license_holds or []

        # Store extended workflow state
        self.workflows[id] = (
            instances, budget, deadline, start_time, mesh,
            software_id, license_holds
        )

        # Track license holds separately for quick access
        if license_holds:
            self.workflow_licenses[id] = license_holds

        return self.workflows[id]

    def getWorkflow(self, id):
        """
        Get workflow state

        Returns:
            For LA workflows: (instances, budget, deadline, start_time, mesh, software_id, license_holds)
            For legacy workflows: (instances, budget, deadline, start_time, mesh)
        """
        return self.workflows.get(id, None)

    def updateWorkflowLicenses(self, id, new_license_holds, mode='append'):
        """
        Update license holds for a workflow

        Args:
            id: Workflow ID
            new_license_holds: List of hold IDs to add or replace with
            mode: 'append' (add to existing) or 'replace' (overwrite existing)
        """
        if id not in self.workflows:
            return

        wf = list(self.workflows[id])

        # Handle both LA and legacy workflow formats
        if len(wf) == 7:
            # LA format - update license_holds
            if mode == 'replace':
                # Replace existing holds entirely (for partial release sync)
                wf[6] = new_license_holds
            else:
                # Append mode (default behavior)
                current_holds = wf[6] or []
                wf[6] = current_holds + new_license_holds
            self.workflows[id] = tuple(wf)

            # Update quick-access dict
            self.workflow_licenses[id] = wf[6]
        else:
            # Legacy format - log warning
            print(f"⚠ Cannot update licenses for legacy workflow {id}")

    def getLicenseHolds(self, id) -> List[str]:
        """
        Get all license holds for a workflow

        Args:
            id: Workflow ID

        Returns:
            List of hold IDs (empty if no licenses)
        """
        return self.workflow_licenses.get(id, [])

    def returnResources(self, id, instances=None):
        """
        Return resources AND release licenses

        Extends base returnResources() to also release license holds.

        Args:
            id: Workflow ID or instances if called with instances parameter
            instances: Optional list of instances to free (for partial freeing)
        """
        # Handle partial freeing (scale-down case)
        if instances is not None:
            # Free compute resources
            for instance, count, ips in instances:
                instance.freeResources(count, ips)

            # Note: For partial freeing, licenses are released in scheduler
            # via freeResourcesWithLicenses(), not here
            self.setResourcesAvailable(True)
            return

        # Full workflow completion - free everything
        if id not in self.workflows:
            return

        wf = self.workflows.pop(id)

        # Free compute resources
        compute_instances = wf[0]
        for instance, count, ips in compute_instances:
            instance.freeResources(count, ips)

        # Free ALL licenses for this workflow
        if id in self.workflow_licenses:
            hold_ids = self.workflow_licenses.pop(id)
            for hold_id in hold_ids:
                try:
                    self.license_manager.release(hold_id)
                    print(f"✓ Released license hold {hold_id} for {id}")
                except LicenseError as e:
                    print(f"✗ Error releasing license {hold_id}: {e}")

        self.setResourcesAvailable(True)

    def releaseLicenseHold(self, id, hold_id):
        """
        Release a specific license hold for a workflow

        Used for partial license release (e.g., scale-down scenarios).

        Args:
            id: Workflow ID
            hold_id: Specific hold ID to release
        """
        if id not in self.workflow_licenses:
            return

        try:
            self.license_manager.release(hold_id)

            # Remove from tracking
            self.workflow_licenses[id] = [
                h for h in self.workflow_licenses[id] if h != hold_id
            ]

            # Update workflow tuple
            if id in self.workflows:
                wf = list(self.workflows[id])
                if len(wf) == 7:
                    wf[6] = self.workflow_licenses[id]
                    self.workflows[id] = tuple(wf)

            print(f"✓ Released license hold {hold_id} for {id}")

        except LicenseError as e:
            print(f"✗ Error releasing license {hold_id}: {e}")

    def getSoftwareId(self, id) -> int:
        """
        Get the software ID for a workflow

        Args:
            id: Workflow ID

        Returns:
            Software ID (0 if unlicensed or not found)
        """
        if id not in self.workflows:
            return 0

        wf = self.workflows[id]
        if len(wf) >= 6:
            return wf[5]
        return 0

    def getWorkflowLicenseInfo(self, id) -> dict:
        """
        Get complete license information for a workflow

        Args:
            id: Workflow ID

        Returns:
            Dict with keys: software_id, license_holds, license_pool
        """
        if id not in self.workflows:
            return {'software_id': 0, 'license_holds': [], 'license_pool': None}

        wf = self.workflows[id]

        if len(wf) >= 7:
            software_id = wf[5]
            license_holds = wf[6] or []

            # Determine license pool from software_id
            license_pool = None
            if software_id > 0:
                # Map software_id to pool name
                # This mapping should match your workflow configuration
                pool_map = {
                    1: 'ANSYS',
                    2: 'ABAQUS',
                    3: 'LSDYNA'
                }
                license_pool = pool_map.get(software_id)

            return {
                'software_id': software_id,
                'license_holds': license_holds,
                'license_pool': license_pool
            }

        return {'software_id': 0, 'license_holds': [], 'license_pool': None}
