"""Drafts start from the newest base, whatever branch the source checkout is on."""

import subprocess

import pytest

from specloop.propose import start_commit
from specloop.seal import IDENTITY, git


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**IDENTITY, "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    sh(tmp_path, "clone", "-q", str(origin), str(seed))
    sh(seed, "commit", "-q", "--allow-empty", "-m", "A")
    sh(seed, "push", "-q", "origin", "HEAD:main")
    source = tmp_path / "source"
    sh(tmp_path, "clone", "-q", str(origin), str(source))
    a = sh(source, "rev-parse", "HEAD")
    # A blocker merges upstream after the source checkout last pulled.
    sh(seed, "commit", "-q", "--allow-empty", "-m", "B")
    sh(seed, "push", "-q", "origin", "HEAD:main")
    return source, a, sh(seed, "rev-parse", "HEAD")


@pytest.mark.scenario("spec-drafting/draft-base/a-just-merged-blocker-is-in-the-base")
def test_draft_starts_from_the_fetched_origin_base(repos):
    source, a, b = repos
    assert sh(source, "rev-parse", "main") == a
    assert start_commit(source, "main") == b


@pytest.mark.scenario("spec-drafting/draft-base/the-source-checkout-branch-does-not-matter")
def test_draft_base_ignores_the_source_checkouts_current_branch(repos):
    source, a, b = repos
    sh(source, "checkout", "-q", "-b", "feature")
    assert start_commit(source, "main") == b


@pytest.mark.scenario("spec-drafting/draft-base/the-source-checkout-branch-does-not-matter")
def test_draft_base_falls_back_to_the_local_base_without_an_origin(tmp_path):
    source = tmp_path / "solo"
    sh(tmp_path, "init", "-q", "-b", "main", str(source))
    sh(source, "commit", "-q", "--allow-empty", "-m", "only")
    sh(source, "checkout", "-q", "-b", "elsewhere")
    assert start_commit(source, "main") == sh(source, "rev-parse", "main")


def test_reviewed_branch_tip_is_used_as_given(repos):
    source, a, b = repos
    sh(source, "checkout", "-q", "-b", "spec/x")
    sh(source, "commit", "-q", "--allow-empty", "-m", "edit")
    assert start_commit(source, "main", from_branch="spec/x") == sh(source, "rev-parse", "spec/x")


def test_unknown_base_is_refused(tmp_path):
    source = tmp_path / "solo"
    sh(tmp_path, "init", "-q", "-b", "main", str(source))
    sh(source, "commit", "-q", "--allow-empty", "-m", "only")
    with pytest.raises(RuntimeError, match="not a commit"):
        start_commit(source, "trunk")
