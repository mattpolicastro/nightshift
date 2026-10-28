"""Real detached groups exercise the lock and durable execution boundary."""

from collections import Counter
from dataclasses import replace
import errno
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from nightshift import factory_land as factory
from test_factory_land_status import IDENTITY, argv, retained, snapshot, status


def until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    pytest.fail("process boundary did not become observable")


def read(path):
    return json.loads(path.read_text())


def absent(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def reap(pid):
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def drained(pid, child_pid=None, command_pid=None):
    def check():
        reap(pid)
        if child_pid:
            reap(child_pid)
        if command_pid:
            reap(command_pid)
        return absent(pid)
    until(check)


def locked(path):
    with (path.parent.parent / "lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


@pytest.fixture
def workers(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]) + os.pathsep
                       + os.environ.get("PYTHONPATH", ""))
    # Reap adopted orphans even under a Linux container's non-reaping PID 1.
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        original = ctypes.c_int()
        assert libc.prctl(37, ctypes.byref(original), 0, 0, 0) == 0
        assert libc.prctl(36, 1, 0, 0, 0) == 0
    (tmp_path / "status.json").write_text(json.dumps(status()))
    (tmp_path / "logged command.py").write_text('''
import json, os, pathlib, signal, sys, time
directory, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
process = json.loads((directory / 'process.json').read_text())
assert os.getpgrp() == os.getsid(0) == process['pgid']
for fd in range(3, 256):
    try:
        assert os.fstat(fd).st_ino != (directory.parent.parent / 'lock').stat().st_ino
    except OSError:
        pass
(directory / 'command.json').write_text(json.dumps({'pid': os.getpid()}))
sys.stdout.buffer.write(('stdout: ' + '☃' * 30000 + '\\r\\n').encode())
sys.stdout.flush()
sys.stderr.buffer.write(('stderr: ' + 'é' * 30000 + '\\r').encode())
sys.stderr.flush()
if mode == 'logged-term':
    os.kill(os.getppid(), signal.SIGTERM)
else:
    while not (directory / 'release').exists():
        time.sleep(.01)
sys.exit(7)
''')
    (tmp_path / "logged invocation.py").write_text('''
import json, os, pathlib, subprocess, sys
from nightshift import factory_land as f
root, directory, mode = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
(directory / 'child.json').write_text(json.dumps({'pid': os.getpid()}))
def confirmed(offer, *, runner):
    command = [sys.executable, str(root / 'logged command.py'), str(directory), mode, 'finish']
    try:
        result = runner(command, capture_output=True, text=True, check=False,
                        timeout=.5 if mode == 'logged-timeout' else 30)
    except subprocess.TimeoutExpired as exc:
        (directory / 'captured.json').write_text(json.dumps({
            'stdout': exc.stdout.decode(), 'stderr': exc.stderr.decode(), 'timeout': True}))
        return 1
    (directory / 'captured.json').write_text(json.dumps({
        'stdout': result.stdout, 'stderr': result.stderr, 'code': result.returncode, 'args': result.args}))
    return result.returncode
f.invoke_confirmed = confirmed
sys.exit(f.run_invocation(directory.name, root=root))
''')
    script = tmp_path / "worker driver.py"
    (tmp_path / "fake invocation.py").write_text('''
import json, os, pathlib, signal, subprocess, sys, time
root, attempt = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
if sys.argv[3] == 'resistant':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
process = json.loads((attempt / 'process.json').read_text())
assert process['pgid'] == os.getpgrp() == os.getsid(0)
# Worker may already have died at the post-spawn crash boundary.
assert process['pid'] == os.getppid() or not (attempt / 'result.json').exists()
for fd in range(3, 256):
    try:
        assert os.fstat(fd).st_ino != (attempt.parent.parent / 'lock').stat().st_ino
    except OSError:
        pass
(attempt / 'child.json').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
print('before write', flush=True)
while not (attempt / 'release').exists():
    time.sleep(.01)
subprocess.run([sys.executable, '-c', 'import os,sys; assert os.getpgrp() == int(sys.argv[1]); print("finish output")', str(os.getpgrp())], check=True)
(attempt / 'mutation').write_text('fake GitHub write')
print('x' * 30000, flush=True)
print('after write', flush=True)
''')
    script.write_text('''
import json, os, pathlib, signal, subprocess, sys
from nightshift import factory_land as f
root, attempt_id, mode = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
directory, offer = f.load_attempt(attempt_id, root)
real_spawn, real_write = subprocess.Popen, f.write_record
def write(path, record):
    if path.name == 'process.json' and mode == 'before-record':
        os.kill(os.getpid(), signal.SIGKILL)
    real_write(path, record)
def spawn(argv, **kwargs):
    assert argv == [sys.executable, '-m', 'nightshift.factory_land', 'invoke', attempt_id]
    assert 'pass_fds' not in kwargs and not kwargs.get('start_new_session')
    assert kwargs['close_fds'] is True and (directory / 'process.json').exists()
    command = ([sys.executable, str(root / 'logged invocation.py'), str(root), str(directory), mode]
               if mode.startswith('logged') else
               [sys.executable, str(root / 'fake invocation.py'), str(root), str(directory), mode])
    child = real_spawn(command, **kwargs)
    if mode == 'after-spawn':
        os.kill(os.getpid(), signal.SIGKILL)
    return child
subprocess.Popen, f.write_record = spawn, write
sys.exit(f.run_worker(attempt_id, root=root,
    read_status=lambda _: json.loads((root / 'status.json').read_text()),
    remote_head=lambda _: json.loads((root / 'status.json').read_text())['runs'][0]['head'],
    pause_file=root / 'pause'))
''')
    uv = tmp_path / "configured uv"
    uv.write_text(f"#!{sys.executable}\n" + '''
import os, pathlib, subprocess, sys
assert sys.argv[1:7] == ['run', '--no-sync', 'python', '-m', 'nightshift.factory_land', 'worker']
root = pathlib.Path.cwd()
# Like uv, the session leader can supervise an actual Python worker PID.
sys.exit(subprocess.Popen([sys.executable, str(root / 'worker driver.py'),
    str(root), sys.argv[7], 'normal']).wait())
''')
    uv.chmod(0o755)
    processes = []

    def start(number, mode="normal", *, caller=False):
        if caller:
            result = subprocess.run([sys.executable, "-c", '''
import json, pathlib, sys
from nightshift import factory_land as f
root = pathlib.Path(sys.argv[1])
document = json.loads((root / 'status.json').read_text())
offer = f.offer_from_status(document, document['runs'][0]['change'])
response = f.start_attempt(offer, root=root, read_status=lambda _: document,
    remote_head=lambda _: offer.head, pause_file=root / 'pause',
    launch=lambda attempt: f.launch_worker(attempt, root=root))
print(json.dumps(response))
''', str(tmp_path)], env={**os.environ, "NIGHTSHIFT_UV": str(uv),
    "NIGHTSHIFT_CHECKOUT": str(tmp_path)}, capture_output=True, text=True, check=True)
            response = json.loads(result.stdout)
            assert response["accepted"]
            path = factory.attempt_directory(tmp_path, IDENTITY, response["attempt_id"])
            until(lambda: (path / "process.json").exists())
            pid, process = read(path / "process.json")["pgid"], None
        else:
            path = retained(tmp_path, number, created_at=time.time())
            factory.record_progress(path, "starting")
            command = [sys.executable, str(script), str(tmp_path), path.name, mode]
            with (path / "transport.log").open("ab") as output:
                process = subprocess.Popen(command, start_new_session=True, stdout=output, stderr=subprocess.STDOUT)
            pid = process.pid
        processes.append((pid, process, path))
        return path, process, pid

    yield start
    for pid, process, path in processes:
        if not absent(pid):
            os.killpg(pid, signal.SIGKILL)
        if process is not None:
            process.wait(timeout=10)
        child_pid = read(path / "child.json")["pid"] if (path / "child.json").exists() else None
        command_pid = read(path / "command.json")["pid"] if (path / "command.json").exists() else None
        drained(pid, child_pid, command_pid)
    if sys.platform.startswith("linux"):
        assert libc.prctl(36, original.value, 0, 0, 0) == 0


