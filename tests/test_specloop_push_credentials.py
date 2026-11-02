"""Source-aware pushes keep credentials ephemeral and legacy behavior intact."""

import json
import os
import subprocess
import traceback
from types import SimpleNamespace

import pytest

from credential_transport_support import (
    admitted, assert_clean, config_snapshot, credential_service, push_transport, service, template,
)
from nightshift.cli import main
from specloop import cli, loop
from test_specloop_command_json import BRANCH, CHANGE
from test_specloop_credential_transport import HELPERS, SENTINEL
from test_specloop_fetch_credentials import assert_safe, set_helpers
from test_specloop_forge_selection import configured
from test_specloop_forge_publication import REQUEST, PATH, FakeGitea, http


def legacy_argv(s):
    ref = f"refs/heads/{BRANCH}"
    return ["git", "-C", str(s.bare), "push", "-q", str(s.remote), f"{ref}:{ref}"]


@pytest.mark.scenario("publish-base-sync/no-helper-compatibility/push-remains-unchanged")
@pytest.mark.parametrize("command", ["low-level", "publish", "finish"])
@pytest.mark.parametrize("indexed_config", [False, True])
def test_no_local_helpers_leaves_push_unchanged(push_transport, monkeypatch, capfd,
                                               command, indexed_config):
    s = push_transport
    if indexed_config:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "example.preserved")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "")
    monkeypatch.setattr(loop, "_credential_transport_env", lambda _: pytest.fail("legacy env changed"))
    before, parent, head = config_snapshot(s), dict(os.environ), s.local_head()
    if command == "low-level":
        cli._push(s.bare, str(s.remote), f"refs/heads/{BRANCH}")
    elif command == "publish":
        s.run.pause("ready_to_publish")
        assert main(["publish", CHANGE, "--json"]) == 0
        assert json.loads(capfd.readouterr().out) == {
            "version": 1, "outcome": "published", "pr": s.url,
        }
        s.run.reload()
        assert s.run.state.pr == s.url and s.run.state.pause.reason == "human_checks"
        assert not s.archives and not s.ready
    else:
        assert main(["finish", CHANGE]) == 0
        s.run.reload()
        assert s.run.state.status == "done" and s.run.state.pause is None
        assert s.archives == [CHANGE] and s.ready == [s.local_head()]
        assert s.local_head() != head
    assert s.transport_calls == [(legacy_argv(s), {"check": True, "timeout": 300})]
    assert s.remote_head() == s.local_head()
    assert_clean(s, before, parent)


@pytest.mark.scenario("publish-base-sync/no-helper-compatibility/push-remains-unchanged")
@pytest.mark.parametrize("failure", ["exit", "timeout", "os-error"])
def test_no_source_push_preserves_original_exception(push_transport, failure):
    s = push_transport
    argv = legacy_argv(s)
    error = {
        "exit": subprocess.CalledProcessError(7, argv, output=b"stdout", stderr=b"stderr"),
        "timeout": subprocess.TimeoutExpired(argv, 300, output=b"stdout", stderr=b"stderr"),
        "os-error": OSError("push could not start"),
    }[failure]
    s.transport_error = error
    before, parent = config_snapshot(s), dict(os.environ)
    with pytest.raises(type(error)) as caught:
        cli._push(s.bare, str(s.remote), f"refs/heads/{BRANCH}")
    assert caught.value is error and caught.value.__cause__ is None
    assert s.transport_calls == [(argv, {"check": True, "timeout": 300})]
    assert_clean(s, before, parent)


