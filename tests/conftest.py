"""Fixtures shared by every suite under tests/ (regression, unit, smoke)."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='session')
def repo() -> Path:
    return REPO


@pytest.fixture(scope='session')
def python() -> str:
    """Interpreter used to spawn the simulators: the one running pytest."""
    return sys.executable
