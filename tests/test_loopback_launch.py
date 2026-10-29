import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import manifest, verify
from test_loopback_profile import ALLOWS
from test_specloop_units import MANIFEST

STEP = manifest.Step("check", (sys.executable, "-c", "pass"), loopback=True)


@pytest.fixture
def launches(monkeypatch):
    events = []
    run, killpg = subprocess.run, os.killpg
    monkeypatch.setattr(verify, "already_sandboxed", lambda: False)
    monkeypatch.setattr(verify, "tool_path", lambda: "/usr/bin:/bin")

    def snapshot():
        ports = frozenset({8000 + len(events)})
        events.append(("snapshot", ports))
        return ports

    def probe(argv, **kw):
        if argv[0] != "/usr/bin/sandbox-exec":
            return run(argv, **kw)
        path = Path(argv[2])
        assert argv == ["/usr/bin/sandbox-exec", "-f", str(path), "/usr/bin/true"]
        assert kw == dict(stdout=kw["stdout"], stderr=subprocess.STDOUT, timeout=5)
        assert path.exists() and not kw["stdout"].closed
        events.append(("probe", path))
        return subprocess.CompletedProcess(argv, 0)

    def wrap(argv, sb):
        events.append(("launch", sb))
        return argv

    def cleanup(pid, sig):
        events.append(("cleanup", pid))
        return killpg(pid, sig)

    monkeypatch.setattr(verify, "_listening_loopback_ports", snapshot)
    monkeypatch.setattr(subprocess, "run", probe)
    monkeypatch.setattr(verify, "wrap", wrap)
    monkeypatch.setattr(os, "killpg", cleanup)
    return events


def execute(out, steps, **fields):
    return verify.run_extracted(out.parent, "sha", steps, replace(manifest.parse(MANIFEST), **fields), out)


def assert_aborted(out, launches):
    assert [r["id"] for r in json.loads((out / "receipts.json").read_text())] == ["before"]
    assert len([e for e in launches if e[0] == "launch"]) == 1
    assert "step 'check' loopback" in (out / "check.log").read_text()
    assert not (out / "later.log").exists()


@pytest.mark.scenario("verify-preparation/launch-time-listener-exclusions/existing-listener-ports-are-denied")
@pytest.mark.scenario("verify-preparation/launch-time-listener-exclusions/each-command-observes-its-own-launch")
@pytest.mark.scenario("verify-preparation/opted-in-localhost-profile/default-profiles-retain-network-denial")
@pytest.mark.scenario("verify-preparation/step-loopback-flag/preparation-retains-its-policy")
@pytest.mark.parametrize("flag", [False, True])
def test_loopback_denies_ports_listening_at_launch(tmp_path, launches, flag):
    for name in ("first", "second"):
        out = tmp_path / name
        start = len(launches)
        steps = (STEP, replace(STEP, id="next"), replace(STEP, id="default", loopback=False))
        prep = replace(STEP, id="prep", loopback=flag)
        assert all(r.passed for r in execute(out, steps, prepare=(prep,)))
        events = launches[start:]
        assert [e[0] for e in events] == ["launch", "cleanup"] + ["snapshot", "probe", "launch", "cleanup"] * 2 + ["launch", "cleanup"]
        default = (out / "verify.sb").read_text()
        assert default == verify.profile(out / "build", manifest.parse(MANIFEST))
        assert "(deny network*)" in default and "localhost" not in default
        for offset in (2, 6):
            ports, path = events[offset][1], events[offset + 1][1]
            assert path == events[offset + 2][1] == out / f"verify-loopback-{1 + (offset - 2) // 4}.sb"
            assert path.read_text() == default.replace("(deny network*)", "(deny network*)\n" + ALLOWS +
                "".join(f'\n(deny network-outbound (remote tcp "localhost:{p}"))' for p in sorted(ports)))


