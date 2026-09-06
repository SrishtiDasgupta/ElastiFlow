"""The policy registry: the dissertation's policy names, the scheduler class
that implements each, how it is constructed, and whether an entry point
offers it.

An entry point resolves a name (or one of its historical aliases) here instead
of carrying its own if-chain. The class is imported only when `load()` is
called, after the runner has patched its constants module, because the
schedulers bind constants at import time (the early-binding pattern of
`simulate_sweep.py`).

Policies the dissertation does not cite are kept, not deleted (author's
decision, 2026-09-06, docs/PHASE_B7_SCHEDULER_MERGE.md): importable,
resolvable by name, run once by the smoke suite against the recorded baseline,
and absent from the choices an entry point prints when `active` is False.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

RUNNER_SORT_KEY = 'runner'   # the constructor's sort_key is the runner's --sort-key


@dataclass(frozen=True)
class Policy:
    name: str                       # the dissertation's name; the module name when it has none
    use_case: str                   # 'seissol' | 'licence' | 'hpo'
    module: str
    cls: str
    sort_key: str | None = None     # constructor kwarg; None when the constructor takes none
    elastic: bool | None = None     # SeisSol: the MOLDABLE constant this name implies; None where the class decides
    aliases: tuple[str, ...] = ()
    active: bool = True
    note: str = ''

    def load(self):
        """Import the scheduler class (only now: the runner has patched its constants)."""
        return getattr(importlib.import_module(self.module), self.cls)

    def kwargs(self, runner_sort_key: str | None = None) -> dict:
        """Constructor keyword arguments beyond the three queues."""
        if self.sort_key is None:
            return {}
        if self.sort_key == RUNNER_SORT_KEY:
            if runner_sort_key is None:
                raise ValueError(f'{self.name} takes its sort key from the runner')
            return {'sort_key': runner_sort_key}
        return {'sort_key': self.sort_key}


def _seissol(name, module, cls, sort_key=None, elastic=None, active=True, note=''):
    return Policy(name, 'seissol', f'elastiflow.scheduler.{module}', cls, sort_key, elastic, (), active, note)


def _licence(name, module, cls, aliases=(), note=''):
    return Policy(name, 'licence', f'elastiflow.scheduler.{module}', cls, 'cost_per_iteration', None, aliases, True, note)


def _hpo(name, module, cls, elastic, note=''):
    return Policy(name, 'hpo', f'elastiflow.scheduler.{module}', cls, None, elastic, (), True, note)


_R, _C = 'runtime_per_iteration', 'cost_per_iteration'

POLICIES: tuple[Policy, ...] = (
    # --- SeisSol--TinyDA (Ch. 7, 9; use_cases/seissol) ---------------------------
    _seissol('FCFS-ST_r', 'fcfs_optimized', 'FCFS_Optimized', _R, elastic=False, note='static FCFS, runtime-sorted resources'),
    _seissol('FCFS-ST_c', 'fcfs_optimized', 'FCFS_Optimized', _C, elastic=False, note='static FCFS, cost-sorted resources'),
    _seissol('Elastic-FCFS_r', 'fcfs_optimized', 'FCFS_Optimized', _R, elastic=True, note='elastic FCFS, runtime-sorted resources'),
    _seissol('Elastic-FCFS_c', 'fcfs_optimized', 'FCFS_Optimized', _C, elastic=True, note='elastic FCFS, cost-sorted resources'),
    _seissol('EDF-ST_r', 'earliest_deadline_edf', 'EarliestDeadlineEDF', _R, elastic=False, note='static EDF, runtime-sorted resources'),
    _seissol('EDF-ST_c', 'earliest_deadline_edf', 'EarliestDeadlineEDF', _C, elastic=False, note='static EDF, cost-sorted resources'),
    _seissol('Elastic-EDF_r', 'earliest_deadline_edf', 'EarliestDeadlineEDF', _R, elastic=True, note='elastic EDF, runtime-sorted resources'),
    _seissol('Elastic-EDF_c', 'earliest_deadline_edf', 'EarliestDeadlineEDF', _C, elastic=True, note='elastic EDF, cost-sorted resources'),
    _seissol('HEFT-ST', 'heft_heft_req', 'HEFT_HEFT_REQ', None, elastic=False, note='static HEFT; takes no sort key'),
    _seissol('Elastic-Rank', 'priority_priority', 'PriorityPriority', None, elastic=True,
             note='elastic rank policy; the (BUDGET_FACTOR, DEADLINE_FACTOR) pair is a runner option: [50,50] = (5.0, 5.0), [25,75] = (2.5, 7.5)'),
    # Not cited by the dissertation. Kept, resolvable by module name, one smoke cell each.
    _seissol('fcfs_scheduler', 'fcfs_scheduler', 'FCFS_Scheduler', RUNNER_SORT_KEY,
             note='not in the dissertation; the policy of the live SeisSol entry point main.py'),
    _seissol('earliest_deadline_fcfs', 'earliest_deadline_fcfs', 'EarliestDeadlineFCFS', RUNNER_SORT_KEY, active=False,
             note='not in the dissertation'),
    _seissol('priority_fcfs', 'priority_fcfs', 'PriorityFCFS', None, active=False, note='not in the dissertation'),
    _seissol('heft_fcfs_req', 'heft_fcfs_req', 'HEFT_FCFS_REQ', None, active=False, note='not in the dissertation'),
    # --- licence-constrained (Ch. 8, 9; use_cases/licence) --------------------------
    _licence('FCFS-ST-LA', 'fcfs_scheduler_LA', 'FCFS_Scheduler_LA', note='Static FCFS with license awareness'),
    _licence('EDF-ST-LA', 'edf_scheduler_LA', 'EDF_Scheduler_LA', note='Static EDF with license awareness'),
    _licence('FCFS-LAMF', 'fcfs_optimized_LA', 'FCFS_Optimized_LA', aliases=('LAMF',), note='License-Aware Moldable FCFS'),
    _licence('EDF-LAMF', 'edf_optimized_LA', 'EDF_Optimized_LA', note='License-Aware Moldable EDF'),
    _licence('HSM', 'edf_hsm_LA', 'EDF_HSM_LA', aliases=('EDF-HSM',), note='Hybrid Static-Moldable EDF'),
    # --- HPO (Ch. 8; live only) -------------------------------------------------------
    _hpo('FCFS-ST', 'fcfs_scheduler_HPO', 'FCFS_Scheduler_HPO', elastic=False, note='static HPO FCFS'),
    _hpo('Elastic-FCFS', 'fcfs_optimized_HPO', 'FCFS_Optimized_HPO', elastic=True, note='elastic HPO FCFS'),
    _hpo('EDF-ST', 'edf_scheduler_HPO', 'EDF_Scheduler_HPO', elastic=False, note='static HPO EDF'),
    _hpo('Elastic-EDF', 'edf_optimized_HPO', 'EDF_Optimized_HPO', elastic=True, note='elastic HPO EDF'),
)

USE_CASES = ('seissol', 'licence', 'hpo')


def policies(use_case: str | None = None, active_only: bool = False) -> list[Policy]:
    return [p for p in POLICIES if (use_case is None or p.use_case == use_case) and (p.active or not active_only)]


def names(use_case: str, active_only: bool = True, with_aliases: bool = False) -> list[str]:
    """The names an entry point offers for a use case, in registry order."""
    out = []
    for p in policies(use_case, active_only):
        out.append(p.name)
        if with_aliases:
            out.extend(p.aliases)
    return out


def get(use_case: str, name: str) -> Policy:
    """A policy by its name or one of its aliases; inactive policies resolve too."""
    for p in policies(use_case):
        if name == p.name or name in p.aliases:
            return p
    raise KeyError(f'unknown {use_case} policy {name!r}; known: {names(use_case, active_only=False, with_aliases=True)}')


def resolve_seissol(algo: str, mode: str, sort_key: str) -> Policy:
    """simulate_sweep.py's (algo, mode, --sort-key) triple. `algo` is one of the
    sweep's words (fcfs, edf, heft, rank) or any SeisSol registry name."""
    suffix = {'runtime': '_r', 'cost': '_c'}[sort_key]
    elastic = mode == 'moldable'
    if algo in ('fcfs', 'edf'):
        return get('seissol', ('Elastic-' if elastic else '') + algo.upper() + ('' if elastic else '-ST') + suffix)
    if algo == 'heft':
        return get('seissol', 'HEFT-ST')
    if algo == 'rank':
        return get('seissol', 'Elastic-Rank')
    return get('seissol', algo)


def resolve_hpo(algo: str, mode: str) -> Policy:
    """The HPO runners' (--algo {fcfs,edf}, --mode {static,moldable}) pair."""
    return get('hpo', ('Elastic-' if mode == 'moldable' else '') + algo.upper() + ('' if mode == 'moldable' else '-ST'))
