"""Verified Stop exercises real worker/landing/finish trees without services."""

import errno
import json
import os
import signal

import pytest

from nightshift import factory_land as factory
from test_factory_land_status import IDENTITY, retained, status
from test_factory_land_worker import absent, drained, locked, read, until, workers


@pytest.fixture
def tree(workers, tmp_path):
    # Preserve the real run_invocation/_run_logged boundary; replace only the
    # external finish command with a controllable local mutation.
    (tmp_path / "logged command.py").write_text('''
import json, os, pathlib, signal, sys, time
attempt, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
def term(signum, frame):
    (attempt / 'term-received').touch()
    print('finish received TERM', flush=True)
    if mode != 'logged-resistant':
        sys.exit(128 + signum)
signal.signal(signal.SIGTERM, term)
process = json.loads((attempt / 'process.json').read_text())
assert process['pgid'] == os.getpgrp() == os.getsid(0)
(attempt / 'command.json').write_text(json.dumps({
    'pid': os.getpid(), 'pgid': os.getpgrp(), 'sid': os.getsid(0)}))
print('finish ready', flush=True)
while not (attempt / 'release').exists():
    time.sleep(.01)
(attempt / 'mutation').write_text('fake GitHub write')
''')

    def start(number=1, *, resistant=False):
        attempt, worker, pgid = workers(number, "logged-resistant" if resistant else "logged")
        until(lambda: (attempt / "command.json").exists()
              and b"finish ready" in (attempt / "output.log").read_bytes())
        assert locked(attempt)
        return attempt, worker, pgid

    return start


def summary(root):
    return factory.landing_status(IDENTITY.change, root=root, read_status=lambda _: status())