@pytest.mark.scenario("factory-landing-jobs/detached-lifetime-lock/caller-exits-while-landing-runs")
@pytest.mark.scenario("factory-landing-jobs/detached-lifetime-lock/concurrent-click-refuses-and-exit-permits-retry")
@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/pause-blocks-new-execution")
def test_caller_loss_contention_full_logs_and_explicit_retry(workers, tmp_path):
    first, _, pid = workers(1, caller=True)
    until(lambda: (first / "child.json").exists())
    assert locked(first)
    process = read(first / "process.json")
    assert process["pgid"] == process["sid"] == pid
    assert process["pid"] != pid  # Persist Python identity, not uv's PID.
    assert factory.worker_alive(process)
    view = factory.landing_status(IDENTITY.change, root=tmp_path, read_status=lambda _: status())
    assert view["current"]["attempt_id"] == first.name
    assert view["current"]["phase"] == "validating"
    second, contender, _ = workers(2)
    assert contender.wait(timeout=10) == 1
    assert "PR lock" in read(second / "result.json")["reason"]
    assert not (second / "child.json").exists()
    (tmp_path / "pause").touch()
    assert locked(first)  # A new daemon pause does not kill a running tree.
    (first / "release").touch()
    until(lambda: (first / "result.json").exists())
    assert read(first / "result.json")["outcome"] == "succeeded"
    output = (first / "output.log").read_text()
    assert all(part in output for part in ("before write", "finish output", "x" * 30000, "after write"))
    drained(pid, read(first / "child.json")["pid"])
    assert not locked(first)
    (tmp_path / "pause").unlink()
    third, retry, _ = workers(3)
    until(lambda: (third / "child.json").exists())
    assert read(first / "drained.json")["drained"] is True
    (third / "release").touch()
    assert retry.wait(timeout=10) == 0


