import errno
import json
import socket
import sys

import pytest

from specloop import manifest, verify
from test_specloop_units import MANIFEST


@pytest.fixture
def real_runner(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("real Seatbelt enforcement requires macOS")
    if verify.already_sandboxed():
        pytest.skip("nested sandbox cannot apply loopback policy; no enforcement evidence")

    def run(steps):
        return verify.run_extracted(tmp_path, "revision", steps, manifest.parse(MANIFEST), tmp_path)

    return run


@pytest.fixture(params=["ipv4", "wildcard", "ipv6", "ipv6-wildcard"])
def listener(request, real_runner):
    ipv6 = request.param.startswith("ipv6")
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    host = "::1" if ipv6 else "127.0.0.1"
    bound = ("::" if ipv6 else "0.0.0.0") if "wildcard" in request.param else host
    sock = None
    try:
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            if ipv6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((bound, 0))
        except OSError as exc:
            if ipv6 and exc.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT,
                                      errno.EADDRNOTAVAIL, errno.ENOPROTOOPT):
                pytest.skip(f"IPv6 listener unavailable: {exc}; no IPv6 enforcement evidence")
            raise
        sock.settimeout(3)
        sock.listen(4)
        yield sock, host
    finally:
        if sock is not None:
            sock.close()


def assert_healthy(listener, host):
    with socket.create_connection((host, listener.getsockname()[1]), timeout=3) as client:
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(3)
            client.sendall(b"x")
            assert peer.recv(1) == b"x"


@pytest.mark.scenario("verify-preparation/real-loopback-enforcement-evidence/own-ephemeral-listener-works-and-existing-listener-is-blocked")
def test_loopback_sandbox_allows_own_port_and_blocks_existing_listener(tmp_path, real_runner, listener):
    sock, host = listener
    port = sock.getsockname()[1]
    script = """
import errno, socket, sys
with socket.socket() as own:
    own.settimeout(3)
    own.bind(('127.0.0.1', 0))
    own.listen(1)
    with socket.create_connection(own.getsockname(), timeout=3) as client:
        peer, _ = own.accept()
        with peer:
            peer.settimeout(3)
            client.sendall(b'x')
            assert peer.recv(1) == b'x'
            peer.sendall(b'y')
            assert client.recv(1) == b'y'
try:
    with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3):
        raise AssertionError('pre-existing listener was reachable')
except OSError as exc:
    assert exc.errno in (errno.EPERM, errno.EACCES), repr(exc)
print('own exchange succeeded; existing listener permission denied')
"""
    step = manifest.Step("sockets", (sys.executable, "-c", script, host, str(port)), timeout=15, loopback=True)
    assert_healthy(sock, host)
    try:
        receipt, = real_runner((step,))
        assert receipt.passed, receipt.tail
        assert receipt.argv == list(step.run) and receipt.exit == 0
        policy = (tmp_path / "verify-loopback-0.sb").read_text()
        assert '(deny network*)' in policy
        assert policy.index(f'(deny network-outbound (remote tcp "localhost:{port}"))') > policy.index(
            '(allow network-outbound (remote tcp "localhost:*"))')
    finally:
        assert_healthy(sock, host)


@pytest.mark.scenario("verify-preparation/real-loopback-enforcement-evidence/real-sandbox-rejection-is-a-setup-error")
@pytest.mark.parametrize("malformed", [True, False], ids=["rejected-profile", "genuine-exit-65"])
@pytest.mark.parametrize("expect", [65, 0])
def test_real_profile_rejection_versus_command_exit(tmp_path, monkeypatch, real_runner, malformed, expect):
    original = verify.profile
    monkeypatch.setattr(verify, "profile", lambda *a, **kw:
                        "(malformed" if malformed and kw.get("loopback") else original(*a, **kw))
    script = "from pathlib import Path; Path('sentinel').write_text('ran'); raise SystemExit(65)"
    step = manifest.Step("check", (sys.executable, "-c", script), expect=expect, loopback=True)
    steps = (manifest.Step("before", ("/usr/bin/true",)), step, manifest.Step("later", ("/usr/bin/true",)))
    if malformed:
        with pytest.raises(RuntimeError, match="check.*probe.*65"):
            real_runner(steps)
        assert not (tmp_path / "build/sentinel").exists()
        assert not (tmp_path / "later.log").exists()
        assert [r["id"] for r in json.loads((tmp_path / "receipts.json").read_text())] == ["before"]
        log = (tmp_path / "check.log").read_text()
        assert "sandbox-exec:" in log and "loopback setup/profile probe failed" in log
        assert (tmp_path / "verify-loopback-1.sb").read_text() == "(malformed"
    else:
        receipts = real_runner(steps)
        assert (tmp_path / "build/sentinel").read_text() == "ran"
        assert (receipts[1].exit, receipts[1].passed, receipts[1].argv) == (65, expect == 65, list(step.run))
        assert [r.id for r in receipts] == (["before", "check", "later"] if expect == 65 else ["before", "check"])
