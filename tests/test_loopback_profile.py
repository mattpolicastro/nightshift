import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import manifest, verify
from test_specloop_units import MANIFEST

KINDS = ("verify", "prepare", "acceptance", "mutation_step")
ALLOWS = ('(allow network-inbound (local tcp "localhost:*"))\n'
          '(allow network-outbound (remote tcp "localhost:*"))')


@pytest.mark.scenario("verify-preparation/step-loopback-flag/boolean-flags-round-trip")
@pytest.mark.parametrize("overrides", [{}, {"change": "new", "branch": "spec/new"}])
def test_loopback_round_trip(tmp_path, overrides):
    data = dict(MANIFEST)
    for kind in KINDS:
        data[kind] = [{"id": kind + str(i), "run": ["tool", "{build}"], "unknown": 1, **fields}
                      for i, fields in enumerate(({}, {"loopback": False, "env": {}},
                          {"loopback": True, "env": {"X": "{build}/a b"}, "expect": 7, "timeout": 19}))]
    parsed = manifest.parse(data)
    path = tmp_path / "manifest.toml"
    path.write_text("\n".join(f"{manifest._toml_value(k)} = {manifest._toml_value(v)}" for k, v in data.items()))
    effective, stored = manifest.template(path, **overrides)
    path.write_text(stored)
    assert manifest.load(path) == effective == replace(parsed, **overrides)
    for kind in KINDS:
        steps = getattr(parsed, kind)
        assert steps == (manifest.Step(kind + "0", ("tool", "{build}")),
                         manifest.Step(kind + "1", ("tool", "{build}"), 0, 300, {}),
                         manifest.Step(kind + "2", ("tool", "{build}"), 7, 19, {"X": "{build}/a b"}, True))
        assert [s.loopback for s in steps] == [False, False, True]
        assert steps[0].env is not steps[1].env


@pytest.mark.scenario("verify-preparation/step-loopback-flag/invalid-flags-cannot-launch")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", ["true", "false", "", 0, 1, 0.0, None, [], {}, [True]])
def test_invalid_loopback(kind, value, tmp_path, monkeypatch):
    monkeypatch.setattr(verify.subprocess, "Popen", lambda *a, **kw: pytest.fail("invalid flag launched"))
    with pytest.raises(ValueError, match=rf"{kind} '{kind}'.*loopback"):
        manifest.parse({**MANIFEST, kind: [{"id": kind, "run": ["true"], "loopback": value}]})
    with pytest.raises(ValueError, match=rf"'{kind}'.*loopback"):
        verify._run_commands([(manifest.Step(kind, ("true",), loopback=value), tmp_path / "sb")],
                             {}, tmp_path, tmp_path, "/usr/bin:/bin")


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "_credential_denials", lambda m: {"/credential"})
    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **kw: pytest.fail("rendering launched a process"))
    m = replace(manifest.parse(MANIFEST), writable=("/extra",), deny_read=("/private-data",))
    build = tmp_path.resolve() / "build"
    expected = f'''(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{build}") (literal "/extra") (subpath "/extra")
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (regex #"^/dev/fd/"))

(deny file-read* (subpath "/credential") (subpath "/private-data"))
'''
    return build, m, expected


@pytest.mark.scenario("verify-preparation/opted-in-localhost-profile/localhost-tcp-is-explicitly-allowed")
def test_loopback_step_profile_allows_localhost_tcp(policy):
    build, m, expected = policy
    assert verify.profile(build, m, loopback=True, listening_ports=frozenset()) == expected.replace(
        "(deny network*)", "(deny network*)\n" + ALLOWS)


@pytest.mark.scenario("verify-preparation/opted-in-localhost-profile/localhost-tcp-is-explicitly-allowed")
@pytest.mark.parametrize("ports", [None, frozenset({0}), frozenset({65536}), frozenset({-1}),
                                  frozenset({True}), frozenset({1.0}), frozenset({"80"})])
def test_loopback_requires_valid_snapshot(policy, ports):
    build, m, _ = policy
    with pytest.raises(ValueError, match="loopback.*snapshot"):
        verify.profile(build, m, loopback=True, listening_ports=ports)


@pytest.mark.scenario("verify-preparation/opted-in-localhost-profile/default-profiles-retain-network-denial")
def test_steps_without_loopback_keep_deny_network(policy):
    build, m, expected = policy
    assert verify.profile(build, m) == expected
    verify.profile(build, m, loopback=True, listening_ports=frozenset({80}))
    for fields in ({}, {"loopback": False}):
        step = manifest.parse({**MANIFEST, "verify": [{"id": "t", "run": ["true"], **fields}]}).verify[0]
        assert verify.profile(build, m, loopback=step.loopback) == expected


@pytest.mark.scenario("verify-preparation/step-loopback-flag/preparation-retains-its-policy")
@pytest.mark.parametrize("flag", [False, True])
def test_prepare_profile_ignores_loopback(policy, flag):
    build, m, expected = policy
    context = verify.SnapshotContext(build.parent / "cache")
    expected = expected.replace("(deny network*)", "").replace('(literal "/extra") (subpath "/extra")', "")
    expected = expected.replace('\n\n(deny file-read*',
        f'\n(deny file-read* (subpath "/home"))\n(allow file-read-metadata)\n'
        f'(allow file-read* (subpath "{build.parent}") (subpath "{build.parent / "src"}"))\n(deny file-read*')
    expected += f'(deny file-write* (subpath "{context.root / "entries"}"))\n'
    assert verify.profile(build, m, prepare=True, home=Path("/home"), src=build.parent / "src",
                          snapshots=context, loopback=flag) == expected


