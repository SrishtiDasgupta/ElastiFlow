"""
Every simulated policy of every campaign runs once and its output parses.

Slower than the regression tests (about seven minutes) and marked `smoke`, so it
is excluded from a plain `pytest` run; invoke with `pytest -m smoke`. It checks
that a policy still runs, not that its numbers are unchanged; the regression
tests do that for the tagged cells.
"""
import glob
import sys

import pytest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'regression'))
from conftest import REPO, la_env, load_module, run  # noqa: E402

sweep = load_module(REPO / 'plain_results' / 'sweep_PLAIN.py')
parse_la_run = load_module(REPO / 'license_results' / 'parse_la_run.py')

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize('variant', sorted(sweep.VARIANT_ARGS))
def test_seissol_policy_runs(variant, tmp_path):
    out = tmp_path / variant
    out.mkdir()
    run([sys.executable, 'simulate_sweep.py', *sweep.VARIANT_ARGS[variant], out,
         '--seed', '7', '--N', '100'], cwd=REPO / 'elastiflow')
    outs = sorted(out.glob('*.out'))
    assert len(outs) == 1
    rec = sweep.parse_out(outs[0].read_text())
    assert not rec.get('_parse_failed') and rec.get('total_cost_eur') is not None


@pytest.mark.parametrize('policy', ['FCFS-ST-LA', 'EDF-ST-LA', 'LAMF', 'EDF-LAMF', 'EDF-HSM'])
def test_licence_policy_runs(policy, tmp_path):
    out = tmp_path / policy
    out.mkdir()
    cp = run([sys.executable, 'simulate_main_LA.py', '--scheduler', policy,
              '--N', '150', '--seed', '7', '--output-dir', out],
             cwd=REPO / 'elastiflow', env=la_env(policy))
    rec = parse_la_run.parse(cp.stdout)
    assert rec.get('avg_cost_eur') is not None
    assert glob.glob(str(out / '*_results.csv')) and glob.glob(str(out / '*_license_usage.csv'))


def test_ch7_validation_runner_runs(tmp_path):
    """simulate_main.py is the entry the Chapter 7 scaling sweep drives; it writes
    its CSVs into the working directory, so it runs from a temporary one."""
    cp = run([sys.executable, str(REPO / 'elastiflow' / 'simulate_main.py')], cwd=tmp_path)
    assert 'Overall miss rate' in cp.stdout
