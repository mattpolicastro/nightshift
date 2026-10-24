"""Build-stage PR revisions through injected GitHub and CLI boundaries."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text
from revision_result_fixtures import revision_success

from nightshift import daemon, queue, specloop_intake, specloop_job
from nightshift.config import Config, Repo
from test_specloop_spec_revision import Commands, RevisionGh, CHANGE, HEAD, REVISED, TIP_DATE, seed_consumed


class BuildCommands(Commands):
    def __init__(self, gh):
        super().__init__(gh)
        self.runs = [{"change": CHANGE, "pr": gh.pr_url}]
        self.result = {"version": 1, "outcome": "revised", "commit": REVISED}
        self._success = deepcopy(self.result)
        self._emitted_success_json = json.dumps(self.result, sort_keys=True)
        self.feedback = []
        self.revise_label = "agent:revise"

    def __call__(self, args, **kwargs):
        if args[:2] != ["specloop", "revise"]:
            return super().__call__(args, **kwargs)
        self.calls.append(args)
        assert args[2] == CHANGE and args[3] == "--feedback" and args[-1] == "--json"
        assert args[5:7] == ["--feedback-format", "json"]
        assert self.revise_label in self.gh.labels
        self.feedback.append(Path(args[4]).read_text())
        # Rebind only intact successes. Deliberate invalid-result mutations bypass
        # the builder, and earlier returned payloads retain their original key.
        if json.dumps(self.result, sort_keys=True) in (
                json.dumps(self._success, sort_keys=True), self._emitted_success_json):
            self.result = revision_success(args, self._success)
            self._emitted_success_json = json.dumps(self.result, sort_keys=True)
        return subprocess.CompletedProcess(args, int(self.result.get("outcome") == "refused"),
                                           json.dumps(self.result), "")


@pytest.fixture
def setup(tmp_path):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    repo = Repo("owner/sample", engine="specloop", manifest=manifest)
    gh = RevisionGh()
    gh.label_event("spec:review", "unlabeled")
    gh.feedback.pop(1)
    gh.inline = [
        {"id": 1, "author_association": "COLLABORATOR", "user": {"login": "reviewer"}, "path": "src/gate.py", "line": 12,
         "body": "Handle empty input.", "created_at": "2026-10-20T13:00:00Z"},
        {"id": 2, "author_association": "COLLABORATOR", "user": {"login": "reviewer"}, "path": "tests/test_gate.py", "line": 42,
         "body": "Cover the empty case.\n\nInclude a regression assertion.",
         "created_at": "2026-10-20T14:00:00Z"},
        {"id": 3, "author_association": "COLLABORATOR", "user": {"login": "reviewer"}, "path": "old.py", "line": 1,
         "body": "Already addressed.", "created_at": TIP_DATE},
    ]
    seed_consumed(tmp_path, ("conversation:101", "inline:3"))
    return repo, gh, BuildCommands(gh)


def poll(setup, tmp_path):
    repo, gh, commands = setup
    return specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                        specloop_runner=commands, git_runner=commands)


@pytest.mark.scenario("pr-revision/build-stage-revision/review-comments-become-tasks")
def test_inline_feedback_queued_through_daemon(setup, tmp_path, monkeypatch):
    repo, gh, commands = setup
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not health.failures
    assert [c[1] for c in commands.calls] == ["status", "revise"]
    feedback, = commands.feedback
    assert json.loads(feedback) == [
        "**reviewer on `src/gate.py:12`:**\n\nHandle empty input.",
        "**reviewer on `tests/test_gate.py:42`:**\n\nCover the empty case.\n\n"
        "Include a regression assertion."
    ]
    assert gh.labels == set() and gh.head == HEAD
    assert len(gh.comments) == 1 and REVISED in gh.comments[0]["body"]
    receipt = json.loads((tmp_path / "revisions" / "owner__sample" / "8.json").read_text())
    assert receipt["result"]["commit"] == REVISED and receipt["delivered"]


@pytest.mark.scenario("pr-revision/no-instruction-no-revision/empty-revise-refused")
@pytest.mark.parametrize("admitted", [False, True])
def test_empty_feedback_refused_without_any_cli(setup, tmp_path, admitted):
    _, gh, commands = setup
    commands.runs = commands.runs if admitted else []
    gh.inline = gh.inline[2:]  # Only consumed feedback and a self comment remain.
    for _ in range(2):
        poll(setup, tmp_path)
    assert not commands.calls
    assert gh.head == HEAD and "agent:revise" not in gh.labels
    comment, = gh.comments
    assert "nothing to revise" in comment["body"] and "no unconsumed review feedback" in comment["body"]
    assert "branch tip" not in comment["body"]


def test_lost_comment_response_does_not_queue_twice(setup, tmp_path):
    _, gh, commands = setup
    gh.fail_comment = True
    with pytest.raises(RuntimeError, match="response lost"):
        poll(setup, tmp_path)
    assert "agent:revise" in gh.labels and len(commands.feedback) == 1
    poll(setup, tmp_path)
    assert len(commands.feedback) == len(gh.comments) == 1
    assert "agent:revise" not in gh.labels
    gh.label_event("agent:revise", "labeled")
    poll(setup, tmp_path)
    assert len(commands.feedback) == 1 and len(gh.comments) == 2
    assert [c[1] for c in commands.calls] == ["status", "revise"]


@pytest.mark.parametrize("write_landed", [False, True])
def test_label_failure_does_not_queue_twice(setup, tmp_path, write_landed):
    repo, gh, commands = setup

    def fail_removal(args):
        if args[:2] == ["pr", "edit"] and "--remove-label" in args:
            if write_landed:
                gh(args)
            raise RuntimeError("label response lost")
        return gh(args)

    with pytest.raises(RuntimeError, match="label response lost"):
        specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=fail_removal,
                                      specloop_runner=commands)
    poll(setup, tmp_path)
    assert len(commands.feedback) == len(gh.comments) == 1
    assert "agent:revise" not in gh.labels


def test_feedback_outage_marks_health_without_refusing_empty(setup, tmp_path, monkeypatch):
    repo, gh, commands = setup
    gh.feedback_error = True
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.failures and not commands.calls and not gh.comments
    assert "agent:revise" in gh.labels


@pytest.mark.parametrize("failure", ["feedback", "status", "invalid_outcome", "missing_commit"])
def test_failures_keep_request_armed(setup, tmp_path, failure):
    _, gh, commands = setup
    if failure == "feedback":
        gh.feedback_error = True
    elif failure == "status":
        commands.status_error = True
    elif failure == "invalid_outcome":
        commands.result["outcome"] = "admitted"
    else:
        del commands.result["commit"]
    with pytest.raises(RuntimeError):
        poll(setup, tmp_path)
    assert "agent:revise" in gh.labels and not gh.comments
    assert not commands.proposals and gh.head == HEAD


@pytest.mark.scenario("pr-revision/keyed-result-acknowledgement/refusal-and-replacement-have-distinct-comments")
def test_refused_revision_explained_and_retried_after_human(setup, tmp_path):
    from test_revision_feedback_recovery import restart, tick

    _, gh, commands = setup
    commands.result = {"version": 1, "outcome": "refused", "reasons": ["Run is busy."]}
    gh.fail_comment = True
    with pytest.raises(RuntimeError, match="response lost"):
        poll(setup, tmp_path)
    tick(setup, tmp_path, module=restart())
    path = tmp_path / "revisions/owner__sample/8.json"
    refused = json.loads(path.read_text())
    refusal_comment = deepcopy(gh.comments[0])
    assert gh.labels == {"agent:revise", "needs-human"}
    assert "Run is busy." in gh.comments[0]["body"]
    poll(setup, tmp_path)
    assert len(commands.feedback) == 1
    gh.label_event("needs-human", "unlabeled")
    commands.result = {"version": 1, "outcome": "revised", "commit": REVISED}
    gh.fail_comment = True
    with pytest.raises(RuntimeError, match="response lost"):
        poll(setup, tmp_path)
    poll(setup, tmp_path)
    assert len(commands.feedback) == 2 and not gh.labels
    succeeded = json.loads(path.read_text())
    assert refused["event"] == succeeded["event"]
    assert refused["round_id"] != succeeded["round_id"]
    assert refused["consumed_feedback_ids"] == ["conversation:101", "inline:3"]
    assert succeeded["consumed_feedback_ids"] == ["conversation:101", "inline:1", "inline:2", "inline:3"]
    for _ in range(2):
        tick(setup, tmp_path, module=restart())
    assert len(commands.feedback) == len(gh.comments) == 2
    assert gh.comments[0] == refusal_comment and REVISED in gh.comments[1]["body"]
    assert REVISED not in refusal_comment["body"]
    for receipt, comment in zip((refused, succeeded), gh.comments):
        marker = f"<!-- nightshift:revise:{receipt['change']}:{receipt['round_id']} -->"
        assert comment["body"].endswith(marker)
        assert sum(marker in entry["body"] for entry in gh.comments) == 1


def test_another_pr_cannot_steer_admitted_change(setup, tmp_path):
    _, gh, commands = setup
    commands.runs[0]["pr"] = "https://github.com/another/repo/pull/8"
    assert poll(setup, tmp_path) == []
    assert not commands.feedback and not gh.comments and "agent:revise" in gh.labels


def test_background_revision_waits_for_landed_commit(setup, tmp_path, monkeypatch):
    repo, gh, commands = setup
    jobs = []

    def start(path, receipt, args, **kwargs):
        jobs.append(args)
        assert Path(args[3]).read_text().count("**reviewer on") == 2
        receipt["job"] = "pending"
        (path.parent / f"{path.stem}.jobs" / receipt["job"]).mkdir(parents=True, exist_ok=True)
        specloop_job.save(path, receipt)

    monkeypatch.setattr(specloop_job, "start", start)
    monkeypatch.setattr(specloop_job, "_collect_locked", lambda *a: None)
    pending = []
    for _ in range(2):
        specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                      status_runner=commands, on_pending=pending.append)
    assert len(jobs) == 1 and jobs[0][:2] == ["revise", CHANGE]
    assert pending == [8, 8] and not gh.comments and "agent:revise" in gh.labels
    expected = revision_success(jobs[0], commands.result)
    monkeypatch.setattr(specloop_job, "_collect_locked", lambda *a: expected)
    poll(setup, tmp_path)
    assert json.loads((tmp_path / "revisions/owner__sample/8.json").read_text())["result"] == expected
    assert not commands.feedback and not gh.labels
    assert REVISED in gh.comments[0]["body"]
