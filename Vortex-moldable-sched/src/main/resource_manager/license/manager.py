"""
Simple License Manager for Simulation

Picklable, thread-safe license accounting for SimPy simulations.
No threading locks (simulation is single-threaded), no Redis, no complex state.
"""

from __future__ import annotations

import time
import uuid
import yaml
from typing import Dict, Optional, List

from .models import Hold, Allocation
from .policy import Policy
from .exceptions import (
    LicenseError, UnknownPool, InsufficientTokens,
    InvalidHold, HoldExpired, AlreadyCommitted
)


class LicenseManager:
    """
    Simple license token accounting for simulation.

    Two-phase commit:
      1. hold(pool, amount, owner, ttl) -> hold_id (reserve tokens)
      2. commit(hold_id) -> convert hold to allocation
      3. release(hold_id) -> free tokens

    Simplified for simulation:
    - No threading locks (simulation is single-process)
    - No Redis/database persistence
    - Simple in-memory state
    - Picklable for multiprocessing
    """

    def __init__(self, config_path: str = "/Users/srishtidasgupta/PhD/intermediate/Vortex-mid/Vortex-moldable-sched/src/main/config/licenses.yaml"):
        """
        Initialize license manager from YAML config

        Args:
            config_path: Path to licenses.yaml
        """
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}

        # Parse pool configuration
        pools_cfg = cfg.get("pools", {})
        self.pools: Dict[str, Dict] = {}

        for pool_name, pool_data in pools_cfg.items():
            self.pools[pool_name] = {
                "total_tokens": pool_data.get("total_tokens", 0),
                "reserved_floor": pool_data.get("reserved_floor", 0),
                "burst_cap": pool_data.get("burst_cap"),
                "allocated": 0,  # Currently allocated tokens
                "held": 0,       # Tokens in uncommitted holds
            }

        # Parse policy configuration
        policy_cfg = cfg.get("policy", {})
        self.policy_mode = policy_cfg.get("mode", "per_core")
        self.two_phase_commit = policy_cfg.get("two_phase_commit", True)
        self.lifo_release = policy_cfg.get("lifo_release", True)

        # Initialize policy calculator
        self.policy = Policy.from_cfg(policy_cfg)

        # Track holds and allocations
        self.holds: Dict[str, Hold] = {}          # hold_id -> Hold
        self.allocations: Dict[str, Allocation] = {}  # hold_id -> Allocation

        # LIFO tracking per pool
        self.allocation_order: Dict[str, List[str]] = {
            pool: [] for pool in self.pools.keys()
        }

    def get_available_tokens(self, pool: str) -> int:
        """
        Get number of available tokens in a pool

        Args:
            pool: Pool name (e.g., 'ANSYS')

        Returns:
            Number of available tokens

        Raises:
            UnknownPool: If pool doesn't exist
        """
        if pool not in self.pools:
            raise UnknownPool(f"Pool '{pool}' not found")

        p = self.pools[pool]
        total = p["total_tokens"]
        allocated = p["allocated"]
        held = p["held"]
        reserved_floor = p["reserved_floor"]

        return max(0, total - allocated - held - reserved_floor)

    def calculate_tokens(self, pool: str, cores: int, chains: int = 1) -> int:
        """
        Calculate tokens needed for given cores/chains

        Args:
            pool: Pool name
            cores: Number of CPU cores
            chains: Number of parallel chains

        Returns:
            Number of tokens required
        """
        # Map pool name to software type for policy
        pool_to_software = {
            'ANSYS': 'ansys',
            'ABAQUS': 'abaqus',
            'LSDYNA': 'lsdyna'
        }

        software = pool_to_software.get(pool, 'default')
        return self.policy.calculate_tokens(software, cores, chains)

    def get_pool_for_software(self, software_id: int) -> Optional[str]:
        """
        Map software ID to pool name

        Args:
            software_id: 0=unlicensed, 1=ANSYS, 2=ABAQUS, 3=LSDYNA

        Returns:
            Pool name or None
        """
        mapping = {
            1: 'ANSYS',
            2: 'ABAQUS',
            3: 'LSDYNA'
        }
        return mapping.get(software_id)

    def hold(self, pool: str, amount: int, owner: str, ttl: int = 300) -> str:
        """
        Create a hold (reserve tokens without committing)

        Args:
            pool: Pool name
            amount: Number of tokens to hold
            owner: Workflow ID or owner identifier
            ttl: Time-to-live in seconds (for expiry checking)

        Returns:
            hold_id: Unique hold identifier

        Raises:
            UnknownPool: Pool doesn't exist
            InsufficientTokens: Not enough tokens available
        """
        if pool not in self.pools:
            raise UnknownPool(f"Pool '{pool}' not found")

        available = self.get_available_tokens(pool)
        if available < amount:
            raise InsufficientTokens(
                f"Pool '{pool}': need {amount}, have {available}"
            )

        # Create hold
        hold_id = str(uuid.uuid4())
        hold = Hold(
            hold_id=hold_id,
            pool=pool,
            amount=amount,
            owner=owner,
            created_at=time.time(),
            ttl=ttl,
            committed=False
        )

        self.holds[hold_id] = hold
        self.pools[pool]["held"] += amount

        return hold_id

    def commit(self, hold_id: str):
        """
        Commit a hold (convert to allocation)

        Args:
            hold_id: Hold identifier from hold()

        Raises:
            InvalidHold: Hold doesn't exist
            AlreadyCommitted: Hold already committed
            HoldExpired: Hold has expired
        """
        if hold_id not in self.holds:
            raise InvalidHold(f"Hold '{hold_id}' not found")

        hold = self.holds[hold_id]

        if hold.committed:
            raise AlreadyCommitted(f"Hold '{hold_id}' already committed")

        # Check expiry (optional in simulation)
        if time.time() - hold.created_at > hold.ttl:
            # Expired hold - release it
            self.pools[hold.pool]["held"] -= hold.amount
            del self.holds[hold_id]
            raise HoldExpired(f"Hold '{hold_id}' expired")

        # Convert hold to allocation
        allocation = Allocation(
            hold_id=hold_id,
            pool=hold.pool,
            amount=hold.amount,
            owner=hold.owner,
            allocated_at=time.time()
        )

        self.allocations[hold_id] = allocation
        self.pools[hold.pool]["held"] -= hold.amount
        self.pools[hold.pool]["allocated"] += hold.amount

        # Track for LIFO release
        if self.lifo_release:
            self.allocation_order[hold.pool].append(hold_id)

        # Mark hold as committed
        hold.committed = True

    def release(self, hold_id: str):
        """
        Release an allocation (free tokens)

        Args:
            hold_id: Hold/allocation identifier

        Raises:
            InvalidHold: Hold/allocation doesn't exist
        """
        if hold_id not in self.allocations:
            # Maybe it's an uncommitted hold - just clean it up
            if hold_id in self.holds:
                hold = self.holds[hold_id]
                self.pools[hold.pool]["held"] -= hold.amount
                del self.holds[hold_id]
                return
            raise InvalidHold(f"Allocation '{hold_id}' not found")

        alloc = self.allocations[hold_id]
        pool = alloc.pool

        # Free tokens
        self.pools[pool]["allocated"] -= alloc.amount

        # Remove from LIFO tracking
        if self.lifo_release and hold_id in self.allocation_order[pool]:
            self.allocation_order[pool].remove(hold_id)

        # Clean up
        del self.allocations[hold_id]
        if hold_id in self.holds:
            del self.holds[hold_id]

    def get_pool_status(self, pool: str) -> Dict:
        """
        Get current status of a pool

        Args:
            pool: Pool name

        Returns:
            Dict with total, allocated, held, available
        """
        if pool not in self.pools:
            raise UnknownPool(f"Pool '{pool}' not found")

        p = self.pools[pool]
        return {
            "total": p["total_tokens"],
            "allocated": p["allocated"],
            "held": p["held"],
            "available": self.get_available_tokens(pool),
            "reserved_floor": p["reserved_floor"]
        }

    def __repr__(self):
        return f"LicenseManager(pools={list(self.pools.keys())}, holds={len(self.holds)}, allocations={len(self.allocations)})"
