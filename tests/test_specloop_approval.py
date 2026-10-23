"""Approved PR admission through fake GitHub, git and specloop boundaries."""

import json
import subprocess
from dataclasses import replace
from functools import partial

import pytest

from manifest_fixtures import manifest_text

from nightshift import daemon, queue, specloop_client, specloop_intake
from nightshift.config import Config, Repo
from test_engine_routing import DraftGh
from test_specloop_jobs import release_receipt


CHANGE = "sample-issue-7-add-a-gate"
BRANCH = f"spec/{CHANGE}"
HEAD = "a" * 40
CONTRADICTION = "Task 1.2 contradicts the frozen checker.\nEvidence: tools/check.py:63 rejects gates."


class ApprovalGh(DraftGh):
    def __init__(self, labels=("spec:approved", "spec:review")):
        super().__init__(labels)
        self.comments = []
        self.state = "OPEN"
        self.branch = BRANCH
        self.head = HEAD
        self.fork = False
        self.events = []
        self.labels.clear()
        for label in labels:
            self.label_event(label, "labeled")

    def label_event(self, label, event, actor="maintainer"):
        if event == "labeled":
            if label in self.labels:
                return
            self.labels.add(label)
        else:
            if label not in self.labels:
                return
            self.labels.remove(label)
        self.events.append({"id": len(self.events) + 1, "event": event, "label": {"name": label},
                            "actor": {"login": actor}})

    def __call__(self, args):
        repository = self.pr_url.split("github.com/", 1)[1].split("/pull/", 1)[0]
        if args == ["api", f"repos/{repository}"]:
            self.calls.append(args)
            return json.dumps({"owner": {"login": "owner", "type": "User"}})
        if args[0] == "api" and args[1].startswith(f"repos/{repository}/collaborators/"):
            self.calls.append(args)
            assert args[2:] == ["--include"]
            status = 204 if args[1].rsplit("/", 1)[1] == "maintainer" else 404
            return f"HTTP/2.0 {status} Status\r\n\r\n"
        if args[0] == "api" and args[1].endswith("/issues/8/events"):
            self.calls.append(args)
            assert args[-2:] == ["--paginate", "--slurp"]
            return json.dumps([self.events[:1], self.events[1:]])
        if args[:2] == ["issue", "list"]:
            self.calls.append(args)
            return "[]"
        if args[:2] == ["pr", "list"]:
            self.calls.append(args)
            assert "--label" in args and "--state" in args
            return json.dumps([{"number": 8}] if args[args.index("--label") + 1] in self.labels else [])
        if args[:2] == ["pr", "view"]:
            self.calls.append(args)
            return json.dumps({"state": self.state, "labels": [{"name": label} for label in self.labels],
                               "headRefName": self.branch, "headRefOid": self.head,
                               "isCrossRepository": self.fork, "url": self.pr_url})
        if args[:2] == ["pr", "comment"]:
            self.calls.append(args)
            self.comments.append({"id": len(self.comments) + 1, "body": args[-1]})
            return ""
        if args[:2] == ["pr", "edit"]:
            self.calls.append(args)
            for i, arg in enumerate(args):
                if arg == "--remove-label":
                    self.label_event(args[i + 1], "unlabeled")
                elif arg == "--add-label":
                    self.label_event(args[i + 1], "labeled")
            return ""
        return super().__call__(args)


class Commands:
    def __init__(self, outcome="admitted"):
        self.result = {"version": 1, "outcome": outcome, "head": HEAD,
                       "reasons": [CONTRADICTION] if outcome == "refused" else [], "risks": []}
        self.runs = []
        self.calls = []
        self.status_error = False
        self.fetch_error = False
        self.lost_admission = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        expected = {"capture_output": True, "text": True, "check": False}
        if args[0] == "git":
            expected["timeout"] = 30
        assert kwargs == expected
        if args[0] == "git":
            assert args[3] in ("fetch", "rev-parse")
            return subprocess.CompletedProcess(args, int(self.fetch_error), HEAD, "fetch failed")
        if args == ["specloop", "status", "--json"]:
            result = {"version": 1, "runs": self.runs}
            if self.status_error:
                result = {"version": 1, "error": "status unavailable"}
            return subprocess.CompletedProcess(args, int(self.status_error), json.dumps(result), "")
        assert args[:2] == ["specloop", "admit"]
        if self.result["outcome"] == "admitted":
            self.runs.append({"change": args[args.index("--change") + 1],
                              "pr": args[args.index("--pr") + 1], "status": "running"})
        if self.lost_admission:
            self.lost_admission = False
            raise OSError("admission response lost")
        return subprocess.CompletedProcess(args, int(self.result["outcome"] == "refused"),
                                           json.dumps(self.result), "")

    @property
    def admissions(self):
        return [args for args in self.calls if args[:2] == ["specloop", "admit"]]


