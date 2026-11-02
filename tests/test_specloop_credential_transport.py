"""Shared credential transport primitives; all helper values are synthetic."""

import os
import subprocess
import traceback

import pytest

from specloop import loop


SENTINEL = b"!printf 'synthetic-\xff-credential'"
HELPERS = (b"duplicate", b"", b"duplicate", b"  whitespace \t", b"!first\nsecond", SENTINEL)


@pytest.fixture
def source(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    config = tmp_path / "global"
    config.write_text('[credential]\nhelper = global-helper\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    return repo


@pytest.mark.scenario("publish-base-sync/source-local-helper-discovery/ordered-values-survive-discovery")
@pytest.mark.parametrize("helpers", [(), (b"",), (b"", b""), HELPERS])
def test_source_local_discovery(source, helpers, monkeypatch):
    included = source / "included"
    included.write_text('[credential]\nhelper = included-helper\n')
    config = source / ".git/config"
    with config.open("ab") as stream:
        stream.write(b'\n[include]\npath = ../included\n[credential "https://example.test"]\nhelper = scoped-helper\n')
    for value in helpers:
        subprocess.run(["git", "-C", str(source), "config", "--local", "--add",
                        "credential.helper", os.fsdecode(value)], check=True, capture_output=True)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "command-helper")
    before, parent = config.read_bytes(), dict(os.environ)
    assert loop._source_credential_helpers(source, timeout=30) == helpers
    assert config.read_bytes() == before and dict(os.environ) == parent


@pytest.mark.scenario("publish-base-sync/source-local-helper-discovery/discovery-failure-blocks-transport")
@pytest.mark.parametrize("failure", [
    (0, b"", b""), (0, SENTINEL, b""), (1, SENTINEL, b""), (1, b"", SENTINEL),
    (2, SENTINEL + b"\0", SENTINEL),
    subprocess.CalledProcessError(2, [SENTINEL], output=SENTINEL, stderr=SENTINEL),
    subprocess.TimeoutExpired([SENTINEL], 5, output=SENTINEL, stderr=SENTINEL),
    OSError(os.fsdecode(SENTINEL)),
])
@pytest.mark.parametrize("timeout", [5, 300])
def test_discovery_failure_is_fixed_and_unchained(monkeypatch, tmp_path, failure, timeout):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv == ["git", "-C", str(tmp_path), "config", "--local", "--null",
                        "--get-all", "credential.helper"]
        assert kwargs == {"capture_output": True, "timeout": min(timeout, 20)}
        if isinstance(failure, Exception):
            raise failure
        return subprocess.CompletedProcess(argv, *failure)
    monkeypatch.setattr(loop.subprocess, "run", run)
    with pytest.raises(RuntimeError) as caught:
        loop._source_credential_helpers(tmp_path, timeout=timeout)
    assert str(caught.value) == "could not read source-local credential helpers"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert len(calls) == 1  # Discovery never attempts transport.
    rendered = "".join(traceback.format_exception(caught.value))
    assert os.fsdecode(SENTINEL) not in rendered and "CalledProcessError:" not in rendered


@pytest.mark.scenario("publish-base-sync/source-local-helper-discovery/discovery-failure-blocks-transport")
def test_malformed_source_config_is_not_disclosed(source):
    config = source / ".git/config"
    with config.open("ab") as stream:
        stream.write(b"\n[" + SENTINEL + b"\n")
    before = config.read_bytes()
    with pytest.raises(RuntimeError) as caught:
        loop._source_credential_helpers(source, timeout=5)
    assert str(caught.value) == "could not read source-local credential helpers"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert config.read_bytes() == before


@pytest.mark.scenario("publish-base-sync/credential-config-environment/helpers-extend-command-config")
@pytest.mark.parametrize("inherited", [None, "0", "2", "02"])
def test_helpers_append_to_child_environment(source, monkeypatch, inherited):
    if inherited is not None:
        monkeypatch.setenv("GIT_CONFIG_COUNT", inherited)
    if inherited in ("2", "02"):
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "inherited-helper")
        monkeypatch.setenv("GIT_CONFIG_KEY_1", "example.empty")
        monkeypatch.setenv("GIT_CONFIG_VALUE_1", "")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setenv("TRANSPORT_TEST_UNRELATED", "preserved")
    parent, before = dict(os.environ), (source / ".git/config").read_bytes()
    env = loop._credential_transport_env(HELPERS)
    count = int(inherited or "0")
    assert env["GIT_CONFIG_COUNT"] == str(count + len(HELPERS))
    for key, value in parent.items():
        if key not in ("GIT_CONFIG_COUNT", "GIT_TERMINAL_PROMPT"):
            assert env[key] == value
    for i, helper in enumerate(HELPERS, count):
        assert env[f"GIT_CONFIG_KEY_{i}"] == "credential.helper"
        assert os.fsencode(env[f"GIT_CONFIG_VALUE_{i}"]) == helper
    argv = ["git", "-C", str(source), "config", "--null", "--get-all", "credential.helper"]
    result = subprocess.run(argv, env=env, capture_output=True, check=True)
    expected = (b"global-helper",) + ((b"inherited-helper",) if count else ()) + HELPERS
    assert result.stdout == b"\0".join(expected) + b"\0"
    assert all(os.fsdecode(h) not in argv for h in HELPERS if h)
    assert env["GIT_TERMINAL_PROMPT"] == "0" and dict(os.environ) == parent
    assert (source / ".git/config").read_bytes() == before


