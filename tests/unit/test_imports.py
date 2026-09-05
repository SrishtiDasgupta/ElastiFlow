"""
Composition smoke test for the framework, including the live-mode entry points.

Live mode (main.py, main_LA.py, main_HPO.py, the executors, AWS provisioning)
cannot be regression-tested without infrastructure. What can be checked is that
every module imports and wires together: no missing module, no import-time error,
nothing that blocks or starts a server on import. Each module is imported in a
fresh interpreter with a time limit, so a module that acts on import fails loudly
instead of hanging the suite.

Excluded: the four simulation runners, which execute a simulation on import by
design (they are covered by tests/regression and tests/smoke).
"""
import pkgutil
import subprocess
import sys

import pytest

import elastiflow

RUNNERS = {
    'elastiflow.simulate_main', 'elastiflow.simulate_main_LA',
    'elastiflow.simulate_main_HPO', 'elastiflow.simulate_sweep',
}

# Standalone analysis scripts that act on import: speedup_HPO fits curves from
# g4.jsonl/g5.jsonl in the working directory, speedup_plot_HPO writes
# plots/speedup_HPO.{pdf,png}. Neither is imported by the framework; both are
# run as scripts. They get a __main__ guard when they move to use_cases/ (A5).
STANDALONE_SCRIPTS = {
    'elastiflow.scripts.speedup_HPO', 'elastiflow.scripts.speedup_plot_HPO',
}


def _modules():
    names = []
    for m in pkgutil.walk_packages(elastiflow.__path__, prefix='elastiflow.'):
        if m.name in RUNNERS or m.name in STANDALONE_SCRIPTS or '-' in m.name:
            continue
        names.append(m.name)
    return sorted(names)


@pytest.mark.parametrize('module', _modules())
def test_module_imports_cleanly(module):
    cp = subprocess.run([sys.executable, '-c', f'import {module}'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
    assert cp.returncode == 0, f'{module} failed to import:\n' + '\n'.join(cp.stdout.splitlines()[-12:])