@pytest.mark.scenario("verify-preparation/loopback-setup-fails-closed/snapshot-failure-prevents-execution")
@pytest.mark.scenario("verify-preparation/loopback-setup-fails-closed/enclosing-sandbox-cannot-bypass-exclusions")
@pytest.mark.parametrize("failure", ["launch", "denied", "timeout", "decode", "partial", "status", "parse", "render", "write", "nested", "resolver"])
def test_loopback_snapshot_failure_fails_closed(tmp_path, monkeypatch, launches, failure):
    real_profile = verify.profile
    def discover(*a, **kw):
        errors = {"launch": FileNotFoundError(), "denied": PermissionError(),
                  "timeout": subprocess.TimeoutExpired("lsof", 5), "decode": UnicodeError()}
        if failure in errors:
            raise errors[failure]
        return subprocess.CompletedProcess(a, 2 if failure == "status" else 0, "p1\nf3\nn*:80\n" if failure == "partial" else "broken", "denied" if failure == "partial" else "")
    if failure in ("launch", "denied", "timeout", "decode", "partial", "status", "parse"):
        monkeypatch.setattr(verify, "_listening_loopback_ports", ORIGINAL_DISCOVERY)
        monkeypatch.setattr(subprocess, "run", discover)
    elif failure == "nested":
        monkeypatch.setattr(verify, "already_sandboxed", lambda: True)
    else:
        def profile(build, m, **kw):
            if kw.get("loopback"):
                if failure == "render":
                    raise ValueError("render failed")
                (build.parent / "verify-loopback-1.sb").mkdir()
            return real_profile(build, m, **kw)
        monkeypatch.setattr(verify, "profile", profile)
    steps = (replace(STEP, id="before", loopback=False), replace(STEP, expect=65), replace(STEP, id="later"))
    with pytest.raises(RuntimeError, match="check.*loopback"):
        if failure == "resolver":
            verify._run_commands([(s, tmp_path / "verify.sb") for s in steps], {}, tmp_path, tmp_path, "/usr/bin:/bin")
        else:
            execute(tmp_path, steps)
    assert_aborted(tmp_path, launches)
    assert not any(e[0] == "probe" for e in launches)


ORIGINAL_DISCOVERY = verify._listening_loopback_ports


@pytest.mark.scenario("verify-preparation/loopback-setup-fails-closed/sandbox-startup-failure-cannot-satisfy-expected-exit")
@pytest.mark.parametrize("expect", [0, 65])
@pytest.mark.parametrize("failure", [65, 1, -9, OSError("probe launch"), subprocess.TimeoutExpired("probe", 5)])
def test_loopback_sandbox_startup_failure_is_not_command_exit(tmp_path, monkeypatch, launches, expect, failure):
    profile, run = verify.profile, subprocess.run
    monkeypatch.setattr(verify, "profile", lambda *a, **kw: "(malformed" if kw.get("loopback") else profile(*a, **kw))
    def reject(argv, **kw):
        result = run(argv, **kw)
        if argv[0] == "/usr/bin/sandbox-exec":
            assert Path(argv[2]).read_text() == "(malformed"
            kw["stdout"].write(b"probe diagnostic\n")
            if isinstance(failure, Exception):
                raise failure
            result.returncode = failure
        return result
    monkeypatch.setattr(subprocess, "run", reject)
    with pytest.raises(RuntimeError, match="check.*probe"):
        execute(tmp_path, (replace(STEP, id="before", loopback=False), replace(STEP, expect=expect), replace(STEP, id="later")))
    assert_aborted(tmp_path, launches)
    assert "probe diagnostic" in (tmp_path / "check.log").read_text()
    assert [e[0] for e in launches] == ["launch", "cleanup", "snapshot", "probe"]


@pytest.mark.scenario("verify-preparation/loopback-setup-fails-closed/actual-command-outcomes-retain-their-meaning")
@pytest.mark.parametrize("body, expect, code, passed", [("sys.exit(65)", 65, 65, True), ("sys.exit(65)", 0, 65, False),
    ("os.kill(os.getpid(), signal.SIGTERM)", 65, -signal.SIGTERM, False), ("time.sleep(60)", 65, None, False)])
def test_loopback_command_exit_retains_expected_exit_semantics(tmp_path, launches, body, expect, code, passed):
    script = "import os,sys,signal,time; assert os.getcwd()==sys.argv[1]; assert os.environ['CUSTOM']=='literal'; " + body
    step = replace(STEP, run=(sys.executable, "-c", script, "{build}"), env={"CUSTOM": "literal"}, expect=expect, timeout=0.3 if code is None else 5)
    receipt, = execute(tmp_path, (step,))
    assert (receipt.exit, receipt.passed) == (code, passed)
    assert receipt.argv == [sys.executable, "-c", script, str(tmp_path / "build")]
    assert [e[0] for e in launches] == ["snapshot", "probe", "launch", "cleanup"]
    with pytest.raises(ProcessLookupError):
        os.killpg(launches[-1][1], 0)