def assert_refused(response):
    assert response["accepted"] is False
    assert "Stop refused" in response["reason"]
    assert "Wait for descendants" in response["reason"]
    assert "manually kill" in response["reason"]


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/stop-reaches-the-normal-tree")
@pytest.mark.parametrize("source", ["stop", "term-worker", "int-worker"])
def test_normal_tree_shutdown_logs_lock_and_retry(tree, workers, tmp_path, capsys, source):
    attempt, worker, pgid = tree()
    process = read(attempt / "process.json")
    command = read(attempt / "command.json")
    child_pid = read(attempt / "child.json")["pid"]
    assert process["pid"] == worker.pid
    assert process["sid"] == command["sid"] == command["pgid"] == pgid
    before = (attempt / "output.log").read_bytes()
    (tmp_path / "pause").touch()  # Stop remains available while admission is paused.
    if source == "stop":
        assert factory.main(["stop", attempt.name, "--json"], root=tmp_path) == 0
        response = json.loads(capsys.readouterr().out)
        assert response["accepted"] and response["stop_requested"]
        assert "outcome" not in response and "completion is not confirmed" in response["reason"]
        assert read(attempt / "stop.json")["process"] == process
    else:
        os.kill(worker.pid, signal.SIGINT if source == "int-worker" else signal.SIGTERM)
    assert worker.wait(timeout=10) == 1
    drained(pgid, child_pid, command["pid"])
    assert absent(pgid) and not locked(attempt)
    result = read(attempt / "result.json")
    assert result["outcome"] == "stopped" and result["phase"] == "finishing"
    assert result["exit_code"] == 128 + (signal.SIGINT if source == "int-worker" else signal.SIGTERM)
    assert (attempt / "output.log").read_bytes().startswith(before)
    assert b"finish received TERM" in (attempt / "output.log").read_bytes()
    assert not (attempt / "mutation").exists() and not (attempt / "drained.json").exists()
    assert summary(tmp_path)["attempts"][0]["outcome"] == "stopped"
    (tmp_path / "pause").unlink()
    following, retry, _ = workers(2)
    until(lambda: (following / "child.json").exists())
    assert read(attempt / "drained.json")["process"] == process
    (following / "release").touch()
    assert retry.wait(timeout=10) == 0


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/stop-reaches-the-normal-tree")
@pytest.mark.parametrize("stage", ["status", "remote-head"])
@pytest.mark.parametrize("stop,terminal", [(True, False), (True, True), (False, False)])
def test_stop_during_startup_validation(workers, tmp_path, stage, stop, terminal):
    # Exercise the real subprocess failure -> Refusal path in validate_offer.
    # Replace only the identity command, before any invocation may be spawned.
    (tmp_path / "validation.py").write_text('''
import json, os, pathlib, sys, time
directory = pathlib.Path(sys.argv[1])
(directory / 'command.json').write_text(json.dumps({
    'pid': os.getpid(), 'pgid': os.getpgrp(), 'sid': os.getsid(0)}))
while not (directory / 'release').exists():
    time.sleep(.01)
sys.exit(7)
''')
    (tmp_path / "worker driver.py").write_text('''
import json, pathlib, subprocess, sys
from nightshift import factory_land as f
root, attempt_id, stage = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
directory, offer = f.load_attempt(attempt_id, root)
document = json.loads((root / 'status.json').read_text())
(directory / 'output.log').write_text('retained startup log' + chr(10))
command = [sys.executable, str(root / 'validation.py'), str(directory)]
real_spawn = subprocess.Popen
def spawn(argv, **kwargs):
    assert argv == command, 'validation must not reach an invocation'
    return real_spawn(argv, **kwargs)
subprocess.Popen = spawn
sys.exit(f.run_worker(attempt_id, root=root,
    read_status=lambda _: f._read_json_command(command) if stage == 'status' else document,
    remote_head=lambda _: f._read_json_command(command) if stage == 'remote-head' else offer.head,
    pause_file=root / 'pause'))
''')
    attempt, worker, pgid = workers(1, stage)
    until(lambda: (attempt / "command.json").exists())
    command = read(attempt / "command.json")
    assert command["pgid"] == command["sid"] == pgid and locked(attempt)
    before = (attempt / "output.log").read_bytes()
    if terminal:
        factory.record_result(attempt, "succeeded", "previously confirmed result", phase="merging", exit_code=0)
        previous = (attempt / "result.json").read_bytes()
    if stop:
        response = factory.stop_attempt(attempt.name, root=tmp_path)
        assert response["accepted"] and response["stop_requested"]
        assert "outcome" not in response
    else:
        (attempt / "release").touch()
    assert worker.wait(timeout=10) == 1
    drained(pgid, command_pid=command["pid"])
    assert not locked(attempt) and not (attempt / "child.json").exists()
    assert not (attempt / "drained.json").exists()
    assert (attempt / "output.log").read_bytes() == before
    if terminal:
        assert (attempt / "result.json").read_bytes() == previous
    else:
        result = read(attempt / "result.json")
        assert result["phase"] == "validating"
        assert result["outcome"] == ("stopped" if stop else "refused")
        assert result["exit_code"] == (128 + signal.SIGTERM if stop else None)
        if stop:
            assert "SIGTERM" in result["reason"]
        else:
            assert result["reason"] == "fresh identity read failed"


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/stop-reaches-the-normal-tree")
def test_nonleader_python_worker_identity_authorizes_stop(workers, tmp_path):
    attempt, _, pgid = workers(1, caller=True)
    until(lambda: (attempt / "child.json").exists())
    process = read(attempt / "process.json")
    assert process["pid"] != pgid and process["pgid"] == process["sid"] == pgid
    assert factory.stop_attempt(attempt.name, root=tmp_path)["accepted"]
    until(lambda: (attempt / "result.json").exists())
    drained(pgid, read(attempt / "child.json")["pid"])
    assert read(attempt / "result.json")["outcome"] == "stopped"
    assert not locked(attempt) and (attempt / "output.log").exists()


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/stop-reaches-the-normal-tree")
@pytest.mark.parametrize("terminal", [False, True])
def test_resistant_finish_mutates_but_stop_or_result_never_authorizes_retry(
        tree, workers, tmp_path, terminal):
    attempt, worker, pgid = tree(resistant=True)
    if terminal:
        factory.record_result(attempt, "succeeded", "previously confirmed result", phase="merging", exit_code=0)
        previous = (attempt / "result.json").read_bytes()
    assert factory.stop_attempt(attempt.name, root=tmp_path)["stop_requested"]
    assert worker.wait(timeout=10) == 1
    assert not locked(attempt) and not absent(pgid)
    assert (attempt / "term-received").exists() and not (attempt / "mutation").exists()
    if terminal:
        assert (attempt / "result.json").read_bytes() == previous
    else:
        assert read(attempt / "result.json")["outcome"] == "stopped"
    contender, refused, _ = workers(2)
    assert refused.wait(timeout=10) == 1
    assert "still exists" in read(contender / "result.json")["reason"]
    assert not (contender / "child.json").exists() and not (attempt / "drained.json").exists()
    view = next(row for row in summary(tmp_path)["attempts"] if row["attempt_id"] == attempt.name)
    assert any("group completion is not confirmed" in diagnostic for diagnostic in view["diagnostics"])
    assert_refused(factory.stop_attempt(attempt.name, root=tmp_path))
    # No escalation/hold/release: the child can still write after refusal.
    (attempt / "release").touch()
    until(lambda: (attempt / "mutation").exists())
    drained(pgid, read(attempt / "child.json")["pid"], read(attempt / "command.json")["pid"])
    following, retry, _ = workers(3)
    until(lambda: (following / "child.json").exists())
    (following / "release").touch()
    assert retry.wait(timeout=10) == 0
    if terminal:
        assert (attempt / "result.json").read_bytes() == previous


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/missing-worker-refuses-stop")
def test_missing_worker_with_surviving_tree_refuses_without_signalling(tree, workers, tmp_path, monkeypatch):
    attempt, worker, pgid = tree(resistant=True)
    os.kill(worker.pid, signal.SIGKILL)
    assert worker.wait(timeout=10) == -signal.SIGKILL
    assert not absent(pgid) and not locked(attempt)
    with monkeypatch.context() as patch:
        patch.setattr(factory.os, "killpg", lambda *args: pytest.fail("orphan group must not be signalled"))
        assert_refused(factory.stop_attempt(attempt.name, root=tmp_path))
        view = summary(tmp_path)["attempts"][0]
    assert view["outcome"] == "failed"
    assert any("interrupted" in diagnostic for diagnostic in view["diagnostics"])
    assert not (attempt / "stop.json").exists() and not (attempt / "result.json").exists()
    contender, refused, _ = workers(2)
    assert refused.wait(timeout=10) == 1
    assert "still exists" in read(contender / "result.json")["reason"]
    assert not (contender / "child.json").exists() and not (attempt / "term-received").exists()
    (attempt / "release").touch()
    drained(pgid, read(attempt / "child.json")["pid"], read(attempt / "command.json")["pid"])
    assert (attempt / "mutation").exists()


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/missing-worker-refuses-stop")
@pytest.mark.parametrize("damage", ["start-time", "unreadable", "changed-during-read", "missing",
    "corrupt", "caller-group", "unsafe", "wrong-group", "wrong-session", "boolean-pid"])
