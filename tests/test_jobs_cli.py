"""Operator-facing job commands use local durable state, even without config."""

import fcntl
import os
import socket
import subprocess
from contextlib import ExitStack
from pathlib import Path

import pytest

from nightshift import cli, config, daemon, specloop_job as jobs


def forbidden(*args, **kwargs):
    pytest.fail("local jobs command attempted configuration, external work, or signalling")


@pytest.fixture
def local_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(daemon, "STATE_DIR", state)
    monkeypatch.setattr(jobs, "_children", {})
    monkeypatch.setattr(config, "load", forbidden)
    monkeypatch.setattr(config, "load_env_file", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(jobs.specloop_client, "call", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)
    return state


def attempt(root, stage="proposals", number=7, digit="a", command=None):
    directory = root / stage / "owner__sample" / f"{number}.jobs" / (digit * 32)
    directory.mkdir(parents=True)
    (directory / "lock").touch()
    (directory / "log").write_text("preserved evidence\n")
    jobs.save(directory / "request.json", {"args": command or ["propose", "--issue", str(number)]})
    return directory


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None
            for p in root.rglob("*")}


@pytest.mark.scenario("background-job-control/local-job-inventory/list-jobs-after-daemon-restart")
@pytest.mark.scenario("specloop-documentation/current-limitations/fixed-limitations-leave-the-open-list")
def test_jobs_lists_persisted_fields_history_and_numeric_order(local_state, capsys):
    directories = []
    with ExitStack() as locks:
        for stage, command, digit, number in [
            ("revisions", "revise", "c", 5), ("proposals", "propose", "b", 10),
            ("admissions", "admit", "a", 9), ("proposals", "propose", "d", 2),
        ]:
            directory = attempt(local_state, stage, number, digit, command=[command, "a change"])
            jobs.save(directory / "process.json", {
                "version": 1, "job_id": directory.name, "pid": 1234 + number,
                "pgid": 1234 + number, "start_identity": "persisted identity",
                "started_at": "2026-10-21T12:00:00+00:00",
            })
            lock = locks.enter_context((directory / "lock").open())
            fcntl.flock(lock, fcntl.LOCK_EX)
            directories.append((directory, stage, command, number))
        historical = attempt(local_state, number=10, digit="0")
        jobs.save(historical / "result.json", {"result": {"outcome": "drafted"}})
        jobs.save(historical.parent.parent / "10.json", {"job": "b" * 32})
        before = snapshot(local_state)
        assert not jobs._children
        assert cli.main(["jobs"]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        for directory, stage, command, number in directories:
            assert f"{directory.name}  {stage}  owner/sample#{number}  running" in output.out
            assert f"command: {command} 'a change'" in output.out
            assert f"PID={1234 + number} PGID={1234 + number}" in output.out
            assert f"log: {directory / 'log'}" in output.out
        assert f"{historical.name}  proposals  owner/sample#10  finished" in output.out
        assert "execution complete; delivery may be pending" in output.out
        rows = [line.split()[0] for line in output.out.splitlines() if not line.startswith(" ")]
        assert rows == [digit * 32 for digit in ("a", "d", "0", "b", "c")]
        assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/local-job-inventory/empty-inventory")
@pytest.mark.parametrize("exists", [False, True])
def test_jobs_empty_does_not_create_state(local_state, capsys, exists):
    if exists:
        local_state.mkdir()
    assert cli.main(["jobs"]) == 0
    output = capsys.readouterr()
    assert output.out == "No background jobs.\n" and output.err == ""
    assert local_state.exists() == exists
    assert snapshot(local_state) == {}


@pytest.mark.scenario("background-job-control/local-job-inventory/legacy-and-unreadable-records")
@pytest.mark.parametrize("broken", ["malformed", "unreadable"])
def test_jobs_partial_inventory_reports_errors_and_keeps_legacy(local_state, monkeypatch, capsys, broken):
    legacy = attempt(local_state)
    jobs.save(legacy / "result.json", {"result": {"outcome": "drafted"}})
    valid = attempt(local_state, digit="b")
    jobs.save(valid / "stop.json", {"state": "stopped"})
    bad = attempt(local_state, digit="c")
    if broken == "malformed":
        (bad / "request.json").write_text("{broken")
    before = snapshot(local_state)
    read_text = Path.read_text
    with monkeypatch.context() as patch:
        if broken == "unreadable":
            def read(path, *args, **kwargs):
                if path == bad / "request.json":
                    raise PermissionError("permission denied reading request.json")
                return read_text(path, *args, **kwargs)
            patch.setattr(Path, "read_text", read)
        assert cli.main(["jobs"]) == 1
    output = capsys.readouterr()
    assert f"{legacy.name}  proposals  owner/sample#7  finished" in output.out
    assert "PID=? PGID=? (process identity unavailable)" in output.out
    assert f"{valid.name}  proposals  owner/sample#7  stopped" in output.out
    assert f"{bad.name}  proposals  owner/sample#7  unknown" in output.out
    assert "command: unavailable" in output.out
    assert "unreadable job:" in output.err and str(bad) in output.err
    assert "request.json" in output.err
    assert snapshot(local_state) == before


def test_inventory_errors_without_rows_are_not_reported_as_empty(local_state, capsys):
    attempt(local_state, digit="z")
    assert cli.main(["jobs"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "unreadable job:" in output.err


@pytest.mark.parametrize("broken_config", [False, True])
@pytest.mark.parametrize("stop", [False, True])
def test_jobs_work_with_missing_or_broken_config_offline(local_state, tmp_path, capsys, broken_config, stop):
    path = tmp_path / "config.toml"
    if broken_config:
        path.write_text("[broken TOML")
    directory = attempt(local_state)
    jobs.save(directory / "result.json", {"result": {}})
    args = ["--config", str(path), "jobs"]
    if stop:
        args += ["stop", directory.name]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert directory.name in output.out and output.err == ""
    assert not (directory / "stop.json").exists()


@pytest.mark.scenario("background-job-control/job-command-syntax/reject-malformed-command")
@pytest.mark.parametrize("args", [
    ["jobs", "unknown"], ["jobs", "stop"], ["jobs", "list"],
    ["jobs", "stop", "a" * 32, "extra"], ["jobs", "unknown", "a" * 32],
    ["jobs", "--unknown"], ["status", "stop", "a" * 32], ["pause", "extra"],
    pytest.param(["jobs", "retry", "a" * 32], marks=pytest.mark.scenario(
        "specloop-documentation/current-limitations/remaining-ledger-gaps-are-explicit")),
])
def test_malformed_jobs_syntax_has_no_side_effects(local_state, monkeypatch, capsys, args):
    attempt(local_state)
    before = snapshot(local_state)
    monkeypatch.setattr(jobs, "list_jobs", forbidden)
    monkeypatch.setattr(jobs, "stop_job", forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "usage: nightshift" in output.err and "error:" in output.err
    assert output.out == ""
    assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/job-command-syntax/reject-path-identifier")
@pytest.mark.parametrize("identifier", ["../outside", "/tmp/outside", "./" + "a" * 32,
                                         "a/../" + "a" * 32, "..", "a" * 8])
def test_path_ids_are_rejected_before_reading_state(local_state, monkeypatch, capsys, identifier):
    attempt(local_state)
    before = snapshot(local_state)
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "list_jobs", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        assert cli.main(["jobs", "stop", identifier]) == 1
    output = capsys.readouterr()
    assert "exact full job ID" in output.err and output.out == ""
    assert snapshot(local_state) == before


@pytest.mark.parametrize("kind, expected, diagnostic", [
    ("missing", 1, "unknown job ID"), ("legacy", 1, "process identity unavailable"),
    ("broken", 1, "unreadable job records"), ("finished", 0, "already finished"),
    ("failed", 0, "already finished"), ("stopped", 0, "stopped"),
])
def test_stop_exit_codes_and_terminal_states(local_state, capsys, kind, expected, diagnostic):
    job_id = "a" * 32
    if kind != "missing":
        directory = attempt(local_state)
        if kind in ("finished", "failed"):
            jobs.save(directory / "result.json", {"result": {}} if kind == "finished" else {"error": "failed"})
        elif kind == "stopped":
            jobs.save(directory / "stop.json", {"state": "stopped", "stopped_at": "earlier"})
        elif kind == "broken":
            (directory / "request.json").write_text("{")
    before = snapshot(local_state)
    assert cli.main(["jobs", "stop", job_id]) == expected
    output = capsys.readouterr()
    assert diagnostic in (output.err if expected else output.out)
    assert job_id in (output.err if expected else output.out)
    # Refusal may create the serialization lock; evidence and state stay intact.
    after = snapshot(local_state)
    assert {p: data for p, data in after.items() if p.name != "control.lock"} == before


def test_stop_operational_failure_is_actionable(local_state, monkeypatch, capsys):
    def fail(state, job_id):
        assert state == local_state and job_id == "a" * 32
        raise jobs.StopJobError("permission denied; stop intent retained; see /job/log")
    monkeypatch.setattr(jobs, "stop_job", fail)
    assert cli.main(["jobs", "stop", "a" * 32]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "permission denied; stop intent retained; see /job/log" in output.err


@pytest.mark.parametrize("args, expected, fragment", [
    ([], 0, "status sentinel"), (["status"], 0, "status sentinel"),
    (["pause"], 0, "paused"), (["resume"], 0, "resumed"),
    (["run"], 0, "shipped=0"), (["run", "--once"], 0, "shipped=0"),
    (["reconcile"], 0, "clean"), (["preflight", "--deep"], 0, "ready"),
    (["digest"], 2, "not implemented"),
    (["attention", "--offline", "--json"], 0, '"offline": true'),
])
def test_existing_commands_and_options_remain_compatible(tmp_path, monkeypatch, capsys, args, expected, fragment):
    cfg = config.Config(repos=[])
    loaded = []
    config_path = tmp_path / "chosen.toml"
    monkeypatch.setattr(config, "load_env_file", lambda: loaded.append("env"))
    monkeypatch.setattr(config, "load", lambda path: loaded.append(path) or cfg)
    monkeypatch.setattr(cli, "_liveness", lambda cfg: "status sentinel")
    def loop(actual, repos, *, once):
        assert actual is cfg and repos == {} and once == ("--once" in args)
        return daemon.Tally()
    monkeypatch.setattr(daemon, "loop", loop)
    monkeypatch.setattr(daemon, "startup", lambda cfg, repos: [])
    def preflight(actual, env, *, deep):
        assert actual is cfg and deep
        return []
    monkeypatch.setattr(cli.preflight, "run", preflight)
    monkeypatch.setattr(cli.outcomes, "tasks", lambda: [])
    monkeypatch.setattr(cli.outcomes, "refresh", forbidden)
    def attention(*, offline):
        assert offline
        return {"entries": [], "error": None}
    monkeypatch.setattr(cli.specloop_attention, "snapshot", attention)
    monkeypatch.setattr(jobs, "list_jobs", forbidden)
    monkeypatch.setattr(jobs, "stop_job", forbidden)
    if args == ["resume"]:
        daemon.PAUSE_FILE.touch()
    assert cli.main(["--config", str(config_path), *args]) == expected
    output = capsys.readouterr()
    assert fragment in output.out + output.err
    assert loaded == (["env"] if args in (["pause"], ["resume"]) else ["env", config_path])
    if args in (["pause"], ["resume"]):
        assert daemon.PAUSE_FILE.exists() == (args == ["pause"])