@pytest.fixture
def repo(tmp_path):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    return Repo("owner/sample", engine="specloop", manifest=manifest)


def poll(repo, tmp_path, gh, commands, labels=queue.Labels()):
    return specloop_intake.admit_pending(repo, labels, tmp_path, runner=gh,
                                       specloop_runner=commands, git_runner=commands)


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.real_trust
def test_approved_spec_admitted_through_daemon(repo, tmp_path, monkeypatch):
    gh, commands = ApprovalGh(), Commands()
    template = repo.manifest.read_bytes()
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(specloop_intake.subprocess, "run", commands)
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not health.failures
    assert repo.manifest.read_bytes() == template
    assert commands.admissions == [["specloop", "admit", str(repo.manifest), "--change", CHANGE,
                                    "--branch", BRANCH, "--head", HEAD, "--pr", gh.pr_url, "--json"]]
    assert commands.runs[0]["pr"] == gh.pr_url
    assert commands.calls[:3] == [
        ["specloop", "status", "--json"],
        ["git", "-C", str(tmp_path / "source"), "fetch", "origin", f"+refs/heads/{BRANCH}:refs/nightshift/admit/{CHANGE}"],
        ["git", "-C", str(tmp_path / "source"), "rev-parse", f"refs/nightshift/admit/{CHANGE}"],
    ]
    assert len(gh.comments) == 1
    assert "build has started" in gh.comments[0]["body"]
    assert f"<!-- nightshift:admit:{CHANGE}:1:{HEAD}:admitted -->" in gh.comments[0]["body"]
    assert gh.comments[0]["body"].startswith(queue.SELF_COMMENT_PREFIX)
    assert gh.labels == set()
    assert [c[:3] for c in gh.calls if c[:2] == ["pr", "comment"]] == [["pr", "comment", "8"]]


@pytest.mark.scenario("issue-intake/approval-admits/refused-admission-explained")
@pytest.mark.real_trust
@pytest.mark.parametrize("reason", [CONTRADICTION, {
    "problem": "Task 1.2 contradicts the frozen checker.",
    "evidence": "tools/check.py:63 rejects gates.",
}])
def test_refused_admission_explained(repo, tmp_path, reason):
    gh, commands = ApprovalGh(), Commands("refused")
    template = repo.manifest.read_bytes()
    commands.result["reasons"] = [reason]
    poll(repo, tmp_path, gh, commands)
    poll(repo, tmp_path, gh, commands)
    assert len(commands.admissions) == 1
    assert repo.manifest.read_bytes() == template
    assert commands.runs == []
    assert len(gh.comments) == 1
    details = reason.values() if isinstance(reason, dict) else [reason]
    assert all(detail in gh.comments[0]["body"] for detail in details)
    assert "Admission refused" in gh.comments[0]["body"]
    assert gh.labels == {"spec:review", "needs-human"}
    assert [c[:3] for c in gh.calls if c[:2] == ["pr", "comment"]] == [["pr", "comment", "8"]]


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.parametrize("name", ["widget.js", "Widget.JS", "widget_js", "_widget"])
def test_repository_names_preserved_by_drafting_can_be_admitted(repo, tmp_path, name):
    repo = replace(repo, name=f"owner/{name}")
    gh, commands = ApprovalGh(), Commands()
    change = f"{name}-issue-7-add-a-gate"
    gh.branch = f"spec/{change}"
    gh.pr_url = f"https://github.com/{repo.name}/pull/8"

    poll(repo, tmp_path, gh, commands)

    assert commands.admissions == [["specloop", "admit", str(repo.manifest), "--change", change,
                                    "--branch", gh.branch, "--head", HEAD, "--pr", gh.pr_url, "--json"]]
    assert commands.runs == [{"change": change, "pr": gh.pr_url, "status": "running"}]
    assert "build has started" in gh.comments[0]["body"]
    assert f"<!-- nightshift:admit:{change}:1:{HEAD}:admitted -->" in gh.comments[0]["body"]
    assert gh.labels == set()


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
def test_rebased_spec_admits_the_reviewed_tip(repo, tmp_path):
    """Use local Git repositories so a real non-fast-forward fetch is required."""
    source, remote = tmp_path / "source", tmp_path / "remote.git"

    def git(directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), "-c", "user.name=Test",
             "-c", "user.email=test@example.com", *args],
            input="", capture_output=True, text=True, check=True,
        ).stdout.strip()

    source.mkdir()
    git(source, "init", "-q", "--bare")
    tree = git(source, "mktree")
    base = git(source, "commit-tree", tree, "-m", "base")
    drafted = git(source, "commit-tree", tree, "-p", base, "-m", "original draft")
    git(source, "update-ref", f"refs/heads/{BRANCH}", drafted)
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    reviewed = git(remote, "commit-tree", tree, "-p", base, "-m", "rebased spec")
    git(remote, "update-ref", f"refs/heads/{BRANCH}", reviewed)
    git(source, "remote", "add", "origin", str(remote))
    assert drafted != reviewed

    gh, commands = ApprovalGh(), Commands()
    gh.head = reviewed
    commands.result["head"] = reviewed

    def cli(args, **kwargs):
        if args[:2] == ["specloop", "admit"]:
            assert git(source, "rev-parse", f"refs/nightshift/admit/{CHANGE}") == reviewed
            assert git(source, "rev-parse", f"refs/heads/{BRANCH}") == drafted
            assert args[args.index("--head") + 1] == reviewed
        return commands(args, **kwargs)

    specloop_intake.admit_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                 specloop_runner=cli, git_runner=subprocess.run)

    assert len(commands.admissions) == 1
    assert commands.runs[0]["pr"] == gh.pr_url
    assert "build has started" in gh.comments[0]["body"]
    assert gh.labels == set()


