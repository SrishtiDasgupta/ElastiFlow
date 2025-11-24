# runner_base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any


class RunnerBase(ABC):
    """Minimal abstract interface for all runners (cloud, PLCL, etc.)."""
    def __init__(self, request: Dict[str, Any] | None = None) -> None:
        self.request: Dict[str, Any] = request or {}

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """Execute the workload and return a result dict."""
        raise NotImplementedError
