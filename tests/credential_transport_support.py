"""Offline push fixtures: real source/run/origin repositories and fake forge I/O."""

import os
import subprocess

import pytest

from specloop import cli
from test_finish_expect_head import service
from test_specloop_act import admitted
from test_specloop_command_json import template


REAL_PUSH = cli._push


@pytest.fixture
def credential_service(tmp_path, monkeypatch, request):
    # Isolate Git before requesting the repository fixture, including its setup.
    for key in tuple(os.environ):
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.delenv(key)
    global_config = tmp_path / "credential-global"
    global_config.write_text("[credential]\nhelper = fixture-helper\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    s = request.getfixturevalue("service")
    # Exercise production push directly, without a permissive _push double.
    monkeypatch.setattr(cli, "_push", REAL_PUSH)
    assert cli.loop._source_credential_helpers(s.source, timeout=20) == ()
    return s


@pytest.fixture
def push_transport(credential_service, monkeypatch):
    s = credential_service
    s.transport_calls, s.transport_error = [], None
    real_run = subprocess.run

    def transport(argv, **kwargs):
        if "push" in argv and str(s.remote) in argv:
            s.transport_calls.append((list(argv), dict(kwargs)))
            if s.transport_error is not None:
                raise s.transport_error
        return real_run(argv, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", transport)
    return s


def config_snapshot(s):
    return [(s.source / ".git/config").read_bytes(), (s.bare / "config").read_bytes()]


def assert_clean(s, before, parent):
    assert config_snapshot(s) == before
    assert dict(os.environ) == parent
    result = subprocess.run(["git", "-C", str(s.bare), "config", "--local", "--get-all",
                             "credential.helper"], capture_output=True)
    assert result.returncode == 1 and result.stdout == result.stderr == b""