@pytest.mark.scenario("publish-base-sync/no-helper-compatibility/push-remains-unchanged")
@pytest.mark.parametrize("command", ["publish", "finish"])
@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_no_helper_command_failure_is_retryable(push_transport, capfd, command, failure):
    s = push_transport
    reason = "ready_to_publish" if command == "publish" else "human_checks"
    s.run.pause(reason)
    before, parent = config_snapshot(s), dict(os.environ)
    saved = (s.run.dir / "state.json").read_bytes()
    error = (subprocess.CalledProcessError(7, legacy_argv(s)) if failure == "exit" else
             subprocess.TimeoutExpired(legacy_argv(s), 300))
    s.transport_error = error
    if command == "publish":
        assert main(["publish", CHANGE, "--json"]) == 1
        assert json.loads(capfd.readouterr().out) == {"version": 1, "error": str(error)}
    else:
        with pytest.raises(type(error)) as caught:
            main(["finish", CHANGE])
        assert caught.value is error
    s.run.reload()
    assert s.run.state.status == "paused" and s.run.state.pause.reason == reason
    assert (s.run.dir / "state.json").read_bytes() == saved
    assert not s.ready
    head = s.local_head()
    s.transport_error = None
    assert main([command, CHANGE]) == 0
    s.run.reload()
    assert s.run.state.status == ("paused" if command == "publish" else "done")
    assert s.archives == ([] if command == "publish" else [CHANGE])
    assert s.ready == ([] if command == "publish" else [head])
    assert s.remote_head() == s.local_head() == head
    assert s.transport_calls == [(legacy_argv(s), {"check": True, "timeout": 300})] * 2
    assert_clean(s, before, parent)


@pytest.mark.scenario("publish-base-sync/no-helper-compatibility/push-remains-unchanged")
@pytest.mark.parametrize("count", ["not-a-count", "-1", "1"])
def test_no_source_push_does_not_validate_inherited_config(credential_service, monkeypatch, count):
    s = credential_service
    monkeypatch.setenv("GIT_CONFIG_COUNT", count)  # Count 1 has no corresponding key/value.
    parent, calls = dict(os.environ), []

    def process(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli.subprocess, "run", process)
    cli._push(s.bare, str(s.remote), f"refs/heads/{BRANCH}")
    assert calls == [(legacy_argv(s), {"check": True, "timeout": 300})]
    assert dict(os.environ) == parent


@pytest.fixture
def helper_push(push_transport, monkeypatch):
    s = push_transport
    s.helpers, s.sources = HELPERS, []
    s.set_helpers = lambda values: set_helpers(SimpleNamespace(src=s.source), values)
    s.set_helpers(s.helpers)
    real_run, real_push = subprocess.run, cli._push

    def push(repo, url, ref, *, source):
        assert source == s.source  # Both production callers must supply it.
        s.sources.append(source)
        return real_push(repo, url, ref, source=source)

    def process(argv, **kw):
        child = kw.get("env", {})
        if "push" in argv and str(s.remote) in argv:
            assert argv == ["git", *loop.seal.SAFE_FLAGS, *legacy_argv(s)[1:]]
            assert kw == dict(check=True, timeout=300, capture_output=True, env=child)
            assert child["GIT_TERMINAL_PROMPT"] == "0"
            assert child["GIT_CONFIG_COUNT"] == str(len(s.helpers))
            probe = real_run(["git", "-C", str(s.bare), "config", "--null", "--get-all",
                              "credential.helper"], env=child, capture_output=True, check=True)
            assert probe.stdout == b"\0".join((b"fixture-helper", *s.helpers)) + b"\0"
            assert all(os.fsdecode(h) not in " ".join(argv) for h in s.helpers if h)
        elif "fetch" not in argv:
            assert not any(os.fsdecode(h) in child.values() for h in s.helpers if h)
        result = real_run(argv, **kw)
        if "push" in argv and str(s.remote) in argv:
            result.stdout = result.stderr = SENTINEL  # Successful output must also stay private.
        return result

    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(cli.subprocess, "run", process)
    return s


