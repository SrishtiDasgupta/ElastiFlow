"""The per-use-case configuration profiles (B7.5).

Each use case runs with one constants module: SeisSol--TinyDA with
`constants.py`, the licence-constrained campaign with `constants_LA.py`
(which starts from `constants.py` through `from .constants import *` and
overrides and extends it), HPO with `constants_HPO.py`. The modules stay
where they are, under the names the drivers, the runners and the
dissertation's provenance notes refer to; this module is the one place that
says which module a use case runs with.

The runners patch a profile before any scheduler module imports it
(`simulate_sweep.py` sets MOLDABLE, SEED, TOTAL_WORKFLOWS and the ablation
constants on `constants`; `simulate_main_LA.py` sets TOTAL_WORKFLOWS on
`constants_LA`), and the schedulers bind the values at import time. `load()`
therefore returns the module object itself, never a copy.
"""
from __future__ import annotations

import importlib

PROFILES = {
    'seissol': 'elastiflow.config.constants',
    'licence': 'elastiflow.config.constants_LA',
    'hpo':     'elastiflow.config.constants_HPO',
}


def load(use_case: str):
    """The constants module a use case runs with."""
    return importlib.import_module(PROFILES[use_case])
