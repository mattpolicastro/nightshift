"""Issue drafting through injected GitHub, specloop and git boundaries."""

import json
import logging
import subprocess
from functools import partial
from pathlib import Path

import pytest

from nightshift import daemon, queue, specloop_intake
from nightshift.config import Config, Repo
from test_engine_routing import DraftGh


CHANGE = "sample-issue-7-add-a-gate"
BRANCH = f"spec/{CHANGE}"


class Commands:
    def __init__(self, **result):
        self.result = {"version": 1, "outcome": "drafted", "branch": BRANCH,
                       "commit": "abc123", "questions": [], "contradictions": [],
                       "problems": [], "risks": ["Timing needs a human check."], **result}
        self.calls = []
        self.push_fails = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        expected = {"capture_output": True, "text": True, "check": False}
        if args[0] == "git":
            expected["timeout"] = 30
        assert kwargs == expected
        if args[0] == "git":
            return subprocess.CompletedProcess(args, int(self.push_fails and "push" in args), "", "push unavailable")
        assert args[:2] == ["specloop", "propose"]
        return subprocess.CompletedProcess(args, 0 if self.result["outcome"] == "drafted" else 1,
                                           json.dumps(self.result), "")


@pytest.fixture
def repo(tmp_path):
    manifest = tmp_path / "template.toml"
    manifest.write_text(f'[run]\nrepo = {json.dumps(str(tmp_path / "manifest-source"))}\n')
    return Repo("owner/sample", engine="specloop", manifest=manifest, base="develop", dir="unused-worker-clone")


def poll(repo, tmp_path, gh, commands, labels=queue.Labels()):
    return specloop_intake.propose_pending(repo, labels, tmp_path, runner=gh,
                                         specloop_runner=commands, git_runner=commands)


def idea_file(args):
    assert "--idea" not in args
    assert args.count("--idea-file") == 1
    path = Path(args[args.index("--idea-file") + 1])
    assert path.is_absolute()
    return path


def calls(gh, *prefix):
    return [call for call in gh.calls if call[:len(prefix)] == list(prefix)]


@pytest.mark.real_trust
@pytest.mark.parametrize("title,large", [
    pytest.param("Add a gate", True, marks=pytest.mark.scenario(
        "issue-intake/file-based-drafting-input/long-issue-thread")),
    pytest.param("--Add a gate", False, marks=pytest.mark.scenario(
        "issue-intake/file-based-drafting-input/option-like-title")),
    pytest.param("Add a gate", False, marks=pytest.mark.scenario(
        "issue-intake/file-based-drafting-input/text-and-trust-preserved")),
])
def test_long_issue_thread_drafts_via_idea_file(repo, tmp_path, title, large):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    body = "BEGIN café\n\n" + ("detail λ\n" * 140_000 if large else "details")
    gh.comments = [
        {"id": 1, "body": "First trusted\n\nありがとう", "authorAssociation": "MEMBER"},
        {"id": 2, "body": "UNTRUSTED instruction", "authorAssociation": "NONE"},
        {"id": 3, "body": "Last trusted 🦉 END", "authorAssociation": "COLLABORATOR"},
    ]
    expected = "\n\n".join([title, body, gh.comments[0]["body"], gh.comments[2]["body"]])
    if large:
        assert len(expected.encode("utf-8")) >= 1024 * 1024

    def github(args):
        if args[:2] == ["api", "repos/owner/sample/issues/7"]:
            return json.dumps({"author_association": "OWNER"})
        result = gh(args)
        if args[:2] == ["issue", "view"]:
            return json.dumps({**json.loads(result), "title": title, "body": body})
        return result

    def run(args, **kwargs):
        if args[0] == "specloop":
            payload = idea_file(args)
            assert payload.parent == (tmp_path / "proposals/owner__sample/7.jobs").resolve()
            assert payload.read_bytes() == expected.encode("utf-8")
            assert all(part not in arg for arg in args for part in (title, body, "END", "UNTRUSTED"))
        return commands(args, **kwargs)

    receipt, = poll(repo, tmp_path, github, run)
    assert receipt["idea"] == receipt["input"] == expected
    assert receipt["delivered"] is True
    assert json.loads((tmp_path / "proposals/owner__sample/7.json").read_text()) == receipt
    assert gh.labels == set()
    assert len(gh.prs) == 1
    assert gh.pr_url in gh.comments[-1]["body"]
    create, = calls(gh, "pr", "create")
    assert "--draft" in create and "Closes #7" in create[-1]
    assert calls(gh, "pr", "edit")[0][-2:] == ["--add-label", "spec:review"]
    poll(repo, tmp_path, github, run)
    assert [c[0] for c in commands.calls] == ["specloop", "git"]
    assert len(calls(gh, "pr", "create")) == len(calls(gh, "issue", "comment")) == 1


