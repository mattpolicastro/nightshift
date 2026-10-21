"""Offline inventory and additive wrapper metadata, including old attempts."""

import fcntl
import json
import os
import select
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
@pytest.mark.parametrize("invalid_fields", [
    pytest.param({"pid": 0}, id="zero-pid"),
    pytest.param({"pgid": 0}, id="zero-pgid"),
    pytest.param({"start_identity": "", "identity_error": "lookup denied"}, id="empty-identity"),
    pytest.param({"start_identity": " ", "identity_error": "lookup denied"}, id="blank-identity"),
    pytest.param({"start_identity": 123, "identity_error": "lookup denied"}, id="non-string-identity"),
    pytest.param({"start_identity": None, "identity_error": ""}, id="empty-error"),
    pytest.param({"start_identity": None, "identity_error": " "}, id="blank-error"),
])
def test_inventory_rejects_invalid_process_identity(tmp_path, invalid_fields):
    directory = attempt(tmp_path)
    process = identity(directory)
    process.update(invalid_fields)
    jobs.save(directory / "process.json", process)
    jobs.save(directory / "result.json", {"result": {"outcome": "drafted"}})

    rows, errors = jobs.list_jobs(tmp_path)

    assert len(rows) == 1 and rows[0].directory == directory
    assert rows[0].state == "unknown"
    assert rows[0].process is None and not rows[0].identity_available
    assert rows[0].pid is None and rows[0].pgid is None
    assert len(errors) == 1
    assert str(directory / "process.json") in errors[0]
    assert "invalid process identity" in errors[0]


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
        elif target.name == "result.json":
            # The production wrapper must use the control protocol, too.
            with (directory / "control.lock").open("r") as probe:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
    assert kwargs["capture_output"] is True and kwargs["text"] is True
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