def test_identity_refusals_never_signal_running_tree(tree, tmp_path, monkeypatch, damage):
    attempt, worker, _ = tree()
    record = read(attempt / "process.json")
    if damage == "start-time":
        record["start_identity"] += ":different"
    elif damage == "caller-group":
        record.update(pgid=os.getpgrp(), sid=os.getpgrp())
    elif damage == "unsafe":
        record.update(pgid=1, sid=1)
    elif damage == "wrong-group":
        record.update(pgid=record["pgid"] + 100000, sid=record["sid"] + 100000)
    elif damage == "wrong-session":
        record["sid"] += 1
    elif damage == "boolean-pid":
        record["pid"] = True
    factory.write_record(attempt / "process.json", record)
    if damage == "missing":
        (attempt / "process.json").unlink()
    elif damage == "corrupt":
        (attempt / "process.json").write_text("{")
    if damage == "unreadable":
        def identity(pid):
            raise PermissionError(errno.EPERM, "unreadable identity")
        monkeypatch.setattr(factory, "read_start_identity", identity)
    elif damage == "changed-during-read":
        identities = iter([record["start_identity"], "reused"])
        monkeypatch.setattr(factory, "read_start_identity", lambda pid: next(identities))
    with monkeypatch.context() as patch:
        patch.setattr(factory.os, "killpg", lambda *args: pytest.fail("unverified signal"))
        assert_refused(factory.stop_attempt(attempt.name, root=tmp_path))
    assert worker.poll() is None and locked(attempt)
    assert not (attempt / "term-received").exists()
    assert not (attempt / "stop.json").exists() and not (attempt / "result.json").exists()


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/stop-reaches-the-normal-tree")
def test_stop_request_is_not_completion_for_resistant_worker(workers, tmp_path):
    attempt, worker, pgid = workers(1, "resistant")
    until(lambda: (attempt / "child.json").exists())
    os.kill(worker.pid, signal.SIGSTOP)  # Child ignores TERM; worker cannot handle it yet.
    try:
        assert factory.stop_attempt(attempt.name, root=tmp_path)["stop_requested"]
        view = summary(tmp_path)
        assert view["current"]["attempt_id"] == attempt.name and view["current"]["outcome"] is None
        assert any("worker is still running" in diagnostic for diagnostic in view["current"]["diagnostics"])
        assert not (attempt / "result.json").exists()
        contender, refused, _ = workers(2)
        assert refused.wait(timeout=10) == 1
        assert "holds the PR lock" in read(contender / "result.json")["reason"]
        assert not (contender / "child.json").exists()
    finally:
        os.kill(worker.pid, signal.SIGCONT)
    assert worker.wait(timeout=10) == 1 and not absent(pgid)
    assert "remains running" in read(attempt / "result.json")["reason"]
    (attempt / "release").touch()
    drained(pgid, read(attempt / "child.json")["pid"])
    assert (attempt / "mutation").exists()