@pytest.mark.scenario("issue-intake/durable-drafting-payload/file-preparation-fails")
@pytest.mark.parametrize("operation", ["mkdir", "write_text"])
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("handled", [False, True])
def test_idea_file_preparation_failure(repo, tmp_path, monkeypatch, operation, background, handled):
    gh = DraftGh(("spec:draft",))
    original = getattr(Path, operation)
    failure = OSError("payload storage unavailable")
    attempted, errors = [], []

    def fail_payload(path, *args, **kwargs):
        if path.name == "7.jobs" or (path.parent.name == "7.jobs" and path.suffix == ".txt"):
            attempted.append(path)
            raise failure
        return original(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("file preparation failure must prevent dispatch and delivery")

    monkeypatch.setattr(Path, operation, fail_payload)
    invoke = partial(specloop_intake.propose_pending, repo, queue.Labels(), tmp_path,
                     runner=gh, specloop_runner=None if background else forbidden,
                     launcher=forbidden, git_runner=forbidden,
                     on_error=(lambda number, exc: errors.append((number, exc))) if handled else None)
    if handled:
        assert invoke() == []
        assert errors == [(7, failure)]
    else:
        with pytest.raises(OSError, match="payload storage unavailable"):
            invoke()
    assert len(attempted) == 1
    assert gh.labels == {"spec:draft"}
    assert not gh.prs and len(gh.comments) == 1
    assert not calls(gh, "issue", "edit")
    assert not (tmp_path / "proposals/owner__sample/7.json").exists()


class BlockedDraftGh(DraftGh):
    """Keep list bodies stale, and serve current bodies and blocker states separately."""

    def __init__(self, armed, states):
        super().__init__((armed,))
        self.states = states
        self.body = "Please draft it.\nblocked-by: " + ", ".join(f"#{n}" for n in states)
        self.blocker_reads = []
        self.body_reads = []

    def __call__(self, args):
        if args[:2] == ["issue", "view"] and args[-2:] == ["--json", "state"]:
            self.calls.append(args)
            number = int(args[2])
            assert args == ["issue", "view", str(number), "--repo", "owner/sample", "--json", "state"]
            self.blocker_reads.append(number)
            state = self.states[number]
            if isinstance(state, Exception):
                raise state
            return json.dumps({"state": state})
        result = super().__call__(args)
        if args[:2] == ["issue", "view"]:
            assert args[2] == "7"
            self.body_reads.append(self.body)
            result = json.dumps({**json.loads(result), "body": self.body})
        return result


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/open-blocker-defers-round")
@pytest.mark.scenario("issue-intake/dependency-gated-drafting/all-blockers-closed-allows-round")
@pytest.mark.scenario("issue-intake/dependency-gated-drafting/unreadable-blocker-defers-round")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("blockers,lookup_failure", [((6,), False), ((5, 6), False), ((6,), True)])
def test_blocked_issue_is_not_drafted_until_blockers_close(
    repo, tmp_path, caplog, armed, blockers, lookup_failure,
):
    states = {n: "OPEN" for n in blockers}
    if lookup_failure:
        states[6] = queue.GhError("lookup unavailable", transient=True)
    gh, commands = BlockedDraftGh(armed, states), Commands()
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)
    # An independent issue after #7 must still finish its eligible delivery.
    neighbor = DraftGh((armed,))
    neighbor.prs = [{"number": 19}]
    neighbor.pr_url = "https://github.com/owner/sample/pull/19"

    def with_neighbor(args):
        if args[:2] == ["issue", "list"]:
            listed = json.loads(gh(args))
            listed.extend({**item, "number": 9} for item in json.loads(neighbor(args)))
            return json.dumps(listed)
        if (args[:3] in (["issue", "view", "9"], ["issue", "edit", "9"],
                        ["issue", "comment", "9"])
                or (args[0] == "api" and "/issues/9/" in args[1])
                or (args[:2] == ["pr", "list"] and "spec/sample-issue-9-add-a-gate" in args)
                or (args[:2] in (["pr", "view"], ["pr", "edit"])
                    and args[2] in ("19", neighbor.pr_url))):
            return neighbor(args)
        return gh(args)

    expected_logs = []
    rounds = [blockers, blockers] + ([(6,)] if len(blockers) > 1 else [])
    for index, remaining in enumerate(rounds):
        if index == 2:
            states[5] = "CLOSED"
        gh.body += f"\nCurrent edit {index}."
        assert poll(repo, tmp_path, with_neighbor, commands) == []
        expected_logs.append((specloop_intake.__name__, logging.INFO,
                              "#7 is blocked by " + ", ".join(f"#{n}" for n in remaining)
                              + " — not drafting"))
        assert caplog.record_tuples == expected_logs
        assert gh.blocker_reads == list(blockers) * (index + 1)
        assert len(gh.body_reads) == index + 1
        assert gh.body_reads[-1] == gh.body
        assert commands.calls == []
        assert gh.labels == {armed}
        assert not (tmp_path / "proposals").exists()
    assert neighbor.labels == set()
    assert neighbor.pr_url in neighbor.comments[-1]["body"]
    assert len(calls(neighbor, "issue", "edit")) == 1

    states.update({n: "CLOSED" for n in blockers})
    receipt, = poll(repo, tmp_path, with_neighbor, commands)
    assert gh.blocker_reads == list(blockers) * (len(rounds) + 1)
    assert len(gh.body_reads) == len(rounds) + 1
    assert gh.body in receipt["idea"]
    assert receipt["delivered"] is True
    assert json.loads((tmp_path / "proposals/owner__sample/7.json").read_text()) == receipt
    assert [c[0] for c in commands.calls] == ["specloop", "git"]
    assert gh.labels == set()
    assert len(gh.prs) == 1
    assert gh.pr_url in gh.comments[-1]["body"]
    assert len(calls(gh, "issue", "comment")) == len(calls(gh, "pr", "create")) == 1
    poll(repo, tmp_path, with_neighbor, commands)
    assert len(commands.calls) == 2
    assert caplog.record_tuples == expected_logs


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/blocked-poll-preserves-state")
@pytest.mark.scenario("issue-intake/dependency-gated-drafting/rearmed-round-waits")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("previous,lost_response", [
    (None, False), ("needs_input", False), ("rejected", False), ("drafted", False),
    ("needs_input", True), ("rejected", True), ("drafted", True),
])
def test_blocked_issue_changes_no_labels_or_comments(
    repo, tmp_path, monkeypatch, caplog, armed, previous, lost_response,
):
    gh = BlockedDraftGh(armed, {6: "CLOSED"})
    commands = Commands(outcome=previous or "drafted")
    path = tmp_path / "proposals/owner__sample/7.json"
    old_receipt = None
    if previous:
        def interrupted(args):
            result = gh(args)
            if lost_response and args[:2] == ["issue", "edit"]:
                raise queue.GhError("label response lost", transient=True)
            return result

        if lost_response:
            with pytest.raises(queue.GhError):
                poll(repo, tmp_path, interrupted, commands)
        else:
            poll(repo, tmp_path, interrupted, commands)
        old_receipt = path.read_bytes()
        assert bool(json.loads(old_receipt).get("delivered")) is not lost_response
        if previous == "drafted":
            gh.prs[0]["state"] = "CLOSED"
        else:
            gh.comments.append({"id": 3, "body": "Use 5V."})
        gh.label_event("needs-human", "unlabeled")
        gh.label_event(armed, "labeled")
    gh.states[6] = "OPEN"
    gh.calls.clear()
    gh.blocker_reads.clear()
    gh.body_reads.clear()
    commands.calls.clear()
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)
    snapshot = json.dumps([sorted(gh.labels), gh.comments, gh.prs, gh.events])
    files = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("blocked polling must not save a receipt, start work, or report pending/error")

    with monkeypatch.context() as guard:
        guard.setattr(specloop_intake, "_save", forbidden)
        guard.setattr(specloop_intake, "_start", forbidden)
        for index in range(2):
            assert specloop_intake.propose_pending(
                repo, queue.Labels(), tmp_path, runner=gh, specloop_runner=commands,
                git_runner=commands, launcher=forbidden, on_pending=forbidden, on_error=forbidden,
            ) == []
            assert gh.blocker_reads == [6] * (index + 1)
            assert gh.body_reads == [gh.body] * (index + 1)
            assert commands.calls == []
            assert json.dumps([sorted(gh.labels), gh.comments, gh.prs, gh.events]) == snapshot
            assert {p.relative_to(tmp_path): p.read_bytes()
                    for p in tmp_path.rglob("*") if p.is_file()} == files
            assert (path.read_bytes() if path.exists() else None) == old_receipt
            assert all(c[:2] in (["issue", "list"], ["issue", "view"],
                                ["pr", "list"], ["pr", "view"])
                       or (c[0] == "api" and c[1].endswith("/events")
                           and "--method" not in c) for c in gh.calls)
    assert caplog.record_tuples == [
        (specloop_intake.__name__, logging.INFO, "#7 is blocked by #6 — not drafting")
    ] * 2

    gh.states[6] = "CLOSED"
    commands.result.update(outcome="drafted", commit="def456")
    receipt, = poll(repo, tmp_path, gh, commands)
    assert receipt["delivered"] is True
    assert gh.labels == set()
    assert len([c for c in commands.calls if c[:2] == ["specloop", "propose"]]) == 1
    assert gh.blocker_reads == [6, 6, 6]
    assert path.read_bytes() != old_receipt
    if old_receipt:
        assert receipt["round_id"] != json.loads(old_receipt)["round_id"]
    assert gh.pr_url in gh.comments[-1]["body"]