@pytest.mark.scenario("config-resilience/health-gates-new-work-only/saved-work-survives-a-hold")
@pytest.mark.parametrize("outcome", ["admitted", "refused"])
@pytest.mark.parametrize("operation,after_write", [
    (("pr", "comment"), False), (("pr", "comment"), True),
    (("pr", "edit"), False), (("pr", "edit"), True),
])
def test_restart_finishes_admission_delivery(repo, tmp_path, outcome, operation, after_write):
    gh, commands = ApprovalGh(), Commands(outcome)

    def interrupted(args):
        if tuple(args[:2]) == operation:
            if after_write:
                gh(args)
            raise queue.GhError("response lost", transient=True)
        return gh(args)

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    if outcome == "admitted":
        repo.manifest.unlink()
        gh.head = "b" * 40
    poll(repo, tmp_path, gh, commands)
    assert len(commands.admissions) == 1
    assert len(gh.comments) == 1
    assert gh.labels == (set() if outcome == "admitted" else {"spec:review", "needs-human"})


@pytest.mark.scenario("config-resilience/health-gates-new-work-only/saved-work-survives-a-hold")
def test_lost_admission_response_uses_status_before_retry(repo, tmp_path, monkeypatch):
    gh, commands = ApprovalGh(), Commands()
    commands.lost_admission = True
    with pytest.raises(specloop_client.SpecloopError):
        poll(repo, tmp_path, gh, commands)
    repo.manifest.unlink()
    monkeypatch.setattr(specloop_intake, "manifest_readiness",
                        lambda *a: pytest.fail("committed admission read enrollment"))
    # The build may have moved the PR since the reviewed admission committed.
    gh.head = "b" * 40
    poll(repo, tmp_path, gh, commands)
    assert len(commands.admissions) == 1
    assert len(gh.comments) == 1
    assert "build has started" in gh.comments[0]["body"]


@pytest.mark.parametrize("failure", ["github", "events", "status", "fetch", "changed_tip"])
def test_unavailable_state_never_admits(repo, tmp_path, monkeypatch, failure):
    gh, commands = ApprovalGh(), Commands()
    commands.status_error = failure == "status"
    commands.fetch_error = failure == "fetch"
    if failure == "changed_tip":
        gh.head = "b" * 40

    def runner(args):
        if failure == "github" and args[:2] == ["pr", "view"]:
            raise queue.GhError("HTTP 503", transient=True)
        if failure == "events" and args[0] == "api" and args[1].endswith("/events"):
            raise queue.GhError("HTTP 503", transient=True)
        return gh(args)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(specloop_intake.subprocess, "run", commands)
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=runner, specloop_runner=commands)
    assert health.failures == {repo.name: 1}
    assert not commands.admissions
    assert not gh.comments
    assert gh.labels == {"spec:review", "spec:approved"}


