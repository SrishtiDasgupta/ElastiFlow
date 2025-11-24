from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class Hold:
    id: str
    pool: str
    amount: int
    owner: Optional[str]
    created_at: float
    ttl: Optional[float]
    committed: bool = False

@dataclass(frozen=True)
class Allocation:
    hold_id: str
    pool: str
    amount: int
    owner: Optional[str]
    committed_at: float
