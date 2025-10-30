from __future__ import annotations

import time
import uuid
import yaml
from threading import RLock
from typing import Dict, Optional, List, Any, Tuple

from .models import Hold,Allocation
from .policy import Policy
from .exceptions import (
    LicenseError, UnknownPool, InsufficientTokens, 
    InvalidHold, HoldExpired, AlreadyCommitted
)

class LicenseManager:
    """
    Two-phase license token accounting across named pools.
      - hold(pool, amount, owner, ttl) -> hold_id
      - commit(hold_id)
      - release(hold_id)
    LIFO release by default. Default policy is per_core.
    """

    def __init__(self, config_path: str = "config/licenses.yaml"):
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}