@pytest.mark.scenario("issue-intake/released-item-intake/ineligible-released-item-waits")
@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("field,value", [
    ("state", "CLOSED"), ("branch", "feature/other"), ("fork", True),
    ("branch", "spec/../outside"), ("branch", "spec/widget..js-issue-7"),
    ("branch", "spec/widget.lock"), ("branch", "spec/widget."),
    ("labels", {"spec:approved", "needs-human"}), ("labels", {"spec:approved", "agent:revise"}),
])
def test_ineligible_pr_is_not_admitted(repo, tmp_path, field, value, released):
    gh, commands = ApprovalGh(), Commands()
    if released:
        release_receipt(tmp_path, tmp_path / "admissions/owner__sample/8.json",
                        {"change": CHANGE, "head": HEAD, "approval_event": gh.events[0]["id"]})
    setattr(gh, field, value)
    poll(repo, tmp_path, gh, commands)
    assert not commands.calls
    assert not gh.comments


def test_existing_run_for_other_pr_is_explained(repo, tmp_path):
    gh, commands = ApprovalGh(), Commands()
    commands.runs = [{"change": CHANGE, "pr": "https://github.com/other/repo/pull/1"}]
    poll(repo, tmp_path, gh, commands)
    assert not commands.admissions
    assert commands.runs[0]["pr"] in gh.comments[0]["body"]
    assert "needs-human" in gh.labels
    assert "build has started" not in gh.comments[0]["body"]


def test_custom_labels_and_reapproval(repo, tmp_path):
    labels = queue.Labels(spec_approved="approved", spec_review="review", needs_human="human")
    gh, commands = ApprovalGh(("approved", "review")), Commands("refused")
    poll(repo, tmp_path, gh, commands, labels)
    assert gh.labels == {"review", "human"}
    gh.label_event("human", "unlabeled")
    gh.label_event("approved", "labeled")
    commands.result.update(outcome="admitted", reasons=[])
    poll(repo, tmp_path, gh, commands, labels)
    assert len(commands.admissions) == 2
    assert len(gh.comments) == 2
    assert CONTRADICTION in gh.comments[0]["body"]
    assert "build has started" in gh.comments[1]["body"]
    assert CONTRADICTION not in gh.comments[1]["body"]
    assert gh.labels == set()


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/approval-admits/refused-admission-explained")
def test_reapproval_after_lost_refusal_label_response(repo, tmp_path):
    gh, commands = ApprovalGh(), Commands("refused")

    def interrupted(args):
        result = gh(args)
        if args[:2] == ["pr", "edit"]:
            raise queue.GhError("label write response lost", transient=True)
        return result

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    receipt_path = tmp_path / "admissions/owner__sample/8.json"
    refused = json.loads(receipt_path.read_text())
    assert not refused.get("delivered")
    assert gh.labels == {"spec:review", "needs-human"}
    assert CONTRADICTION in gh.comments[0]["body"]

    # The human fixes the trusted manifest, without changing the reviewed PR
    # tip or waiting for another daemon poll, and requests admission again.
    repo.manifest.write_text(repo.manifest.read_text() + '\n[scope]\nallow = ["tools/check.py"]\n')
    commands.result.update(outcome="admitted", reasons=[])
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:approved", "labeled")
    approval_id = gh.events[-1]["id"]
    gh.label_event("unrelated", "labeled")
    gh.label_event("unrelated", "unlabeled")
    assert approval_id != refused["approval_event"]
    assert gh.head == refused["head"] == HEAD

    retry_start = len(commands.calls)
    poll(repo, tmp_path, gh, commands)
    poll(repo, tmp_path, gh, commands)

    assert len(commands.admissions) == 2
    assert commands.calls[retry_start] == ["specloop", "status", "--json"]
    assert commands.runs == [{"change": CHANGE, "pr": gh.pr_url, "status": "running"}]
    admitted = json.loads(receipt_path.read_text())
    assert admitted["approval_event"] == approval_id
    assert admitted["delivered"]
    assert len(gh.comments) == 2
    assert CONTRADICTION in gh.comments[0]["body"]
    assert "build has started" in gh.comments[1]["body"]
    assert CONTRADICTION not in gh.comments[1]["body"]
    assert gh.labels == set()


