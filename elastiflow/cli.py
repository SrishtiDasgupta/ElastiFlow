"""The command line: one switch for the execution mode, one for the policy.

    python -m elastiflow run --mode simulated|live --use-case seissol|licence|hpo
                             [--policy NAME] [runner options]

`--mode` selects the entry point that constructs the execution backend
(dissertation Fig. 7.1): the simulated entry points build a `SimulatedBackend`
on a simulus simulator, the live ones a `LiveBackend` on Redis and AWS.
`--policy` names a scheduling policy as the dissertation does
(elastiflow/policies.py) and is translated into the entry point's own
arguments; everything else after the options is handed to the entry point
unchanged, so each use case keeps its runner options (`--help` after them
prints those).
"""
from __future__ import annotations

import runpy
import sys

from elastiflow import policies

ENTRY_POINTS = {
    ('seissol', 'simulated'): 'elastiflow.simulate_sweep',
    ('seissol', 'live'):      'elastiflow.main',
    ('licence', 'simulated'): 'elastiflow.simulate_main_LA',
    ('licence', 'live'):      'elastiflow.main_LA',
    ('hpo', 'simulated'):     'elastiflow.simulate_main_HPO',   # the hybrid driver: simulus clock, live services (see docs/PHASE_B_BACKEND.md)
    ('hpo', 'live'):          'elastiflow.main_HPO',
}
MODES = ('simulated', 'live')
USE_CASES = ('seissol', 'licence', 'hpo')
OPTIONS = ('--mode', '--use-case', '--policy')
# The live SeisSol and licence entry points construct their policy themselves.
LIVE_FIXED = {'seissol': 'fcfs_scheduler (main.py)', 'licence': 'FCFS-LAMF (main_LA.py)'}


def usage() -> str:
    lines = [f"usage: elastiflow run --mode {{{'|'.join(MODES)}}} --use-case {{{'|'.join(USE_CASES)}}} [--policy NAME] [runner options]", ""]
    lines += ["  --mode      simulated: discrete-event backend (simulus); live: Redis, HTTP and AWS backend",
              "  --use-case  which entry point: seissol (SeisSol--TinyDA), licence (licence-constrained), hpo",
              "  --policy    the scheduling policy by its dissertation name, translated into the runner's arguments:"]
    for uc in USE_CASES:
        lines.append(f"                {uc:8s} {', '.join(policies.names(uc))}")
    lines += ["              (the live SeisSol and licence entry points fix their policy: " + ", ".join(LIVE_FIXED.values()) + ")",
              "  runner options are passed unchanged to the entry point; add --help to see them, e.g.",
              "    elastiflow run --mode simulated --use-case seissol --help",
              "    elastiflow run --mode simulated --use-case seissol --policy Elastic-EDF_c /tmp/out --seed 7 --N 100",
              "    elastiflow run --mode simulated --use-case licence --policy EDF-LAMF --N 150 --seed 7 --output-dir /tmp/la",
              "    elastiflow run --mode simulated --use-case seissol edf moldable /tmp/out --sort-key cost --seed 7 --N 100", ""]
    return "\n".join(lines)


def parse(argv: list[str]) -> tuple[str, str, str | None, list[str]]:
    """Split argv into (use_case, mode, policy or None, runner_args). The options
    come first; the first token that is not one of them starts the runner's own
    arguments."""
    if not argv or argv[0] != 'run':
        raise SystemExit(usage())
    tokens, opts, i = argv[1:], {}, 0
    while i < len(tokens):
        t = tokens[i]
        if t in OPTIONS:
            if i + 1 >= len(tokens):
                raise SystemExit(f'{t} needs a value\n{usage()}')
            opts[t] = tokens[i + 1]; i += 2
        elif any(t.startswith(o + '=') for o in OPTIONS):
            k, v = t.split('=', 1); opts[k] = v; i += 1
        else:
            break
    rest = tokens[i:]
    if rest[:1] == ['--']:
        rest = rest[1:]
    mode, use_case = opts.get('--mode'), opts.get('--use-case')
    if mode not in MODES or use_case not in USE_CASES:
        raise SystemExit(usage())
    return use_case, mode, opts.get('--policy'), rest


def argv_for(use_case: str, mode: str, policy: str | None, rest: list[str]) -> list[str]:
    """The entry point's argv (without argv[0])."""
    if policy is None:
        return rest
    if mode == 'live' and use_case in LIVE_FIXED:
        raise SystemExit(f'--policy does not apply to the live {use_case} entry point, which runs {LIVE_FIXED[use_case]}')
    try:
        p = policies.get(use_case, policy)
    except KeyError as e:
        raise SystemExit(str(e.args[0]))
    return policies.runner_args(p, rest)


def main(argv: list[str] | None = None) -> None:
    use_case, mode, policy, rest = parse(sys.argv[1:] if argv is None else argv)
    module = ENTRY_POINTS[(use_case, mode)]
    sys.argv = [module, *argv_for(use_case, mode, policy, rest)]   # run_module sets argv[0] to the module's file
    runpy.run_module(module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