@pytest.mark.scenario("factory-landing-jobs/operator-execution-isolation/background-paths-cannot-start-landing")
def test_stop_records_add_no_job_inventory_or_adapter(tmp_path, monkeypatch):
    from nightshift import cli, specloop_job
    from specloop import cli as alias, state
    attempt = retained(tmp_path, 1)
    factory.record_result(attempt, "stopped", "worker stopped")
    factory.write_record(attempt / "stop.json", {"version": 1, "requested_at": 1})
    monkeypatch.setattr(state, "ROOT", tmp_path)
    monkeypatch.setenv("NIGHTSHIFT_LEGACY_STATE_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(factory, "stop_attempt", lambda *a, **k: pytest.fail("jobs called landing Stop"))
    monkeypatch.setattr(factory.subprocess, "Popen", lambda *a, **k: pytest.fail("control spawned landing"))
    assert specloop_job.list_jobs(tmp_path) == ([], [])
    for entry in (cli.main, alias.main):
        for action in ("stop", "release"):
            assert entry(["jobs", action, attempt.name]) == 1
        with pytest.raises(SystemExit) as error:
            entry(["factory_land", "stop", attempt.name])
        assert error.value.code == 2


def test_private_stop_syntax_and_missing_attempt(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        factory.main(["stop", "../outside", "--json"], root=tmp_path)
    assert error.value.code == 2
    assert factory.main(["stop", "a" * 32, "--json"], root=tmp_path) == 1
    assert_refused(json.loads(capsys.readouterr().out))


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/missing-worker-refuses-stop")
@pytest.mark.parametrize("error", [errno.ESRCH, errno.EPERM])
def test_failed_signal_is_refused_without_recording_a_stop_request(tree, tmp_path, monkeypatch, error):
    attempt, worker, pgid = tree()
    signals = []

    def fail(group, sig):
        signals.append((group, sig))
        raise OSError(error, "signal failed")

    with monkeypatch.context() as patch:
        patch.setattr(factory.os, "killpg", fail)
        response = factory.stop_attempt(attempt.name, root=tmp_path)
    assert_refused(response)
    assert "SIGTERM could not be sent" in response["reason"]
    assert signals == [(pgid, signal.SIGTERM)]
    assert worker.poll() is None and not (attempt / "stop.json").exists()
    assert not (attempt / "result.json").exists()


@pytest.mark.scenario("factory-landing-jobs/verified-landing-stop/stop-reaches-the-normal-tree")
def test_delivered_stop_remains_accepted_if_request_evidence_write_fails(tree, tmp_path, monkeypatch):
    attempt, worker, pgid = tree()

    def fail(*args):
        raise OSError("request evidence unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(factory, "write_record", fail)
        response = factory.stop_attempt(attempt.name, root=tmp_path)
    assert response["accepted"] and response["stop_requested"]
    assert "evidence could not be saved" in response["reason"]
    assert worker.wait(timeout=10) == 1
    drained(pgid, read(attempt / "child.json")["pid"], read(attempt / "command.json")["pid"])
    assert read(attempt / "result.json")["outcome"] == "stopped"
    assert not (attempt / "stop.json").exists() and not locked(attempt)
