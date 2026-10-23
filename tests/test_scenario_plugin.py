import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "examples" / "specloop"

SUITE = '''
import pytest

@pytest.mark.scenario("cap/req/first")
def test_first():
    pass

@pytest.mark.scenario("cap/req/second")
@pytest.mark.parametrize("n", [1, 2, 3])
def test_second(n):
    pass

@pytest.mark.scenario("cap/req/failing")
def test_failing():
    assert False
'''


def scenarios(tmp_path, *workers):
    (tmp_path / "test_suite.py").write_text(SUITE)
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "scenario_plugin", *workers,
         str(tmp_path / "test_suite.py")],
        capture_output=True, text=True, cwd=tmp_path, env={"PYTHONPATH": str(PLUGIN_DIR), "PATH": "/usr/bin:/bin"},
    ).stdout
    return sorted(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("SCENARIO "))


@pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["serial", "xdist"])
@pytest.mark.scenario("specloop-documentation/operations-guidance/parallel-tests-retain-scenario-evidence")
def test_scenario_plugin_reports_passed_scenarios_serially_and_under_xdist(tmp_path, workers):
    # Before 2026-10-21 the plugin collected IDs inside xdist workers and printed
    # from the controller, so parallel runs reported no scenarios at all.
    assert scenarios(tmp_path, *workers) == ["cap/req/first", "cap/req/second"]
