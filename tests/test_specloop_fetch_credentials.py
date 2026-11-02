"""Fetch credentials stay in child environments, including across caller retries."""

import json
import os
import subprocess
import traceback
from dataclasses import asdict

import pytest

from nightshift.cli import main
from specloop import loop, seal
from test_specloop_base_sync import base_env, publish_env, forbid_publication, sh
from test_specloop_base_publication import publication_command, retry_explanation
from test_specloop_credential_transport import HELPERS, SENTINEL


@pytest.fixture
def credentials(publish_env, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("GIT_CONFIG_") and key not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
            monkeypatch.delenv(key)
    return publish_env


def set_helpers(env, helpers):
    subprocess.run(["git", "-C", str(env.src), "config", "--local", "--unset-all",
                    "credential.helper"], check=False, capture_output=True)
    for helper in helpers:
        sh(env.src, "git", "config", "--local", "--add", "credential.helper", os.fsdecode(helper))


def config_snapshot(env):
    return [(env.src / ".git/config").read_bytes(), (env.ctx.repo / "config").read_bytes()]


def assert_clean(env, before, parent):
    assert config_snapshot(env) == before and dict(os.environ) == parent
    result = subprocess.run(["git", "-C", str(env.ctx.repo), "config", "--local", "--get-all",
                             "credential.helper"], capture_output=True)
    assert result.returncode == 1 and result.stdout == result.stderr == b""


def assert_safe(text):
    for helper in (*HELPERS, b"stdout-only-secret"):
        if helper:
            assert helper not in text.encode("utf-8", errors="surrogateescape")
            assert helper.decode(errors="replace") not in text
            assert os.fsdecode(helper) not in text
            assert json.dumps(os.fsdecode(helper))[1:-1] not in text
            assert json.dumps(helper.decode(errors="replace"))[1:-1] not in text
    assert "CalledProcessError:" not in text and "TimeoutExpired:" not in text


@pytest.mark.scenario("publish-base-sync/base-fetch-helper-forwarding/base-fetch-uses-source-helpers")
@pytest.mark.parametrize("helpers", [HELPERS, (b"",)])
@pytest.mark.parametrize("timeout", [20, 300])
def test_base_fetch_uses_source_local_credential_helpers(credentials, monkeypatch, helpers, timeout):
    env, ctx = credentials, credentials.ctx
    set_helpers(env, helpers)
    before, parent = config_snapshot(env), dict(os.environ)
    real_run, calls = subprocess.run, []

    def runner(argv, **kwargs):
        if "fetch" in argv:
            calls.append(argv)
            assert argv == ["git", *seal.SAFE_FLAGS, "-C", str(ctx.repo), "fetch", "--no-tags",
                            "--no-recurse-submodules", "--", str(env.origin),
                            "+refs/heads/release/stable:refs/specloop/publish-base"]
            child = kwargs["env"]
            assert kwargs == dict(capture_output=True, check=True, timeout=timeout, env=child)
            assert child["GIT_TERMINAL_PROMPT"] == "0"
            assert child["GIT_CONFIG_COUNT"] == str(len(helpers))
            probe = real_run(["git", "-C", str(ctx.repo), "config", "--null", "--get-all",
                              "credential.helper"], env=child, capture_output=True, check=True)
            assert probe.stdout == b"\0".join((b"fixture-helper", *helpers)) + b"\0"
            assert all(os.fsdecode(h) not in " ".join(argv) for h in helpers if h)
        else:
            assert "GIT_CONFIG_COUNT" not in kwargs.get("env", {})
        return real_run(argv, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    assert loop._fetch_publish_base(ctx, timeout=timeout) == env.initial
    assert len(calls) == 1
    assert_clean(env, before, parent)


@pytest.mark.scenario("publish-base-sync/no-helper-compatibility/fetch-remains-unchanged")
def test_no_local_helpers_leaves_fetch_unchanged(credentials, monkeypatch):
    env, ctx = credentials, credentials.ctx
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "example.preserved")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "")
    parent, before = dict(os.environ), config_snapshot(env)
    real_run, calls = subprocess.run, []

    def runner(argv, **kwargs):
        if "fetch" in argv:
            calls.append(argv)
            assert argv == ["git", *seal.SAFE_FLAGS, "-C", str(ctx.repo), "fetch", "--no-tags",
                            "--no-recurse-submodules", "--", str(env.origin),
                            "+refs/heads/release/stable:refs/specloop/publish-base"]
            assert kwargs == dict(capture_output=True, text=True, check=True, timeout=20,
                                  env={**parent, "GIT_TERMINAL_PROMPT": "0"})
        return real_run(argv, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    monkeypatch.setattr(loop, "_credential_transport_env", lambda _: pytest.fail("legacy env changed"))
    assert loop._fetch_publish_base(ctx, timeout=20) == env.initial
    assert len(calls) == 1
    assert_clean(env, before, parent)


@pytest.mark.scenario("publish-base-sync/ephemeral-helper-configuration/run-config-stays-credential-free")
@pytest.mark.scenario("publish-base-sync/credential-failure-boundary/failed-transport-diagnostics-are-safe")
@pytest.mark.scenario("publish-base-sync/byte-level-diagnostic-redaction/invalid-utf8-bytes-are-redacted")
@pytest.mark.scenario("publish-base-sync/source-local-helper-discovery/discovery-failure-blocks-transport")
@pytest.mark.scenario("publish-base-sync/base-fetch-helper-forwarding/base-fetch-uses-source-helpers")
@pytest.mark.parametrize("mode", ["driver", "json", "act"])
@pytest.mark.parametrize("failure", ["exit", "timeout", "os-error", "read-exit", "read-timeout", "malformed"])
@pytest.mark.parametrize("with_stderr", [False, True])
def test_run_repo_config_never_gains_credential_helpers(
        credentials, monkeypatch, capfd, caplog, mode, failure, with_stderr):
    env, ctx = credentials, credentials.ctx
    stale = loop._fetch_publish_base(ctx, timeout=300)
    set_helpers(env, HELPERS)
    if mode != "driver":
        ctx.pause("ready_to_publish", detail="retained human checks")
    before_state, head = asdict(ctx.run.state), ctx.head()
    before, parent = config_snapshot(env), dict(os.environ)
    real_run, reads, fetches, notes = subprocess.run, [], [], []
    ctx.notify = lambda message: notes.append(message.text())
    timeout = 300 if mode == "driver" else 20
    failing = True
    expected = HELPERS
    stderr = b"prefix " + SENTINEL + b" / " + b" / ".join(HELPERS) + b" safe-tail"
    stderr = stderr if with_stderr else b""

    def runner(argv, **kwargs):
        discovery = argv[3:] == ["config", "--local", "--null", "--get-all", "credential.helper"]
        if discovery:
            reads.append(argv)
            assert kwargs == dict(capture_output=True, timeout=20)
        if "fetch" in argv:
            fetches.append(argv)
            child = kwargs["env"]
            assert kwargs == dict(capture_output=True, check=True, timeout=timeout, env=child)
            assert tuple(os.fsencode(child[f"GIT_CONFIG_VALUE_{i}"]) for i in range(len(expected))) == expected
        injected = discovery if failure.startswith("read-") or failure == "malformed" else "fetch" in argv
        if failing and injected:
            if failure == "malformed":
                return subprocess.CompletedProcess(argv, 0, SENTINEL, SENTINEL)
            if failure.endswith("timeout"):
                raise subprocess.TimeoutExpired(argv, timeout, output=SENTINEL + b"stdout-only-secret", stderr=stderr)
            if failure == "os-error":
                raise OSError(os.fsdecode(SENTINEL))
            raise subprocess.CalledProcessError(23, argv, output=SENTINEL + b"stdout-only-secret", stderr=stderr)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    with monkeypatch.context() as guard:
        forbid_publication(guard)
        guard.setattr(loop, "_publish_needs_prepare", lambda *a: pytest.fail("stale base used"))
        if mode == "driver":
            assert loop.run(ctx) == "paused"
            detail = ctx.run.state.pause.detail
            assert ctx.run.state.pause.reason == "base_fetch" and ctx.run.state.pause.resume_at == 0
            assert asdict(ctx.run.state) == {**before_state, "status": "paused", "pause": asdict(ctx.run.state.pause)}
            output = capfd.readouterr()
        else:
            assert main(publication_command(mode)) == 1
            output = capfd.readouterr()
            detail = retry_explanation(output, mode)
            ctx.run.reload()
            assert asdict(ctx.run.state) == before_state
            assert "Retry" in detail and "nothing was published" in detail
    assert ctx.head() == head and sh(ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == stale
    discovery_failure = failure.startswith("read-") or failure == "malformed"
    assert len(reads) == 1 and len(fetches) == (0 if discovery_failure else 1)
    expected_detail = ("could not read source-local credential helpers" if discovery_failure else
                       "could not start" if failure == "os-error" else
                       f"timed out after {timeout}s" if failure == "timeout" else "failed (git exited 23)")
    assert expected_detail in detail
    if with_stderr and failure in ("exit", "timeout"):
        assert "[REDACTED]" in detail and "safe-tail" in detail
    with pytest.raises(RuntimeError) as caught:
        loop._fetch_publish_base(ctx, timeout=timeout)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    rendered = "".join(traceback.format_exception(caught.value))
    artifacts = "".join(p.read_text() for p in ctx.run.dir.rglob("*")
                        if p.is_file() and p.suffix in (".json", ".jsonl", ".log"))
    assert_safe(output.out + output.err + detail + rendered + artifacts + caplog.text + "".join(notes))
    assert_clean(env, before, parent)
    # A corrected source helper is discovered on retry, without copying config.
    expected = (b"corrected-helper",)
    set_helpers(env, expected)
    before = config_snapshot(env)
    failing = False
    if mode == "driver":
        ctx.run.state.status, ctx.run.state.pause = "running", None
        ctx.run.save()
        assert loop.run(ctx) == "paused"
        assert ctx.run.state.pause.reason == "ready_to_publish"
    else:
        assert main(publication_command(mode)) == 0
        assert json.loads(capfd.readouterr().out)["outcome"] == ("done" if mode == "act" else "published")
        ctx.run.reload()
        assert ctx.run.state.pause.reason == "human_checks"
    assert len(reads) == 3 and len(fetches) == (1 if discovery_failure else 3)
    assert_clean(env, before, parent)
    artifacts = "".join(p.read_text() for p in ctx.run.dir.rglob("*")
                        if p.is_file() and p.suffix in (".json", ".jsonl", ".log"))
    assert_safe(artifacts + caplog.text + "".join(notes))
    assert "corrected-helper" not in artifacts + caplog.text + "".join(notes)