@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/worker-death-leaves-a-mutating-child")
@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/process-record-precedes-child-creation")
@pytest.mark.parametrize("mode", ["normal", "after-spawn"])
def test_worker_death_blocks_unchanged_offer_until_child_drains(workers, tmp_path, mode):
    first, worker, pid = workers(1, mode)
    until(lambda: (first / "child.json").exists())
    if mode == "normal":
        os.kill(pid, signal.SIGKILL)
    assert worker.wait(timeout=10) == -signal.SIGKILL
    assert not locked(first) and not (first / "mutation").exists()
    child_pid = read(first / "child.json")["pid"]
    assert not absent(pid)
    before = snapshot(tmp_path)
    view = factory.landing_status(root=tmp_path)
    assert "interrupted" in str(view["attempts"][0]["diagnostics"])
    assert snapshot(tmp_path) == before
    second, contender, _ = workers(2)
    assert contender.wait(timeout=10) == 1
    assert "still exists" in read(second / "result.json")["reason"]
    assert not (second / "child.json").exists() and not (first / "mutation").exists()
    assert read(tmp_path / "status.json") == status()
    (first / "release").touch()
    drained(pid, child_pid)
    assert (first / "mutation").exists()
    third, retry, _ = workers(3)
    until(lambda: (third / "child.json").exists())
    assert read(first / "drained.json")["process"] == read(first / "process.json")
    (third / "release").touch()
    assert retry.wait(timeout=10) == 0


@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/process-record-precedes-child-creation")
def test_death_before_process_record_never_creates_invocation(workers):
    first, worker, pid = workers(1, "before-record")
    assert worker.wait(timeout=10) == -signal.SIGKILL
    assert not (first / "process.json").exists()
    assert not (first / "child.json").exists() and not (first / "mutation").exists()
    assert absent(pid) and not locked(first)
    second, retry, _ = workers(2)
    until(lambda: (second / "child.json").exists())
    (second / "release").touch()
    assert retry.wait(timeout=10) == 0


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/republication-invalidates-confirmation")
@pytest.mark.parametrize("field,value", [("head", "b" * 40), ("since", .25),
    ("pause_id", "human_checks:new"), ("pr", "https://github.com/Example/Repo/pull/8"), ("change", "another-change")])
