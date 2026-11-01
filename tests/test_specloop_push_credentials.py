"""Legacy push behavior stays intact before source-aware push is introduced."""

import json
import os
import subprocess

import pytest

from credential_transport_support import (
    admitted, assert_clean, config_snapshot, credential_service, push_transport, service, template,
)
from nightshift.cli import main
from specloop import cli, loop
from test_specloop_command_json import BRANCH, CHANGE


pytestmark = pytest.mark.scenario("publish-base-sync/no-helper-compatibility/push-remains-unchanged")


def legacy_argv(s):
    ref = f"refs/heads/{BRANCH}"
    return ["git", "-C", str(s.bare), "push", "-q", str(s.remote), f"{ref}:{ref}"]


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
