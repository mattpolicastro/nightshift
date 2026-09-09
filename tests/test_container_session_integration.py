"""Opt-in owned-session tests against a real local Docker engine, no model calls.

Set NIGHTSHIFT_TEST_CONTAINER_SESSION_IMAGE to an already-local immutable image
with /bin/sh, /bin/tar, /bin/sleep, setsid, ip, and nc (the pinned Alpine fixture).
An optional NIGHTSHIFT_TEST_DOCKER_HOST must identify a local unix socket;
otherwise the active Docker context is inspected. No image pull or host mount.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from nightshift.workers import snapshot

IMAGE = os.environ.get("NIGHTSHIFT_TEST_CONTAINER_SESSION_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="explicit immutable local session image required")


@pytest.fixture
def engine(tmp_path):
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE)
    executable = shutil.which("docker")
    assert executable
    host = os.environ.get("NIGHTSHIFT_TEST_DOCKER_HOST")
    if not host:
        host = subprocess.run([executable, "context", "inspect", "--format",
                               "{{.Endpoints.docker.Host}}"], check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    assert re.fullmatch(r"unix:///[^\0\r\n]+", host), "A local Docker socket is required"
    config = tmp_path / "docker-config"
    config.mkdir(mode=0o700)
    env = {"PATH": os.defpath, "DOCKER_CONFIG": str(config), "DOCKER_HOST": host}

    def docker(*args, check=True):
        result = subprocess.run([executable, *args], env=env, capture_output=True,
                                text=True, timeout=15)
        if check and result.returncode:
            pytest.fail(result.stderr[:2048])
        return result

    docker("image", "inspect", IMAGE)
    return host, docker


def source():
    return [snapshot.SourceFile("answer", b"41\n"),
            snapshot.SourceFile("verify.sh", b'#!/bin/sh\nset -eu\ntest "$(cat /workspace/answer)" = 42\n')]


def _journal(recovery):
    records = list(recovery.glob("*.json"))
    assert len(records) == 1, "An owned-session record must exist before commands run"
    assert records[0].stat().st_mode & 0o777 == 0o600
    return json.loads(records[0].read_text())


def _names(session):
    return session.name, session.volume_name


def _live_owned(docker, session):
    container = json.loads(docker("inspect", session.name).stdout)[0]
    volume = json.loads(docker("volume", "inspect", session.volume_name).stdout)[0]
    assert container["Config"]["Labels"]["nightshift.session.owner"] == session.owner
    assert volume["Labels"]["nightshift.session.owner"] == session.owner
    assert container["HostConfig"]["NetworkMode"] == "none"
    assert container["HostConfig"]["ReadonlyRootfs"] is True
    assert not any(mount["Type"] == "bind" for mount in container["Mounts"])


def _gone(docker, names, recovery):
    name, volume = names
    assert docker("inspect", "--type", "container", name, check=False).returncode != 0
    assert docker("volume", "inspect", volume, check=False).returncode != 0
    assert not list(recovery.glob("*.json")), "Remove the journal only after confirmed cleanup"


def test_repeated_commands_roundtrip_and_verification(engine, tmp_path, monkeypatch):
    from nightshift.workers.container_session import Session

    host, docker = engine
    recovery = tmp_path / "recovery"
    sentinel = tmp_path / "host-only-sentinel"
    sentinel.write_text("SYNTHETIC_HOST_ONLY")
    monkeypatch.setenv("NIGHTSHIFT_OPENAI_API_KEY", "SYNTHETIC_PROVIDER_ONLY")
    monkeypatch.setenv("GH_TOKEN", "SYNTHETIC_GITHUB_ONLY")
    initial = source()
    with Session(IMAGE, initial, docker_host=host, recovery_dir=recovery,
                 timeout_s=30, max_output_bytes=4096) as session:
        names = _names(session)
        _journal(recovery)
        _live_owned(docker, session)
        bad = session.run(["/bin/sh", "/workspace/verify.sh"])
        assert not bad.ok and bad.exit_code == 1
        assert session.run(["/bin/sh", "-c", "printf '42\\n' > /workspace/answer; printf first > /workspace/history"]).ok
        assert session.run(["/bin/sh", "-c", 'test "$(cat /workspace/history)" = first; printf second >> /workspace/history']).ok
        assert session.run(["/bin/sh", "/workspace/verify.sh"]).ok
        boundary = session.run(["/bin/sh", "-c",
            "set -eu; test ! -e " + shlex.quote(str(sentinel)) + "; "
            'test -z "${NIGHTSHIFT_OPENAI_API_KEY:-}${GH_TOKEN:-}"; '
            'test ! -e /workspace/.git; test ! -e /var/run/docker.sock; '
            'test -z "$(ip route show)"; '
            'if nc -v -w 1 198.18.0.1 9; then exit 1; fi'])
        assert boundary.ok and "Network unreachable" in boundary.output
        returned = session.finish()
    assert returned == snapshot.validate([
        snapshot.SourceFile("answer", b"42\n"), initial[1],
        snapshot.SourceFile("history", b"firstsecond")])
    assert sentinel.read_text() == "SYNTHETIC_HOST_ONLY"
    _gone(docker, names, recovery)


def test_detached_child_is_removed_with_owned_session(engine, tmp_path):
    from nightshift.workers.container_session import Session, SessionError

    host, docker = engine
    recovery = tmp_path / "recovery"
    with Session(IMAGE, source(), docker_host=host, recovery_dir=recovery, timeout_s=30) as session:
        names = _names(session)
        child = session.run(["/bin/sh", "-c",
            "setsid /bin/sh -c 'while :; do sleep 1; done' </dev/null >/dev/null 2>&1 & "
            "echo $! > /workspace/child.pid; sleep 0.1; kill -0 $(cat /workspace/child.pid)"])
        assert child.status == "backgrounded" and child.exit_code == 0
        docker("exec", session.name, "/bin/sh", "-c", "kill -0 $(cat /workspace/child.pid)")
        with pytest.raises(SessionError):
            session.finish()
    # Docker removes the owned PID namespace, including detached processes;
    # this does not claim host PID inspection or generic host-process cleanup.
    _gone(docker, names, recovery)


@pytest.mark.parametrize("limit", ["timeout", "output"])
def test_command_budget_closes_session_and_cleans_resources(engine, tmp_path, limit):
    from nightshift.workers.container_session import Session

    host, docker = engine
    recovery = tmp_path / "recovery"
    began = time.monotonic()
    with Session(IMAGE, source(), docker_host=host, recovery_dir=recovery,
                 timeout_s=3 if limit == "timeout" else 20, max_output_bytes=256) as session:
        names = _names(session)
        command = ["/bin/sleep", "30"] if limit == "timeout" else ["/bin/sh", "-c", "yes OUTPUT_FLOOD"]
        result = session.run(command)
        assert result.status == ("timed_out" if limit == "timeout" else "output_exhausted")
        assert len(result.output.encode()) <= 256
    assert time.monotonic() - began < 25
    _gone(docker, names, recovery)


def test_untrusted_symlink_export_is_rejected_and_cleaned(engine, tmp_path):
    from nightshift.workers.container_session import Session, SessionError

    host, docker = engine
    recovery = tmp_path / "recovery"
    with pytest.raises((SessionError, ValueError)):
        with Session(IMAGE, source(), docker_host=host, recovery_dir=recovery, timeout_s=30) as session:
            names = _names(session)
            _journal(recovery)
            assert session.run(["/bin/ln", "-s", "/tmp/untrusted", "/workspace/escape"]).ok
            session.finish()
    _gone(docker, names, recovery)
    # A rejected export must not poison a subsequent attempt's ownership state.
    with Session(IMAGE, source(), docker_host=host, recovery_dir=recovery, timeout_s=30) as following:
        following_names = _names(following)
        assert following.finish() == snapshot.validate(source())
    _gone(docker, following_names, recovery)


@pytest.mark.parametrize("scenario", ["passes", "first_fails", "mutates_then_restores"])
def test_real_isolated_verification_chain(engine, tmp_path, monkeypatch, scenario):
    from nightshift.workers import container_session, isolated_verification

    host, docker = engine
    repository = tmp_path / "candidate"
    repository.mkdir()
    git_env = {"PATH": os.environ.get("PATH", os.defpath),
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=repository, env=git_env,
                              check=True, capture_output=True, text=True,
                              timeout=10).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repository / "answer").write_text("42\n")
    (repository / "check.sh").write_text('set -eu\ntest "$(cat answer)" = 42\nprintf CHECKED\n')
    (repository / "fail.sh").write_text("printf EXPECTED_FAILURE\nexit 7\n")
    (repository / "mutate.sh").write_text("printf '43\\n' > answer\nprintf MUTATED\n")
    (repository / "restore.sh").write_text("printf '42\\n' > answer\nprintf LATER_RESTORE\n")
    git("add", ".")
    git("commit", "-qm", "Synthetic candidate")
    sha = git("rev-parse", "HEAD")
    # The committed candidate, rather than dirty host contents, must be checked.
    (repository / "answer").write_text("HOST_DIRTY_SENTINEL\n")
    recovery = tmp_path / "recovery"
    sessions = []
    real_session = container_session.Session

    def capture_session(*args, **kwargs):
        session = real_session(*args, **kwargs)
        sessions.append(session)
        return session

    # Observe resource identities; all operations still use the real session.
    monkeypatch.setattr(container_session, "Session", capture_session)
    command = {"passes": "sh check.sh && /bin/sh check.sh",
               "first_fails": "sh fail.sh && /bin/echo LATER_MARKER",
               "mutates_then_restores": "sh mutate.sh && /bin/sh restore.sh"}[scenario]
    result = isolated_verification.run(repository, sha, command, image_id=IMAGE,
        docker_host=host, recovery_dir=recovery, timeout_s=30, max_output_bytes=4096)
    assert len(sessions) == 1
    assert result.cleanup_succeeded, result.detail
    _gone(docker, _names(sessions[0]), recovery)
    assert result.candidate_sha == sha and result.image_id == IMAGE
    assert (repository / "answer").read_text() == "HOST_DIRTY_SENTINEL\n"
    if scenario == "passes":
        assert result.ok, result.detail
        assert len(result.clauses) == 2
        assert all(clause.output == "CHECKED" for clause in result.clauses)
        assert result.source_fingerprint == result.final_fingerprint
    elif scenario == "first_fails":
        assert not result.ok and result.status == "failed"
        assert len(result.clauses) == 1 and result.clauses[0].exit_code == 7
        assert "LATER_MARKER" not in result.clauses[0].output
    else:
        assert not result.ok and result.status == "candidate_changed", result.detail
        assert len(result.clauses) == 1 and result.clauses[0].ok
        assert result.source_fingerprint != result.final_fingerprint
        assert "LATER_RESTORE" not in result.clauses[0].output


def test_readonly_session_enforces_source_mount_and_preserves_snapshot(engine, tmp_path, monkeypatch):
    import copy
    from nightshift.workers.container_session import Session

    host, docker = engine
    recovery = tmp_path / "readonly-recovery"
    sentinel = tmp_path / "host-private"
    sentinel.write_text("SYNTHETIC_HOST_ONLY")
    monkeypatch.setenv("NIGHTSHIFT_PROVIDER_SENTINEL", "SYNTHETIC_PROVIDER_ONLY")
    initial = [snapshot.SourceFile("source.txt", b"READONLY_SOURCE\n")]
    fingerprint = snapshot.fingerprint(initial)
    with Session(IMAGE, initial, docker_host=host, recovery_dir=recovery,
                 readonly_source=True, timeout_s=30, max_output_bytes=8192) as session:
        names = _names(session)
        assert session.readonly_source_confirmed
        _journal(recovery)
        _live_owned(docker, session)
        assert docker("inspect", "--type", "container", session.loader_name, check=False).returncode != 0
        effective = json.loads(docker("inspect", session.name).stdout)[0]
        configured = effective["HostConfig"]["Mounts"]
        volumes = [mount for mount in effective["Mounts"] if mount["Type"] == "volume"]
        assert len(configured) == len(volumes) == 1
        assert configured[0]["ReadOnly"] is True
        assert volumes[0]["RW"] is False and volumes[0]["Destination"] == "/workspace"
        # Validate reject paths against real inspect data without creating any
        # extra daemon mount: a writable flag or alias must not pass the policy.
        for kind in ("configured_writable", "effective_writable", "alias"):
            wrong = copy.deepcopy(effective)
            if kind == "configured_writable":
                wrong["HostConfig"]["Mounts"][0]["ReadOnly"] = False
            elif kind == "effective_writable":
                next(m for m in wrong["Mounts"] if m["Type"] == "volume")["RW"] = True
            else:
                wrong["Mounts"].append({**volumes[0], "Destination": "/alias", "RW": True})
            assert not session._container_policy(wrong, running=True)

        read = session.run(["/bin/cat", "/workspace/source.txt"])
        assert read.ok and read.output == "READONLY_SOURCE\n"
        scratch = session.run(["/bin/sh", "-c",
            "set -eu; printf SCRATCH_OK > /tmp/scratch; "
            "ln -s /workspace/source.txt /tmp/source-link; cat /tmp/scratch"])
        assert scratch.ok and scratch.output == "SCRATCH_OK"
        attempts = {
            "overwrite": ["/bin/sh", "-c", "printf CHANGED > /workspace/source.txt"],
            "unlink": ["/bin/rm", "/workspace/source.txt"],
            "rename": ["/bin/mv", "/workspace/source.txt", "/workspace/renamed"],
            "chmod": ["/bin/chmod", "777", "/workspace/source.txt"],
            "create": ["/bin/touch", "/workspace/created"],
            "hardlink_source": ["/bin/ln", "/workspace/source.txt", "/workspace/hardlink"],
            "hardlink_scratch": ["/bin/ln", "/workspace/source.txt", "/tmp/hardlink"],
            "scratch_symlink_write": ["/bin/sh", "-c", "printf CHANGED > /tmp/source-link"],
        }
        for label, argv in attempts.items():
            denied = session.run(argv)
            assert denied.status == "failed" and denied.exit_code != 0, (label, denied)
            expected = "Cross-device link" if label == "hardlink_scratch" else "Read-only file system"
            assert expected.lower() in denied.output.lower(), (label, denied)
        boundary = session.run(["/bin/sh", "-c",
            "set -eu; test ! -e " + shlex.quote(str(sentinel)) + "; "
            'test -z "${NIGHTSHIFT_PROVIDER_SENTINEL:-}"; '
            'test ! -e /workspace/.git; test ! -e /var/run/docker.sock; '
            'test -z "$(ip route show)"; '
            'if nc -v -w 1 198.18.0.1 9; then exit 1; fi'])
        assert boundary.ok and "Network unreachable" in boundary.output
        assert snapshot.fingerprint(session.checkpoint()) == fingerprint
        returned = session.finish()
    assert returned == initial and snapshot.fingerprint(returned) == fingerprint
    assert sentinel.read_text() == "SYNTHETIC_HOST_ONLY"
    assert session.cleanup_succeeded
    _gone(docker, names, recovery)
    assert docker("inspect", "--type", "container", session.loader_name, check=False).returncode != 0