def test_startup_independently_revalidates_every_field(workers, tmp_path, field, value):
    (tmp_path / "status.json").write_text(json.dumps(status(replace(IDENTITY, **{field: value}))))
    attempt, worker, _ = workers(1)
    assert worker.wait(timeout=10) == 1
    result = read(attempt / "result.json")
    assert result["outcome"] == "refused"
    assert "stale" in result["reason"] or "existing run" in result["reason"]
    assert not (attempt / "child.json").exists()


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/pause-blocks-new-execution")
@pytest.mark.parametrize("failure", ["pause", "unreadable"])
def test_startup_pause_and_identity_failures_refuse(workers, tmp_path, failure):
    if failure == "pause":
        (tmp_path / "pause").touch()
    else:
        (tmp_path / "status.json").write_text("{")
    attempt, worker, _ = workers(1)
    assert worker.wait(timeout=10) == 1
    assert read(attempt / "result.json")["outcome"] == "refused"
    assert not (attempt / "child.json").exists()


@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/exclusion-evidence-fails-closed")
def test_terminal_result_with_live_child_older_than_display_page(workers, tmp_path):
    first, worker, pid = workers(1, "resistant")
    until(lambda: (first / "child.json").exists())
    os.kill(pid, signal.SIGKILL)
    worker.wait(timeout=10)
    os.kill(read(first / "child.json")["pid"], signal.SIGTERM)
    factory.record_result(first, "succeeded", "terminal does not establish group absence")
    for number in range(2, 25):
        newer = retained(tmp_path, number, created_at=time.time())
        factory.record_result(newer, "refused", "another confirmation")
    view = factory.landing_status(root=tmp_path)
    assert first.name not in [row["attempt_id"] for row in view["attempts"]]
    assert view["total"] == 24
    contender, process, _ = workers(25)
    assert process.wait(timeout=10) == 1
    assert "still exists" in read(contender / "result.json")["reason"]
    assert not (contender / "child.json").exists() and not (first / "drained.json").exists()
    (first / "release").touch()
    drained(pid, read(first / "child.json")["pid"])


@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/exclusion-evidence-fails-closed")
@pytest.mark.parametrize("damage", ["permission", "other-error", "corrupt-json", "unsafe", "boolean", "overflow", "missing-field", "drained"])
def test_probe_errors_and_corrupt_evidence_refuse_with_retained_reason(tmp_path, monkeypatch, damage):
    prior, current = retained(tmp_path, 1), retained(tmp_path, 2)
    process = {"version": 1, "pid": 999991, "pgid": 999991, "sid": 999991,
               "started_at": 0, "start_identity": "birth"}
    if damage == "unsafe":
        process.update(pgid=os.getpgrp(), sid=os.getpgrp())
    if damage == "boolean":
        process["pid"] = True
    if damage == "overflow":
        process.update(pid=2**64, pgid=2**64, sid=2**64)
    if damage == "missing-field":
        del process["start_identity"]
    factory.write_record(prior / "process.json", process)
    if damage == "corrupt-json":
        (prior / "process.json").write_text("{")
    if damage == "drained":
        factory.write_record(prior / "drained.json", {"version": 1, "drained": True})
    probes = []
    def probe(pgid, sig):
        probes.append((pgid, sig))
        raise OSError(errno.EPERM if damage == "permission" else errno.EIO, "probe failed")
    monkeypatch.setattr(factory.os, "killpg", probe)
    monkeypatch.setattr(factory.subprocess, "Popen", lambda *a, **k: pytest.fail("refusal spawned a child"))
    assert factory.run_worker(current.name, root=tmp_path) == 1
    assert read(current / "result.json")["outcome"] == "refused"
    assert "prior attempt" in read(current / "result.json")["reason"]
    assert probes == ([(999991, 0)] if damage in ("permission", "other-error") else [])
    if damage != "drained":
        assert not (prior / "drained.json").exists()


