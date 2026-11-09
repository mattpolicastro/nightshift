import json
import subprocess

import pytest

from nightshift.cli import main
from specloop import agents, propose
from test_draft_base import repos as base_repos, sh
from test_specloop_command_json import template
from test_specloop_propose import M, draft_evidence_snapshot


@pytest.fixture
def repos(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    return base_repos.__wrapped__(root)


@pytest.mark.scenario("spec-drafting/draft-base/a-just-merged-blocker-is-in-the-base")
@pytest.mark.scenario("spec-drafting/draft-base/the-source-checkout-branch-does-not-matter")
@pytest.mark.scenario("spec-drafting/draft-base/reviewed-tip-remains-authoritative")
@pytest.mark.parametrize("mode", ["upstream", "no-origin", "reviewed", "fetch-failed", "fetch-timeout"])
def test_injected_base_resolution(repos, mode):
    source, local, upstream = repos
    sh(source, "checkout", "-qb", "reviewed")
    sh(source, "commit", "--allow-empty", "-qm", "review")
    if mode == "no-origin":
        sh(source, "remote", "remove", "origin")
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 30
        if "fetch" in args and mode == "fetch-timeout":
            raise subprocess.TimeoutExpired(args, 30)
        if "fetch" in args and mode == "fetch-failed":
            return subprocess.CompletedProcess(args, 1)
        return subprocess.run(args, **kwargs)
    reviewed = "reviewed" if mode == "reviewed" else None
    expected = sh(source, "rev-parse", "reviewed") if reviewed else upstream if mode == "upstream" else local
    assert propose.start_commit(source, "main", reviewed, runner=runner) == expected
    assert any("fetch" in call for call in calls) == (not reviewed)


@pytest.mark.parametrize("step", ["fetch", "rev-parse"])
def test_injected_pause_propagates_without_fallback(tmp_path, step):
    def paused(args, **kwargs):
        if step not in args:
            return subprocess.CompletedProcess(args, 0)
        raise RuntimeError("paused")
    with pytest.raises(RuntimeError, match="paused"):
        propose.start_commit(tmp_path, "main", runner=paused)


@pytest.mark.scenario("spec-drafting/draft-base/existing-feature-on-stale-local-base")
def test_pinned_cli_uses_discovered_tree_after_origin_advances(repos, template, monkeypatch, capfd):
    source, local, _ = repos
    seed = source.parent / "seed"
    feature = seed / "specs/042-add-a-gate/spec.md"
    feature.parent.mkdir(parents=True)
    feature.write_text("**Status**: Draft\n")
    sh(seed, "add", ".")
    sh(seed, "commit", "-qm", "existing feature")
    sh(seed, "push", "-q", "origin", "HEAD:main")
    pin = propose.start_commit(source, "main")
    assert sh(source, "rev-parse", "main") == local
    assert sh(source, "show", f"{pin}:specs/042-add-a-gate/spec.md") == "**Status**: Draft"
    sh(seed, "commit", "--allow-empty", "-qm", "advance")
    sh(seed, "push", "-q", "origin", "HEAD:main")
    path, old_repo = template
    path.write_text(path.read_text().replace(str(old_repo), str(source)).replace('[run]', '[run]\nformat = "speckit"'))
    def agent(call):
        assert sh(call.cwd, "rev-parse", "HEAD") == pin
        assert (call.cwd / "specs/042-add-a-gate/spec.md").read_text() == feature.read_text()
        return agents.Result("ok", {"status": "needs_input", "questions": ["review existing feature"]})
    monkeypatch.setattr(agents, "run", agent)
    monkeypatch.setattr(propose, "start_commit", lambda *a: pytest.fail("second resolution"))
    assert main(["propose", str(path), "--change", "042-add-a-gate", "--idea", "revise",
                 "--base-commit", pin, "--json"]) == 2
    assert json.loads(capfd.readouterr().out)["questions"] == ["review existing feature"]


@pytest.mark.scenario("spec-drafting/draft-base/invalid-or-conflicting-pin-refuses")
@pytest.mark.parametrize("invalid", ["short", "missing", "tree", "tag", "ref", "empty", "conflict"])
def test_bad_pins_preserve_workspace(repos, tmp_path, monkeypatch, invalid):
    source, commit, _ = repos
    sh(source, "tag", "-a", "annotated", "-m", "tag")
    pin = {"short": commit[:12], "missing": "0" * 40, "tree": sh(source, "rev-parse", "HEAD^{tree}"),
           "tag": sh(source, "rev-parse", "annotated"), "ref": "main", "empty": "", "conflict": commit}[invalid]
    out = tmp_path / "draft"
    out.mkdir()
    (out / "sentinel").write_text("retain")
    before = draft_evidence_snapshot(out)
    monkeypatch.setattr(propose, "start_commit", lambda *a: pytest.fail("pin fell back"))
    with pytest.raises((ValueError, RuntimeError)):
        propose.draft(source, "main", M.change, "idea", [], M, out, base_commit=pin,
                      from_branch="main" if invalid == "conflict" else None,
                      agent=lambda _: pytest.fail("agent called"))
    assert draft_evidence_snapshot(out) == before
