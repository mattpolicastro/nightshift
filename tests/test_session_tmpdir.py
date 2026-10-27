"""Offline Codex lifecycle checks; no provider binary or operator home is used."""

import errno
import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from specloop.agents import Call, Result, codex
from specloop.manifest import Profile


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NIGHTSHIFT_BIN_DIR", str(tmp_path / "bin"))
    call = Call(Profile("test", "codex", "model"), "write", "prompt", tmp_path, {},
                tmp_path / "quota-authentication-required.jsonl", 60, 30, tmp_path / "home")
    call.home.mkdir()
    (call.home / "auth.json").write_text("{}")
    s = SimpleNamespace(call=call, tmp=call.transcript.with_suffix(".tmp"),
                        stderr=call.transcript.with_suffix(".stderr"), evidence={})

    def invoke(outcome="ok", hook=lambda: None):
        def spawn(argv, env, actual, stdin):
            assert actual == call and stdin == call.prompt
            assert env["TMPDIR"] == str(s.tmp) and env["CODEX_HOME"] == str(call.home)
            assert argv[argv.index("-s") + 1] == "workspace-write"
            assert "sandbox_workspace_write.exclude_slash_tmp=true" in argv
            assert "sandbox_workspace_write.network_access=false" in argv
            (s.tmp / "nested").mkdir(exist_ok=True)
            (s.tmp / "nested" / "scratch").write_bytes(b"writable")
            call.transcript.write_text(json.dumps({"type": "thread.started", "thread_id": "session"})
                                       + '\n' + json.dumps({"type": "turn.completed", "usage": {"tokens": 3}}))
            s.stderr.write_bytes(b"provider evidence")
            if outcome != "missing":
                Path(argv[argv.index("-o") + 1]).write_text("{" if outcome == "invalid" else '{"done": true}')
            s.evidence = {p: p.read_bytes() for p in call.transcript.parent.glob("*.json*")}
            hook()
            return {"failed": (7, ""), "signal": (-9, ""), "wall": (None, "wall timeout"),
                    "idle": (None, "idle timeout")}.get(outcome, (0, ""))

        monkeypatch.setattr(codex, "spawn", spawn)
        return codex.run(call)

    s.invoke = invoke
    return s


def stub_removal(monkeypatch, action):
    action.avoids_symlink_attacks = True
    monkeypatch.setattr(codex, "shutil", SimpleNamespace(rmtree=action))


def fail(*args, **kwargs):
    raise PermissionError(errno.EACCES, "quota; authentication required")


def assert_event(s, error, prefix=b"provider evidence"):
    payload = s.stderr.read_bytes()
    assert payload.startswith(prefix + (b"\n" if prefix else b""))
    assert json.loads(payload.splitlines()[-1]) == {
        "event": "session_tmpdir_cleanup_failed", "tmpdir": str(s.tmp),
        "error_type": type(error).__name__, "errno": getattr(error, "errno", None)}


@pytest.mark.scenario("agent-session-tmpdir/session-scratch-lifetime/successful-session")
@pytest.mark.scenario("agent-session-tmpdir/confined-scratch-removal/other-artifacts-survive")
def test_session_tmpdir_removed_after_success(session):
    neighbors = [session.call.cwd / name for name in
                 ("seal.json", "receipts.json", "review.jsonl", "other.tmp/scratch", "build/product")]
    for path in neighbors:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"retained")
    assert session.invoke() == Result("ok", {"done": True}, "session", usage={"tokens": 3})
    assert not session.tmp.exists()
    assert all(p.read_bytes() == b"retained" for p in neighbors)
    assert all(p.read_bytes() == value for p, value in session.evidence.items())
    assert session.stderr.read_bytes() == b"provider evidence"


@pytest.mark.scenario("agent-session-tmpdir/session-scratch-lifetime/failed-or-killed-session")
@pytest.mark.parametrize("outcome", ["failed", "signal", "invalid", "missing"])
def test_failed_session(session, monkeypatch, outcome):
    with monkeypatch.context() as m:
        m.setattr(codex, "_remove_session_tmpdir", lambda tmp: None)
        original = session.invoke(outcome)
    assert original.disposition == ("infra" if outcome in ("failed", "signal") else "invalid_output")
    assert session.invoke(outcome) == original
    assert not session.tmp.exists()


