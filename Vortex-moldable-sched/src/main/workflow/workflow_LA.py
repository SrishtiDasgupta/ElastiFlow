"""
License-Aware Workflow Class

Extends the base Workflow class with license-related attributes.
Parses software and license pool information from workflow plans.
"""

from .workflow import Workflow


class Workflow_LA(Workflow):
    """
    License-Aware Workflow

    Extends base Workflow with license tracking fields.

    Expected workflow plan structure:
    ```yaml
    id: workflow-id
    constraints:
      budget: 100.0
      deadline: 3600
      chains: 4
      tinydaIterations: 10
      license_pool: ANSYS  # NEW: License pool name (optional)
    config:
      mesh: M1
      software_id: 1       # NEW: Software ID (0 = unlicensed, 1 = ANSYS, 2 = ABAQUS, 3 = LSDYNA)
    ```
    """

    def __init__(self, wf_plan):
        super().__init__(wf_plan)

        # Extract license-specific fields
        self.software_id = wf_plan.get('config', {}).get('software_id', 0)
        self.license_pool = wf_plan.get('constraints', {}).get('license_pool', None)

        # Auto-detect license pool from software_id if not explicitly set
        if self.software_id > 0 and not self.license_pool:
            pool_map = {
                1: 'ANSYS',
                2: 'ABAQUS',
                3: 'LSDYNA'
            }
            self.license_pool = pool_map.get(self.software_id)

        # Track license holds (populated during execution)
        self.license_holds = []

    def requiresLicenses(self) -> bool:
        """
        Check if this workflow requires licenses

        Returns:
            True if license_pool is specified or software_id > 0
        """
        return self.license_pool is not None or self.software_id > 0

    def getSoftwareId(self) -> int:
        """Get software identifier (0 = unlicensed)"""
        return self.software_id

    def getLicensePool(self) -> str:
        """Get license pool name (None if unlicensed)"""
        return self.license_pool

    def addLicenseHold(self, hold_id: str):
        """
        Track a license hold for this workflow

        Args:
            hold_id: License hold identifier from LicenseManager
        """
        if hold_id not in self.license_holds:
            self.license_holds.append(hold_id)

    def getLicenseHolds(self) -> list:
        """Get all license holds for this workflow"""
        return self.license_holds.copy()

    def __repr__(self):
        return (f"Workflow_LA(id={self.id}, software_id={self.software_id}, "
                f"license_pool={self.license_pool}, holds={len(self.license_holds)})")