@pytest.mark.scenario("issue-intake/draft-lifecycle-independent-of-blockers/finished-draft-is-delivered")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_finished_draft_is_delivered_even_if_a_blocker_reopens(repo, tmp_path, caplog, armed):
    gh, commands = BlockedDraftGh(armed, {6: "CLOSED"}), Commands()
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)
    commands.push_fails = True
    with pytest.raises(RuntimeError, match="could not push"):
        poll(repo, tmp_path, gh, commands)
    path = tmp_path / "proposals/owner__sample/7.json"
    saved = json.loads(path.read_text())
    assert saved["result"]["outcome"] == "drafted"
    assert not saved.get("delivered")
    assert gh.blocker_reads == [6]
    assert gh.labels == {armed}
    assert not gh.prs

    gh.states[6] = "OPEN"
    commands.push_fails = False
    receipt, = poll(repo, tmp_path, gh, commands)
    assert receipt == {**saved, "delivered": True}
    assert json.loads(path.read_text()) == receipt
    assert gh.blocker_reads == [6]
    assert gh.body_reads == [gh.body, gh.body]
    assert [c[0] for c in commands.calls] == ["specloop", "git", "git"]
    assert len(gh.prs) == 1
    assert gh.pr_url in gh.comments[-1]["body"]
    assert len(calls(gh, "issue", "comment")) == len(calls(gh, "pr", "create")) == 1
    assert gh.labels == set()
    assert caplog.records == []


@pytest.mark.scenario("issue-intake/draft-lifecycle-independent-of-blockers/running-job-is-preserved")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_running_draft_is_collected_and_delivered_after_blocker_reopens(
    repo, tmp_path, monkeypatch, caplog, armed,
):
    gh, commands = BlockedDraftGh(armed, {6: "CLOSED"}), Commands()
    path = tmp_path / "proposals/owner__sample/7.json"
    starts, collections, pending = [], [], []
    completed = False
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)

    def start(receipt_path, receipt, args, *, launcher):
        assert receipt_path == path
        assert args[:2] == ["propose", str(repo.manifest)]
        assert args[args.index("--branch") + 1] == BRANCH
        starts.append(args)
        receipt["job"] = "running-draft-job"
        specloop_intake.specloop_job.save(receipt_path, receipt)

    def collect(receipt_path, receipt):
        assert receipt_path == path
        assert receipt["job"] == "running-draft-job"
        collections.append(receipt.copy())
        return commands.result.copy() if completed else None

    def forbidden(*args, **kwargs):
        pytest.fail("background drafting must not launch a real process or report an error")

    monkeypatch.setattr(specloop_intake.specloop_job, "start", start)
    monkeypatch.setattr(specloop_intake.specloop_job, "collect", collect)

    def background_poll():
        return specloop_intake.propose_pending(
            repo, queue.Labels(), tmp_path, runner=gh, git_runner=commands,
            launcher=forbidden, on_pending=pending.append, on_error=forbidden,
        )

    assert background_poll() == []
    saved_bytes = path.read_bytes()
    saved = json.loads(saved_bytes)
    assert saved["job"] == "running-draft-job"
    assert "result" not in saved
    assert pending == [7]
    assert collections == []
    assert gh.blocker_reads == [6]

    gh.states[6] = "OPEN"
    assert background_poll() == []
    assert path.read_bytes() == saved_bytes
    assert collections == [saved]
    assert pending == [7, 7]
    assert len(starts) == 1
    assert gh.blocker_reads == [6]
    assert commands.calls == []
    assert gh.labels == {armed}
    assert not gh.prs
    assert not calls(gh, "issue", "edit")
    assert not calls(gh, "issue", "comment")

    completed = True
    receipt, = background_poll()
    assert receipt == {**saved, "result": commands.result, "delivered": True}
    assert json.loads(path.read_text()) == receipt
    assert collections == [saved, saved]
    assert pending == [7, 7]
    assert len(starts) == 1
    assert gh.states[6] == "OPEN"
    assert gh.blocker_reads == [6]
    assert [c[0] for c in commands.calls] == ["git"]
    assert commands.calls[0][-1] == f"abc123:refs/heads/{BRANCH}"
    assert gh.labels == set()
    assert len(gh.prs) == 1
    assert gh.pr_url in gh.comments[-1]["body"]
    assert len(calls(gh, "pr", "create")) == len(calls(gh, "issue", "comment")) == 1
    assert caplog.records == []


