"""Offline inventory and additive wrapper metadata, including old attempts."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nightshift import specloop_job as jobs


def attempt(root, stage="proposals", repo="owner__sample", number=7, job_id=None):
    directory = root / stage / repo / f"{number}.jobs" / (job_id or "a" * 32)
    directory.mkdir(parents=True)
    (directory / "lock").touch()
    jobs.save(directory / "request.json", {"args": ["propose", "--issue", str(number)]})
    return directory


def identity(directory):
    return {"version": 1, "job_id": directory.name, "pid": 1234, "pgid": 1234,
            "start_identity": "Wed Oct 21 12:00:00 2026",
            "started_at": "2026-10-21T12:00:00+00:00"}


def eventually(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "wrapper did not reach its checkpoint"
        time.sleep(.01)


@pytest.fixture
def wrappers(tmp_path, monkeypatch):
    executable = tmp_path / "specloop"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, sys, time
from pathlib import Path
checkpoint = Path(sys.argv[2])
checkpoint.touch()
deadline = time.monotonic() + 15
while not (checkpoint.parent / "release").exists():
    if time.monotonic() > deadline:
        sys.exit(1)
    time.sleep(.01)
print(json.dumps({"version": 1, "outcome": "needs_input", "questions": ["Which?"]}))
''')
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", str(Path(jobs.__file__).resolve().parent.parent))
    monkeypatch.setattr(jobs, "_children", {})
    children = []

    def launch(path, receipt, args):
        def launcher(*args, **kwargs):
            child = subprocess.Popen(*args, **kwargs)
            children.append(child)
            return child
        jobs.start(path, receipt, args, launcher=launcher)
        return children[-1]

    yield launch
    (tmp_path / "release").touch()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
    jobs.reap()


@pytest.mark.scenario("background-job-control/local-job-inventory/list-jobs-after-daemon-restart")
def test_restart_inventory_all_stages_history_and_order(tmp_path, monkeypatch, wrappers):
    root = tmp_path / "state"
    live = []
    for stage, command in [("revisions", "revise"), ("proposals", "propose"), ("admissions", "admit")]:
        path = root / stage / "owner__sample" / "10.json"
        receipt = {"head": "unchanged"}
        args = [command, str(tmp_path / stage)]
        child = wrappers(path, receipt, args)
        eventually(lambda: (tmp_path / stage).exists())
        directory = path.parent / "10.jobs" / receipt["job"]
        process = json.loads((directory / "process.json").read_text())
        assert process["version"] == 1 and process["job_id"] == receipt["job"]
        assert process["pid"] == process["pgid"] == child.pid
        try:
            current_identity = jobs.process_start_identity(child.pid)
        except (OSError, subprocess.SubprocessError, ValueError):
            assert process["start_identity"] is None and process["identity_error"]
        else:
            assert process["start_identity"] == current_identity
        assert process["started_at"]
        assert json.loads(path.read_text()) == receipt == {"head": "unchanged", "job": directory.name}
        assert json.loads((directory / "request.json").read_text()) == {"args": args}
        assert jobs.collect(path, receipt) is None
        live.append((directory, path, receipt, args, child))

    # These attempts have no current receipt pointing to them.
    historical = attempt(root, "proposals", number=10, job_id="0" * 32)
    jobs.save(historical / "result.json", {"result": {"outcome": "drafted"}})
    failed = attempt(root, "proposals", number=2)
    jobs.save(failed / "result.json", {"error": "old failure"})
    other = attempt(root, "proposals", repo="aaa__repo", number=20)
    attempt(root, "worker")  # Outside the three inventory stages.
    jobs._children.clear()

    with monkeypatch.context() as offline:
        def forbidden(*args, **kwargs):
            pytest.fail("inventory must only read local job files and locks")
        offline.setattr(subprocess, "run", forbidden)
        offline.setattr(subprocess, "Popen", forbidden)
        offline.setattr(jobs.specloop_client, "call", forbidden)
        offline.setattr(jobs, "reap", forbidden)
        rows, errors = jobs.list_jobs(root)
        assert jobs.list_jobs(root) == (rows, errors)
    assert not errors
    assert [(row.stage, row.repository, row.number, row.id) for row in rows] == sorted(
        (row.stage, row.repository, row.number, row.id) for row in rows)
    assert len(rows) == 6
    by_directory = {row.directory: row for row in rows}
    assert by_directory[historical].state == "finished"
    assert by_directory[failed].state == by_directory[other].state == "failed"
    for directory, _, _, args, child in live:
        row = by_directory[directory]
        assert row.id == directory.name and row.repository == "owner/sample" and row.number == 10
        assert row.command == tuple(args) and row.state == "running"
        assert row.pid == row.pgid == child.pid
        assert row.log_path == directory / "log"

    (tmp_path / "release").touch()
    for directory, path, receipt, _, child in live:
        child.wait(timeout=5)
        result = jobs.collect(path, receipt)
        assert result == {"version": 1, "outcome": "needs_input", "questions": ["Which?"]}
        assert json.loads((directory / "result.json").read_text()) == {"result": result}
        assert json.loads(path.read_text()) == receipt
    rows, errors = jobs.list_jobs(root)
    assert not errors
    assert all(row.state == "finished" for row in rows if row.process)


@pytest.mark.scenario("background-job-control/local-job-inventory/list-jobs-after-daemon-restart")
@pytest.mark.parametrize("stage", ["proposals", "admissions", "revisions"])
def test_inventory_preserves_double_underscores_in_repository_name(tmp_path, stage):
    repositories = ["owner/my__repo", "owner/my_repo", "owner/__my__repo__"]
    expected = {}
    for repository in repositories:
        directory = attempt(tmp_path, stage, repo=repository.replace("/", "__"))
        jobs.save(directory / "result.json", {"result": {"outcome": "drafted"}})
        expected[repository] = directory

    rows, errors = jobs.list_jobs(tmp_path)

    assert not errors
    assert [row.repository for row in rows] == sorted(repositories)
    assert {row.repository: row.directory for row in rows} == expected
    assert all(row.stage == stage and row.number == 7 and row.state == "finished" for row in rows)


@pytest.mark.scenario("background-job-control/local-job-inventory/empty-inventory")
@pytest.mark.parametrize("existing", [False, True])
def test_empty_inventory_does_not_create_state(tmp_path, existing):
    root = tmp_path / "state"
    if existing:
        root.mkdir()
        (root / "config.toml").write_text("deliberately invalid configuration")
    before = sorted(tmp_path.rglob("*"))
    assert jobs.list_jobs(root) == ([], [])
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.scenario("background-job-control/local-job-inventory/legacy-and-unreadable-records")
@pytest.mark.parametrize("record,contents", [
    ("request.json", "{"), ("request.json", "[]"), ("request.json", '{"args": "propose"}'),
    ("request.json", '{"args": [4]}'), ("process.json", "{"),
    ("process.json", "null"), ("process.json", '{"version": 9}'),
    ("result.json", "{"), ("result.json", '{"result": null}'),
    ("result.json", '{"result": {}, "error": "conflicting"}'),
    ("stop.json", '{"state": "invented"}'),
])
def test_legacy_and_malformed_records_preserve_valid_rows(tmp_path, record, contents):
    legacy = attempt(tmp_path, job_id="1" * 32)
    jobs.save(legacy / "result.json", {"result": {"outcome": "drafted"}})
    good = attempt(tmp_path, job_id="2" * 32)
    jobs.save(good / "process.json", identity(good))
    jobs.save(good / "result.json", {"error": "execution failed"})
    bad = attempt(tmp_path, job_id="3" * 32)
    (bad / record).write_text(contents)
    rows, errors = jobs.list_jobs(tmp_path)
    assert [row.id for row in rows] == [legacy.name, good.name, bad.name]
    assert rows[0].state == "finished" and rows[0].process is None
    assert rows[0].pid is None and rows[0].pgid is None and not rows[0].identity_available
    assert rows[1].state == "failed" and rows[1].pid == 1234
    assert rows[2].state == "unknown"
    assert errors and any(str(bad) in error and record in error for error in errors)


@pytest.mark.scenario("background-job-control/local-job-inventory/legacy-and-unreadable-records")
def test_unreadable_record_and_directory_are_reported(tmp_path, monkeypatch):
    good = attempt(tmp_path, job_id="1" * 32)
    bad = attempt(tmp_path, job_id="2" * 32)
    unreadable = tmp_path / "revisions"
    unreadable.mkdir()
    read_text, iterdir = Path.read_text, Path.iterdir

    def read(path, *args, **kwargs):
        if path == bad / "request.json":
            raise PermissionError("test denied read")
        return read_text(path, *args, **kwargs)

    def entries(path):
        if path == unreadable:
            raise PermissionError("test denied listing")
        return iterdir(path)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "iterdir", entries)
    rows, errors = jobs.list_jobs(tmp_path)
    assert [row.id for row in rows] == [good.name, bad.name]
    assert rows[0].state == "failed" and rows[1].state == "unknown"
    assert len(errors) == 2 and all("denied" in error for error in errors)


def test_starting_legacy_live_and_stop_states_are_read_without_writes(tmp_path):
    directory = attempt(tmp_path)
    before = {p: p.read_bytes() for p in directory.iterdir()}
    with (directory / "lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows, errors = jobs.list_jobs(tmp_path)
        assert not errors and rows[0].state == "starting" and rows[0].process is None
        assert {p: p.read_bytes() for p in directory.iterdir()} == before
        for state in ("stopping", "stopped"):
            jobs.save(directory / "stop.json", {"state": state})
            jobs.save(directory / "result.json", {"result": {"outcome": "drafted"}})
            rows, errors = jobs.list_jobs(tmp_path)
            assert not errors and rows[0].state == state


def test_wrapper_atomically_records_identity_before_cli(tmp_path, monkeypatch):
    directory = attempt(tmp_path)
    replace = Path.replace
    replacements = []

    def observe_replace(path, target):
        if target.name == "process.json":
            assert not target.exists()
            assert json.loads(path.read_text())["start_identity"] == "stable identity"
            replacements.append(target)
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", observe_replace)
    monkeypatch.setattr(jobs, "process_start_identity", lambda pid: "stable identity")
    fd = os.open(directory / "lock", os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(directory), str(fd)])

    def call(args, *, runner):
        assert replacements == [directory / "process.json"]
        assert runner.keywords == {"pass_fds": (fd,)}
        assert json.loads((directory / "process.json").read_text())["pid"] == os.getpid()
        assert jobs.list_jobs(tmp_path)[0][0].state == "running"
        return {"version": 1, "outcome": "drafted"}

    monkeypatch.setattr(jobs.specloop_client, "call", call)
    try:
        jobs.main()
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    assert not (directory / "process.tmp").exists()
    assert jobs.list_jobs(tmp_path)[0][0].state == "finished"


def test_process_start_identity_uses_bounded_local_ps(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, " Wed Oct 21 12:00:00 2026\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert jobs.process_start_identity(123) == "Wed Oct 21 12:00:00 2026"
    args, kwargs = calls[0]
    assert args == ["/bin/ps", "-p", "123", "-o", "lstart="]
    assert kwargs["timeout"] == 5 and kwargs["check"]
    assert kwargs["env"]["LC_ALL"] == "C"


def test_process_lookup_failure_is_explicit_without_preventing_execution(tmp_path, monkeypatch):
    directory = attempt(tmp_path)

    def denied(pid):
        raise PermissionError("process inspection denied")

    monkeypatch.setattr(jobs, "process_start_identity", denied)
    called = []
    monkeypatch.setattr(jobs.specloop_client, "call", lambda *a, **kw: called.append(a) or {"version": 1})
    fd = os.open(directory / "lock", os.O_RDONLY)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(directory), str(fd)])
    jobs.main()
    rows, errors = jobs.list_jobs(tmp_path)
    assert called and not errors and rows[0].state == "finished"
    assert rows[0].pid == os.getpid() and not rows[0].identity_available
    assert rows[0].process["identity_error"] == "process inspection denied"