@pytest.mark.scenario("issue-intake/approval-admits/refused-admission-explained")
@pytest.mark.parametrize("outcome", ["refused", "admitted"])
@pytest.mark.parametrize("lost_label_response", [False, True])
def test_reapproval_posts_new_comment_and_retries_only_that_round(
    repo, tmp_path, outcome, lost_label_response,
):
    gh, commands = ApprovalGh(), Commands("refused")

    def interrupted_label(args):
        result = gh(args)
        if args[:2] == ["pr", "edit"]:
            raise queue.GhError("label response lost", transient=True)
        return result

    if lost_label_response:
        with pytest.raises(queue.GhError):
            poll(repo, tmp_path, interrupted_label, commands)
    else:
        poll(repo, tmp_path, gh, commands)
    original = gh.comments[0].copy()
    assert CONTRADICTION in original["body"]
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:approved", "labeled")
    # No head or evidence change is required for a new approval round.
    commands.result["outcome"] = outcome

    def interrupted_comment(args):
        result = gh(args)
        if args[:2] == ["pr", "comment"] or "PATCH" in args:
            raise queue.GhError("comment response lost", transient=True)
        return result

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted_comment, commands)
    assert gh.comments[0] == original
    assert len(gh.comments) == 2
    new_comment = gh.comments[1].copy()
    assert new_comment["body"].split("<!--")[-1] != original["body"].split("<!--")[-1]
    if outcome == "refused":
        assert CONTRADICTION in new_comment["body"]
        assert "Admission refused" in new_comment["body"]
    else:
        assert "build has started" in new_comment["body"]
        assert CONTRADICTION not in new_comment["body"]

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted_comment, commands)
    poll(repo, tmp_path, gh, commands)
    poll(repo, tmp_path, gh, commands)
    assert len(commands.admissions) == 2
    assert gh.comments == [original, new_comment]
    assert [c[1] for c in gh.calls if "PATCH" in c] == [
        f"repos/{repo.name}/issues/comments/{new_comment['id']}",
    ] * 2
    assert gh.labels == (set() if outcome == "admitted" else {"spec:review", "needs-human"})


@pytest.mark.parametrize("history", [[], [{
    "id": 3, "event": "unlabeled", "label": {"name": "spec:approved"},
}]])
def test_unconfirmed_approval_event_leaves_pr_for_retry(repo, tmp_path, history):
    gh, commands = ApprovalGh(), Commands()
    gh.events = history
    with pytest.raises(RuntimeError, match="could not confirm"):
        poll(repo, tmp_path, gh, commands)
    assert not commands.admissions
    assert not gh.comments
    assert gh.labels == {"spec:approved", "spec:review"}


@pytest.mark.parametrize("outcome", ["admitted", "refused"])
def test_comment_create_response_loss_reconciles_inside_gh(repo, tmp_path, monkeypatch, outcome):
    gh, commands = ApprovalGh(), Commands(outcome)
    creates = []

    def run(args, **kwargs):
        assert args[0] == "gh"
        command = args[1:]
        if command[:2] == ["pr", "comment"]:
            creates.append(command)
            gh(command)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        return subprocess.CompletedProcess(args, 0, gh(command), "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    poll(repo, tmp_path, partial(queue._gh, sleep=lambda _: None), commands)
    assert len(creates) == len(gh.comments) == 1
    assert len(commands.admissions) == 1
    assert "spec:approved" not in gh.labels


def test_pause_and_worker_routing_prevent_admission(repo, tmp_path, monkeypatch):
    gh, commands = ApprovalGh(), Commands()
    assert poll(Repo("owner/worker"), tmp_path, gh, commands) == []
    assert not gh.calls
    pause = tmp_path / "pause"
    pause.touch()
    monkeypatch.setattr(daemon, "PAUSE_FILE", pause)
    monkeypatch.setattr(queue, "_gh", lambda *a: pytest.fail("GitHub polled while paused"))
    monkeypatch.setattr(specloop_client, "call", lambda *a, **k: pytest.fail("CLI ran while paused"))
    daemon.loop(Config(repos=[repo]), {}, once=True)


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/released-item-intake/recovery-precedes-replacement")
@pytest.mark.parametrize("released", [False, True])
def test_approval_does_not_cover_a_revision_pushed_after_it(repo, tmp_path, released):
    class Moving(Commands):  # git reports whatever the PR's head now is
        def __call__(self, args, **kwargs):
            result = super().__call__(args, **kwargs)
            if args[0] == "git":
                result.stdout = gh.head
            return result

    gh, commands = ApprovalGh(labels=("spec:approved", "spec:review", "agent:revise")), Moving()
    poll(repo, tmp_path, gh, commands)  # approved alongside agent:revise: skipped, head recorded
    if released:
        release_receipt(tmp_path, tmp_path / "admissions/owner__sample/8.json",
                        {"change": CHANGE, "head": HEAD, "approval_event": gh.events[0]["id"]})
    gh.head = "b" * 40  # the revision lands and agent:revise is removed
    gh.label_event("agent:revise", "unlabeled")
    poll(repo, tmp_path, gh, commands)
    assert commands.admissions == [] and "spec:approved" not in gh.labels
    assert "changed after it was approved" in gh.comments[-1]["body"]
    gh.label_event("spec:approved", "labeled")  # a new approval, of the revision
    poll(repo, tmp_path, gh, commands)
    assert len(commands.admissions) == 1 and commands.admissions[0][commands.admissions[0].index("--head") + 1] == "b" * 40
