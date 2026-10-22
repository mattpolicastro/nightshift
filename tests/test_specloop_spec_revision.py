"""Spec review routing with injected GitHub, git and CLI boundaries."""

import json
import subprocess

import pytest

from nightshift import daemon, queue, specloop_intake, specloop_job
from nightshift.config import Config, Repo
from test_specloop_approval import ApprovalGh, BRANCH, CHANGE, HEAD
from test_specloop_intake import idea_file


REVISED = "b" * 40
TIP_DATE = "2026-10-20T12:00:00Z"


class RevisionGh(ApprovalGh):
    def __init__(self):
        super().__init__(("spec:review", "agent:revise"))
        self.feedback_error = False
        self.inline = []
        self.reviews = []
        self.fail_comment = False
        self.fail_needs_human = False
        self.feedback = [
            {"author": {"login": "reviewer"}, "body": "Old feedback", "createdAt": "2026-10-19T12:00:00Z"},
            {"author": {"login": "reviewer"}, "body": "Handle empty input", "createdAt": "2026-10-20T13:00:00Z"},
            {"author": {"login": "bot"}, "body": queue.SELF_COMMENT_PREFIX + " Draft ready",
             "createdAt": "2026-10-20T13:00:00Z"},
        ]
        for comment in self.feedback:
            comment["authorAssociation"] = "COLLABORATOR"

    def __call__(self, args):
        if args[:2] == ["pr", "view"] and args[-1] == "comments,reviews":
            self.calls.append(args)
            if self.feedback_error:
                raise RuntimeError("feedback unavailable")
            return json.dumps({"comments": self.feedback, "reviews": self.reviews})
        if args[:2] == ["pr", "view"]:
            return json.dumps({**json.loads(super().__call__(args)), "title": "Spec: Add a gate", "body": "Closes #7"})
        if args[0] == "api" and "/commits/" in args[1]:
            self.calls.append(args)
            return json.dumps({"commit": {"committer": {"date": TIP_DATE}}})
        if args[0] == "api" and args[1].endswith("/pulls/8/comments"):
            self.calls.append(args)
            pages = [self.inline[i:i + 30] for i in range(0, len(self.inline), 30)] or [[]]
            # Match gh's output: separate JSON arrays unless --slurp is used.
            return json.dumps(pages) if "--slurp" in args else "\n".join(map(json.dumps, pages))
        if args[:2] == ["pr", "comment"] and self.fail_comment:
            self.fail_comment = False
            super().__call__(args)  # The write landed, but its response was lost.
            raise RuntimeError("comment response lost")
        if args[:2] == ["pr", "edit"] and args[-2:] == ["--add-label", "needs-human"] and self.fail_needs_human:
            self.fail_needs_human = False
            super().__call__(args)
            raise RuntimeError("label response lost")
        return super().__call__(args)


class Commands:
    def __init__(self, gh):
        self.gh = gh
        self.calls = []
        self.runs = []
        self.push_error = False
        self.status_error = False
        self.fetch_error = False
        self.fetch_head = HEAD
        self.result = {"version": 1, "outcome": "drafted", "commit": REVISED, "branch": BRANCH}

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        code, out, err = 0, "", ""
        if args[0] == "git":
            assert kwargs["timeout"] == 30
            if args[3] == "rev-parse":
                out = self.fetch_head
            elif args[3] == "push":
                assert args[4:] == ["origin", f"{REVISED}:refs/heads/{BRANCH}"]
                if self.push_error:
                    code, err = 1, "push rejected"
                else:
                    self.gh.head = REVISED
            else:
                assert args[3] == "fetch"
                if self.fetch_error:
                    code, err = 1, "remote unavailable"
        elif args[1] == "status":
            out = json.dumps({"version": 1, "runs": self.runs} if not self.status_error else
                             {"version": 1, "error": "status unavailable"})
        else:
            assert args[1] == "propose"
            out = json.dumps(self.result)
        # Honor subprocess.run's output and failure semantics so callers must
        # decode git output and handle unsuccessful commands correctly.
        if not kwargs.get("text", False):
            out, err = out.encode(), err.encode()
        result = subprocess.CompletedProcess(args, code, out, err)
        if kwargs.get("check", False):
            result.check_returncode()
        return result

    @property
    def proposals(self):
        return [c for c in self.calls if c[:2] == ["specloop", "propose"]]


