"""Showing a finished phase in Paseo, without letting Paseo run anything.

Dispatching THROUGH Paseo was measured on 2026-09-05 and rejected: `paseo run`
cannot pass `--allowedTools`/`--disallowedTools`, its agents park on permission
prompts rather than hard-denying, and its persisted transcript has no `result`
event. Reporting INTO it costs none of that — Nightshift keeps `claude -p`, its
flags and its telemetry, and Paseo gets a read-only copy.

Everything here is best effort. A phase that RAN is worth strictly more than a
phase that is visible, so nothing in this path may affect a task's outcome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nightshift import mirror as mirror_mod

SESSION = "f53d2600-12d7-4215-9d7f-58ad3941b4d0"


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """Stand in for `~/.claude/projects`, which Paseo's daemon reads."""
    d = tmp_path / "claude-projects"
    monkeypatch.setattr(mirror_mod, "DEFAULT_PROJECTS", d)
    return d


@pytest.fixture
def worktree(tmp_path):
    d = tmp_path / "wt" / "sandbox-40"
    d.mkdir(parents=True)
    return d


def transcript_at(path: Path, worktree: Path, session=SESSION) -> Path:
    """A streamed transcript, stderr line and all."""
    path.write_text(
        "[claude-code:unrecognized_model] {\"model\":\"qwen3-coder-next\"}\n"
        + json.dumps({
            "type": "system", "subtype": "init",
            "session_id": session, "cwd": str(worktree),
        }) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"
    )
    return path


def session_file(root: Path, worktree: Path, session=SESSION) -> Path:
    """Where a worker's isolated CLAUDE_CONFIG_DIR keeps its session."""
    slug = str(worktree).replace("/", "-")
    d = root / "evo" / "projects" / slug
    d.mkdir(parents=True)
    f = d / f"{session}.jsonl"
    f.write_text("{}\n")
    return f


def test_a_finished_phase_is_linked_and_imported(tmp_path, projects, worktree):
    calls: list[list[str]] = []
    root = tmp_path / "nsconfig"
    source = session_file(root, worktree)
    t = transcript_at(tmp_path / "t.jsonl", worktree)

    note = mirror_mod.mirror(
        t, repo="matt/sandbox", issue=40, phase="implement",
        config_root=root,
        runner=lambda args: (calls.append(args), '{"agentId": "914fa75c-1"}')[1],
    )

    # The symlink is what lets Paseo's daemon see a session that lives in the
    # worker's ISOLATED config dir — the isolation stays intact.
    linked = projects / source.parent.name / source.name
    assert linked.is_symlink() and linked.resolve() == source.resolve()

    args = calls[0]
    assert args[:3] == ["paseo", "import", SESSION]
    assert "--label" in args and "nightshift_issue=40" in args
    assert "nightshift_phase=implement" in args
    assert args[args.index("--cwd") + 1] == str(worktree)
    assert "914fa75c" in note


def test_a_torn_down_worktree_is_reported_not_imported(tmp_path, projects, worktree):
    """`paseo import` requires the cwd to exist, and the daemon removes
    worktrees on teardown — which is why this runs before that."""
    root = tmp_path / "nsconfig"
    session_file(root, worktree)
    t = transcript_at(tmp_path / "t.jsonl", worktree)
    for child in sorted(worktree.parent.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()

    called = []
    note = mirror_mod.mirror(
        t, repo="matt/sandbox", issue=40, phase="implement",
        config_root=root, runner=lambda args: called.append(args) or "{}",
    )

    assert "too late to import" in note
    assert called == [], "it must not call paseo with a cwd that is gone"


def test_the_stderr_line_does_not_hide_the_session_id(tmp_path, projects, worktree):
    """`worker._run` merges stderr into the stream, so line one is usually the
    `unrecognized_model` warning rather than JSON."""
    root = tmp_path / "nsconfig"
    session_file(root, worktree)
    t = transcript_at(tmp_path / "t.jsonl", worktree)

    assert mirror_mod._session_of(t) == (SESSION, str(worktree))


def test_a_missing_session_file_is_reported_not_raised(tmp_path, projects, worktree):
    t = transcript_at(tmp_path / "t.jsonl", worktree)
    note = mirror_mod.mirror(
        t, repo="matt/sandbox", issue=40, phase="review",
        config_root=tmp_path / "empty", runner=lambda args: "{}",
    )
    assert "not on disk" in note


def test_paseo_being_absent_is_not_a_failure(tmp_path, projects, worktree):
    """Most machines do not run Paseo. A display feature must never be
    something a task trips over."""
    root = tmp_path / "nsconfig"
    session_file(root, worktree)
    t = transcript_at(tmp_path / "t.jsonl", worktree)

    def missing(args):
        raise FileNotFoundError("paseo")

    note = mirror_mod.mirror(
        t, repo="matt/sandbox", issue=40, phase="implement",
        config_root=root, runner=missing,
    )
    assert "paseo import failed" in note


def test_non_json_preamble_is_skipped(tmp_path, projects, worktree):
    """`--json` is not pure JSON: paseo prints `Created workspace …` first."""
    root = tmp_path / "nsconfig"
    session_file(root, worktree)
    t = transcript_at(tmp_path / "t.jsonl", worktree)

    note = mirror_mod.mirror(
        t, repo="matt/sandbox", issue=40, phase="implement", config_root=root,
        runner=lambda args: 'Created workspace wks_1\nTip: pass --workspace\n{"agentId":"abc12345"}',
    )
    assert "abc12345" in note


def test_no_transcript_means_nothing_to_mirror(tmp_path):
    assert mirror_mod.mirror(None, repo="r", issue=1, phase="implement") == ""


def test_mirroring_is_off_unless_asked_for():
    from nightshift.config import Config
    assert Config(repos=[]).mirror_to_paseo is False