@pytest.mark.scenario("issue-intake/draft-lifecycle-independent-of-blockers/existing-pr-recovery-continues")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_open_pr_without_receipt_is_delivered_while_blocker_is_open(repo, tmp_path, caplog, armed):
    gh, commands = BlockedDraftGh(armed, {6: "OPEN"}), Commands()
    gh.prs = [{"number": 8, "state": "OPEN", "isDraft": True}]
    original_prs = [pr.copy() for pr in gh.prs]
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)
    assert not (tmp_path / "proposals").exists()

    assert poll(repo, tmp_path, gh, commands) == []

    assert gh.body_reads == [gh.body]
    assert gh.blocker_reads == []
    assert commands.calls == []
    assert not (tmp_path / "proposals").exists()
    assert gh.prs == original_prs
    assert not calls(gh, "pr", "create")
    assert calls(gh, "pr", "edit") == [
        ["pr", "edit", gh.pr_url, "--repo", repo.name, "--add-label", "spec:review"],
    ]
    assert gh.pr_url in gh.comments[-1]["body"]
    assert f"<!-- nightshift:draft:{CHANGE}:pr-8 -->" in gh.comments[-1]["body"]
    assert len(calls(gh, "issue", "comment")) == len(calls(gh, "issue", "edit")) == 1
    assert gh.labels == set()
    assert caplog.records == []


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/no-dependencies-allows-round")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_body_without_dependencies_drafts_without_blocker_lookups(repo, tmp_path, caplog, armed):
    gh, commands = BlockedDraftGh(armed, {6: "OPEN"}), Commands()
    gh.body = "Please draft it. Related discussion: #6."
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)

    receipt, = poll(repo, tmp_path, gh, commands)

    assert gh.body_reads == [gh.body]
    assert gh.blocker_reads == []
    assert gh.body in receipt["idea"]
    assert receipt["result"] == commands.result
    assert receipt["delivered"] is True
    assert json.loads((tmp_path / "proposals/owner__sample/7.json").read_text()) == receipt
    assert [c[0] for c in commands.calls] == ["specloop", "git"]
    assert gh.body in idea_file(commands.calls[0]).read_text(encoding="utf-8")
    assert gh.labels == set()
    assert len(gh.prs) == 1
    assert gh.pr_url in gh.comments[-1]["body"]
    assert len(calls(gh, "pr", "create")) == len(calls(gh, "issue", "comment")) == 1
    assert caplog.records == []


@pytest.mark.scenario("issue-intake/dependency-gated-drafting/open-blocker-defers-round")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_blocked_skip_allows_another_eligible_issue_to_draft(repo, tmp_path, caplog, armed):
    gh = BlockedDraftGh(armed, {6: "OPEN"})
    neighbor = DraftGh((armed,))
    neighbor_branch = "spec/sample-issue-9-add-a-gate"
    commands = Commands(branch=neighbor_branch)
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)
    blocked_state = json.dumps([sorted(gh.labels), gh.comments, gh.prs, gh.events])

    def with_neighbor(args):
        if args[:2] == ["issue", "list"]:
            listed = json.loads(gh(args))
            listed.extend({**item, "number": 9} for item in json.loads(neighbor(args)))
            return json.dumps(listed)
        if (args[:3] in (["issue", "view", "9"], ["issue", "edit", "9"],
                        ["issue", "comment", "9"])
                or (args[0] == "api" and "/issues/9/" in args[1])
                or (args[:2] == ["pr", "list"] and neighbor_branch in args)
                or args[:2] in (["pr", "create"], ["pr", "view"], ["pr", "edit"])):
            return neighbor(args)
        return gh(args)

    receipt, = poll(repo, tmp_path, with_neighbor, commands)
    assert receipt["issue"] == 9
    assert receipt["result"]["branch"] == neighbor_branch
    assert receipt["delivered"] is True
    assert json.loads((tmp_path / "proposals/owner__sample/9.json").read_text()) == receipt
    assert [c[0] for c in commands.calls] == ["specloop", "git"]
    assert commands.calls[0][commands.calls[0].index("--branch") + 1] == neighbor_branch
    assert commands.calls[1][-1] == f"abc123:refs/heads/{neighbor_branch}"
    assert neighbor.labels == set()
    assert len(neighbor.prs) == 1
    assert neighbor.pr_url in neighbor.comments[-1]["body"]
    assert "Closes #9" in calls(neighbor, "pr", "create")[0][-1]

    assert poll(repo, tmp_path, with_neighbor, commands) == []
    assert gh.blocker_reads == [6, 6]
    assert gh.body_reads == [gh.body, gh.body]
    assert json.dumps([sorted(gh.labels), gh.comments, gh.prs, gh.events]) == blocked_state
    assert not (tmp_path / "proposals/owner__sample/7.json").exists()
    assert not calls(gh, "pr", "create")
    assert not calls(gh, "issue", "edit")
    assert not calls(gh, "issue", "comment")
    assert len(commands.calls) == 2
    assert len(calls(neighbor, "pr", "create")) == len(calls(neighbor, "issue", "comment")) == 1
    assert caplog.record_tuples == [
        (specloop_intake.__name__, logging.INFO, "#7 is blocked by #6 — not drafting"),
    ] * 2


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.parametrize("armed", [("spec:draft",), ("agent:ready",), ("spec:draft", "agent:ready")])
def test_draft_pr_opened(repo, tmp_path, armed):
    gh, commands = DraftGh(armed), Commands()
    receipt, = poll(repo, tmp_path, gh, commands)
    propose, push = commands.calls
    assert propose == ["specloop", "propose", str(repo.manifest), "--change", CHANGE,
                       "--branch", BRANCH, "--base", "develop", "--idea-file",
                       str(idea_file(propose)), "--json"]
    assert idea_file(propose).read_text(encoding="utf-8") == "Add a gate\n\nPlease draft it.\n\nKeep it small."
    assert push == ["git", "-C", str(tmp_path / "manifest-source"), "push", "origin",
                    f"abc123:refs/heads/{BRANCH}"]
    create, = calls(gh, "pr", "create")
    assert create == ["pr", "create", "--repo", repo.name, "--head", BRANCH, "--base", "develop",
                      "--draft", "--title", "Spec: Add a gate", "--body",
                      "Closes #7\n\nFeasibility risks:\n- Timing needs a human check."]
    assert calls(gh, "pr", "edit") == [["pr", "edit", gh.pr_url, "--repo", repo.name,
                                         "--add-label", "spec:review"]]
    assert gh.pr_url in gh.comments[-1]["body"]
    assert f"<!-- nightshift:draft:{CHANGE}:pr-8 -->" in gh.comments[-1]["body"]
    assert gh.comments[-1]["body"].startswith(queue.SELF_COMMENT_PREFIX)
    assert gh.calls[-1][:3] == ["issue", "edit", "7"]
    assert gh.labels == set()
    assert receipt["delivered"] is True
    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 2
    assert len(calls(gh, "pr", "create")) == len(calls(gh, "issue", "comment")) == 1