@pytest.mark.scenario("agent-session-tmpdir/session-scratch-lifetime/timed-out-session")
@pytest.mark.parametrize("timeout", ["wall", "idle"])
def test_session_tmpdir_removed_after_timeout(session, timeout):
    assert session.invoke(timeout) == Result("infra", session="session", detail=f"{timeout} timeout",
                                             usage={"tokens": 3})
    assert not session.tmp.exists()


@pytest.mark.scenario("agent-session-tmpdir/session-scratch-lifetime/setup-or-spawn-raises")
@pytest.mark.parametrize("seam", ["_home", "base_env", "argv", "spawn", "read_jsonl"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_setup_or_spawn_raises(session, monkeypatch, seam, cleanup_fails):
    session.invoke()  # Install the local spawn double before replacing a setup seam.
    session.stderr.unlink()
    original = RuntimeError("original setup failure")

    def raises(*args, **kwargs):
        assert session.tmp.is_dir()
        raise original

    if cleanup_fails:
        stub_removal(monkeypatch, fail)
    monkeypatch.setattr(codex, seam, raises)
    with pytest.raises(RuntimeError) as caught:
        codex.run(session.call)
    assert caught.value is original
    assert session.tmp.exists() is cleanup_fails
    if cleanup_fails:
        assert_event(session, PermissionError(errno.EACCES, "denied"),
                     prefix=b"provider evidence" if seam == "read_jsonl" else b"")
    elif seam == "read_jsonl":
        assert session.stderr.read_bytes() == b"provider evidence"
    else:
        assert not session.stderr.exists()


@pytest.mark.scenario("agent-session-tmpdir/confined-scratch-removal/read-only-entries")
def test_read_only_entries(session, monkeypatch):
    real_chmod, repairs = os.fchmod, []

    def chmod(fd, mode):
        before = os.fstat(fd)
        assert stat.S_ISDIR(before.st_mode)
        assert mode == stat.S_IMODE(before.st_mode) | stat.S_IWUSR
        repairs.append(mode)
        real_chmod(fd, mode)

    def readonly():
        (session.tmp / "nested/scratch").chmod(0o444)
        (session.tmp / "nested").chmod(0o551)
        session.tmp.chmod(0o555)

    monkeypatch.setattr(os, "fchmod", chmod)
    assert session.invoke(hook=readonly).disposition == "ok"
    assert not session.tmp.exists() and sorted(repairs) == [0o751, 0o755]
    assert session.stderr.read_bytes() == b"provider evidence"


@pytest.mark.scenario("agent-session-tmpdir/confined-scratch-removal/symlinks-stay-confined")
@pytest.mark.scenario("agent-session-tmpdir/non-fatal-cleanup-diagnostics/removal-fails")
@pytest.mark.parametrize("kind", ["nested", "root", "dangling-root", "dangling-open",
                                  "repair-link", "repair-root", "repair-parent", "unsafe"])
def test_session_tmpdir_removal_never_follows_symlinks(session, monkeypatch, kind):
    assert shutil.rmtree.avoids_symlink_attacks is True
    outside = session.call.cwd / "outside"
    outside.mkdir(mode=0o555)
    outside.chmod(0o755)
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside sentinel")
    sentinel.chmod(0o444)
    outside.chmod(0o555)
    modes = [p.stat().st_mode for p in (outside, sentinel)]

    def links():
        (session.tmp / "dangling").symlink_to(outside / "absent")
        (session.tmp / "escape").symlink_to(outside, target_is_directory=True)
        if kind in ("root", "repair-root"):
            shutil.rmtree(session.tmp)
            session.tmp.symlink_to(outside, target_is_directory=True)
        elif kind.startswith("dangling-"):
            shutil.rmtree(session.tmp)
            session.tmp.symlink_to(outside / "absent", target_is_directory=True)

    if kind == "dangling-open":
        # Some stdlib versions report ENOENT while opening a dangling root.
        stub_removal(monkeypatch, lambda tmp, onexc: onexc(os.open, tmp,
                     FileNotFoundError(errno.ENOENT, "missing symlink target")))
    elif kind.startswith("repair"):
        def repair(tmp, *, onexc):
            path = tmp / "escape/sentinel" if kind == "repair-link" else tmp / "sentinel"
            onexc(os.rmdir if kind == "repair-parent" else os.unlink,
                  tmp if kind == "repair-parent" else path, PermissionError(errno.EACCES, "denied"))
        stub_removal(monkeypatch, repair)
    elif kind == "unsafe":
        stub_removal(monkeypatch, fail)
        codex.shutil.rmtree.avoids_symlink_attacks = False
    assert session.invoke(hook=links).disposition == "ok"
    assert sentinel.read_bytes() == b"outside sentinel"
    assert [p.stat().st_mode for p in (outside, sentinel)] == modes
    if kind == "nested":
        assert not session.tmp.exists()
        assert session.stderr.read_bytes() == b"provider evidence"
    elif kind.startswith("dangling-"):
        assert session.tmp.is_symlink() and not session.tmp.exists()
        assert json.loads(session.stderr.read_bytes().splitlines()[-1])["event"] == "session_tmpdir_cleanup_failed"
        if kind == "dangling-open":
            assert_event(session, FileNotFoundError(errno.ENOENT, "missing symlink target"))
    else:
        assert session.tmp.exists()
        assert json.loads(session.stderr.read_bytes().splitlines()[-1])["event"] == "session_tmpdir_cleanup_failed"


@pytest.mark.scenario("agent-session-tmpdir/confined-scratch-removal/scratch-is-already-absent")
def test_missing_scratch(session):
    assert session.invoke(hook=lambda: shutil.rmtree(session.tmp)).disposition == "ok"
    assert not session.tmp.exists() and session.stderr.read_bytes() == b"provider evidence"


@pytest.mark.scenario("agent-session-tmpdir/non-fatal-cleanup-diagnostics/removal-fails")
@pytest.mark.scenario("agent-session-tmpdir/non-fatal-cleanup-diagnostics/descendant-races-cleanup")
@pytest.mark.parametrize("outcome", ["ok", "failed", "wall", "idle"])
@pytest.mark.parametrize("error", [PermissionError(errno.EACCES, "quota; authentication required"),
                                  OSError(errno.ENOTEMPTY, "descendant raced removal"), RuntimeError("quota")])
def test_session_tmpdir_removal_failure_does_not_fail_attempt(session, monkeypatch, outcome, error):
    original = session.invoke(outcome)

    def raises(tmp, **kwargs):
        assert tmp == session.tmp
        raise error

    stub_removal(monkeypatch, raises)
    assert session.invoke(outcome) == original
    assert session.tmp.exists()
    assert_event(session, error)
    assert all(p.read_bytes() == value for p, value in session.evidence.items())


@pytest.mark.scenario("agent-session-tmpdir/confined-scratch-removal/read-only-entries")
def test_permission_retry_is_bounded(session, monkeypatch):
    attempts = []
    real_unlink = os.unlink

    def unlink(*args, **kwargs):
        if "dir_fd" not in kwargs:
            return real_unlink(*args, **kwargs)
        attempts.append((args, kwargs))
        fail()

    monkeypatch.setattr(os, "unlink", unlink)
    stub_removal(monkeypatch, lambda tmp, onexc: onexc(os.unlink, tmp / "nested/scratch",
                                                      PermissionError(errno.EACCES, "denied")))
    assert session.invoke().disposition == "ok"
    assert len(attempts) == 1 and "dir_fd" in attempts[0][1]
    assert_event(session, PermissionError(errno.EACCES, "denied"))


@pytest.mark.scenario("agent-session-tmpdir/non-fatal-cleanup-diagnostics/diagnostic-write-fails")
@pytest.mark.parametrize("raises", [False, True])
def test_diagnostic_write_fails(session, monkeypatch, raises):
    original = session.invoke()
    real_open = Path.open
    stub_removal(monkeypatch, fail)
    monkeypatch.setattr(Path, "open", lambda path, mode="r", *a, **kw:
                        fail() if mode == "a+b" else real_open(path, mode, *a, **kw))
    if raises:
        error = ValueError("setup")
        def setup(call):
            raise error
        monkeypatch.setattr(codex, "_home", setup)
        with pytest.raises(ValueError) as caught:
            session.invoke()
        assert caught.value is error
    else:
        assert session.invoke() == original
    assert session.stderr.read_bytes() == b"provider evidence"
