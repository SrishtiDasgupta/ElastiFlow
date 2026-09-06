"""
The in-process runtime model (B4b) must give exactly what the service stub gave
when it ran in a subprocess: same runtime, same cohesion, for the same request.
The stub is kept as a CLI over the same functions, so this compares the two
paths the simulated backend can take.
"""
import subprocess
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pytest

from elastiflow.scripts.tinyda_runtime import iteration_runtime

STUB = Path(__file__).resolve().parents[2] / 'elastiflow' / 'scripts' / 'simulate-tinyda-seissol.py'


def _requests():
    for mesh, n_hosts, chains, tinyda in product((500, 750, 1000), (1, 3, 6), (1, 4, 6), (1, 9)):
        yield {'chains': chains, 'cohesion': 3, 'hosts': {'on-prem': ['0.0.0.0'] * n_hosts},
               'tinyda_iterations': tinyda, 'mesh': mesh}
    # a mixed allocation across tiers, as the scheduler hands it to the engine
    yield {'chains': 5, 'cohesion': 3, 'hosts': {'on-prem': ['0.0.0.0'], 'c7i.24xlarge': ['1.2.3.4', '1.2.3.5'],
                                               'hpc7a.24xlarge': ['1.2.3.6']}, 'tinyda_iterations': 5, 'mesh': 750}


@pytest.mark.parametrize('request_', list(_requests()))
def test_in_process_matches_stub(request_):
    cp = subprocess.run([sys.executable, str(STUB), str(request_)], check=True, capture_output=True, text=True,
                        cwd=STUB.parent)
    from_stub = eval(cp.stdout, {'np': np})
    in_process = iteration_runtime(request_)
    assert in_process == from_stub, (in_process, from_stub)
    assert type(in_process['runtime']) is type(from_stub['runtime']) or float(in_process['runtime']) == float(from_stub['runtime'])