def test_draft_change_and_branch_preserve_all_title_digits(repo, tmp_path):
    gh = DraftGh(("spec:draft",))
    change = "sample-issue-7-gate-0123456789"
    branch = f"spec/{change}"
    commands = Commands(branch=branch)

    def titled_issue(args):
        result = gh(args)
        if args[:2] == ["issue", "view"]:
            issue = json.loads(result)
            issue["title"] = "!! Gate 0123456789 !!"
            return json.dumps(issue)
        return result

    receipt, = poll(repo, tmp_path, titled_issue, commands)

    propose, push = commands.calls
    assert propose[propose.index("--change") + 1] == change
    assert propose[propose.index("--branch") + 1] == branch
    assert push[-1] == f"abc123:refs/heads/{branch}"
    create, = calls(gh, "pr", "create")
    assert create[create.index("--head") + 1] == branch
    assert receipt["change"] == change
    assert receipt["delivered"] is True


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/questions-posted")
def test_questions_posted(repo, tmp_path):
    gh = DraftGh(("spec:draft", "agent:ready"))
    questions = ["Which gate voltage?", "Should it retrigger?"]
    commands = Commands(outcome="needs_input", commit="", questions=questions)
    poll(repo, tmp_path, gh, commands)
    assert all(question in gh.comments[-1]["body"] for question in questions)
    assert gh.labels == {"needs-human"}
    assert not calls(gh, "pr", "create")
    assert [c[0] for c in commands.calls] == ["specloop"]
    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 1


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/unfixable-draft-escalated")
@pytest.mark.parametrize("findings", [
    {"contradictions": [{"task": "1.2", "problem": "Frozen files must change", "evidence": "checker.py:7"}]},
    {"problems": ["Scenario has no covering task", "Strict validation failed after 3 rounds"]},
])
def test_unfixable_draft_escalated(repo, tmp_path, findings):
    gh, commands = DraftGh(("spec:draft",)), Commands(outcome="rejected", **findings)
    poll(repo, tmp_path, gh, commands)
    body = gh.comments[-1]["body"]
    for items in findings.values():
        for item in items:
            assert all(value in body for value in item.values()) if isinstance(item, dict) else item in body
    assert gh.labels == {"needs-human"}
    assert not calls(gh, "pr", "create")
    assert [c[0] for c in commands.calls] == ["specloop"]


@pytest.mark.parametrize("operation,after_write", [
    (("pr", "create"), False), (("pr", "create"), True),
    (("pr", "edit"), False), (("issue", "comment"), True), (("issue", "edit"), False),
])
def test_restart_finishes_delivery_without_duplicate_pr_or_comment(repo, tmp_path, operation, after_write):
    gh, commands = DraftGh(("spec:draft",)), Commands()

    def interrupted(args):
        if tuple(args[:2]) == operation:
            if after_write:
                gh(args)
            raise queue.GhError("response lost", transient=True)
        return gh(args)

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    assert gh.labels == {"spec:draft"}
    # The next pass reloads the receipt from disk and rechecks GitHub.
    poll(repo, tmp_path, gh, commands)
    assert len([c for c in commands.calls if c[0] == "specloop"]) == 1
    assert len(gh.prs) == 1
    assert len(gh.comments) == 2
    assert gh.pr_url in gh.comments[-1]["body"]
    assert gh.labels == set()


def test_escalation_retry_edits_marked_comment(repo, tmp_path):
    gh, commands = DraftGh(("spec:draft",)), Commands(outcome="needs_input", questions=["Which voltage?"])

    def interrupted(args):
        result = gh(args)
        if args[:2] == ["issue", "comment"]:
            raise queue.GhError("response lost", transient=True)
        return result

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 1
    assert len(gh.comments) == 2
    assert "Which voltage?" in gh.comments[-1]["body"]
    assert any("PATCH" in call for call in gh.calls)
    assert gh.labels == {"needs-human"}


