import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from nightshift import cli
from specloop import cli as build_cli, migrate, serve, state, state_locks
from specloop.state import Pause, Run, TaskState
from test_migration_preflight import held_lock, snapshot
from test_migration_recovery import child
from test_state_roots import tree_snapshot
from test_supervisor_ownership import CHILD, line


def legacy_run(reason="infra"):
    run = Run(state.legacy_root() / "runs" / reason)
    run.state.status = "paused"
    run.state.pause = Pause(reason, "task", "keep this pause", resume_at=1)
    run.state.tasks["task"] = TaskState(auto_resumes=1, infra=1)
    run.save()
    run.request_pause()
    run.event("paused", reason=reason)
    (run.dir / "lock").touch()
    return run


def forbid_scheduler(monkeypatch):
    calls = []
    for owner, name in ((serve, "pass_once"), (serve, "lift_due"),
                        (serve, "beat"), (serve.subprocess, "Popen")):
        spy = Mock(side_effect=AssertionError(f"unexpected {name}"))
        monkeypatch.setattr(owner, name, spy)
        calls.append(spy)
    return calls


@pytest.mark.scenario("state-migration/migration-only-command/due-pauses-remain-paused")
def test_migrate_preserves_due_pauses(monkeypatch):
    runs = [legacy_run(reason) for reason in ("infra", "usage_limit")]
    before = [{p.name: p.read_bytes() for p in run.dir.iterdir()} for run in runs]
    calls = forbid_scheduler(monkeypatch)
    monkeypatch.setattr(build_cli, "load_env", Mock(side_effect=AssertionError("credentials")))
    monkeypatch.setattr(cli.config, "load", Mock(side_effect=AssertionError("configuration")))
    assert cli.main(["migrate"]) == 0
    for run, contents in zip(runs, before):
        current = Run.for_change(run.state.change, require_existing=True)
        assert current.dir == state.runs_dir() / run.state.change
        assert run.dir.is_symlink() and run.dir.readlink() == current.dir
        assert {p.name: p.read_bytes() for p in current.dir.iterdir()} == contents
        assert current.state.status == "paused" and current.state.pause.resume_at == 1
    assert cli.main(["migrate"]) == 0
    for spy in calls:
        spy.assert_not_called()


@pytest.mark.scenario("state-migration/migration-only-command/dedicated-command-refuses-live-serve")
@pytest.mark.parametrize("module", ["nightshift.cli", "specloop.cli"])
def test_migrate_refuses_live_supervisor(module):
    proc = subprocess.Popen([sys.executable, "-u", "-c", CHILD, module, "--once"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0)
    try:
        line(proc, "startup")  # Real supervisor owns the lock before its first heartbeat.
        legacy_run()
        roots = state.legacy_root(), state.state_root()
        before = snapshot(roots)
        marker = (roots[0] / migrate.MARKER).read_bytes()
        result = subprocess.run([sys.executable, "-m", "nightshift.cli", "migrate"],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and "Stop the supervisor" in result.stderr
        assert snapshot(roots) == before
        assert (roots[0] / migrate.MARKER).read_bytes() == marker
        assert not (roots[1] / "runs/infra").exists()
        assert not state_locks.journal_path().exists()
    finally:
        proc.kill()
        proc.communicate(timeout=10)


@pytest.mark.scenario("state-migration/migration-only-command/startup-fallback-precedes-scheduling")
@pytest.mark.parametrize("recover", [False, True])
def test_startup_migrates_before_polling(monkeypatch, recover):
    run = legacy_run()
    before = (run.dir / "state.json").read_bytes()
    if recover:
        result = child("rename:after", "runs/infra")
        assert result.returncode == 73, result.stderr
        assert state_locks.journal_path().exists() and not run.dir.exists()
    calls = []

    def sweep(*args, **kwargs):
        assert json.loads((state.legacy_root() / migrate.MARKER).read_text())["completed"]
        assert not state_locks.journal_path().exists()
        assert run.dir.is_symlink() and run.dir.readlink() == state.runs_dir() / "infra"
        assert (run.dir / "state.json").read_bytes() == before
        with pytest.raises(state_locks.SupervisorBusy):
            with state_locks.serve_lock():
                pytest.fail("startup released supervisor ownership")
        calls.append("poll")

    monkeypatch.setattr(serve, "pass_once", sweep)
    monkeypatch.setattr(build_cli, "load_env", lambda _: {})
    monkeypatch.setattr(build_cli, "notifier", lambda _: lambda message: None)
    assert cli.main(["serve", "--once"]) == 0
    assert calls == ["poll"]


@pytest.mark.scenario("state-migration/migration-only-command/startup-fallback-precedes-scheduling")
@pytest.mark.parametrize("refusal", ["locked", "corrupt-journal"])
def test_startup_refusal_prevents_polling(monkeypatch, capsys, refusal):
    run = legacy_run()
    if refusal == "corrupt-journal":
        state_locks.journal_path().write_text("{")
    with held_lock(run.dir / "lock"):
        before = snapshot((state.legacy_root(), state.state_root()))
        with monkeypatch.context() as patch:
            calls = forbid_scheduler(patch)
            assert serve.serve(1, lambda _: pytest.fail("notification before migration"), once=True) == 1
        for spy in calls:
            spy.assert_not_called()
        assert "Migration refused" in capsys.readouterr().err
        assert snapshot((state.legacy_root(), state.state_root())) == before
        assert not (state.legacy_root() / migrate.MARKER).exists()


@pytest.mark.scenario("state-migration/migration-only-command/startup-fallback-precedes-scheduling")
@pytest.mark.parametrize("argv", [["--help"], ["migrate", "--help"], [], ["status", "--json"],
                                 ["attention", "--offline", "--json"]])
def test_reads_never_migrate(monkeypatch, tmp_path, argv):
    legacy_run()
    selected = tmp_path / "config.toml"
    selected.write_text("")
    # Existing attention reads its own stores; use isolated empty providers.
    monkeypatch.setattr(cli.config, "load_env_file", lambda: None)
    monkeypatch.setattr(cli.outcomes, "tasks", lambda: [])
    monkeypatch.setattr(cli.specloop_attention, "snapshot",
                        lambda **_: {"entries": [], "error": None})
    if argv[0:1] == ["attention"]:
        argv = [*argv, "--config", str(selected)]
    calls = forbid_scheduler(monkeypatch)
    migration = Mock(side_effect=AssertionError("read triggered migration"))
    monkeypatch.setattr(migrate, "migrate", migration)
    before = tree_snapshot(state.legacy_root(), state.state_root())
    if "--help" in argv:
        with pytest.raises(SystemExit) as exited:
            cli.main(argv)
        assert exited.value.code == 0
    else:
        assert cli.main(argv) == 0
    migration.assert_not_called()
    for spy in calls:
        spy.assert_not_called()
    assert tree_snapshot(state.legacy_root(), state.state_root()) == before
