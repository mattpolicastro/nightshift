"""Keep private landing unreachable from scheduling and public commands."""

import ast
from contextlib import nullcontext
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nightshift import cli, config, daemon, specloop_job
from specloop import cli as alias, serve, state
from test_factory_land_status import retained
from test_land import background_references


@pytest.mark.scenario("factory-landing-jobs/operator-execution-isolation/background-paths-cannot-start-landing")
def test_complete_existing_dependency_traversal_without_exclusions():
    root = Path(__file__).resolve().parents[1]
    entries = ["nightshift.daemon", "specloop.serve", "nightshift.cli", "specloop.cli",
               "nightshift.specloop_job"]
    visited, violations = background_references(root, entries)
    assert set(entries) | {"nightshift.specloop_intake"} <= visited
    assert not violations
    assert not {"nightshift.factory_land", "nightshift.land"} & visited
    # Supplement, never modify or exclude modules from the existing checker.
    # Catch shell/module strings as well as the imports it already traverses.
    for module in visited:
        path = root.joinpath(*module.split(".")).with_suffix(".py")
        if not path.exists():
            path = root.joinpath(*module.split("."), "__init__.py")
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "factory_land"
                assert not re.search(r"\bnightshift(?:\.factory_land\b|\s+factory_land\b)", node.value)


@pytest.mark.scenario("factory-landing-jobs/operator-execution-isolation/background-paths-cannot-start-landing")
def test_read_control_daemon_and_supervisor_launch_spies(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    monkeypatch.setenv("NIGHTSHIFT_LEGACY_STATE_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    monkeypatch.setattr(specloop_job, "_children", {})
    launches = []

    def spawn(argv, **kwargs):
        launches.append(argv)
        assert argv == [sys.executable, "-m", "nightshift.cli", "run", "running-change"]
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected external read/control work"))
    retained(tmp_path, 1)  # Landing records never become a background job kind.
    previous_handler = daemon.signal.getsignal(daemon.signal.SIGTERM)
    try:
        daemon.loop(config.Config(repos=[]), {}, once=True)
    finally:
        daemon.signal.signal(daemon.signal.SIGTERM, previous_handler)
    for entry in (cli.main, alias.main):
        assert entry(["status", "--json"]) == 0
        assert entry(["jobs"]) == 0
        assert entry(["jobs", "--all"]) == 0
        assert entry(["jobs", "compact"]) == 0
        for action in ("stop", "release"):
            assert entry(["jobs", action, "missing"]) == 1
        for command in ("land", "factory_land"):
            with pytest.raises(SystemExit) as error:
                entry([command])
            assert error.value.code == 2
        with pytest.raises(SystemExit) as error:
            entry(["jobs", "land"])
        assert error.value.code == 2
        assert entry(["act", "unknown", "land", "--json"]) == 1
    assert launches == []
    assert specloop_job.list_jobs(tmp_path) == ([], [])

    def run(change, status, pause=None):
        return SimpleNamespace(state=SimpleNamespace(change=change, status=status, pause=pause, created=0),
                               lock=lambda: nullcontext(True), reload=lambda: None)

    monkeypatch.setattr(serve, "runs", lambda: [
        run("waiting-change", "paused", SimpleNamespace(reason="human_checks", resume_at=None)),
        run("running-change", "running"), run("done-change", "done")])
    assert serve.pass_once(lambda _: None) == ["running-change"]
    assert launches == [[sys.executable, "-m", "nightshift.cli", "run", "running-change"]]