def test_existing_pr_without_receipt_is_reused(repo, tmp_path):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    gh.prs = [{"number": 8}]
    poll(repo, tmp_path, gh, commands)
    assert commands.calls == []
    assert not calls(gh, "pr", "create")
    assert gh.pr_url in gh.comments[-1]["body"]
    assert gh.labels == set()


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("delivery", ["delivered", "response_lost", "no_receipt"])
def test_closed_pr_is_drafted_afresh_when_issue_is_rearmed(repo, tmp_path, armed, delivery):
    gh, commands = DraftGh((armed,)), Commands()

    def lost_label_response(args):
        result = gh(args)
        if args[:2] == ["issue", "edit"]:
            raise queue.GhError("label response lost", transient=True)
        return result

    if delivery == "response_lost":
        with pytest.raises(queue.GhError):
            poll(repo, tmp_path, lost_label_response, commands)
    else:
        poll(repo, tmp_path, gh, commands)
    path = tmp_path / "proposals/owner__sample/7.json"
    if delivery == "no_receipt":
        path.unlink()
    gh.prs[0]["state"] = "CLOSED"
    poll(repo, tmp_path, gh, commands)  # Closing alone does not arm the issue.
    assert len(commands.calls) == 2
    gh.label_event("needs-human", "unlabeled")
    gh.label_event(armed, "labeled")
    gh.comments.append({"id": 3, "body": "Try again with a 5V gate."})
    gh.calls.clear()
    commands.result["commit"] = "def456"

    receipt, = poll(repo, tmp_path, gh, commands)

    propose, lookup, push = commands.calls[2:]
    assert propose[:2] == ["specloop", "propose"]
    assert "ls-remote" in lookup
    assert propose[propose.index("--branch") + 1] == BRANCH
    assert "Try again with a 5V gate." in idea_file(propose).read_text(encoding="utf-8")
    assert f"--force-with-lease=refs/heads/{BRANCH}:abc123" in push
    assert push[-1] == f"def456:refs/heads/{BRANCH}"
    create, = calls(gh, "pr", "create")
    assert "--draft" in create
    assert create[create.index("--head") + 1] == BRANCH
    assert "Closes #7" in create[-1]
    assert [pr["state"] for pr in gh.prs] == ["CLOSED", "OPEN"]
    assert calls(gh, "pr", "edit") == [[
        "pr", "edit", "https://github.com/owner/sample/pull/9", "--repo", repo.name,
        "--add-label", "spec:review",
    ]]
    assert len(gh.comments) == 4
    assert "/pull/8" in gh.comments[1]["body"]
    assert gh.pr_url in gh.comments[-1]["body"]
    assert gh.labels == set()
    assert receipt["replaces_pr"] == {"number": 8, "head": "abc123"}
    assert json.loads(path.read_text()) == receipt
    assert receipt["delivered"] is True
    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 5


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.parametrize("failure", ["push", "create", "create_response", "label"])
def test_replacement_draft_delivery_retry_does_not_redraft(repo, tmp_path, failure):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    poll(repo, tmp_path, gh, commands)
    gh.prs[0]["state"] = "CLOSED"
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:draft", "labeled")
    commands.result["commit"] = "def456"
    commands.push_fails = failure == "push"

    def interrupted(args):
        if args[:2] == ["pr", "create"] and failure in ("create", "create_response"):
            if failure == "create_response":
                gh(args)
            raise queue.GhError("create unavailable", transient=True)
        if args[:2] == ["pr", "edit"] and failure == "label":
            raise queue.GhError("label unavailable", transient=True)
        return gh(args)

    with pytest.raises(RuntimeError):
        poll(repo, tmp_path, interrupted, commands)
    commands.push_fails = False
    poll(repo, tmp_path, gh, commands)
    assert len([c for c in commands.calls if c[0] == "specloop"]) == 2
    assert len(gh.prs) == 2
    assert gh.labels == set()
    assert len(gh.comments) == 3
    assert "/pull/8" in gh.comments[1]["body"]
    assert gh.pr_url in gh.comments[-1]["body"]


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.parametrize("branch_state", ["unchanged", "advanced", "deleted"])
def test_fresh_draft_replaces_only_the_abandoned_git_tip(repo, tmp_path, branch_state):
    source, remote = tmp_path / "manifest-source", tmp_path / "remote.git"

    def git(*args, input=None):
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            input=input, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "--bare", str(source))
    git("init", "--bare", str(remote))
    git("-C", str(source), "remote", "add", "origin", str(remote))
    tree = git("-C", str(source), "hash-object", "-w", "-t", "tree", "--stdin", input="")
    base = git("-C", str(source), "commit-tree", tree, "-m", "Base")
    old = git("-C", str(source), "commit-tree", tree, "-p", base, "-m", "Abandoned draft")
    fresh = git("-C", str(source), "commit-tree", tree, "-p", base, "-m", "Fresh draft")
    gh, commands = DraftGh(("spec:draft",)), Commands(commit=old)

    def real_push_poll(runner=gh):
        return specloop_intake.propose_pending(
            repo, queue.Labels(), tmp_path, runner=runner,
            specloop_runner=commands, git_runner=subprocess.run,
        )

    real_push_poll()
    gh.prs[0].update(state="CLOSED", headRefOid=old)
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:draft", "labeled")
    commands.result["commit"] = fresh
    if branch_state == "advanced":
        newer = git("-C", str(source), "commit-tree", tree, "-p", old, "-m", "Human update")
        git("-C", str(source), "push", "origin", f"{newer}:refs/heads/{BRANCH}")
        with pytest.raises(RuntimeError, match="could not push"):
            real_push_poll()
        assert git("-C", str(remote), "rev-parse", BRANCH) == newer
        assert len(gh.prs) == 1
        assert gh.labels == {"spec:draft"}
    else:
        if branch_state == "deleted":
            git("-C", str(source), "push", "origin", f":refs/heads/{BRANCH}")

        def create_unavailable(args):
            if args[:2] == ["pr", "create"]:
                raise queue.GhError("HTTP 503", transient=True)
            return gh(args)

        with pytest.raises(queue.GhError):
            real_push_poll(create_unavailable)
        assert git("-C", str(remote), "rev-parse", BRANCH) == fresh
        # Retrying a successful push with the original lease is safe when
        # the remote already has exactly the desired commit.
        real_push_poll()
        assert git("-C", str(remote), "rev-parse", BRANCH) == fresh
        assert len(commands.calls) == 2
        assert len(gh.prs) == 2
        assert gh.labels == set()


def test_merged_pr_is_not_relabelled_or_redrafted(repo, tmp_path):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    poll(repo, tmp_path, gh, commands)
    gh.prs[0]["state"] = "MERGED"
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:draft", "labeled")
    gh.calls.clear()
    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 2
    assert not calls(gh, "pr", "edit")
    assert not calls(gh, "issue", "edit")
    assert not calls(gh, "api")


@pytest.mark.parametrize("existing", [False, True])
def test_pr_closed_during_delivery_is_not_relabelled(repo, tmp_path, existing):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    if existing:
        gh.prs = [{"number": 8}]

    def closed_during_delivery(args):
        if args[:2] == ["pr", "view"] and args[-1] == "state,url":
            gh.prs[0]["state"] = "CLOSED"
        return gh(args)

    with pytest.raises(RuntimeError, match="no longer open"):
        poll(repo, tmp_path, closed_during_delivery, commands)
    assert not calls(gh, "pr", "edit")
    assert all(call[1].endswith("/events") for call in calls(gh, "api"))
    assert not calls(gh, "issue", "comment")
    assert gh.labels == {"spec:draft"}


def test_pr_state_lookup_failure_preserves_receipt_and_armed_issue(repo, tmp_path):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    poll(repo, tmp_path, gh, commands)
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:draft", "labeled")
    path = tmp_path / "proposals/owner__sample/7.json"
    saved = path.read_text()
    gh.calls.clear()

    def unavailable(args):
        if args[:2] == ["pr", "view"]:
            raise queue.GhError("HTTP 503", transient=True)
        return gh(args)

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, unavailable, commands)
    assert path.read_text() == saved
    assert len(commands.calls) == 2
    assert gh.labels == {"spec:draft"}
    assert not calls(gh, "pr", "edit")