@pytest.fixture
def setup(tmp_path):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(f'[run]\nrepo = {json.dumps(str(tmp_path / "source"))}\n')
    repo = Repo("owner/sample", engine="specloop", manifest=manifest)
    gh = RevisionGh()
    commands = Commands(gh)
    specloop_job.save(tmp_path / "proposals" / "owner__sample" / "7.json",
                      {"change": CHANGE, "idea": "Add a gate\n\nThe original issue and comments."})
    return repo, gh, commands


def poll(setup, tmp_path, **kwargs):
    repo, gh, commands = setup
    return specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                        specloop_runner=commands, git_runner=commands, **kwargs)


@pytest.mark.scenario("pr-revision/spec-stage-revision/spec-revised-from-review")
@pytest.mark.parametrize("inline_count", [0, 1, 31, 61])
def test_spec_revised_from_review_through_daemon(setup, tmp_path, monkeypatch, inline_count):
    repo, gh, commands = setup
    gh.inline = [
        {"user": {"login": "reviewer"}, "path": "specs/gate/spec.md", "line": i + 1,
         "body": f"Clarify gate case {i + 1}.", "created_at": "2026-10-20T13:00:00Z"}
        for i in range(inline_count)
    ]
    if inline_count:
        # Filtering still applies after flattening, including on the last page.
        gh.inline.append({"user": {"login": "reviewer"}, "body": "Old inline feedback",
                          "created_at": "2026-10-19T12:00:00Z"})
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    monkeypatch.setattr(specloop_intake.subprocess, "run", commands)
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not health.failures
    proposal, = commands.proposals
    assert proposal[:8] == ["specloop", "propose", str(repo.manifest), "--change", CHANGE,
                            "--branch", BRANCH, "--from-branch"]
    assert proposal[8] == f"refs/nightshift/revise/{CHANGE}"
    idea = idea_file(proposal).read_text(encoding="utf-8")
    assert "The original issue and comments." in idea and "Handle empty input" in idea
    assert "Old feedback" not in idea and "Draft ready" not in idea
    assert gh.head == REVISED and gh.labels == {"spec:review"}
    comment, = gh.comments
    assert "Handle empty input" in comment["body"] and REVISED in comment["body"]
    expected = [f"**reviewer on `specs/gate/spec.md:{i + 1}`:**\n\nClarify gate case {i + 1}."
                for i in range(inline_count)]
    for text in (idea, comment["body"]):
        assert "Old inline feedback" not in text
        for block in expected:
            assert text.count(block) == 1
        positions = [text.index(block) for block in expected]
        assert positions == sorted(positions)
    inline_call, = [c for c in gh.calls if c[0] == "api" and c[1].endswith("/pulls/8/comments")]
    assert inline_call[-2:] == ["--paginate", "--slurp"]
    assert f"<!-- nightshift:revise:{CHANGE}:2 -->" in comment["body"]
    push = next(i for i, c in enumerate(commands.calls) if c[0] == "git" and c[3] == "push")
    assert commands.calls[push - 1] == proposal