@pytest.mark.scenario("verify-preparation/step-loopback-flag/preparation-retains-its-policy")
@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("warming", [False, True])
def test_preparation_execution_keeps_policy(tmp_path, monkeypatch, flag, warming):
    def forbidden(*args, **kwargs):
        pytest.fail("preparation attempted listener discovery or a profile probe")

    monkeypatch.setattr(verify, "_listening_loopback_ports", forbidden)
    monkeypatch.setattr(verify.subprocess, "run", forbidden)
    monkeypatch.setattr(verify, "tool_path", lambda: "/usr/bin:/bin")
    profiles = []

    def launch(argv, sb):
        profiles.append(sb)
        assert sb.name == "prepare.sb" and "(deny network*)" not in sb.read_text()
        assert "localhost" not in sb.read_text()
        return argv

    monkeypatch.setattr(verify, "wrap", launch)
    m = replace(manifest.parse(MANIFEST), prepare=(manifest.Step("prep", ("/usr/bin/true",), loopback=flag),))
    if warming:
        def extract(repo, sha, src):
            src.mkdir(parents=True)
            (src / "lock").write_text("dependencies")
        monkeypatch.setattr(verify, "extract", extract)
        m = replace(m, snapshots=(manifest.Snapshot("deps", ("lock",), ("/usr/bin/true",)),))
        verify.warm_snapshots(tmp_path, "a" * 40, m, verify.SnapshotContext(tmp_path / "cache"))
        assert list((tmp_path / "cache/entries").iterdir())
    else:
        receipts = verify.run_extracted(tmp_path, "revision", (), m, tmp_path / "execution")
        assert len(receipts) == 1 and receipts[0].passed
    assert len(profiles) == 1


@pytest.mark.scenario("verify-preparation/launch-time-listener-exclusions/existing-listener-ports-are-denied")
@pytest.mark.parametrize("output, ports", [
    ("p1\nf3\nn127.0.0.1:90\nf4\nn[::1]:80\nf5\nn[::ffff:127.1.2.3]:90\n"
     "p2\nf3\nn*:443\nf4\nn0.0.0.0:443\nf5\nn[::]:8080\nf6\nn127.2.3.4:81\n"
     "p3\nf3\nn192.0.2.1:22\nf4\nn[2001:db8::1]:23\nf5\nn[::ffff:192.0.2.1]:24\n", {80, 81, 90, 443, 8080}),
    ("p1\nf0\nn::1:65535\nf123\nn127.255.255.255:1\n", {1, 65535}),
    ("p305\nf3\nn*:8881\n", {8881}),  # Actual macOS -Fpn output from the review reproduction.
    ("p1\nf3\nn192.0.2.1:80\n", set()), ("", set()),
])
def test_loopback_denies_ports_listening_at_launch(policy, monkeypatch, output, ports):
    build, m, expected = policy
    def discover(argv, **kwargs):
        assert argv == ["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"]
        assert kwargs == dict(capture_output=True, encoding="utf-8", errors="strict", timeout=5)
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(verify.subprocess, "run", discover)
    snapshot = verify._listening_loopback_ports()
    assert snapshot == frozenset(ports)
    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **kw: pytest.fail("profile discovered listeners"))
    denies = "".join(f'\n(deny network-outbound (remote tcp "localhost:{p}"))' for p in sorted(ports))
    assert verify.profile(build, m, loopback=True, listening_ports=snapshot) == expected.replace(
        "(deny network*)", "(deny network*)\n" + ALLOWS + denies)


@pytest.mark.parametrize("code, stdout, stderr", [(1, "", ""), (2, "", ""), (1, "p1\nf3\nn*:80\n", ""),
    (0, "p1\nf3\nn*:80\n", "denied"), (1, "", "denied"), (0, "p1\n", ""),
    *[(0, output, "") for output in ("n*:80\n", "p1\np2\nf3\nn*:80\n", "pX\nf3\nn*:80\n", "p1\nf3\n",
        "f3\nn*:80\n", "p1\nn*:80\n", "p1\nfX\nn*:80\n", "p1\nf-1\nn*:80\n", "p1\nf\nn*:80\n",
        "p1\nf3\nf4\nn*:80\n", "p1\nf3\np2\nf4\nn*:80\n", "p1\nf3\nn*:80\nn*:81\n",
        "p1\nf3\nnlocalhost:80\n", "p1\nf3\nn*:http\n", "p1\nf3\nn*:0\n", "p1\nf3\nn*:65536\n", "p1\nf3\nn*\n",
        "p1\nf3\nn[::1:80\n", "p1\nf3\nn*:80\n\n", "p1\nf3\nn*:80", "p1\nf3\nn[*]:80\n", "p1\nf3\nn[127.0.0.1]:80\n")]])
def test_discovery_empty_or_uncertain(monkeypatch, code, stdout, stderr):
    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, code, stdout, stderr))
    if (code, stdout, stderr) == (1, "", ""):
        assert verify._listening_loopback_ports() == frozenset()
    else:
        with pytest.raises(RuntimeError, match="loopback listener discovery failed"):
            verify._listening_loopback_ports()


@pytest.mark.parametrize("error", [FileNotFoundError("lsof"), PermissionError("denied"),
    subprocess.TimeoutExpired("lsof", 5), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")])
def test_discovery_process_failures(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(verify.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="loopback listener discovery failed"):
        verify._listening_loopback_ports()