def test_github_lookup_failure_reaches_repo_health_without_proposing(repo, tmp_path, monkeypatch):
    gh, commands = DraftGh(("spec:draft",)), Commands()

    def unavailable(args):
        if args[:2] == ["pr", "list"]:
            raise queue.GhError("HTTP 503", transient=True)
        return gh(args)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=unavailable, specloop_runner=commands)
    assert health.failures == {repo.name: 1}
    assert commands.calls == []
    assert gh.labels == {"spec:draft"}
    assert not calls(gh, "pr", "create")
    # The worker lookup retains its existing best-effort contract.
    assert queue._latest_pr_number(repo.name, BRANCH, runner=unavailable) is None


def test_push_failure_keeps_receipt_and_retries_without_redrafting(repo, tmp_path):
    gh, commands = DraftGh(("spec:draft",)), Commands()
    commands.push_fails = True
    with pytest.raises(RuntimeError, match="could not push"):
        poll(repo, tmp_path, gh, commands)
    assert not calls(gh, "pr", "create")
    assert gh.labels == {"spec:draft"}
    commands.push_fails = False
    poll(repo, tmp_path, gh, commands)
    assert [c[0] for c in commands.calls] == ["specloop", "git", "git"]
    assert len(gh.prs) == 1


def test_custom_labels(repo, tmp_path):
    labels = queue.Labels(ready="ready", spec_draft="draft", spec_review="review", needs_human="human")
    gh, commands = DraftGh(("draft", "ready")), Commands()
    poll(repo, tmp_path, gh, commands, labels)
    assert calls(gh, "pr", "edit")[0][-1] == "review"
    assert gh.labels == set()


@pytest.mark.scenario("issue-intake/durable-drafting-payload/later-round-preserves-earlier-input")
def test_answered_escalation_can_be_rearmed(repo, tmp_path):
    gh, commands = DraftGh(("spec:draft",)), Commands(outcome="needs_input", questions=["Which voltage?"])
    poll(repo, tmp_path, gh, commands)
    earlier = idea_file(commands.calls[0])
    original = earlier.read_bytes()
    gh.label_event("needs-human", "unlabeled")
    gh.label_event("spec:draft", "labeled")
    gh.comments.append({"id": 3, "body": "Use 5V."})
    expected = "\n\n".join(["Add a gate", "Please draft it.", *(c["body"] for c in gh.comments)])
    commands.result["outcome"] = "drafted"
    commands.result["questions"] = []
    poll(repo, tmp_path, gh, commands)
    later = idea_file(commands.calls[1])
    assert later != earlier
    assert later.read_bytes() == expected.encode("utf-8")
    assert earlier.read_bytes() == original
    assert original == b"Add a gate\n\nPlease draft it.\n\nKeep it small."
    assert len(gh.comments) == 4
    assert "Which voltage?" in gh.comments[1]["body"]
    assert gh.pr_url in gh.comments[-1]["body"]


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/questions-posted")
@pytest.mark.scenario("issue-intake/drafting-from-an-issue/unfixable-draft-escalated")
@pytest.mark.parametrize("outcome,findings", [
    ("needs_input", {"questions": ["Which voltage?"]}),
    ("rejected", {"problems": ["The voltage requirements contradict each other."]}),
])
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_answer_after_lost_escalation_label_response_is_drafted(repo, tmp_path, outcome, findings, armed):
    gh, commands = DraftGh((armed,)), Commands(outcome=outcome, **findings)

    def interrupted(args):
        result = gh(args)
        if args[:2] == ["issue", "edit"]:
            raise queue.GhError("label response lost", transient=True)
        return result

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    assert gh.labels == {"needs-human"}
    path = tmp_path / "proposals/owner__sample/7.json"
    assert not json.loads(path.read_text()).get("delivered")
    assert not gh.prs
    # A poll while awaiting the answer cannot complete the local receipt.
    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 1
    assert not json.loads(path.read_text()).get("delivered")

    gh.comments.append({"id": 3, "body": "Use 5V."})
    gh.label_event("needs-human", "unlabeled")
    gh.label_event(armed, "labeled")
    commands.result.update(outcome="drafted", questions=[], problems=[])
    poll(repo, tmp_path, gh, commands)

    assert [c[0] for c in commands.calls] == ["specloop", "specloop", "git"]
    assert "Use 5V." in idea_file(commands.calls[1]).read_text(encoding="utf-8")
    assert len(gh.prs) == 1
    assert gh.labels == set()
    assert len(gh.comments) == 4
    assert gh.pr_url in gh.comments[-1]["body"]
    assert gh.comments[2]["body"] == "Use 5V."
    assert all(finding in gh.comments[1]["body"] for values in findings.values() for finding in values)
    assert json.loads(path.read_text())["delivered"] is True


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/questions-posted")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("answered", [False, True])
@pytest.mark.parametrize("lost_label_response", [False, True])
def test_new_question_round_preserves_history_and_retries_its_own_comment(
    repo, tmp_path, armed, answered, lost_label_response,
):
    gh = DraftGh((armed,))
    commands = Commands(outcome="needs_input", questions=["Which voltage?"])
    path = tmp_path / "proposals/owner__sample/7.json"

    def interrupted_label(args):
        result = gh(args)
        if args[:2] == ["issue", "edit"]:
            raise queue.GhError("label response lost", transient=True)
        return result

    if lost_label_response:
        with pytest.raises(queue.GhError):
            poll(repo, tmp_path, interrupted_label, commands)
    else:
        poll(repo, tmp_path, gh, commands)
    first = json.loads(path.read_text())
    assert bool(first.get("delivered")) is not lost_label_response
    assert gh.labels == {"needs-human"}
    assert first["drafting_events"] == {armed: 1}
    original = gh.comments[1].copy()
    if answered:
        gh.comments.append({"id": 3, "body": "Use 5V."})
        commands.result["questions"] = ["Should it retrigger?"]
    # Even identical questions and input belong to a new round after re-arming.
    gh.label_event("needs-human", "unlabeled")
    gh.label_event(armed, "labeled")
    rearm_event = gh.events[-1]["id"]
    gh.label_event("unrelated", "labeled")
    gh.label_event("unrelated", "unlabeled")

    def interrupted(args):
        result = gh(args)
        if args[:2] == ["issue", "comment"] or "PATCH" in args:
            raise queue.GhError("comment response lost", transient=True)
        return result

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    second = json.loads(path.read_text())
    assert second["round_id"] != first["round_id"]
    assert second["drafting_events"] == {armed: rearm_event}
    assert not second.get("delivered")
    assert gh.labels == {armed}
    assert gh.comments[1] == original
    new_comment = gh.comments[-1].copy()
    assert commands.result["questions"][0] in new_comment["body"]
    assert f":{second['round_id']} -->" in new_comment["body"]

    # A lost PATCH response on the retry still cannot start a third round.
    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    poll(repo, tmp_path, gh, commands)
    assert json.loads(path.read_text())["round_id"] == second["round_id"]
    assert len(commands.calls) == 2
    assert gh.labels == {"needs-human"}
    assert len(gh.comments) == (4 if answered else 3)
    assert gh.comments[1] == original
    assert gh.comments[-1] == new_comment
    assert [c[1] for c in gh.calls if "PATCH" in c] == [
        f"repos/{repo.name}/issues/comments/{new_comment['id']}",
    ] * 2
    assert not gh.prs


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/questions-posted")
@pytest.mark.parametrize("failure", ["unavailable", "empty", "unlabeled", "missing_id"])
def test_unconfirmed_drafting_event_preserves_round_for_retry(repo, tmp_path, failure):
    gh = DraftGh(("spec:draft",))
    commands = Commands(outcome="needs_input", questions=["Which voltage?"])

    def interrupted(args):
        result = gh(args)
        if args[:2] == ["issue", "comment"]:
            raise queue.GhError("comment response lost", transient=True)
        return result

    with pytest.raises(queue.GhError):
        poll(repo, tmp_path, interrupted, commands)
    path = tmp_path / "proposals/owner__sample/7.json"
    saved = path.read_text()
    original = gh.comments[1].copy()

    def unconfirmed(args):
        if args[0] == "api" and args[1].endswith("/events"):
            if failure == "unavailable":
                raise queue.GhError("HTTP 503", transient=True)
            event = {"id": 2, "event": "labeled", "label": {"name": "spec:draft"}}
            if failure == "unlabeled":
                event["event"] = "unlabeled"
            elif failure == "missing_id":
                del event["id"]
            return json.dumps([[] if failure == "empty" else [event]])
        return gh(args)

    gh.calls.clear()
    with pytest.raises(RuntimeError):
        poll(repo, tmp_path, unconfirmed, commands)
    assert path.read_text() == saved
    assert len(commands.calls) == 1
    assert gh.comments[1] == original
    assert not calls(gh, "issue", "edit")
    assert not calls(gh, "issue", "comment")
    assert not any("PATCH" in call for call in gh.calls)
    assert gh.labels == {"spec:draft"}

    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 1
    assert len(gh.comments) == 2
    assert gh.comments[1] == original
    assert gh.labels == {"needs-human"}
    assert not gh.prs