@pytest.mark.real_trust
@pytest.mark.scenario("issue-intake/file-based-drafting-input/spec-revision-feedback")
@pytest.mark.parametrize("original_receipt", [True, False])
def test_large_spec_feedback_uses_idea_file(setup, tmp_path, original_receipt):
    repo, gh, commands = setup
    original_path = tmp_path / "proposals" / "owner__sample" / "7.json"
    if original_receipt:
        original = json.loads(original_path.read_text())["idea"]
    else:
        original_path.unlink()
        original = "Spec: Add a gate\n\nCloses #7"
    large = "BEGIN REVIEW λ\n\n" + "Preserve every case 🦉\n" * 60_000 + "END REVIEW"
    assert len(large.encode("utf-8")) >= 1024 * 1024
    for comment in gh.feedback:
        comment["authorAssociation"] = "COLLABORATOR"
    gh.feedback[1]["body"] = large
    gh.feedback.append({"author": {"login": "outsider"}, "authorAssociation": "NONE",
                        "body": "Untrusted instruction", "createdAt": "2026-10-20T14:00:00Z"})
    gh.inline = [
        {"user": {"login": "reviewer"}, "author_association": "MEMBER",
         "path": "specs/gate/spec.md", "line": 9, "body": "Keep blank lines.\n\nAnd Unicode: café.",
         "created_at": "2026-10-20T14:00:00Z"},
        {"user": {"login": "outsider"}, "author_association": "NONE",
         "body": "Untrusted inline instruction", "created_at": "2026-10-20T14:00:00Z"},
    ]
    feedback = (f"**reviewer:**\n\n{large}\n\n---\n\n"
                "**reviewer on `specs/gate/spec.md:9`:**\n\nKeep blank lines.\n\nAnd Unicode: café.")
    expected = original + "\n\n## New PR review feedback\n\n" + feedback

    def run(args, **kwargs):
        if args[:2] == ["specloop", "propose"]:
            payload = idea_file(args)
            assert payload.parent == (tmp_path / "revisions" / "owner__sample" / "8.jobs").resolve()
            assert payload.read_bytes() == expected.encode("utf-8")
            assert args == ["specloop", "propose", str(repo.manifest), "--change", CHANGE,
                            "--branch", BRANCH, "--from-branch", f"refs/nightshift/revise/{CHANGE}",
                            "--idea-file", str(payload), "--json"]
            assert all("BEGIN REVIEW" not in arg and original not in arg for arg in args)
        return commands(args, **kwargs)

    for _ in range(2):
        specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                      specloop_runner=run, git_runner=commands)
    proposal, = commands.proposals
    assert idea_file(proposal).read_bytes() == expected.encode("utf-8")
    assert gh.head == REVISED and gh.labels == {"spec:review"}
    comment, = gh.comments
    assert feedback in comment["body"] and REVISED in comment["body"]
    assert "Old feedback" not in comment["body"] and "Untrusted" not in comment["body"]
    assert not any(call[:2] == ["pr", "create"] for call in gh.calls)
    assert all(call[2] == "8" for call in gh.calls if call[:2] in (["pr", "edit"], ["pr", "comment"]))
    receipt = json.loads((tmp_path / "revisions" / "owner__sample" / "8.json").read_text())
    assert receipt["delivered"] is True


@pytest.mark.scenario("issue-intake/durable-drafting-payload/later-round-preserves-earlier-input")
def test_delivery_retry_does_not_redraft_or_duplicate_comment(setup, tmp_path):
    _, gh, commands = setup
    gh.fail_comment = True
    with pytest.raises(RuntimeError, match="response lost"):
        poll(setup, tmp_path)
    earlier = idea_file(commands.proposals[0])
    saved = earlier.read_bytes()
    assert gh.head == REVISED and "agent:revise" in gh.labels
    poll(setup, tmp_path)
    assert len(commands.proposals) == len(gh.comments) == 1
    assert gh.labels == {"spec:review"}
    assert list(earlier.parent.glob("idea-*.txt")) == [earlier]
    assert saved == ("Add a gate\n\nThe original issue and comments."
                     "\n\n## New PR review feedback\n\n**reviewer:**\n\nHandle empty input").encode("utf-8")
    gh.feedback.append({"author": {"login": "reviewer"}, "body": "Second round: café.\n\nKeep it complete.",
                        "createdAt": "2026-10-20T14:00:00Z"})
    gh.label_event("agent:revise", "labeled")
    commands.fetch_head = REVISED
    poll(setup, tmp_path)
    assert len(commands.proposals) == len(gh.comments) == 2
    later = idea_file(commands.proposals[-1])
    assert later != earlier and later.parent == earlier.parent
    assert later.read_bytes() == saved + (
        "\n\n---\n\n**reviewer:**\n\nSecond round: café.\n\nKeep it complete."
    ).encode("utf-8")
    assert earlier.read_bytes() == saved
    assert set(earlier.parent.glob("idea-*.txt")) == {earlier, later}
    assert gh.head == REVISED and gh.labels == {"spec:review"}
    assert "Second round: café." in gh.comments[-1]["body"]