@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/exclusion-evidence-fails-closed")
def test_real_zombie_group_blocks_until_reaped(tmp_path):
    prior, current = retained(tmp_path, 1), retained(tmp_path, 2)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.2)"], start_new_session=True)
    try:
        # Observe exit without reaping. Never infer drainage from a listing.
        if sys.platform == "darwin":
            import select
            queue = select.kqueue()
            try:
                event = select.kevent(child.pid, filter=select.KQ_FILTER_PROC,
                                     flags=select.KQ_EV_ADD, fflags=select.KQ_NOTE_EXIT)
                assert queue.control([event], 1, 10)
            finally:
                queue.close()
        else:
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        factory.write_record(prior / "process.json", {"version": 1, "pid": child.pid,
            "pgid": child.pid, "sid": child.pid, "start_identity": "birth", "started_at": 0})
        factory.record_result(prior, "failed", "child exited but not reaped")
        try:
            os.killpg(child.pid, 0)
        except OSError as exc:
            # Darwin may deny even signal 0 once only zombies remain.
            assert exc.errno == errno.EPERM
        assert factory.run_worker(current.name, root=tmp_path) == 1
        assert "prior group" in read(current / "result.json")["reason"]
        assert not (prior / "drained.json").exists()
        assert child.wait(timeout=10) == 0
        with (current.parent.parent / "lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            factory.exclude_prior_groups(current)
        assert read(prior / "drained.json")["drained"] is True
    finally:
        child.wait(timeout=10)


def test_private_start_uses_literal_configured_detached_uv(tmp_path, monkeypatch, capsys):
    for name, value in {"NIGHTSHIFT_UV": "/tools with spaces/uv", "NIGHTSHIFT_CHECKOUT": "/checkout with spaces",
                        "NIGHTSHIFT_BIN_DIR": "/host tools", "NIGHTSHIFT_NODE_BIN": "/node tools"}.items():
        monkeypatch.setenv(name, value)
    calls = []
    def spawn(command, **options):
        attempt_id = command[-1]
        path = factory.attempt_directory(tmp_path, IDENTITY, attempt_id)
        assert read(path / "request.json")["checks_done"] is True
        assert command == ["/tools with spaces/uv", "run", "--no-sync", "python", "-m",
                           "nightshift.factory_land", "worker", attempt_id]
        assert options["cwd"] == "/checkout with spaces"
        assert options["env"]["PATH"] == "/node tools:/host tools:/usr/bin:/bin:/usr/sbin:/sbin"
        assert options["env"]["NIGHTSHIFT_STATE_ROOT"] == str(tmp_path)
        assert options["start_new_session"] is True and options["close_fds"] is True
        assert "pass_fds" not in options and not options.get("shell")
        assert options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.STDOUT
        assert options["stdout"].name == str(path / "output.log")
        calls.append(command)
        return SimpleChild()
    class SimpleChild:
        def wait(self, *a, **k):
            pytest.fail("start must not supervise the worker")
    monkeypatch.setattr(factory.subprocess, "Popen", spawn)
    assert factory.main(argv(), root=tmp_path, read_status=lambda _: status(),
                        remote_head=lambda _: IDENTITY.head, pause_file=tmp_path / "pause") == 0
    response = json.loads(capsys.readouterr().out)
    assert response["accepted"] and calls[0][-1] == response["attempt_id"]


@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/process-record-precedes-child-creation")
@pytest.mark.parametrize("failure", ["file-sync", "directory-sync"])
def test_process_record_sync_failure_never_spawns_invocation(tmp_path, monkeypatch, failure):
    current = retained(tmp_path, 1)
    monkeypatch.setattr(factory, "process_identity", lambda: {"version": 1, "pid": 123,
        "pgid": 123, "sid": 123, "start_identity": "birth", "started_at": 0})
    original_write, original_sync = factory.write_record, factory.os.fsync
    def write(path, record):
        if path.name != "process.json":
            return original_write(path, record)
        count = 0
        def sync(fd):
            nonlocal count
            count += 1
            if count == (1 if failure == "file-sync" else 2):
                raise OSError("process sync failed")
            original_sync(fd)
        with monkeypatch.context() as patch:
            patch.setattr(factory.os, "fsync", sync)
            original_write(path, record)
    monkeypatch.setattr(factory, "write_record", write)
    monkeypatch.setattr(factory.subprocess, "Popen", lambda *a, **k: pytest.fail("unpersisted execution spawned"))
    assert factory.run_worker(current.name, root=tmp_path) == 1
    assert "process sync failed" in read(current / "result.json")["reason"]
    assert (current / "process.json").exists() == (failure == "directory-sync")


def test_invoke_mode_connects_frozen_offer_logged_runner_and_progress(tmp_path, monkeypatch):
    current = retained(tmp_path, 1)
    factory.write_record(current / "process.json", {"version": 1, "pid": os.getppid(),
        "pgid": os.getpgrp(), "sid": os.getsid(0)})
    commands = [[sys.executable, "-m", "nightshift.cli", "finish", IDENTITY.change,
                 "--expect-head", IDENTITY.head], ["gh", "pr", "checks"], ["gh", "pr", "merge"]]
    results = [subprocess.CompletedProcess(command, 0, "x" * 10000, "diagnostic") for command in commands]
    def confirmed(offer, *, runner):
        assert offer == IDENTITY
        for command, result, phase in zip(commands, results, ("finishing", "checking CI", "merging")):
            assert runner(command, capture_output=True, text=True, check=False, timeout=300) is result
            assert read(current / "progress.json")["phase"] == phase
        return 7
    def run(command, output, **options):
        assert output == current / "output.log"
        assert options == dict(capture_output=True, text=True, check=False, timeout=300)
        return results[commands.index(command)]
    monkeypatch.setattr(factory, "invoke_confirmed", confirmed)
    monkeypatch.setattr(factory, "_run_logged", run)
    assert factory.main(["invoke", current.name], root=tmp_path) == 7


@pytest.mark.scenario("factory-landing-jobs/detached-lifetime-lock/caller-exits-while-landing-runs")
@pytest.mark.scenario("factory-landing-jobs/prior-execution-exclusion/process-record-precedes-child-creation")
@pytest.mark.parametrize("mode", ["logged", "logged-term", "logged-timeout"])
def test_actual_logged_runner_retains_output_before_exit_and_on_interruption(workers, mode):
    attempt, worker, _ = workers(1, mode)
    stdout = ("stdout: " + "☃" * 30000 + "\r\n").encode()
    stderr = ("stderr: " + "é" * 30000 + "\r").encode()
    log = attempt / "output.log"
    if mode == "logged":
        until(lambda: log.exists() and log.stat().st_size == len(stdout) + len(stderr))
        assert worker.poll() is None and locked(attempt)
        assert read(attempt / "progress.json")["phase"] == "finishing"
        assert not (attempt / "captured.json").exists()
        (attempt / "release").touch()
    assert worker.wait(timeout=10) == 1
    output = log.read_bytes()
    # No deferred logging, clipping, newline rewriting or double printing.
    assert output.count(b"stdout: ") == output.count(b"stderr: ") == 1
    assert Counter(output) == Counter(stdout + stderr)
    result = read(attempt / "result.json")
    assert result["outcome"] == "failed" and result["phase"] == "finishing"
    assert result["reason"] and len(result["reason"]) <= factory.DETAIL_LIMIT
    if mode == "logged-term":
        assert result["exit_code"] == 128 + signal.SIGTERM
        assert not (attempt / "captured.json").exists()
    else:
        captured = read(attempt / "captured.json")
        if mode == "logged-timeout":
            assert captured == {"stdout": stdout.decode(), "stderr": stderr.decode(), "timeout": True}
        else:
            assert captured["stdout"] == stdout.decode().replace("\r\n", "\n")
            assert captured["stderr"] == stderr.decode().replace("\r", "\n")
            assert captured["code"] == 7 and captured["args"][-1] == "finish"