@pytest.mark.scenario("issue-intake/drafting-from-an-issue/draft-pr-opened")
@pytest.mark.scenario("issue-intake/drafting-from-an-issue/questions-posted")
@pytest.mark.scenario("issue-intake/drafting-from-an-issue/unfixable-draft-escalated")
@pytest.mark.parametrize("outcome,findings", [
    ("drafted", {}),
    ("needs_input", {"questions": ["Which voltage?"]}),
    ("rejected", {"problems": ["The voltage requirements contradict each other."]}),
])
@pytest.mark.parametrize("after_write", [False, True])
def test_comment_creation_reconciled_inside_gh_retry(
    repo, tmp_path, monkeypatch, outcome, findings, after_write,
):
    gh = DraftGh(("spec:draft",))
    commands = Commands(outcome=outcome, **findings)
    attempts, waits = [], []

    def run(args, **kwargs):
        assert args[0] == "gh"
        command = args[1:]
        attempts.append(command)
        if command[0] == "api" and command[1].endswith("/comments") and "PATCH" not in command:
            # Reconciliation must read this issue in this repository before
            # deciding whether the failed create needs to be retried.
            assert command == [
                "api", "repos/owner/sample/issues/7/comments", "--paginate", "--slurp",
            ]
        if command[:2] == ["issue", "comment"] and sum(
            c[:2] == ["issue", "comment"] for c in attempts
        ) == 1:
            if after_write:
                gh(command)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        return subprocess.CompletedProcess(args, 0, gh(command), "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    runner = partial(queue._gh, sleep=waits.append)
    poll(repo, tmp_path, runner, commands)

    assert waits == [2]
    assert len(gh.comments) == 2  # one human comment, one marked delivery
    assert sum(c[:2] == ["issue", "comment"] for c in attempts) == (1 if after_write else 2)
    assert gh.labels == (set() if outcome == "drafted" else {"needs-human"})
    assert len(gh.prs) == (1 if outcome == "drafted" else 0)
    assert len([c for c in commands.calls if c[0] == "specloop"]) == 1

    if outcome != "drafted":
        # The next round posts a PR link while keeping the earlier findings.
        gh.comments.append({"id": 3, "body": "Use 5V."})
        gh.label_event("needs-human", "unlabeled")
        gh.label_event("spec:draft", "labeled")
        commands.result.update(outcome="drafted", questions=[], problems=[])
        poll(repo, tmp_path, runner, commands)
        assert len(gh.comments) == 4
        assert gh.pr_url in gh.comments[-1]["body"]
        assert all(finding in gh.comments[1]["body"] for values in findings.values() for finding in values)
        assert "Which voltage?" not in gh.comments[-1]["body"]
        assert "contradict" not in gh.comments[-1]["body"]


def test_comment_retry_lookup_failure_preserves_issue_for_recovery(repo, tmp_path, monkeypatch):
    gh = DraftGh(("spec:draft",))
    commands = Commands(outcome="needs_input", questions=["Which voltage?"])
    writes = []

    def run(args, **kwargs):
        command = args[1:]
        if command[:2] == ["issue", "comment"]:
            writes.append(command)
            gh(command)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        if command[0] == "api" and writes:
            return subprocess.CompletedProcess(args, 1, "", "HTTP 503: Service Unavailable")
        return subprocess.CompletedProcess(args, 0, gh(command), "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    with pytest.raises(queue.GhError) as caught:
        poll(repo, tmp_path, partial(queue._gh, sleep=lambda _: None), commands)
    assert caught.value.transient
    assert len(writes) == 1
    assert len(gh.comments) == 2
    assert gh.labels == {"spec:draft"}
    assert not gh.prs

    poll(repo, tmp_path, gh, commands)
    assert len(commands.calls) == 1
    assert len(gh.comments) == 2
    assert gh.labels == {"needs-human"}