@pytest.mark.parametrize("failure", ["feedback", "status", "fetch", "push"])
def test_failures_preserve_revision_label(setup, tmp_path, failure):
    _, gh, commands = setup
    if failure == "feedback":
        gh.feedback_error = True
    elif failure == "status":
        commands.status_error = True
    elif failure == "fetch":
        commands.fetch_head = "c" * 40
    else:
        commands.push_error = True
    with pytest.raises(RuntimeError):
        poll(setup, tmp_path)
    assert gh.head == HEAD and "agent:revise" in gh.labels and not gh.comments
    assert len(commands.proposals) == (1 if failure == "push" else 0)
    if failure == "push":
        commands.push_error = False
        poll(setup, tmp_path)
        assert len(commands.proposals) == 1 and gh.head == REVISED


def test_fetch_failure_explained_and_retried(setup, tmp_path):
    _, gh, commands = setup
    commands.fetch_error = True
    with pytest.raises(RuntimeError, match=f"could not fetch {BRANCH}: remote unavailable"):
        poll(setup, tmp_path)
    assert gh.head == HEAD and gh.labels == {"spec:review", "agent:revise"}
    assert not gh.comments and not commands.proposals
    assert [c[3] for c in commands.calls if c[0] == "git"] == ["fetch"]
    assert not (tmp_path / "revisions" / "owner__sample" / "8.json").exists()

    commands.fetch_error = False
    poll(setup, tmp_path)
    assert len(commands.proposals) == len(gh.comments) == 1
    assert gh.head == REVISED and gh.labels == {"spec:review"}


@pytest.mark.parametrize("state,revise_label", [
    ("CLOSED", True), ("MERGED", True), ("OPEN", False),
])
def test_revision_rechecks_state_and_label_after_listing(setup, tmp_path, state, revise_label):
    repo, gh, commands = setup

    def changed_after_listing(args):
        response = gh(args)
        if args[:2] == ["pr", "list"]:
            assert json.loads(response) == [{"number": 8}]
            gh.state = state
            if not revise_label:
                gh.label_event("agent:revise", "unlabeled")
        return response

    assert specloop_intake.revise_pending(
        repo, queue.Labels(), tmp_path, runner=changed_after_listing,
        specloop_runner=commands, git_runner=commands,
    ) == []
    assert [c[:2] for c in gh.calls] == [["pr", "list"], ["pr", "view"]]
    assert not commands.calls and not gh.comments and gh.head == HEAD
    assert gh.state == state
    assert gh.labels == ({"spec:review", "agent:revise"} if revise_label else {"spec:review"})
    assert not (tmp_path / "revisions").exists()


def test_admitted_change_is_not_redrafted(setup, tmp_path):
    _, gh, commands = setup
    commands.runs = [{"change": CHANGE}]
    assert poll(setup, tmp_path) == []
    assert not commands.proposals and not gh.comments and "agent:revise" in gh.labels


def test_empty_review_does_not_redraft(setup, tmp_path):
    _, gh, commands = setup
    gh.feedback.pop(1)
    poll(setup, tmp_path)
    assert not commands.proposals and gh.head == HEAD
    assert "nothing to revise" in gh.comments[0]["body"]
    assert gh.labels == {"spec:review"}


def test_rejected_revision_explained_without_push(setup, tmp_path):
    _, gh, commands = setup
    commands.result = {"version": 1, "outcome": "needs_input", "questions": ["Mono or stereo?"]}
    poll(setup, tmp_path)
    assert gh.head == HEAD and "needs-human" in gh.labels
    assert "agent:revise" in gh.labels and "Mono or stereo?" in gh.comments[0]["body"]
    gh.labels.remove("needs-human")
    commands.result = {"version": 1, "outcome": "drafted", "commit": REVISED, "branch": BRANCH}
    poll(setup, tmp_path)
    assert len(commands.proposals) == 2 and gh.head == REVISED
    assert gh.labels == {"spec:review"} and len(gh.comments) == 1