@pytest.mark.scenario("publish-base-sync/publish-push-helper-forwarding/publish-uses-source-helpers")
@pytest.mark.scenario("specloop-forge/manifest-forge-selection/github-stays-compatible")
@pytest.mark.scenario("specloop-forge/gitea-draft-publication/new-draft-opens-with-wip-title")
@pytest.mark.parametrize("forge", ["github", "github-host", "gitea"])
@pytest.mark.parametrize("helpers", [HELPERS, (b"",)])
@pytest.mark.parametrize("saved_pr", [False, True])
def test_publish_push_uses_source_local_credential_helpers(
        helper_push, configured, monkeypatch, capfd, forge, helpers, saved_pr):
    s, calls = helper_push, []
    s.helpers = helpers
    s.set_helpers(helpers)
    repository = "github.com/example/repo" if forge == "github-host" else "example/repo"
    fake = FakeGitea("https://forge.example:8443/git")
    fake.repository("example/repo", labels=[])
    if forge == "gitea":
        repository = "forge.example/example/repo"
        origin = "https://forge.example:8443/git/example/repo.git"
        subprocess.run(["git", "-C", str(s.source), "remote", "set-url", "origin", origin], check=True)
        real_run = subprocess.run

        def local_transport(argv, **kw):
            if "push" in argv:
                assert argv == ["git", *loop.seal.SAFE_FLAGS, "-C", str(s.bare), "push", "-q", origin,
                                f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"]
            return real_run([str(s.remote) if a == origin else a for a in argv], **kw)

        monkeypatch.setattr(cli.subprocess, "run", local_transport)
        monkeypatch.setattr(cli.GiteaForge, "_request", REQUEST)
        monkeypatch.setattr(http, "build_opener", lambda: fake)
    s.run.manifest_path.write_text(s.run.manifest_path.read_text().replace('"example/repo"', f'"{repository}"'))

    def gh(*args):
        assert forge != "gitea"
        calls.append(args)
        return "[]" if args[0] == "api" else s.url

    monkeypatch.setattr(cli, "_gh", gh)
    url = "https://forge.example:8443/git/example/repo/pulls/1" if forge == "gitea" else s.url
    s.run.state.pr = url if saved_pr else ""
    s.run.pause("ready_to_publish")
    body = cli._pr_body(cli._ctx(s.run))
    before, parent = config_snapshot(s), dict(os.environ)
    assert main(["publish", CHANGE, "--json"]) == 0
    output = capfd.readouterr()
    assert_safe(output.out + output.err)
    result = json.loads(output.out)
    s.run.reload()
    title = f"{CHANGE}: OpenSpec change via specloop"
    if saved_pr:
        assert not calls and not fake.calls and s.run.state.pr == url
    elif forge == "gitea":
        assert not calls and [c.method for c in fake.calls] == ["GET", "POST"]
        assert fake.calls[1].path == PATH
        assert fake.calls[1].body == dict(title="Draft: " + title, body=body, head=BRANCH, base="main")
        assert s.run.state.pr == "https://forge.example:8443/git/example/repo/pulls/1"
    else:
        host = ("--hostname", "github.com") if forge == "github-host" else ()
        assert calls == [("api", "repos/example/repo/pulls", *host, "--method", "GET", "-f", "state=open",
                          "-f", "base=main", "-f", "per_page=30", "-f", "page=1"),
                         ("pr", "create", "--draft", "-R", repository, "--head", BRANCH,
                          "--base", "main", "--title", title, "--body", body)]
        assert not fake.calls and s.run.state.pr == s.url
    assert result == dict(version=1, outcome="published", pr=s.run.state.pr)
    assert s.run.state.pause.reason == "human_checks" and not s.archives and not s.ready
    assert len(s.transport_calls) == 1 and s.sources == [s.source]
    assert s.remote_head() == s.local_head()
    assert_clean(s, before, parent)


@pytest.mark.scenario("publish-base-sync/finish-push-helper-forwarding/finish-and-retry-use-source-helpers")
@pytest.mark.scenario("publish-base-sync/ephemeral-helper-configuration/run-config-stays-credential-free")
@pytest.mark.scenario("publish-base-sync/credential-failure-boundary/failed-transport-diagnostics-are-safe")
@pytest.mark.scenario("publish-base-sync/byte-level-diagnostic-redaction/invalid-utf8-bytes-are-redacted")
@pytest.mark.parametrize("command", ["publish", "finish"])
@pytest.mark.parametrize("mode", ["direct", "act"])
@pytest.mark.parametrize("failure", ["exit", "timeout", "os-error"])
@pytest.mark.parametrize("with_stderr", [False, True])
def test_run_repo_config_never_gains_credential_helpers(
        helper_push, monkeypatch, capfd, caplog, command, mode, failure, with_stderr):
    s, api = helper_push, []
    reason = "ready_to_publish" if command == "publish" else "human_checks"
    if command == "publish":
        s.run.state.pr = ""
    s.run.pause(reason)
    before, parent = config_snapshot(s), dict(os.environ)
    saved, old_head = (s.run.dir / "state.json").read_bytes(), s.local_head()
    stderr = b"prefix " + b" / ".join(HELPERS) + b" safe-tail" if with_stderr else b""
    stdout = SENTINEL + b"stdout-only-secret"
    s.transport_error = (subprocess.CalledProcessError(23, legacy_argv(s), stdout, stderr) if failure == "exit" else
                         subprocess.TimeoutExpired(legacy_argv(s), 300, stdout, stderr) if failure == "timeout" else
                         OSError(os.fsdecode(SENTINEL)))

    def gh(*args):
        api.append(args)
        return s.gh(*args) if command == "finish" else "[]" if args[0] == "api" else s.url

    monkeypatch.setattr(cli, "_gh", gh)
    argv = (["act", CHANGE, command, "--json"] if mode == "act" else
            [command, CHANGE] + (["--json"] if command == "publish" else []))
    if mode == "direct" and command == "finish":
        with pytest.raises(RuntimeError) as caught:
            main(argv)
        detail = str(caught.value)
    else:
        assert main(argv) == 1
        output = capfd.readouterr()
        detail = json.loads(output.out)["detail" if mode == "act" else "error"]
        assert_safe(output.out + output.err)
    assert f"push refs/heads/{BRANCH} to source origin" in detail
    assert ("failed (git exited 23)" if failure == "exit" else
            "timed out after 300s" if failure == "timeout" else "could not start") in detail
    if with_stderr and failure != "os-error":
        assert "[REDACTED]" in detail and detail.endswith("safe-tail")
    assert not api and not s.ready and s.draft and not s.merged
    s.run.reload()
    assert s.run.state.status == "paused" and s.run.state.pause.reason == reason
    assert (s.run.dir / "state.json").read_bytes() == saved
    head = s.local_head()
    assert (head != old_head) == (command == "finish")
    assert s.archives == ([] if command == "publish" else [CHANGE])
    assert_clean(s, before, parent)
    with pytest.raises(RuntimeError) as caught:
        cli._push(s.bare, str(s.remote), f"refs/heads/{BRANCH}", source=s.source)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    rendered = "".join(traceback.format_exception(caught.value))
    for retry in (False, True):
        if retry:
            s.helpers = (b"corrected-helper",)
            s.set_helpers(s.helpers)
            before = config_snapshot(s)
            s.transport_error = None
            assert main(argv) == 0
        output = capfd.readouterr()
        artifacts = "".join(p.read_text() for p in s.run.dir.rglob("*")
                            if p.is_file() and p.suffix in (".json", ".jsonl", ".log"))
        assert_safe(output.out + output.err + detail + rendered + artifacts + caplog.text)
        assert "corrected-helper" not in output.out + output.err + artifacts + caplog.text
        assert_clean(s, before, parent)
    s.run.reload()
    assert s.run.state.status == ("paused" if command == "publish" else "done")
    assert (s.run.state.pause.reason if s.run.state.pause else None) == ("human_checks" if command == "publish" else None)
    assert s.archives == ([] if command == "publish" else [CHANGE])
    assert s.ready == ([] if command == "publish" else [head])
    assert s.remote_head() == s.local_head() == head and not s.merged
    assert len(s.transport_calls) == 3 and s.sources == [s.source] * 3
