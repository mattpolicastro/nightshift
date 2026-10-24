"""Real disposable process groups; no origin or shared process names."""

import fcntl
import os
import select
import signal
import subprocess
import sys
import time

import pytest

from nightshift import specloop_intake as intake


def eventually(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "process readiness/exit deadline expired"
        time.sleep(.01)


@pytest.mark.scenario("issue-intake/git-process-group-timeout/git-timeout-kills-process-group")
@pytest.mark.parametrize("escaped", [False, True])
def test_git_timeout_kills_process_group(tmp_path, monkeypatch, escaped):
    fake_git = tmp_path / "git"
    fake_git.write_text(f"#!{sys.executable}\n" + '''
import fcntl, os, pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
if len(sys.argv) == 3:
    lock = (root / "transport.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    (root / "transport.ready").touch()
else:
    child = subprocess.Popen([sys.executable, __file__, str(root), "child"],
                             start_new_session=os.environ["ESCAPED"] == "1")
    (root / "transport.pid").write_text(str(child.pid))
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    time.sleep(1)
''')
    fake_git.chmod(0o755)
    monkeypatch.setenv("ESCAPED", str(int(escaped)))
    children = []
    popen = subprocess.Popen
    communicate = popen.communicate
    budgets, started = [], []

    def launch(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def synchronized(child, *args, **kwargs):
        # Start the command budget only after its transport has acquired the
        # liveness lock. Four workers need no assumptions about startup speed.
        eventually(lambda: (tmp_path / "transport.ready").exists()
                   and (tmp_path / "transport.pid").exists())
        if not started:
            assert os.getpgid(child.pid) == child.pid
            transport_group = os.getpgid(int((tmp_path / "transport.pid").read_text()))
            assert (transport_group != child.pid) == escaped
        budgets.append(kwargs["timeout"])
        started.append(time.monotonic())
        return communicate(child, *args, **kwargs)

    unrelated = popen([sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
                      start_new_session=True, stdout=subprocess.PIPE)
    try:
        assert select.select([unrelated.stdout], [], [], 5)[0]
        assert unrelated.stdout.readline() == b"ready\n"
        monkeypatch.setattr(subprocess, "Popen", launch)
        monkeypatch.setattr(popen, "communicate", synchronized)
        with pytest.raises(subprocess.TimeoutExpired) as failure:
            intake.run_git([str(fake_git), str(tmp_path)], timeout=.05, capture_output=True)
        assert failure.value.timeout == .05
        assert time.monotonic() - started[0] < 1.8
        assert budgets[0] == .05 and 0 <= budgets[1] <= 1
        child, = children
        assert child.stdout.closed and child.stderr.closed
        assert child.returncode == -signal.SIGKILL
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)  # Direct child was reaped.
        assert unrelated.poll() is None
        with (tmp_path / "transport.lock").open() as lock:
            def stopped():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except BlockingIOError:
                    return False
            if escaped:
                assert not stopped()  # An escaped group can still hold the pipes.
            else:
                eventually(stopped)
    finally:
        # Independent of runner cleanup, including assertion/readiness failures.
        pid_file = tmp_path / "transport.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in [*children, unrelated]:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass  # macOS reports EPERM for a group that is already gone
            child.kill()
            child.wait(timeout=5)
            for pipe in (child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()


@pytest.mark.parametrize("text", [False, True])
@pytest.mark.parametrize("code,check", [(0, True), (7, False), (7, True)])
def test_git_completed_process_output_and_check(text, code, check, tmp_path):
    args = [sys.executable, "-c",
            f"import sys; print(sys.stdin.read()); print('error', file=sys.stderr); sys.exit({code})"]
    kwargs = dict(timeout=5, capture_output=True, text=text, check=check,
                  input="input" if text else b"input", cwd=tmp_path)
    if code and check:
        with pytest.raises(subprocess.CalledProcessError) as failure:
            intake.run_git(args, **kwargs)
        result = failure.value
        assert result.cmd == args
    else:
        result = intake.run_git(args, **kwargs)
        assert isinstance(result, subprocess.CompletedProcess) and result.args == args
    assert result.returncode == code
    assert result.stdout == ("input\n" if text else b"input\n")
    assert result.stderr == ("error\n" if text else b"error\n")


def test_git_signal_failure_is_diagnosed_and_drain_is_bounded(monkeypatch):
    class Child:
        pid = 123
        stdin = stdout = stderr = None
        def communicate(self, *args, timeout):
            assert timeout == 30 or 0 <= timeout <= 1
            raise subprocess.TimeoutExpired(["git"], timeout)
        def wait(self, *, timeout):
            assert timeout <= 1

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Child())
    def denied(*args):
        raise PermissionError("signal denied")
    monkeypatch.setattr(os, "killpg", denied)
    with pytest.raises(RuntimeError, match="failed to kill process group 123.*signal denied"):
        intake.run_git(["git"], timeout=30)
    def gone(*args):
        raise ProcessLookupError()
    monkeypatch.setattr(os, "killpg", gone)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        intake.run_git(["git"], timeout=30)
    assert failure.value.timeout == 30


def test_bounded_runner_selection_and_injection(monkeypatch):
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return "result"
    monkeypatch.setattr(intake, "run_git", runner)
    assert intake._bounded_run(["git", "fetch"], text=True) == "result"
    monkeypatch.setattr(subprocess, "run", runner)
    assert intake._bounded_run(["specloop", "publish"]) == "result"
    assert intake._bounded_run(["git"], runner=lambda *a, **k: "injected") == "injected"
    assert calls == [(["git", "fetch"], {"timeout": 30, "text": True}),
                     (["specloop", "publish"], {"timeout": 30})]