@pytest.mark.scenario("pr-revision/spec-stage-revision/spec-revised-from-review")
@pytest.mark.parametrize("outcome", ["needs_input", "rejected"])
def test_answer_after_lost_needs_human_response_retries_propose(setup, tmp_path, outcome):
    _, gh, commands = setup
    commands.result = {"version": 1, "outcome": outcome, "questions": ["Mono or stereo?"]}
    gh.fail_needs_human = True
    with pytest.raises(RuntimeError, match="label response lost"):
        poll(setup, tmp_path)
    assert "needs-human" in gh.labels and "agent:revise" in gh.labels
    receipt = json.loads((tmp_path / "revisions" / "owner__sample" / "8.json").read_text())
    assert not receipt.get("delivered")
    poll(setup, tmp_path)
    assert len(commands.proposals) == 1
    gh.feedback.append({"author": {"login": "reviewer"}, "body": "Use stereo.",
                        "createdAt": "2026-10-20T14:00:00Z"})
    gh.label_event("needs-human", "unlabeled")
    commands.result = {"version": 1, "outcome": "drafted", "commit": REVISED, "branch": BRANCH}
    poll(setup, tmp_path)
    assert len(commands.proposals) == 2 and gh.head == REVISED
    proposal = commands.proposals[-1]
    assert "Use stereo." in idea_file(proposal).read_text(encoding="utf-8")
    assert gh.labels == {"spec:review"} and len(gh.comments) == 1
    assert REVISED in gh.comments[0]["body"]


def test_moved_pr_does_not_overwrite_human_commit(setup, tmp_path):
    _, gh, commands = setup
    commands.push_error = True
    with pytest.raises(specloop_intake.DeliveryError):
        poll(setup, tmp_path)
    commands.push_error = False
    gh.head = "c" * 40
    before = len(commands.calls)
    with pytest.raises(specloop_intake.DeliveryError, match="moved"):
        poll(setup, tmp_path)
    assert not any(c[0] == "git" for c in commands.calls[before:])
    assert len(commands.proposals) == 1 and "agent:revise" in gh.labels


@pytest.mark.scenario("issue-intake/file-based-drafting-input/spec-revision-feedback")
def test_async_revision_is_collected_before_delivery(setup, tmp_path, monkeypatch):
    repo, gh, commands = setup
    jobs = []
    expected = ("Add a gate\n\nThe original issue and comments."
                "\n\n## New PR review feedback\n\n**reviewer:**\n\nHandle empty input")

    def start(path, receipt, args, **kwargs):
        payload = idea_file(args)
        assert payload.parent == (tmp_path / "revisions" / "owner__sample" / "8.jobs").resolve()
        assert payload.read_bytes() == expected.encode("utf-8")
        assert args == ["propose", str(repo.manifest), "--change", CHANGE, "--branch", BRANCH,
                        "--from-branch", f"refs/nightshift/revise/{CHANGE}", "--idea-file", str(payload)]
        jobs.append(args)
        receipt["job"] = "pending"
        specloop_job.save(path, receipt)

    monkeypatch.setattr(specloop_job, "start", start)
    monkeypatch.setattr(specloop_job, "collect", lambda *a: None)
    pending = []
    for _ in range(2):
        specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                      git_runner=commands, status_runner=commands, on_pending=pending.append)
    assert len(jobs) == 1 and pending == [8, 8]
    payload = idea_file(jobs[0])
    assert list(payload.parent.glob("idea-*.txt")) == [payload]
    assert payload.read_bytes() == expected.encode("utf-8")
    assert gh.head == HEAD and not gh.comments
    monkeypatch.setattr(specloop_job, "collect", lambda *a: commands.result)
    poll(setup, tmp_path)
    assert not commands.proposals and gh.head == REVISED and gh.labels == {"spec:review"}
    assert payload.read_bytes() == expected.encode("utf-8")
    comment, = gh.comments
    assert "Handle empty input" in comment["body"] and REVISED in comment["body"]