@pytest.fixture
def control_race(monkeypatch):
    """Hold one operation at its commit and observe the other's lock attempt."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, current_thread

    @contextmanager
    def race(directory, record, first, second):
        at_commit, release, contending = Event(), Event(), Event()
        save, lock = jobs.save, jobs.control_lock
        owner = []

        def gated_save(path, data):
            if path == directory / record and current_thread() == owner[0]:
                at_commit.set()
                assert release.wait(5), "commit barrier was not released"
            save(path, data)

        @contextmanager
        def observed_lock(path):
            if owner and current_thread() != owner[0]:
                contending.set()
            with lock(path):
                yield

        def run_first():
            owner.append(current_thread())
            return first()

        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
            patch.setattr(jobs, "save", gated_save)
            patch.setattr(jobs, "control_lock", observed_lock)
            winner = pool.submit(run_first)
            try:
                assert at_commit.wait(5), "first operation did not reach commit"
                with (directory / "control.lock").open("r") as probe:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                loser = pool.submit(second)
                assert contending.wait(5), "second operation did not contend"
                assert not loser.done()
            finally:
                release.set()
            yield winner.result(timeout=5), loser.result(timeout=5)

    return race


@pytest.mark.scenario("background-job-control/durable-cancellation/stop-wins-completion-race")
@pytest.mark.parametrize("competitor", ["finalize", "collect"])
def test_stop_commit_precedes_concurrent_result(tmp_path, control_race, competitor):
    directory = attempt(tmp_path)
    path = directory.parent.parent / "7.json"
    receipt = {"job": directory.name}
    jobs.save(path, receipt)
    outcome = {"result": {"version": 1, "outcome": "drafted"}}

    def late_result():
        if competitor == "finalize":
            jobs.finalize(directory, outcome)
        else:
            # An older wrapper may write without the control protocol. Even
            # this late result must not beat accepted stop intent.
            jobs.save(directory / "result.json", outcome)
            return jobs.collect(path, receipt)

    with control_race(directory, "stop.json",
                      lambda: jobs.request_stop(directory, verify_identity=lambda: identity(directory)),
                      late_result) as (stopped, result):
        assert stopped == "stopping" and result is None
    stop = json.loads((directory / "stop.json").read_text())
    assert stop["requested_at"] and stop["process"] == identity(directory)
    assert json.loads((directory / "result.json").read_text()) == outcome
    assert jobs.collect(path, receipt) is None
    assert json.loads(path.read_text()) == receipt == {"job": directory.name}
    assert not (directory / "collected.json").exists()
    assert jobs.list_jobs(tmp_path)[0][0].state == "stopping"
    jobs.finish_stop(directory)
    assert jobs.collect(path, receipt) is None
    assert jobs.list_jobs(tmp_path)[0][0].state == "stopped"


@pytest.mark.scenario("background-job-control/durable-cancellation/completion-wins-stop-race")
@pytest.mark.parametrize("completion", ["finalize", "collect", "error"])
def test_completion_commit_precedes_stop(tmp_path, control_race, completion, monkeypatch):
    directory = attempt(tmp_path)
    path = directory.parent.parent / "7.json"
    receipt = {"job": directory.name}
    jobs.save(path, receipt)
    outcome = {"error": "CLI failed"} if completion == "error" else {"result": {"outcome": "drafted"}}

    def forbidden(*args):
        pytest.fail("completed jobs must not verify or signal a process")

    monkeypatch.setattr(os, "killpg", forbidden)
    monkeypatch.setattr(jobs, "_verify_process", forbidden)
    if completion == "collect":
        jobs.finalize(directory, outcome)
        record = "collected.json"
        first = lambda: jobs.collect(path, receipt)
    else:
        record = "result.json"
        first = lambda: jobs.finalize(directory, outcome)
    with control_race(directory, record, first,
                      lambda: jobs.stop_job(tmp_path, directory.name)) as (_, stopped):
        assert stopped == "finished"
    assert not (directory / "stop.json").exists()
    assert json.loads((directory / "result.json").read_text()) == outcome
    assert jobs.list_jobs(tmp_path)[0][0].state == ("failed" if completion == "error" else "finished")
    if "result" in outcome:
        assert jobs.collect(path, receipt) == outcome["result"]
        assert json.loads(path.read_text()) == {"job": directory.name}
        marker = (directory / "collected.json").read_text()
        # Acceptance remains durable independently of the original result
        # and before intake has cached it in the parent receipt.
        (directory / "result.json").unlink()
        jobs._children.clear()
        assert jobs.stop_job(tmp_path, directory.name) == "finished"
        assert (directory / "collected.json").read_text() == marker


def test_refused_stop_does_not_accept_intent_and_retry_preserves_it(tmp_path):
    directory = attempt(tmp_path)

    def refuse():
        raise ValueError("ownership unavailable")

    with pytest.raises(ValueError, match="ownership unavailable"):
        jobs.request_stop(directory, verify_identity=refuse)
    assert not (directory / "stop.json").exists()
    assert jobs.request_stop(directory, verify_identity=lambda: identity(directory)) == "stopping"
    intent = (directory / "stop.json").read_bytes()
    with pytest.raises(ValueError, match="ownership unavailable"):
        jobs.request_stop(directory, verify_identity=refuse)
    assert (directory / "stop.json").read_bytes() == intent
    assert jobs.request_stop(directory, verify_identity=lambda: identity(directory)) == "stopping"
    assert (directory / "stop.json").read_bytes() == intent


@pytest.mark.scenario("background-job-control/owned-process-group-termination/stop-wrapper-and-descendants")
@pytest.mark.scenario("background-job-control/durable-cancellation/repeat-stop-safely")
@pytest.mark.parametrize("ignoring_descendant, fresh_cli", [
    (False, False), pytest.param(True, False, marks=pytest.mark.scenario(
        "background-job-control/owned-process-group-termination/escalate-unresponsive-group")),
    (False, True),
])
def test_jobs_stop_terminates_process_group(tmp_path, monkeypatch, ignoring_descendant, fresh_cli):
    """Real groups and pipe EOF prove termination, even where ps is denied.

    When ps is forbidden, only process inspection is injected. Each group
    inherits a pipe writer; EOF cannot occur until the wrapper, child AND
    grandchild have exited.
    The inspection adapter reports the actual launch argv and a lifetime
    token persisted by the production wrapper. Separate tests cover the ps
    contract and fail-closed inspection errors without signalling anything.
    """
    # The managed macOS test sandbox disallows executing /bin/ps. Give only
    # this test's wrappers an injectable start identity, without changing
    # production's refusal when real inspection is unavailable.
    try:
        jobs.process_start_identity(os.getpid())
    except PermissionError:
        inject_inspection = True
    else:
        inject_inspection = False
    (tmp_path / "sitecustomize.py").write_text('''
import os, subprocess
real_run = subprocess.run
def inspect_self(args, **kwargs):
    if "TEST_JOB_LIFELINE" not in os.environ:
        return real_run(args, **kwargs)
    if os.environ["TEST_INJECT_INSPECTION"] == "1" and args == ["/bin/ps", "-p", str(os.getpid()), "-o", "lstart="]:
        return subprocess.CompletedProcess(args, 0, os.environ["TEST_JOB_IDENTITY"], "")
    kwargs["pass_fds"] = (*kwargs.get("pass_fds", ()), int(os.environ["TEST_JOB_LIFELINE"]))
    return real_run(args, **kwargs)
subprocess.run = inspect_self
''')
    executable = tmp_path / "specloop"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, signal, sys, time
from pathlib import Path
checkpoint = Path(sys.argv[2])
os.fstat(int(os.environ["TEST_JOB_LIFELINE"]))
grandchild = os.fork()
if grandchild == 0:
    if os.environ["TEST_IGNORE_TERM"] == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    checkpoint.with_suffix(".grandchild").touch()
    time.sleep(20)
    os._exit(0)
while not checkpoint.with_suffix(".grandchild").exists():
    time.sleep(.01)
checkpoint.with_suffix(".tmp").write_text(json.dumps({
    "child": os.getpid(), "grandchild": grandchild, "pgid": os.getpgrp()}))
checkpoint.with_suffix(".tmp").replace(checkpoint)
time.sleep(20)
''')
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        (str(tmp_path), str(Path(jobs.__file__).resolve().parent.parent))))
    monkeypatch.setattr(jobs, "_children", {})
    root = tmp_path / "state with spaces"
    root.mkdir()
    (root / "pause").write_text("hold\n")
    children, attempts, inspections = [], [], {}

    def executing(pgid):
        # Every descendant inherits this descriptor, including through exec.
        # No producer writes data, so readability means all writers closed.
        return not select.select([inspections[pgid]["reader"]], [], [], 0)[0]

    def launch(*args, **kwargs):
        reader, writer = os.pipe()
        token = f"test-lifetime-{len(children)}"
        try:
            child = subprocess.Popen(*args, **{
                **kwargs, "pass_fds": (*kwargs["pass_fds"], writer),
                "env": {**os.environ, "TEST_JOB_IDENTITY": token,
                        "TEST_IGNORE_TERM": "1" if ignoring_descendant else "0",
                        "TEST_INJECT_INSPECTION": "1" if inject_inspection else "0",
                        "TEST_JOB_LIFELINE": str(writer)},
            })
        except BaseException:
            os.close(reader)
            raise
        finally:
            os.close(writer)
        children.append(child)
        inspections[child.pid] = {"reader": reader, "identity": token,
                                  "command": " ".join(args[0]), "wrapper": child}
        return child

    real_run = subprocess.run

    def inspect(args, **kwargs):
        if args[0] != "/bin/ps":
            return real_run(args, **kwargs)
        if args == ["/bin/ps", "-axo", "pid=,pgid=,stat="]:
            # Include the caller, so an empty selected group is still a valid
            # process table. Actual group membership was checked at barriers.
            rows = [f"{os.getpid()} {os.getpgrp()} S"]
            for pgid, record in inspections.items():
                if executing(pgid):
                    for pid in record["members"]:
                        if pid == pgid and record["wrapper"].poll() is not None:
                            continue
                        # Conservatively retain descendants until pipe EOF;
                        # getpgid may fail before their descriptors close.
                        rows.append(f"{pid} {pgid} S")
            output = "\n".join(rows)
        else:
            pid = int(args[args.index("-p") + 1])
            pgid, record = next((pgid, record) for pgid, record in inspections.items()
                                if pid in record["members"])
            assert executing(pgid), "inspection requested after group termination"
            if args == ["/bin/ps", "-p", str(pid), "-o", "lstart="]:
                output = record["identity"] + ("" if pid == pgid else f"-{pid}")
            else:
                assert args == ["/bin/ps", "-ww", "-p", str(pid), "-o", "args="]
                output = inspections[pid]["command"]
        return subprocess.CompletedProcess(args, 0, output, "")

    if inject_inspection:
        monkeypatch.setattr(subprocess, "run", inspect)

    try:
        for number in (1, 2):
            checkpoint = tmp_path / f"ready-{number}.json"
            path = root / "proposals" / "owner__repo" / f"{number}.json"
            receipt = {"head": "original"}
            jobs.start(path, receipt, ["propose", str(checkpoint)], launcher=launch)
            eventually(checkpoint.exists)
            directory = path.parent / f"{number}.jobs" / receipt["job"]
            (directory / "log").write_text("job evidence\n")
            family = json.loads(checkpoint.read_text())
            assert family["pgid"] == children[-1].pid
            for pid in (family["child"], family["grandchild"]):
                assert os.getpgid(pid) == children[-1].pid
            inspections[children[-1].pid]["members"] = {
                children[-1].pid, family["child"], family["grandchild"]}
            attempts.append((directory, path, receipt, family))
        selected, unrelated = attempts
        directory, path, receipt, family = selected
        unrelated_evidence = {p: p.read_bytes() for p in
                              [unrelated[1], *unrelated[0].iterdir()] if p.is_file()}
        preserved = {p: p.read_bytes() for p in (directory / "process.json",
                     directory / "request.json", directory / "log", path, root / "pause")}
        caller_group = os.getpgrp()
        assert caller_group not in {child.pid for child in children}
        jobs._children.clear()
        signals = []
        killpg, finish_stop = os.killpg, jobs.finish_stop

        def signal_group(pgid, sig):
            assert pgid == children[0].pid
            assert json.loads((directory / "stop.json").read_text())["state"] == "stopping"
            if sig == signal.SIGKILL:
                assert executing(pgid)
                assert children[0].poll() is not None
                assert os.getpgid(family["grandchild"]) == pgid
            signals.append((sig, time.monotonic()))
            killpg(pgid, sig)

        def confirmed_stop(path):
            assert not executing(children[0].pid), "cannot mark stopped with live descendants"
            finish_stop(path)

        started = time.monotonic()
        if fresh_cli:
            # Only test process inspection crosses the boundary. The CLI
            # resolves its target and ownership from the wrapper's durable
            # records, with no daemon handles or in-memory Job objects.
            inspection_file = tmp_path / "inspection.json"
            inspection_file.write_text(json.dumps({str(pgid): {
                key: list(value) if isinstance(value, set) else value
                for key, value in record.items() if key != "wrapper"
            } for pgid, record in inspections.items()}))
            signal_file = tmp_path / "signals.json"
            stopped = real_run([sys.executable, "-c", r'''
import json, os, select, signal, subprocess, sys, time
from pathlib import Path
from nightshift import cli, config, daemon, specloop_job as jobs
root, job_id, inspection_path, signal_path, inject = sys.argv[1:]
daemon.STATE_DIR = Path(root)
assert not jobs._children
def forbidden(*args, **kwargs):
    raise AssertionError("jobs must not load configuration or invoke services")
config.load = config.load_env_file = jobs.specloop_client.call = forbidden
records = {int(pgid): record for pgid, record in json.loads(Path(inspection_path).read_text()).items()}
def executing(record):
    return not select.select([record["reader"]], [], [], 0)[0]
def inspect(args, **kwargs):
    if args == ["/bin/ps", "-axo", "pid=,pgid=,stat="]:
        rows = [f"{os.getpid()} {os.getpgrp()} S"]
        for pgid, record in records.items():
            if executing(record):
                rows.extend(f"{pid} {pgid} S" for pid in record["members"])
        output = "\n".join(rows)
    else:
        pid = int(args[args.index("-p") + 1])
        pgid, record = next((pgid, record) for pgid, record in records.items() if pid in record["members"])
        assert executing(record)
        if args == ["/bin/ps", "-p", str(pid), "-o", "lstart="]:
            output = record["identity"] + ("" if pid == pgid else f"-{pid}")
        else:
            assert args == ["/bin/ps", "-ww", "-p", str(pid), "-o", "args="]
            output = record["command"]
    return subprocess.CompletedProcess(args, 0, output, "")
if inject == "1":
    subprocess.run = inspect
directory = next(job.directory for job in jobs.list_jobs(daemon.STATE_DIR)[0] if job.id == job_id)
process = json.loads((directory / "process.json").read_text())
killpg, finish = os.killpg, jobs.finish_stop
signals = []
def signal_group(pgid, sig):
    assert pgid == process["pgid"] and pgid != os.getpgrp()
    assert json.loads((directory / "stop.json").read_text())["state"] == "stopping"
    signals.append((sig, time.monotonic()))
    Path(signal_path).write_text(json.dumps(signals))
    killpg(pgid, sig)
def confirmed_stop(path):
    assert not executing(records[process["pgid"]]), "descendants must exit before stopped"
    finish(path)
os.killpg, jobs.finish_stop = signal_group, confirmed_stop
raise SystemExit(cli.main(["jobs", "stop", job_id, "--config", str(Path(root) / "missing.toml")]))
''', str(root), directory.name, str(inspection_file), str(signal_file),
                "1" if inject_inspection else "0"],
                pass_fds=tuple(record["reader"] for record in inspections.values()),
                capture_output=True, text=True, timeout=10)
            assert stopped.returncode == 0, stopped.stderr
            assert stopped.stdout.strip() == f"{directory.name}: stopped"
            signals = json.loads(signal_file.read_text())
        else:
            with monkeypatch.context() as patch:
                patch.setattr(os, "killpg", signal_group)
                patch.setattr(jobs, "finish_stop", confirmed_stop)
                assert jobs.stop_job(root, directory.name) == "stopped"
        assert time.monotonic() - started < 10
        if ignoring_descendant:
            assert [sig for sig, _ in signals] == [signal.SIGTERM, signal.SIGKILL]
            assert signals[1][1] - signals[0][1] >= 5
            assert time.monotonic() - signals[1][1] <= 1
        else:
            assert [sig for sig, _ in signals] == [signal.SIGTERM]
        assert not executing(children[0].pid)
        assert not jobs._executing_group(children[0].pid)
        children[0].wait(timeout=2)
        assert children[1].poll() is None and os.getpgrp() == caller_group
        assert executing(children[1].pid)
        for pid in inspections[children[1].pid]["members"]:
            assert os.getpgid(pid) == children[1].pid
        assert jobs._executing_group(children[1].pid) >= {
            children[1].pid, unrelated[3]["child"], unrelated[3]["grandchild"]}
        assert all(p.read_bytes() == data for p, data in preserved.items())
        assert b"job evidence" in (directory / "log").read_bytes()
        assert not (unrelated[0] / "stop.json").exists()
        assert all(p.read_bytes() == data for p, data in unrelated_evidence.items())
        stop = json.loads((directory / "stop.json").read_text())
        assert stop["state"] == "stopped" and stop["requested_at"] and stop["stopped_at"]
        assert stop["process"] == json.loads((directory / "process.json").read_text())
        assert jobs.collect(path, receipt) is None
        assert {row.id: row.state for row in jobs.list_jobs(root)[0]}[directory.name] == "stopped"
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", lambda *a: pytest.fail("repeat stop must not signal"))
            assert jobs.stop_job(root, directory.name) == "stopped"
        terminal = (directory / "stop.json").read_bytes()
        # Repeat through the CLI in another process, with signalling and
        # identity inspection forbidden even if the old PID has been reused.
        repeated = real_run([sys.executable, "-c", '''
import os, sys
from pathlib import Path
from nightshift import cli, config, daemon
def forbidden(*args, **kwargs):
    raise AssertionError("repeat stop must not signal or inspect a reused PID")
os.killpg = os.getpgid = forbidden
config.load = config.load_env_file = forbidden
daemon.STATE_DIR = Path(sys.argv[1])
raise SystemExit(cli.main(["jobs", "stop", sys.argv[2]]))
''', str(root), directory.name], capture_output=True, text=True, timeout=5)
        assert repeated.returncode == 0, repeated.stderr
        assert repeated.stdout.strip() == f"{directory.name}: stopped"
        assert (directory / "stop.json").read_bytes() == terminal
    finally:
        try:
            # Kill the whole group even if its wrapper already exited.
            for child in children:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # macOS can deny a signal to an already vanished group.
                    # Pipe EOF must independently prove there is no work left.
                    eventually(lambda: not executing(child.pid))
            for child in children:
                child.wait(timeout=3)
            eventually(lambda: all(not executing(pgid) for pgid in inspections))
        finally:
            for record in inspections.values():
                os.close(record["reader"])


@pytest.fixture
def owned_job(tmp_path, monkeypatch):
    directory = attempt(tmp_path)
    process = identity(directory)
    jobs.save(directory / "process.json", process)
    monkeypatch.setattr(os, "getpgrp", lambda: 9999)
    monkeypatch.setattr(os, "getpgid", lambda pid: process["pgid"])
    monkeypatch.setattr(os, "getsid", lambda pid: process["pgid"])
    monkeypatch.setattr(jobs, "process_start_identity", lambda pid: process["start_identity"])

    def ps(args, **kwargs):
        assert args == ["/bin/ps", "-ww", "-p", str(process["pid"]), "-o", "args="]
        return subprocess.CompletedProcess(args, 0,
            f"{sys.executable} -m nightshift.specloop_job {directory.resolve()} 4\n", "")

    monkeypatch.setattr(subprocess, "run", ps)
    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("unsafe stop sent a signal"))
    return directory, process


def test_wrapper_command_inspection_uses_five_second_timeout(monkeypatch, owned_job):
    directory, process = owned_job
    command_ps = subprocess.run
    calls = []

    def ps(args, **kwargs):
        calls.append(kwargs)
        return command_ps(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", ps)
    assert jobs._verify_process(directory) == process
    assert calls == [{"capture_output": True, "text": True, "timeout": 5, "check": True}]


@pytest.mark.scenario("background-job-control/owned-process-group-termination/refuse-unsafe-identity")
@pytest.mark.parametrize("problem", [
    "unknown", "legacy", "unavailable", "reused", "missing-leader", "wrong-group",
    "wrong-session", "wrong-command", "wrong-path", "path-prefix", "not-leader",
    "group-zero", "group-one", "caller-group", "duplicate", "changed-intent",
])
def test_stop_refuses_unsafe_identity(tmp_path, monkeypatch, owned_job, problem):
    directory, process = owned_job
    job_id = directory.name
    if problem == "unknown":
        job_id = "b" * 32
    elif problem == "legacy":
        (directory / "process.json").unlink()
    elif problem == "unavailable":
        jobs.save(directory / "process.json", {**process, "start_identity": None,
                                              "identity_error": "ps denied"})
    elif problem == "reused":
        monkeypatch.setattr(jobs, "process_start_identity", lambda pid: "another start time")
    elif problem == "missing-leader":
        def gone(pid):
            raise ProcessLookupError("wrapper leader no longer exists")
        monkeypatch.setattr(os, "getpgid", gone)
        monkeypatch.setattr(jobs, "_executing_group", lambda *a, **kw: {4321})
    elif problem in ("wrong-group", "wrong-session"):
        monkeypatch.setattr(os, "getpgid" if problem == "wrong-group" else "getsid", lambda pid: 4567)
    elif problem in ("wrong-command", "wrong-path", "path-prefix"):
        command = {
            "wrong-command": f"python unrelated.py {directory.resolve()} 4",
            "wrong-path": f"python -m nightshift.specloop_job {directory.resolve()}-other 4",
            "path-prefix": f"python -m nightshift.specloop_job {directory.resolve()} 4 extra",
        }[problem]
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
                            subprocess.CompletedProcess(a, 0, command, ""))
    elif problem in ("not-leader", "group-zero", "group-one", "caller-group"):
        pgid = {"not-leader": 2222, "group-zero": 0, "group-one": 1, "caller-group": 9999}[problem]
        jobs.save(directory / "process.json", {**process, "pgid": pgid,
                  "pid": process["pid"] if problem == "not-leader" else pgid})
    elif problem == "duplicate":
        attempt(tmp_path, stage="admissions")
    elif problem == "changed-intent":
        jobs.save(directory / "stop.json", {"state": "stopping", "process": {**process, "pid": 5678}})
    previous = (directory / "stop.json").read_bytes() if (directory / "stop.json").exists() else None
    with pytest.raises(jobs.StopJobError) as error:
        jobs.stop_job(tmp_path, job_id)
    assert str(error.value)
    if previous is None:
        assert not (directory / "stop.json").exists()
    else:
        assert (directory / "stop.json").read_bytes() == previous


@pytest.mark.scenario("background-job-control/job-command-syntax/reject-path-identifier")
@pytest.mark.parametrize("job_id", ["../outside", "a/../b", "/tmp/job", ".", "..",
                                   "a" * 8, "A" * 32, "./" + "a" * 32,
                                   "a" * 32 + "/", "a" * 32 + "\n", "a\\..\\b"])
def test_stop_rejects_path_and_inexact_identifiers_before_io(tmp_path, monkeypatch, job_id):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid IDs must be rejected before reading, writing or signalling")
    monkeypatch.setattr(jobs, "list_jobs", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)
    with pytest.raises(jobs.StopJobError, match="exact full job ID"):
        jobs.stop_job(tmp_path, job_id)


@pytest.mark.scenario("background-job-control/owned-process-group-termination/escalate-unresponsive-group")
def test_stop_escalates_only_verified_group_and_releases_control_lock(tmp_path, monkeypatch, owned_job):
    directory, process = owned_job
    signals, waits = [], []
    monkeypatch.setattr(jobs, "_executing_group", lambda *a, **kw: {1234, 2345})

    def signal_group(pgid, sig):
        with (directory / "control.lock").open("r") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert json.loads((directory / "stop.json").read_text())["state"] == "stopping"
        signals.append((pgid, sig))

    def wait(identity, seconds):
        assert identity == process
        waits.append(seconds)
        return seconds == 1

    monkeypatch.setattr(os, "killpg", signal_group)
    monkeypatch.setattr(jobs, "_wait_group", wait)
    assert jobs.stop_job(tmp_path, directory.name) == "stopped"
    assert signals == [(1234, signal.SIGTERM), (1234, signal.SIGKILL)]
    assert waits == [5, 1]


@pytest.mark.scenario("background-job-control/owned-process-group-termination/report-termination-failure")
@pytest.mark.parametrize("failure", ["permission", "surviving", "reused-after-term", "missing-after-term"])
def test_stop_failure_retains_intent_without_false_success(tmp_path, monkeypatch, owned_job, failure):
    directory, process = owned_job
    signals = []
    monkeypatch.setattr(jobs, "_executing_group", lambda *a, **kw: {1234, 2345})
    monkeypatch.setattr(jobs, "_wait_group", lambda *a: False)

    def signal_group(pgid, sig):
        signals.append(sig)
        if failure == "permission":
            raise PermissionError("signal permission denied")
        if failure == "reused-after-term":
            monkeypatch.setattr(jobs, "process_start_identity", lambda pid: "reused")
        if failure == "missing-after-term":
            def gone(pid):
                raise ProcessLookupError("leader gone, descendants still executing")
            monkeypatch.setattr(os, "getpgid", gone)

    monkeypatch.setattr(os, "killpg", signal_group)
    with pytest.raises(jobs.StopJobError, match="see .*log"):
        jobs.stop_job(tmp_path, directory.name)
    stop = json.loads((directory / "stop.json").read_text())
    assert stop["state"] == "stopping" and "stopped_at" not in stop
    assert stop["process"] == process
    assert signals == ([signal.SIGTERM, signal.SIGKILL] if failure == "surviving" else [signal.SIGTERM])


@pytest.fixture
def stop_clock(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay))
    monkeypatch.setattr(jobs, "reap", lambda: None)
    monkeypatch.setattr(os, "waitpid", lambda *a: (0, 0))
    return elapsed


@pytest.mark.scenario("background-job-control/owned-process-group-termination/report-termination-failure")
@pytest.mark.parametrize("failure,diagnostic", [
    ("term-permission", "signal permission denied"),
    ("kill-permission", "signal permission denied"),
    ("survives", "still executing after SIGKILL"),
    ("probe-permission", "inspection denied"),
    ("probe-timeout", "timed out"),
    ("late-confirmation", "termination unconfirmed"),
])
def test_stop_failure_with_real_wait_retains_evidence(
        tmp_path, monkeypatch, owned_job, stop_clock, failure, diagnostic):
    directory, process = owned_job
    path = directory.parent.parent / "7.json"
    receipt = {"job": directory.name, "head": "original"}
    jobs.save(path, receipt)
    (directory / "log").write_text("preserved evidence\n")
    evidence = {p: p.read_bytes() for p in (path, directory / "request.json",
                directory / "process.json", directory / "log")}
    signals, intents = [], []

    def signal_group(pgid, sig):
        assert pgid == process["pgid"]
        signals.append((sig, stop_clock[0]))
        intents.append((directory / "stop.json").read_bytes())
        assert json.loads(intents[-1])["state"] == "stopping"
        # Late finalization must remain evidence, never delivery eligibility.
        jobs.finalize(directory, {"result": {"outcome": "late success"}})
        if ((failure == "term-permission" and sig == signal.SIGTERM)
                or (failure == "kill-permission" and sig == signal.SIGKILL)):
            raise PermissionError("signal permission denied")

    def probe(pgid, *, timeout=5):
        assert pgid == process["pgid"]
        if signals and signals[-1][0] == signal.SIGKILL:
            assert 0 < timeout <= 1
            if failure == "probe-permission":
                raise PermissionError("inspection denied")
            if failure in ("probe-timeout", "late-confirmation"):
                stop_clock[0] += timeout + .01
                if failure == "probe-timeout":
                    raise subprocess.TimeoutExpired("ps", timeout)
                return set()  # Confirmation arrived too late to claim success.
        return {process["pid"], 2345}

    monkeypatch.setattr(os, "killpg", signal_group)
    monkeypatch.setattr(jobs, "_executing_group", probe)
    with pytest.raises(jobs.StopJobError, match=diagnostic) as error:
        jobs.stop_job(tmp_path, directory.name)
    assert f"see {directory / 'log'}" in str(error.value)
    assert signals[0] == (signal.SIGTERM, 0)
    if failure != "term-permission":
        assert signals[1] == (signal.SIGKILL, 5)
    else:
        assert len(signals) == 1
    if failure == "survives":
        assert stop_clock[0] == 6
    assert (directory / "stop.json").read_bytes() == intents[0]
    stop = json.loads(intents[0])
    assert stop["requested_at"] and stop["process"] == process
    assert "stopped_at" not in stop
    assert all(p.read_bytes() == contents for p, contents in evidence.items())
    assert json.loads((directory / "result.json").read_text())["result"]["outcome"] == "late success"
    jobs._children.clear()
    assert jobs.collect(path, receipt) is None
    assert not (directory / "collected.json").exists()
    assert jobs.list_jobs(tmp_path)[0][0].state == "stopping"


@pytest.mark.scenario("background-job-control/owned-process-group-termination/escalate-unresponsive-group")
@pytest.mark.scenario("background-job-control/owned-process-group-termination/report-termination-failure")
@pytest.mark.parametrize("outcome", ["confirmed", "unconfirmed", "ownership-changed"])
def test_grace_probe_timeout_still_verifies_and_escalates(
        tmp_path, monkeypatch, owned_job, stop_clock, outcome):
    directory, process = owned_job
    signals, timeouts, verified = [], [], []
    command_ps = subprocess.run
    latency = .028
    executing = {process["pid"], 2345}

    def ps(args, **kwargs):
        if args != ["/bin/ps", "-axo", "pid=,pgid=,stat="]:
            return command_ps(args, **kwargs)
        timeout = kwargs["timeout"]
        if timeout < latency:
            stop_clock[0] += timeout
            timeouts.append((signals[-1][0], timeout))
            raise subprocess.TimeoutExpired(args, timeout)
        stop_clock[0] += latency
        # Include the caller so an exited group is a valid, nonempty table.
        rows = ["9999 9999 S", *(f"{pid} 1234 S" for pid in executing)]
        return subprocess.CompletedProcess(args, 0, "\n".join(rows), "")

    def start_identity(pid):
        if timeouts:
            verified.append(pid)
            if outcome == "ownership-changed":
                return "reused descendant"
        return process["start_identity"]

    def signal_group(pgid, sig):
        assert pgid == process["pgid"]
        assert json.loads((directory / "stop.json").read_text())["state"] == "stopping"
        signals.append((sig, stop_clock[0]))
        if sig == signal.SIGTERM:
            executing.discard(process["pid"])  # Only the ignoring descendant remains.
        elif outcome == "confirmed":
            executing.clear()

    real_finish = jobs.finish_stop

    def finish(directory):
        assert not executing, "must never mark stopped while members execute"
        real_finish(directory)

    monkeypatch.setattr(subprocess, "run", ps)
    monkeypatch.setattr(jobs, "process_start_identity", start_identity)
    monkeypatch.setattr(os, "killpg", signal_group)
    monkeypatch.setattr(jobs, "finish_stop", finish)
    if outcome == "confirmed":
        assert jobs.stop_job(tmp_path, directory.name) == "stopped"
        assert json.loads((directory / "stop.json").read_text())["state"] == "stopped"
        assert stop_clock[0] - signals[-1][1] <= 1
    else:
        diagnostic = ("cannot verify surviving group ownership" if outcome == "ownership-changed"
                      else "termination unconfirmed")
        with pytest.raises(jobs.StopJobError, match=diagnostic):
            jobs.stop_job(tmp_path, directory.name)
        stop = json.loads((directory / "stop.json").read_text())
        assert stop["state"] == "stopping" and "stopped_at" not in stop
        assert stop["process"] == process
        assert executing == {2345}
    assert timeouts[0][0] == signal.SIGTERM
    assert timeouts[0][1] == pytest.approx(.008)
    assert verified == [2345]  # Ownership must be rechecked after the timed-out probe.
    if outcome == "ownership-changed":
        assert [sig for sig, _ in signals] == [signal.SIGTERM]
    else:
        assert [sig for sig, _ in signals] == [signal.SIGTERM, signal.SIGKILL]
        assert signals[1][1] - signals[0][1] == pytest.approx(5 + latency)


@pytest.mark.scenario("background-job-control/owned-process-group-termination/escalate-unresponsive-group")
@pytest.mark.parametrize("survivor", ["owned", "reused", "moved-group", "moved-session", "new"])
def test_escalation_checks_surviving_descendant_identity(
        tmp_path, monkeypatch, owned_job, stop_clock, survivor):
    directory, process = owned_job
    signals = []

    def probe(pgid, *, timeout=5):
        if not signals:
            return {1234, 2345}
        if signals[-1][0] == signal.SIGKILL and stop_clock[0] >= 5.25:
            return set()
        return {3456} if survivor == "new" else {2345}

    def signal_group(pgid, sig):
        assert pgid == 1234
        assert json.loads((directory / "stop.json").read_text())["state"] == "stopping"
        signals.append((sig, stop_clock[0]))
        if survivor == "reused":
            monkeypatch.setattr(jobs, "process_start_identity", lambda pid: "new identity")
        elif survivor in ("moved-group", "moved-session"):
            monkeypatch.setattr(os, "getpgid" if survivor == "moved-group" else "getsid", lambda pid: 9998)

    monkeypatch.setattr(jobs, "_executing_group", probe)
    monkeypatch.setattr(os, "killpg", signal_group)
    if survivor == "owned":
        assert jobs.stop_job(tmp_path, directory.name) == "stopped"
        assert signals == [(signal.SIGTERM, 0), (signal.SIGKILL, 5)]
        assert 5.25 <= stop_clock[0] <= 6
        assert json.loads((directory / "stop.json").read_text())["state"] == "stopped"
    else:
        with pytest.raises(jobs.StopJobError, match="cannot verify surviving group ownership"):
            jobs.stop_job(tmp_path, directory.name)
        assert signals == [(signal.SIGTERM, 0)]
        stop = json.loads((directory / "stop.json").read_text())
        assert stop["state"] == "stopping" and "stopped_at" not in stop


@pytest.mark.parametrize("returncode,diagnostic", [
    (1, "cannot verify surviving group ownership"),
    (2, "returned non-zero exit status 2"),
])
def test_escalation_distinguishes_exited_witness_from_inspection_failure(
        tmp_path, monkeypatch, owned_job, stop_clock, returncode, diagnostic):
    directory, process = owned_job
    signals = []

    def start_identity(pid):
        if signals:
            assert pid == 2345  # The wrapper exited after SIGTERM.
            raise subprocess.CalledProcessError(returncode, ["/bin/ps", "-p", str(pid)])
        return process["start_identity"]

    monkeypatch.setattr(jobs, "process_start_identity", start_identity)
    monkeypatch.setattr(jobs, "_executing_group",
                        lambda *a, **kw: {2345} if signals else {1234, 2345})
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    # Exit status 1 means the last witness vanished; other ps failures must
    # retain their inspection diagnostic rather than masquerading as an exit.
    with pytest.raises(jobs.StopJobError, match=diagnostic):
        jobs.stop_job(tmp_path, directory.name)
    assert signals == [(process["pgid"], signal.SIGTERM)]
    stop = json.loads((directory / "stop.json").read_text())
    assert stop["state"] == "stopping" and "stopped_at" not in stop
    assert stop["process"] == process


@pytest.mark.scenario("background-job-control/owned-process-group-termination/refuse-unsafe-identity")
@pytest.mark.parametrize("inspection", ["start", "command", "members"])
@pytest.mark.parametrize("failure", [PermissionError("inspection denied"),
                                     subprocess.TimeoutExpired("ps", 5)])
def test_stop_inspection_failure_never_signals(tmp_path, monkeypatch, owned_job, inspection, failure):
    directory, process = owned_job

    def denied(*args, **kwargs):
        raise failure

    if inspection == "start":
        monkeypatch.setattr(jobs, "process_start_identity", denied)
    elif inspection == "command":
        monkeypatch.setattr(subprocess, "run", denied)
    else:
        monkeypatch.setattr(jobs, "_executing_group", denied)
    with pytest.raises(jobs.StopJobError, match="cannot stop job .*see .*log"):
        jobs.stop_job(tmp_path, directory.name)
    if inspection == "members":
        stop = json.loads((directory / "stop.json").read_text())
        assert stop["state"] == "stopping" and stop["process"] == process
        assert "stopped_at" not in stop
    else:
        assert not (directory / "stop.json").exists()


@pytest.mark.parametrize("record,contents,expected", [
    ("result.json", {"result": {}}, "finished"),
    ("result.json", {"error": "failed"}, "finished"),
    ("collected.json", {"collected_at": "earlier"}, "finished"),
    ("stop.json", {"state": "stopped", "stopped_at": "earlier"}, "stopped"),
])
def test_stop_terminal_attempt_is_signal_free(tmp_path, monkeypatch, record, contents, expected):
    directory = attempt(tmp_path)
    jobs.save(directory / record, contents)
    before = (directory / record).read_bytes()
    monkeypatch.setattr(jobs, "_verify_process", lambda *a: pytest.fail("terminal job verified"))
    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("terminal job signalled"))
    assert jobs.stop_job(tmp_path, directory.name) == expected
    assert (directory / record).read_bytes() == before
    if record != "stop.json":
        assert not (directory / "stop.json").exists()


def test_group_probe_excludes_only_zombies_and_other_groups(monkeypatch):
    def ps(args, **kwargs):
        assert args == ["/bin/ps", "-axo", "pid=,pgid=,stat="]
        assert kwargs["timeout"] == .25 and kwargs["check"]
        return subprocess.CompletedProcess(args, 0,
            " 100 100 Z+\n 101 100 S\n 102 100 R+\n 103 100 T\n 104 200 R\n", "")
    monkeypatch.setattr(subprocess, "run", ps)
    assert jobs._executing_group(100, timeout=.25) == {101, 102, 103}


@pytest.mark.parametrize("output", ["", "not a process table", "123 123"])
def test_group_probe_refuses_unreadable_snapshot(monkeypatch, output):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, output, ""))
    with pytest.raises(ValueError):
        jobs._executing_group(123)


@pytest.mark.parametrize("seconds", [5, 1])
def test_group_wait_is_bounded_and_observes_descendants(monkeypatch, seconds):
    elapsed, probes, reaped = [0.0], [], []
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay))
    monkeypatch.setattr(jobs, "reap", lambda: None)

    def waitpid(pid, flags):
        assert flags == os.WNOHANG
        reaped.append(pid)
        raise ChildProcessError("handle lost on restart")

    def probe(pgid, *, timeout):
        assert pgid == 1234 and 0 < timeout <= seconds
        probes.append(timeout)
        return {2345}  # Wrapper gone; a descendant is still executing.

    monkeypatch.setattr(os, "waitpid", waitpid)
    monkeypatch.setattr(jobs, "_executing_group", probe)
    assert jobs._wait_group({"pid": 1234, "pgid": 1234}, seconds) is False
    assert elapsed[0] == pytest.approx(seconds)
    assert len(probes) > 1 and set(reaped) == {1234}
    assert probes == sorted(probes, reverse=True)
    monkeypatch.setattr(jobs, "_executing_group", lambda *a, **kw: set())
    assert jobs._wait_group({"pid": 1234, "pgid": 1234}, seconds) is True


@pytest.mark.parametrize("seconds", [5, 1])
@pytest.mark.parametrize("offset,confirmed", [(-.01, True), (0, True), (.01, False)])
def test_group_wait_confirmation_deadline_is_inclusive(monkeypatch, stop_clock, seconds,
                                                       offset, confirmed):
    stop_clock[0] = 100.0
    deadline = stop_clock[0] + seconds
    probes = []

    def probe(pgid, *, timeout):
        probes.append((pgid, timeout))
        # Model a probe that starts within budget and reports an empty group
        # just before, exactly at, or just after the confirmation deadline.
        stop_clock[0] = deadline + offset
        return set()

    monkeypatch.setattr(jobs, "_executing_group", probe)
    assert jobs._wait_group({"pid": 1234, "pgid": 1234}, seconds) is confirmed
    assert probes == [(1234, seconds)]


def test_retry_stop_preserves_intent_and_late_result(tmp_path, monkeypatch, owned_job):
    directory, process = owned_job
    stop = {"state": "stopping", "requested_at": "original request", "process": process}
    jobs.save(directory / "stop.json", stop)
    jobs.finalize(directory, {"result": {"outcome": "late"}})
    evidence = (directory / "result.json").read_bytes()
    monkeypatch.setattr(jobs, "_executing_group", lambda *a, **kw: {1234})
    monkeypatch.setattr(jobs, "_wait_group", lambda *a: True)
    signals = []
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))
    assert jobs.stop_job(tmp_path, directory.name) == "stopped"
    assert signals == [(1234, signal.SIGTERM)]
    terminal = json.loads((directory / "stop.json").read_text())
    assert terminal == {**stop, "state": "stopped", "stopped_at": terminal["stopped_at"]}
    assert (directory / "result.json").read_bytes() == evidence


@pytest.mark.scenario("background-job-control/job-command-syntax/reject-path-identifier")
def test_stop_resolves_only_enumerated_directories(tmp_path, monkeypatch):
    outside = attempt(tmp_path / "outside")
    root = tmp_path / "state"
    parent = root / "proposals" / "owner__sample" / "7.jobs"
    parent.mkdir(parents=True)
    (parent / outside.name).symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("untracked job signalled"))
    read = Path.read_text

    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(tmp_path / "outside")
        assert not path.is_relative_to(parent / outside.name)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    before = sorted(tmp_path.rglob("*"))
    with pytest.raises(jobs.StopJobError, match="unknown job ID"):
        jobs.stop_job(root, outside.name)
    assert sorted(tmp_path.rglob("*")) == before