@pytest.mark.scenario("publish-base-sync/credential-config-environment/helpers-extend-command-config")
@pytest.mark.parametrize("active", [
    {"COUNT": value} for value in ("", "-1", "+1", " 1", "1.0", "١", "secret-count", "9" * 5000)
] + [{"COUNT": "1"}, {"COUNT": "1", "KEY_0": "", "VALUE_0": "secret-value"},
     {"COUNT": "1", "KEY_0": "secret-key"},
     {"COUNT": "2", "KEY_0": "example.valid", "VALUE_0": ""}])
def test_invalid_inherited_config_is_value_free(source, monkeypatch, active):
    for key, value in active.items():
        monkeypatch.setenv(f"GIT_CONFIG_{key}", value)
    parent = dict(os.environ)
    with pytest.raises(RuntimeError) as caught:
        loop._credential_transport_env((SENTINEL,))
    assert str(caught.value) == "invalid inherited Git config environment"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert dict(os.environ) == parent
    assert loop._credential_transport_env(()) == parent  # No-helper config is untouched.


@pytest.mark.scenario("publish-base-sync/byte-level-diagnostic-redaction/invalid-utf8-bytes-are-redacted")
@pytest.mark.scenario("publish-base-sync/byte-level-diagnostic-redaction/redaction-precedes-tail-selection")
@pytest.mark.parametrize("exception", [subprocess.CalledProcessError, subprocess.TimeoutExpired])
@pytest.mark.parametrize("case", ["repeated", "boundary", "unicode-tail", "no-stderr", "empty-stderr", "text-stderr"])
def test_byte_first_diagnostics(exception, case, tmp_path, capsys):
    multiline = b"!first-secret\nsecond-secret"
    helpers = (b"", SENTINEL, multiline, b"!first")  # Overlapping patterns need longest first.
    if case == "repeated":
        raw = b"\xfe " + SENTINEL + b" / " + SENTINEL + b" / " + multiline + b" / !first-secret / second-secret"
        expected = "\ufffd " + " / ".join(["[REDACTED]"] * 5)
    elif case == "boundary":
        raw = b"prefix " + SENTINEL + b"." * 390 + b" \n"
        expected = ("prefix [REDACTED]" + "." * 390)[-400:]
        assert SENTINEL not in raw.strip()[-400:]
        assert b"redential'" in raw.strip()[-400:]
    elif case == "unicode-tail":
        raw = SENTINEL + ("é" * 401).encode() + b" \n"
        expected = "é" * 400
    elif case == "no-stderr":
        raw, expected = None, ""
    elif case == "empty-stderr":
        raw, expected = b"", ""
    else:
        # Text cannot be safely matched against the original non-UTF-8 bytes.
        raw, expected = SENTINEL.decode(errors="replace"), ""
    exc = exception(23, [SENTINEL], output=SENTINEL, stderr=raw) if exception is subprocess.CalledProcessError \
        else exception([SENTINEL], 12.5, output=SENTINEL, stderr=raw)
    error = loop._credential_transport_error("fetch refs/heads/main", exc, helpers)
    status = "failed (git exited 23)" if exception is subprocess.CalledProcessError else "timed out after 12.5s"
    assert str(error) == "fetch refs/heads/main " + status + (f": {expected}" if expected else "")
    assert error.__cause__ is None and error.__context__ is None and not vars(error)
    # Exercise the caller's required boundary, including the active raw exception.
    with pytest.raises(RuntimeError) as caught:
        try:
            raise exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise error from None
    rendered = "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert exception.__name__ + ":" not in rendered
    saved = tmp_path / "diagnostic.log"
    saved.write_text(rendered)
    assert saved.read_text() == rendered
    assert capsys.readouterr() == ("", "")
    assert SENTINEL not in rendered.encode("utf-8", errors="surrogateescape")
    assert SENTINEL.decode(errors="replace") not in rendered and os.fsdecode(SENTINEL) not in rendered
    assert "first-secret" not in rendered and "second-secret" not in rendered
