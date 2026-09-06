"""The command line: one switch for the execution mode.

    python -m elastiflow run --mode simulated|live --use-case seissol|licence|hpo [runner options]

`--mode` selects the entry point that constructs the execution backend
(dissertation Fig. 7.1): the simulated entry points build a `SimulatedBackend`
on a simulus simulator, the live ones a `LiveBackend` on Redis and AWS. Every
token after the two options is handed to that entry point unchanged, so each
use case keeps its own runner options; `--help` after them prints those.
"""
from __future__ import annotations

import runpy
import sys

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

USAGE = f"""usage: elastiflow run --mode {{{'|'.join(MODES)}}} --use-case {{{'|'.join(USE_CASES)}}} [runner options]

  --mode      simulated: discrete-event backend (simulus); live: Redis, HTTP and AWS backend
  --use-case  which entry point: seissol (SeisSol--TinyDA), licence (licence-constrained), hpo
  runner options are passed unchanged to the entry point; add --help to see them, e.g.
    elastiflow run --mode simulated --use-case seissol --help
    elastiflow run --mode simulated --use-case seissol edf moldable /tmp/out --sort-key cost --seed 7 --N 100
    elastiflow run --mode simulated --use-case licence --scheduler EDF-LAMF --N 150 --seed 7 --output-dir /tmp/la
"""


def parse(argv: list[str]) -> tuple[str, str, list[str]]:
    """Split argv into (use_case, mode, runner_args). The two options come first;
    the first token that is not one of them starts the runner's own arguments."""
    if not argv or argv[0] != 'run':
        raise SystemExit(USAGE)
    tokens, opts, i = argv[1:], {}, 0
    while i < len(tokens):
        t = tokens[i]
        if t in ('--mode', '--use-case'):
            if i + 1 >= len(tokens):
                raise SystemExit(f'{t} needs a value\n{USAGE}')
            opts[t] = tokens[i + 1]; i += 2
        elif t.startswith('--mode=') or t.startswith('--use-case='):
            k, v = t.split('=', 1); opts[k] = v; i += 1
        else:
            break
    rest = tokens[i:]
    if rest[:1] == ['--']:
        rest = rest[1:]
    mode, use_case = opts.get('--mode'), opts.get('--use-case')
    if mode not in MODES or use_case not in USE_CASES:
        raise SystemExit(USAGE)
    return use_case, mode, rest


def main(argv: list[str] | None = None) -> None:
    use_case, mode, rest = parse(sys.argv[1:] if argv is None else argv)
    module = ENTRY_POINTS[(use_case, mode)]
    sys.argv = [module, *rest]                     # run_module sets argv[0] to the module's file
    runpy.run_module(module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
