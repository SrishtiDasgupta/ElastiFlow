"""
The Licence Manager (dissertation Chapter 6, Licence Manager).

Pool accounting with hold, commit and release (the holdUnits, commitHold and
releaseHold of the two-phase commit), hold expiry, and per-workflow licence
cost. Invoked only from the Scheduler's serialised loop, so it needs no locks;
the clock arrives through set_sim_time in both execution modes. Picklable, no
Redis, no complex state.
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
from elastiflow.config.paths import PACKAGE_DIR


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

    def __init__(self, config_path: str = f"{PACKAGE_DIR}/config/licenses.yaml"):
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

        # Experimental token-capacity knobs: scale pool CAPACITY only (never the
        # cited cost model / token laws). LA_LICENSE_SCALE<1 forces scarcity;
        # per-pool LA_LICENSE_SCALE_<POOL> overrides it (e.g. an Abaqus-heavy deck
        # needs a bigger Abaqus pool to stay non-binding, isolating the solver law).
        import os
        _scale = float(os.environ.get("LA_LICENSE_SCALE", "1.0"))

        for pool_name, pool_data in pools_cfg.items():
            _s = float(os.environ.get(f"LA_LICENSE_SCALE_{pool_name}", _scale))
            self.pools[pool_name] = {
                "total_tokens": max(1, int(pool_data.get("total_tokens", 0) * _s)),
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

        # === Honest license billing (token-hold ledger, sim time) ===
        # Single source of truth: bill for tokens actually CHECKED OUT of the pool
        # (Henkel & Treiber "Token-Hours"), so cost == contention == occupancy basis.
        # Fixes (1) the partial-release buffer (held-but-unbilled) and (2) any
        # instance-vs-pool token mismatch, by billing the pool's own amounts.
        self.sim_now: float = 0.0                       # set by scheduler each tick
        self._open_alloc: Dict[str, tuple] = {}         # hold_id -> (owner, pool, amount, start_sim)
        self.cost_ledger: list = []                     # finalized (owner, pool, amount, start_sim, end_sim)

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
        return self.policy.tokens_required(cores, software)

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
            id=hold_id,
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
            committed_at=time.time()
        )

        self.allocations[hold_id] = allocation
        self.pools[hold.pool]["held"] -= hold.amount
        self.pools[hold.pool]["allocated"] += hold.amount

        # Open a ledger interval at the current sim time (tokens now checked out).
        self._open_alloc[hold_id] = (hold.owner, hold.pool, hold.amount, self.sim_now)

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

        # Close the ledger interval at the current sim time.
        iv = self._open_alloc.pop(hold_id, None)
        if iv is not None:
            owner, ipool, amount, start_sim = iv
            self.cost_ledger.append((owner, ipool, amount, start_sim, self.sim_now))

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

    def set_sim_time(self, t: float):
        """Advance the ledger clock (sim time) used to stamp hold/release events."""
        self.sim_now = float(t)

    def license_cost_by_owner(self, final_sim: float = None) -> Dict[str, float]:
        """
        Honest license cost per owner, in EUR (Henkel & Treiber 'Token-Hours').

        cost(owner) = sum over its token-hold intervals of
                      (tokens checked out) x (per-token EUR/s) x (duration held in sim s).

        This is the single source of truth: it bills exactly the tokens the pool
        had checked out — so it includes the partial-release buffer and matches the
        occupancy timeline by construction. Still-open intervals are closed at
        final_sim (defaults to the latest stamped sim time).
        """
        SPY = 365.25 * 24 * 3600.0
        RATE = {'LSDYNA': 1000.0 / SPY, 'ABAQUS': 2500.0 / SPY}
        ANSYS_MEBA = 14000.0 / SPY      # 1 MEBA token per allocation
        ANSYS_WG = 1700.0 / SPY         # workgroup tokens (amount-1)

        if final_sim is None:
            final_sim = self.sim_now

        intervals = list(self.cost_ledger)
        for _hid, (owner, pool, amount, start_sim) in self._open_alloc.items():
            intervals.append((owner, pool, amount, start_sim, final_sim))

        cost: Dict[str, float] = {}
        for owner, pool, amount, start, end in intervals:
            end = final_sim if end is None else end
            dur = end - start
            if dur <= 0:
                continue
            p = (pool or '').upper()
            if p == 'ANSYS':
                c = (ANSYS_MEBA + max(0, amount - 1) * ANSYS_WG) * dur
            else:
                c = amount * RATE.get(p, 0.0) * dur
            cost[owner] = cost.get(owner, 0.0) + c
        return cost

    def license_cost_by_pool(self, final_sim: float = None) -> Dict[str, float]:
        """Honest license cost per pool (same Token-Hours basis as license_cost_by_owner)."""
        SPY = 365.25 * 24 * 3600.0
        RATE = {'LSDYNA': 1000.0 / SPY, 'ABAQUS': 2500.0 / SPY}
        ANSYS_MEBA = 14000.0 / SPY
        ANSYS_WG = 1700.0 / SPY
        if final_sim is None:
            final_sim = self.sim_now
        intervals = list(self.cost_ledger)
        for _hid, (owner, pool, amount, start_sim) in self._open_alloc.items():
            intervals.append((owner, pool, amount, start_sim, final_sim))
        cost: Dict[str, float] = {}
        for owner, pool, amount, start, end in intervals:
            end = final_sim if end is None else end
            dur = end - start
            if dur <= 0:
                continue
            p = (pool or '').upper()
            c = ((ANSYS_MEBA + max(0, amount - 1) * ANSYS_WG) * dur if p == 'ANSYS'
                 else amount * RATE.get(p, 0.0) * dur)
            cost[p] = cost.get(p, 0.0) + c
        return cost

    def __repr__(self):
        return f"LicenseManager(pools={list(self.pools.keys())}, holds={len(self.holds)}, allocations={len(self.allocations)})"
