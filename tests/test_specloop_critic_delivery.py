"""Critic notes cross real proposal serialization and the existing PR renderer."""

import json
import subprocess

import pytest

from nightshift.cli import main

from nightshift import queue, specloop_intake
from nightshift.config import Repo
from specloop import agents, cli, critic, openspec, state


CHANGE = "sample-issue-7-sign-helpers"
BRANCH = f"spec/{CHANGE}"
DIRECTORY = f"openspec/changes/{CHANGE}"
FEASIBILITY_RISKS = ['Clarify "empty" input.\nKeep café examples.', "Timing needs a human check."]
CRITIC_NOTES = ["Keep the helper small.", "Coordinate the production-file change with #21."]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("notes", [
    pytest.param(CRITIC_NOTES, id="non-blocking-notes", marks=pytest.mark.scenario(
        "spec-critic/notes-through-existing-risks/notes-reach-the-pr-body")),
    pytest.param([], id="empty-critic", marks=pytest.mark.scenario(
        "spec-critic/ordered-proposal-gates/clean-draft-receives-the-extra-review")),
])
def test_critic_notes_reach_the_pr_body(tmp_path, monkeypatch, capfd, notes):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    (source / "calc.py").write_text("def sign(x): return 0\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "base")
    base = git(source, "rev-parse", "HEAD")
    template = tmp_path / "template.toml"
    template.write_text(f"""
[run]
repo = {json.dumps(str(source))}
change = "template"
branch = "spec/template"
github = "owner/sample"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "test"
[paths]
allowed = ["calc.py", "tests/**"]
[[verify]]
id = "tests"
run = ["true"]
""")
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    monkeypatch.setattr(cli, "load_env", lambda path: {})
    notifications = []
    monkeypatch.setattr(cli, "notifier", lambda secrets: notifications.append)
    events, writes, reviewed_heads = [], [], []

    def validate(repo, change):
        assert change == CHANGE
        events.append("lint")
        return []

    # Replace the external OpenSpec CLI; retain proposal lint and both reviews.
    monkeypatch.setattr(openspec, "validate", validate)
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"calc/sign/zero": ""})

    def lookup(github, branch):
        assert (github, branch) == ("owner/sample", BRANCH)
        events.append("lookup")
        return {}, []

    monkeypatch.setattr(critic, "open_pr_files", lookup)

    def agent(call):
        if call.access == "write":
            events.append("write")
            writes.append(call.cwd)
            directory = call.cwd / DIRECTORY
            (directory / "specs/calc").mkdir(parents=True, exist_ok=True)
            (directory / "proposal.md").write_text("## Why\nDefine sign at zero.\n")
            (directory / "design.md").write_text("Use a small sign helper.\n")
            (directory / "tasks.md").write_text(
                "## 1. Sign\n\n- [ ] 1.1 Implement sign covering `calc/sign/zero`\n")
            (directory / "specs/calc/spec.md").write_text(
                "## ADDED Requirements\n### Requirement: Sign\nThe helper SHALL return zero.\n"
                "#### Scenario: Zero\n- **WHEN** input is zero\n- **THEN** return zero\n")
            return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})
        assert call.access == "read"
        assert f"{DIRECTORY}/" in call.prompt
        assert (call.cwd / DIRECTORY / "design.md").read_text() == "Use a small sign helper.\n"
        reviewed_heads.append(git(writes[0], "rev-parse", "HEAD"))
        if call.prompt.startswith("# Spec critic of an OpenSpec change"):
            events.append("critic")
            return agents.Result("ok", {"contradictions": [], "risks": notes})
        assert call.prompt.startswith("# Feasibility check")
        events.append("feasibility")
        return agents.Result("ok", {"contradictions": [], "risks": FEASIBILITY_RISKS})

    monkeypatch.setattr(agents, "run", agent)
    code = main(["propose", str(template), "--change", CHANGE, "--branch", BRANCH,
                     "--base", "main", "--idea", "Define sign at zero", "--json"])
    captured = capfd.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    expected_risks = FEASIBILITY_RISKS + [f"Critic: {note}" for note in notes]
    head = git(source, "rev-parse", BRANCH)
    # Exact equality guards the version-1 fields as well as unchanged risk text.
    assert result == {
        "version": 1, "outcome": "drafted", "branch": BRANCH, "commit": head,
        "questions": [], "contradictions": [], "risks": expected_risks, "problems": [],
    }
    assert reviewed_heads == [head, head]
    assert git(source, "rev-parse", f"{head}^") == base
    assert git(source, "branch", "--show-current") == "main"
    assert events == ["write", "lint", "feasibility", "lookup", "critic"]
    assert len(writes) == len(notifications) == 1

    github_calls, git_calls = [], []
    url = "https://github.com/owner/sample/pull/42"

    def github(args):
        github_calls.append(args)
        if args[:2] == ["pr", "create"]:
            return url + "\n"
        if args[:2] == ["pr", "view"]:
            return json.dumps({"state": "OPEN", "url": url})
        if args[:2] == ["api", "repos/owner/sample/issues/7/comments"]:
            return "[[]]"
        assert args[:2] in (["pr", "edit"], ["issue", "comment"], ["issue", "edit"])
        return ""

    def git_runner(args, **kwargs):
        git_calls.append(args)
        assert kwargs == {"capture_output": True, "text": True, "check": False, "timeout": 30}
        return subprocess.CompletedProcess(args, 0, "", "")

    repo = Repo("owner/sample", engine="specloop", manifest=template, base="main")
    specloop_intake._deliver(
        repo, queue.Labels(), 7, CHANGE, {"title": "Sign helpers", "result": result},
        {"spec:draft"}, runner=github, git_runner=git_runner,
    )
    create, = [args for args in github_calls if args[:2] == ["pr", "create"]]
    assert create == [
        "pr", "create", "--repo", "owner/sample", "--head", BRANCH, "--base", "main",
        "--draft", "--title", "Spec: Sign helpers", "--body",
        "Closes #7\n\nFeasibility risks:\n" + "\n".join(f"- {risk}" for risk in expected_risks),
    ]
    assert git_calls == [["git", "-C", str(source), "push", "origin", f"{head}:refs/heads/{BRANCH}"]]
    assert ["pr", "edit", url, "--repo", "owner/sample", "--add-label", "spec:review"] in github_calls
    comment, = [args for args in github_calls if args[:2] == ["issue", "comment"]]
    assert url in comment[-1]
    assert github_calls[-1] == ["issue", "edit", "7", "--repo", "owner/sample",
                               "--remove-label", "spec:draft"]
    assert events == ["write", "lint", "feasibility", "lookup", "critic"]
