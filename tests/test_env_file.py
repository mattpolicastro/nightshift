"""Parsing `~/.config/nightshift/env`.

Two things went wrong on 2026-08-04 and both live here. The wrapper sources
this file with bash, which strips quotes; the Python parser did not, so a
worker's `GH_TOKEN` carried literal `'` characters the daemon's own did not.
And nothing parsed it for a human at a terminal at all, so `status` reported
`alerts: NOT CONFIGURED` against a correctly-configured webhook and preflight's
negative control probed the interactive login instead of the token under test —
a false pass in the one check written to rule out false passes.
"""

from __future__ import annotations

import os

from nightshift import config


def write(tmp_path, body: str):
    p = tmp_path / "env"
    p.write_text(body)
    return p


def test_single_quotes_are_stripped_because_bash_strips_them(tmp_path):
    p = write(tmp_path, "export GH_TOKEN='github_pat_abc'\n")
    assert config.parse_env_file(p) == {"GH_TOKEN": "github_pat_abc"}


def test_double_quotes_too(tmp_path):
    p = write(tmp_path, 'export NIGHTSHIFT_SLACK_WEBHOOK="https://hooks/x"\n')
    assert config.parse_env_file(p) == {
        "NIGHTSHIFT_SLACK_WEBHOOK": "https://hooks/x"
    }


def test_bare_values_are_left_alone(tmp_path):
    p = write(tmp_path, "export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-plain\n")
    assert config.parse_env_file(p) == {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-plain"
    }


def test_an_interior_apostrophe_survives(tmp_path):
    """Only a matched surrounding pair is a quote; anything else is the value."""
    p = write(tmp_path, "export X='it''s'\n")
    assert config.parse_env_file(p) == {"X": "it''s"}


def test_a_line_without_export_is_not_a_variable(tmp_path):
    """The wrapper sources this file, so only `export` lines reach a worker.

    A bare `GH_TOKEN=...` is invisible to `worker._env()` and equally invisible
    to preflight, which is why it was worth being strict rather than lenient
    here: silently accepting it would make the CLI disagree with the daemon.
    """
    p = write(tmp_path, "GH_TOKEN='x'\n# export Y='z'\nexport OK='y'\n")
    assert config.parse_env_file(p) == {"OK": "y"}


def test_a_missing_file_is_empty_not_an_error(tmp_path):
    assert config.parse_env_file(tmp_path / "nope") == {}


def test_load_injects_into_environ_and_the_file_wins(tmp_path, monkeypatch):
    """File-wins matches `worker._env()`, so preflight checks what workers get."""
    monkeypatch.setenv("GH_TOKEN", "stale-ambient-value")
    p = write(tmp_path, "export GH_TOKEN='from_the_file'\n")

    loaded = config.load_env_file(p)

    assert os.environ["GH_TOKEN"] == "from_the_file"
    assert loaded == ["GH_TOKEN"]
