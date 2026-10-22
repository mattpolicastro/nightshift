"""Shared label authorization and rejection recovery at injected GitHub boundaries.

Includes proposal receipt, durable job and open-PR delivery recovery through
the production entry point, selection, membership and per-item loop.
"""

import importlib.util
import json
import os
import subprocess
from contextlib import ExitStack
from copy import deepcopy
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import daemon, queue, specloop_intake
from nightshift.config import Config
from test_engine_routing import DraftGh
from test_specloop_approval import ApprovalGh, Commands as AdmissionCommands, CHANGE, HEAD
from test_specloop_intake import BlockedDraftGh, Commands, idea_file, repo
from test_specloop_label_authorization import revision_case, assert_no_revision_reads
from test_specloop_spec_revision import REVISED


pytestmark = pytest.mark.real_trust
REPO = "owner/sample"
REPEATED = "issue-intake/rejection-comment-idempotency/repeated-rejection-comments-once"
LOST = "issue-intake/rejection-comment-idempotency/lost-comment-response-reconciled"
NEW = "issue-intake/rejection-comment-idempotency/new-rejection-has-separate-explanation"
RECEIPT = "issue-intake/authorization-preserves-intake-recovery/receipt-cannot-bypass-authorization"
TRUSTED = "issue-intake/latest-label-actor-authorization/trusted-reapplication-recovers"


class GitHub:
    """Remote state survives new intake module instances and runner wrappers."""

    def __init__(self, label="spec:draft"):
        self.calls = []
        self.events = {7: [], 8: []}
        self.labels = {7: {"unrelated"}, 8: {"unrelated"}}
        self.comments = {7: [{"id": 100, "body": "Earlier human comment"}], 8: []}
        self.collaborators = set()
        self.members = set()
        self.owner_type = "User"
        self.apply(7, label, "outsider")
        self.apply(8, label, "owner")

    def apply(self, number, label, actor, event="labeled"):
        self.events[number].append({
            "id": len(self.events[number]) + 1, "event": event,
            "label": {"name": label}, "actor": {"login": actor},
        })
        if event == "labeled":
            self.labels[number].add(label)
        else:
            self.labels[number].discard(label)

    @property
    def writes(self):
        return [call for call in self.calls if call[:2] in (
            ["issue", "comment"], ["pr", "comment"],
        ) or "--method" in call]

    def __call__(self, args):
        self.calls.append(args)
        if args == ["api", f"repos/{REPO}"]:
            return json.dumps({"owner": {"login": "owner", "type": self.owner_type}})
        if args[0] == "api" and "/collaborators/" in args[1]:
            assert args[2:] == ["--include"]
            status = 204 if args[1].rsplit("/", 1)[1] in self.collaborators else 404
            return f"HTTP/2.0 {status} Status\r\n\r\n"
        if args[0] == "api" and args[1].startswith("orgs/owner/members/"):
            assert args[2:] == ["--include"]
            status = 204 if args[1].rsplit("/", 1)[1] in self.members else 404
            return f"HTTP/2.0 {status} Status\r\n\r\n"
        if args[0] == "api" and args[1].endswith(("/events", "/comments")):
            number = int(args[1].split("/")[-2])
            resource = args[1].rsplit("/", 1)[1]
            assert args == [
                "api", f"repos/{REPO}/issues/{number}/{resource}", "--paginate", "--slurp",
            ]
            items = (self.events if resource == "events" else self.comments)[number]
            # The most recent comment/event is always on a later page. Include
            # an empty page so traversal cannot treat one as the end of history.
            return json.dumps([items[:1], [], items[1:]])
        if args[:2] in (["issue", "comment"], ["pr", "comment"]):
            number = int(args[2])
            assert args[3:6] == ["--repo", REPO, "--body"]
            self.comments[number].append({"id": 100 + len(self.comments[number]), "body": args[6]})
            return ""
        if args[0] == "api" and "PATCH" in args:
            comment_id = int(args[1].rsplit("/", 1)[1])
            assert args[1] == f"repos/{REPO}/issues/comments/{comment_id}"
            assert args[2:5] == ["--method", "PATCH", "-f"]
            comment = next(c for comments in self.comments.values() for c in comments
                           if c["id"] == comment_id)
            assert args[5].startswith("body=")
            comment["body"] = args[5][5:]
            return ""
        pytest.fail(f"Unexpected GitHub operation (including any label mutation): {args}")


def poll(gh, *, module=specloop_intake, runner=None, numbers=(7,), kind="issue",
         work=None, on_error=None):
    """Exercise the shared gate's return contract using the production item loop."""
    runner = runner or gh

    def act(number):
        events = module._latest_label_events(
            REPO, number, gh.labels[number] - {"unrelated"}, runner=runner,
        )
        if not module._authorize_label_events(REPO, number, events, runner=runner, kind=kind):
            return None
        if work is not None:
            work.append(number)
        return {"number": number}

    return module._each(numbers, act, on_error=on_error)


def restarted_intake():
    # Load fresh module globals, with no local authorization/comment cache,
    # while retaining only the fake remote GitHub service across the restart.
    spec = importlib.util.spec_from_file_location("nightshift._restarted_intake", specloop_intake.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.scenario(REPEATED)
@pytest.mark.parametrize("kind,label", [
    ("issue", "spec:draft"), ("issue", "agent:ready"),
    ("pr", "spec:approved"), ("pr", "agent:revise"), ("issue", "custom:draft"),
])
def test_repeated_rejection_and_restart_leave_comment_and_labels_unchanged(kind, label):
    gh, work = GitHub(label), []
    original_labels = deepcopy(gh.labels)
    assert poll(gh, kind=kind, numbers=(7, 8), work=work) == [{"number": 8}]
    assert work == [8]
    assert len(gh.comments[7]) == 2
    explanation = gh.comments[7][-1]["body"]
    assert explanation.startswith(queue.SELF_COMMENT_PREFIX)
    assert f"`{label}`" in explanation and "`outsider`" in explanation
    assert all(association in explanation for association in queue.TRUSTED_ASSOCIATIONS)
    assert "trusted actor" in explanation and "remove and reapply" in explanation
    assert "<!-- nightshift:draft:label-authorization-7:1 -->" in explanation
    saved = deepcopy(gh.comments)
    writes = gh.writes.copy()

    for module in (specloop_intake, restarted_intake(), restarted_intake()):
        assert poll(gh, module=module, kind=kind, numbers=(7, 8)) == [{"number": 8}]

    assert gh.comments == saved
    assert gh.writes == writes
    assert gh.labels == original_labels
    assert not gh.comments[8]


@pytest.mark.scenario(NEW)
@pytest.mark.parametrize("kind", ["issue", "pr"])
def test_new_untrusted_application_has_its_own_unchanged_explanation(kind):
    gh = GitHub()
    assert poll(gh, kind=kind) == []
    original = deepcopy(gh.comments[7][-1])
    gh.apply(7, "spec:draft", "outsider", "unlabeled")
    gh.apply(7, "spec:draft", "another-outsider")
    for module in (specloop_intake, restarted_intake(), specloop_intake):
        assert poll(gh, module=module, kind=kind) == []

    assert len(gh.comments[7]) == 3
    assert gh.comments[7][1] == original
    assert "`another-outsider`" in gh.comments[7][2]["body"]
    assert "<!-- nightshift:draft:label-authorization-7:3 -->" in gh.comments[7][2]["body"]
    assert len(gh.writes) == 2
    assert gh.labels[7] == {"unrelated", "spec:draft"}


@pytest.mark.scenario(REPEATED)
def test_both_intake_labels_are_checked_and_each_denial_is_explained():
    gh = GitHub()
    gh.apply(7, "agent:ready", "another-outsider")
    original_labels = deepcopy(gh.labels)
    assert poll(gh) == []
    assert len(gh.comments[7]) == 3
    assert "<!-- nightshift:draft:label-authorization-7:1 -->" in gh.comments[7][1]["body"]
    assert "<!-- nightshift:draft:label-authorization-7:2 -->" in gh.comments[7][2]["body"]
    assert all("remove the unauthorized alias" in c["body"] for c in gh.comments[7][1:])
    assert poll(gh, module=restarted_intake()) == []
    assert len(gh.writes) == 2
    assert gh.labels == original_labels


@pytest.mark.parametrize("actor", [None, {}, {"login": "ghost"}, {"login": "bad/login"}])
def test_unavailable_identity_still_gets_one_explanation(actor):
    gh = GitHub()
    gh.events[7][-1]["actor"] = actor
    assert poll(gh) == []
    assert poll(gh, module=restarted_intake()) == []
    assert "unavailable actor identity" in gh.comments[7][-1]["body"]
    assert len(gh.writes) == 1


@pytest.mark.parametrize("relationship", ["OWNER", "COLLABORATOR", "MEMBER"])
def test_trusted_reapplication_and_membership_changes_are_rechecked(relationship):
    gh = GitHub()
    assert poll(gh) == []
    original = deepcopy(gh.comments)
    writes = gh.writes.copy()
    actor = "owner" if relationship == "OWNER" else "helper"
    gh.apply(7, "spec:draft", "outsider", "unlabeled")
    gh.apply(7, "spec:draft", actor)
    if relationship == "COLLABORATOR":
        gh.collaborators.add(actor)
    elif relationship == "MEMBER":
        gh.owner_type = "Organization"
        gh.members.add(actor)
    assert poll(gh, module=restarted_intake()) == [{"number": 7}]
    assert gh.comments == original and gh.writes == writes

    gh.collaborators.clear()
    gh.members.clear()
    gh.owner_type = "Organization"
    assert poll(gh) == []
    assert len(gh.comments[7]) == 3
    # A rejection is not cached as a permanent denial either.
    gh.collaborators.add(actor)
    assert poll(gh) == [{"number": 7}]
    assert len(gh.writes) == 2


def test_trusted_alias_does_not_hide_rejection_and_lookup_failure_writes_nothing():
    gh = GitHub()
    gh.apply(7, "agent:ready", "owner")
    saved = deepcopy(gh.labels)

    def failing_lookup(args):
        if args == ["api", f"repos/{REPO}"] and sum(
                call == args for call in gh.calls) == 1:
            raise queue.GhError("membership lookup unavailable", transient=True)
        return gh(args)

    with pytest.raises(queue.GhError):
        poll(gh, runner=failing_lookup)
    assert not gh.writes
    assert poll(gh) == []
    assert len(gh.writes) == 1
    assert "`spec:draft`" in gh.comments[7][-1]["body"]
    assert gh.labels == saved


@pytest.mark.scenario(LOST)
@pytest.mark.parametrize("after_write", [False, True])
def test_failed_comment_create_recovers_on_next_poll_after_restart(after_write):
    gh, work = GitHub(), []
    saved = deepcopy(gh.labels)

    def failing_post(args):
        if args[:2] == ["issue", "comment"]:
            if after_write:
                gh(args)
            raise queue.GhError("response lost", transient=True)
        return gh(args)

    with pytest.raises(queue.GhError):
        poll(gh, runner=failing_post, work=work)
    assert len(gh.comments[7]) == (2 if after_write else 1)
    assert poll(gh, module=restarted_intake(), work=work) == []
    assert poll(gh, work=work) == []
    assert len(gh.comments[7]) == 2
    assert len(gh.writes) == 1
    assert not work
    assert gh.labels == saved


@pytest.mark.scenario(LOST)
@pytest.mark.parametrize("response", [
    "not json", "null", "{}", "[{}]", "[[null]]", '[[{"body": 42}]]',
])
def test_malformed_comment_lookup_does_not_authorize_a_post(response):
    gh, work = GitHub(), []

    def bad_comments(args):
        if args[0] == "api" and args[1].endswith("/comments"):
            return response
        return gh(args)

    with pytest.raises((RuntimeError, ValueError)):
        poll(gh, runner=bad_comments, work=work)
    assert not gh.writes and not work


@pytest.mark.scenario(LOST)
@pytest.mark.parametrize("kind", ["issue", "pr"])
@pytest.mark.parametrize("failure", ["before-write", "after-write", "after-patch"])
def test_real_gh_reconciles_create_before_retrying(monkeypatch, kind, failure):
    gh, work, attempts, waits = GitHub(), [], [], []
    saved = deepcopy(gh.labels)

    def run(args, **kwargs):
        assert args[0] == "gh"
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        command = args[1:]
        attempts.append(command)
        first_create = (command[:2] == [kind, "comment"] and
                        sum(c[:2] == [kind, "comment"] for c in attempts) == 1)
        first_patch = "PATCH" in command and sum("PATCH" in c for c in attempts) == 1
        if first_create or (first_patch and failure == "after-patch"):
            if not first_create or failure != "before-write":
                gh(command)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        return subprocess.CompletedProcess(args, 0, gh(command), "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    runner = partial(queue._gh, sleep=waits.append)
    assert poll(gh, runner=runner, kind=kind, work=work) == []
    assert len(gh.comments[7]) == 2
    creates = [c for c in attempts if c[:2] == [kind, "comment"]]
    patches = [c for c in attempts if "PATCH" in c]
    assert len(creates) == (2 if failure == "before-write" else 1)
    assert len(patches) == {"before-write": 0, "after-write": 1, "after-patch": 2}[failure]
    assert all(c[-1] == "body=" + creates[0][-1] for c in patches)
    assert waits == ([2, 2] if failure == "after-patch" else [2])
    # Both the initial check and transport reconciliation fetch all pages.
    assert sum(c[0] == "api" and c[1].endswith("/comments") for c in attempts) == 2
    writes = gh.writes.copy()
    original = deepcopy(gh.comments)
    assert poll(gh, runner=runner, module=restarted_intake(), kind=kind, work=work) == []
    assert gh.writes == writes and gh.comments == original
    assert not work and gh.labels == saved


@pytest.mark.scenario(LOST)
@pytest.mark.parametrize("failure", ["initial-lookup", "before-write", "after-write"])
def test_real_gh_failed_comment_lookup_prevents_blind_post(monkeypatch, failure):
    gh, work, creates, errors = GitHub(), [], [], []
    saved = deepcopy(gh.labels)

    def run(args, **kwargs):
        assert args[0] == "gh"
        command = args[1:]
        if command[:2] == ["issue", "comment"]:
            creates.append(command)
            if failure == "after-write":
                gh(command)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        if (command[0] == "api" and command[1].endswith("/comments")
                and (failure == "initial-lookup" or creates)):
            return subprocess.CompletedProcess(args, 1, "", "HTTP 503: Service Unavailable")
        return subprocess.CompletedProcess(args, 0, gh(command), "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    runner = partial(queue._gh, sleep=lambda _: None)
    assert poll(gh, runner=runner, numbers=(7, 8), work=work,
                on_error=lambda number, exc: errors.append((number, exc))) == [{"number": 8}]
    assert len(errors) == 1 and errors[0][0] == 7
    assert isinstance(errors[0][1], queue.GhError) and errors[0][1].transient
    assert work == [8]
    assert len(creates) == (0 if failure == "initial-lookup" else 1)
    assert len(gh.comments[7]) == (2 if failure == "after-write" else 1)
    assert gh.labels == saved
    assert not any("PATCH" in c for c in gh.calls)

    # A healthy later poll either finds the lost successful write on page 3
    # or creates the still-missing explanation, never another triggered action.
    assert poll(gh, module=restarted_intake(), work=work) == []
    assert len(gh.comments[7]) == 2 and len(gh.writes) == 1
    assert work == [8]


def forbidden_recovery_work(*args, **kwargs):
    pytest.fail("recovery must not dispatch work or report rejection as pending/error")


def proposal_poll(repo, state, gh, *, module=specloop_intake, git_runner=forbidden_recovery_work):
    return module.propose_pending(
        repo, queue.Labels(), state, runner=gh, specloop_runner=forbidden_recovery_work,
        git_runner=git_runner, launcher=forbidden_recovery_work,
        on_error=forbidden_recovery_work, on_pending=forbidden_recovery_work,
    )


def files_under(path):
    return {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def reapply(gh, label, actor):
    gh.label_event(label, "unlabeled", actor)
    gh.label_event(label, "labeled", actor)


def assert_rejection_only(gh, original_comments, label, kind="issue"):
    assert gh.comments[:-1] == original_comments
    body = gh.comments[-1]["body"]
    assert body.startswith(queue.SELF_COMMENT_PREFIX)
    assert f"`{label}`" in body and "`outsider`" in body
    assert all(association in body for association in queue.TRUSTED_ASSOCIATIONS)
    assert "trusted actor" in body and "remove and reapply" in body
    assert len([c for c in gh.calls if c[:2] == [kind, "comment"]]) == 1
    assert not any("PATCH" in c or c[:2] in (
        ["issue", "edit"], ["pr", "edit"], ["pr", "create"],
    ) for c in gh.calls)


@pytest.mark.scenario(RECEIPT)
@pytest.mark.scenario(REPEATED)
@pytest.mark.scenario(TRUSTED)
@pytest.mark.parametrize("recovery", ["receipt", "legacy", "open-pr", "open-pr-with-receipt"])
@pytest.mark.parametrize("armed,both", [("spec:draft", False), ("agent:ready", False),
                                        ("agent:ready", True)])
def test_proposal_recovery_requires_current_actor_and_preserves_completed_work(
    repo, tmp_path, recovery, armed, both,
):
    gh, commands = BlockedDraftGh(armed, {6: "CLOSED"}), Commands()
    if both:
        gh.label_event("spec:draft", "labeled")
    path = tmp_path / "proposals/owner__sample/7.json"
    if recovery != "open-pr":
        commands.push_fails = True
        with pytest.raises(specloop_intake.DeliveryError, match="could not push"):
            specloop_intake.propose_pending(
                repo, queue.Labels(), tmp_path, runner=gh,
                specloop_runner=commands, git_runner=commands,
            )
        if recovery == "legacy":
            legacy = json.loads(path.read_text())
            legacy.pop("round_id")
            # Old receipts carry only integer application IDs, never actors.
            assert legacy["drafting_events"] == {
                event["label"]["name"]: event["id"] for event in gh.events
            }
            path.write_text(json.dumps(legacy))
    if recovery.startswith("open-pr"):
        gh.prs = [{"number": 8, "state": "OPEN", "headRefOid": "abc123", "isDraft": True}]
    commands.push_fails = False
    commands.calls.clear()
    gh.calls.clear()
    gh.blocker_reads.clear()
    # Neither rejection nor trusted delivery may consult this dependency.
    gh.states[6] = queue.GhError("blocker lookup unavailable", transient=True)
    reapply(gh, armed, "outsider")
    original_labels, original_comments = gh.labels.copy(), deepcopy(gh.comments)
    original_prs, original_files = deepcopy(gh.prs), files_under(tmp_path)

    for module in (specloop_intake, specloop_intake, restarted_intake()):
        assert proposal_poll(repo, tmp_path, gh, module=module) == []
        assert files_under(tmp_path) == original_files
        assert gh.labels == original_labels and gh.prs == original_prs
        assert gh.blocker_reads == []
        assert_rejection_only(gh, original_comments, armed)
    rejection = deepcopy(gh.comments[-1])
    assert f"label-authorization-7:{gh.events[-1]['id']}" in rejection["body"]

    reapply(gh, armed, "maintainer")
    results = proposal_poll(repo, tmp_path, gh, module=restarted_intake(), git_runner=commands)
    assert gh.comments[-2] == rejection
    assert gh.pr_url in gh.comments[-1]["body"]
    assert gh.labels == set() and len(gh.prs) == 1
    assert gh.blocker_reads == []
    assert [c[0] for c in commands.calls] == ([] if recovery.startswith("open-pr") else ["git"])
    if recovery != "open-pr":
        saved = json.loads(original_files[path.relative_to(tmp_path)])
        assert results == [saved if recovery.startswith("open-pr") else {**saved, "delivered": True}]
        assert json.loads(path.read_text()) == results[0]
    else:
        assert results == [] and not path.exists()
    # Delivery cannot overwrite or replace an earlier invocation's idea file.
    assert {p: data for p, data in files_under(tmp_path).items() if p != path.relative_to(tmp_path)} == {
        p: data for p, data in original_files.items() if p != path.relative_to(tmp_path)
    }
    writes = deepcopy(gh.comments)
    assert proposal_poll(repo, tmp_path, gh) == []
    assert gh.comments == writes


@pytest.mark.scenario(RECEIPT)
@pytest.mark.scenario(REPEATED)
@pytest.mark.scenario(TRUSTED)
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
def test_untrusted_reapplication_holds_running_and_finished_job_and_its_payload(
    repo, tmp_path, monkeypatch, armed,
):
    gh, commands, launches, pending = BlockedDraftGh(armed, {6: "CLOSED"}), Commands(), [], []
    path = tmp_path / "proposals/owner__sample/7.json"
    job_module = specloop_intake.specloop_job
    monkeypatch.setattr(job_module, "_children", {})
    with ExitStack() as locks:
        def launch(args, **kwargs):
            launches.append(args)
            # Retain the real lifetime lock as a detached worker would, without
            # starting a subprocess. Collection and persistence remain real.
            locks.callback(os.close, os.dup(kwargs["pass_fds"][0]))
            return SimpleNamespace(poll=lambda: None)

        assert specloop_intake.propose_pending(
            repo, queue.Labels(), tmp_path, runner=gh, launcher=launch,
            git_runner=forbidden_recovery_work, on_pending=pending.append,
        ) == []
        assert pending == [7] and len(launches) == 1
        saved = json.loads(path.read_text())
        directory = Path(launches[0][-2])
        assert directory.name == saved["job"]
        request = json.loads((directory / "request.json").read_text())
        payload = idea_file(request["args"])
        assert payload.read_text() == saved["idea"]
        reapply(gh, armed, "outsider")
        gh.states[6] = "OPEN"
        gh.calls.clear()
        gh.blocker_reads.clear()
        original_comments, original_labels = deepcopy(gh.comments), gh.labels.copy()

        for finished in (False, True):
            if finished:
                job_module.finalize(directory, {"result": commands.result})
                locks.close()
            snapshot = files_under(tmp_path)
            for module in (specloop_intake, restarted_intake()):
                assert proposal_poll(repo, tmp_path, gh, module=module) == []
                assert files_under(tmp_path) == snapshot
                assert gh.labels == original_labels and not gh.prs
                assert_rejection_only(gh, original_comments, armed)
            assert not (directory / "collected.json").exists()
            assert path.read_text() == json.dumps(saved) + "\n"
        rejection = deepcopy(gh.comments[-1])

        reapply(gh, armed, "maintainer")
        receipt, = proposal_poll(repo, tmp_path, gh, module=restarted_intake(), git_runner=commands)
        assert receipt == {**saved, "result": commands.result, "delivered": True}
        assert json.loads(path.read_text()) == receipt
        assert (directory / "collected.json").is_file()
        assert json.loads((directory / "request.json").read_text()) == request
        assert payload.read_text() == saved["idea"]
        assert len(launches) == 1 and [c[0] for c in commands.calls] == ["git"]
        assert gh.blocker_reads == [] and gh.labels == set()
        assert gh.comments[-2] == rejection and len(gh.prs) == 1
        assert proposal_poll(repo, tmp_path, gh) == []


@pytest.mark.scenario(RECEIPT)
@pytest.mark.scenario(REPEATED)
def test_rejected_open_pr_recovery_does_not_prevent_next_eligible_issue(repo, tmp_path):
    gh, neighbor = BlockedDraftGh("spec:draft", {6: "OPEN"}), DraftGh(("spec:draft",))
    gh.prs = [{"number": 8, "state": "OPEN", "headRefOid": "abc123"}]
    reapply(gh, "spec:draft", "outsider")
    original_comments = deepcopy(gh.comments)
    branch = "spec/sample-issue-9-add-a-gate"
    commands = Commands(branch=branch)

    def github(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps(json.loads(gh(args)) + [
                {**item, "number": 9} for item in json.loads(neighbor(args))
            ])
        if (args[:3] in (["issue", "view", "9"], ["issue", "edit", "9"], ["issue", "comment", "9"])
                or (args[0] == "api" and ("/issues/9/" in args[1] or args[1].endswith("/issues/9")))
                or (args[:2] == ["pr", "list"] and branch in args)
                or args[:2] in (["pr", "create"], ["pr", "edit"])
                or (args[:2] == ["pr", "view"] and args[-1] == "state,url")):
            # The shared fake models issue 7; route the independent issue's API
            # identity lookup to that fixture as well as its paginated resources.
            routed = [arg.replace("/issues/9", "/issues/7") for arg in args]
            return neighbor(routed)
        return gh(args)

    for module in (specloop_intake, restarted_intake(), specloop_intake):
        results = module.propose_pending(
            repo, queue.Labels(), tmp_path, runner=github,
            specloop_runner=commands, git_runner=commands, on_error=forbidden_recovery_work,
        )
        if results:
            assert len(results) == 1 and results[0]["issue"] == 9 and results[0]["delivered"]
        assert_rejection_only(gh, original_comments, "spec:draft")
    assert [c[0] for c in commands.calls] == ["specloop", "git"]
    assert neighbor.labels == set() and len(neighbor.prs) == 1
    assert json.loads((tmp_path / "proposals/owner__sample/9.json").read_text())["delivered"]
    assert not (tmp_path / "proposals/owner__sample/7.json").exists()
    assert gh.labels == {"spec:draft"} and gh.blocker_reads == []


def admission_poll(repo, state, gh, *, module=specloop_intake,
                   commands=forbidden_recovery_work, labels=queue.Labels()):
    return module.admit_pending(
        repo, labels, state, runner=gh, specloop_runner=commands, git_runner=commands,
        launcher=forbidden_recovery_work, on_pending=forbidden_recovery_work,
    )


@pytest.mark.scenario("issue-intake/authorization-preserves-intake-recovery/approval-head-remains-protected")
@pytest.mark.parametrize("unauthorized_reapplication", [False, True])
def test_admission_head_binding_survives_denial_and_requires_trusted_reapproval(
    repo, tmp_path, unauthorized_reapplication,
):
    gh = ApprovalGh(("spec:approved", "spec:review", "agent:revise"))
    # An authorized approval alongside revision records the reviewed head.
    assert admission_poll(repo, tmp_path, gh) == []
    path = tmp_path / "approvals/owner__sample/8.json"
    saved = path.read_bytes()
    assert json.loads(saved) == {"event": 1, "head": HEAD}
    gh.head = "b" * 40
    gh.label_event("agent:revise", "unlabeled")
    gh.calls.clear()
    if unauthorized_reapplication:
        reapply(gh, "spec:approved", "outsider")
        snapshot, labels = files_under(tmp_path), gh.labels.copy()
        for module in (specloop_intake, restarted_intake()):
            assert admission_poll(repo, tmp_path, gh, module=module) == []
            assert files_under(tmp_path) == snapshot and gh.labels == labels
            assert_rejection_only(gh, [], "spec:approved", kind="pr")
    else:
        # The same trusted event cannot cover a later tip, even after restart.
        assert admission_poll(repo, tmp_path, gh, module=restarted_intake()) == []
        assert "changed after it was approved" in gh.comments[-1]["body"]
        assert gh.labels == {"spec:review"}
    assert path.read_bytes() == saved
    original_comments = deepcopy(gh.comments)
    reapply(gh, "spec:approved", "maintainer")
    event_id = gh.events[-1]["id"]

    class MovingHead(AdmissionCommands):
        def __call__(self, args, **kwargs):
            result = super().__call__(args, **kwargs)
            if args[0] == "git":
                result.stdout = gh.head
            return result

    commands = MovingHead()
    commands.result["head"] = gh.head
    result, = admission_poll(repo, tmp_path, gh, commands=commands, module=restarted_intake())
    assert result["outcome"] == "admitted"
    assert json.loads(path.read_text()) == {"event": event_id, "head": gh.head}
    receipt = json.loads((tmp_path / "admissions/owner__sample/8.json").read_text())
    assert type(receipt["approval_event"]) is int and receipt["approval_event"] == event_id
    assert receipt["head"] == gh.head and receipt["delivered"]
    assert len(commands.admissions) == 1
    assert commands.admissions[0][commands.admissions[0].index("--head") + 1] == gh.head
    assert gh.comments[:-1] == original_comments and "build has started" in gh.comments[-1]["body"]
    assert gh.labels == set()


@pytest.mark.scenario(RECEIPT)
@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/approval-admits/refused-admission-explained")
@pytest.mark.parametrize("outcome", ["admitted", "refused"])
@pytest.mark.parametrize("configured", [False, True])
def test_admission_receipt_waits_for_trusted_reapproval(repo, tmp_path, outcome, configured):
    labels = queue.Labels(spec_approved="custom:approved") if configured else queue.Labels()
    armed = labels.spec_approved
    gh, commands = ApprovalGh((armed, "spec:review")), AdmissionCommands(outcome)

    def lost_delivery(args):
        if args[:2] == ["pr", "comment"]:
            raise queue.GhError("delivery unavailable", transient=True)
        return gh(args)

    with pytest.raises(queue.GhError):
        admission_poll(repo, tmp_path, lost_delivery, commands=commands, labels=labels)
    path = tmp_path / "admissions/owner__sample/8.json"
    original = json.loads(path.read_text())
    assert not original.get("delivered") and type(original["approval_event"]) is int
    assert len(commands.admissions) == 1
    reapply(gh, armed, "outsider")
    snapshot, original_labels = files_under(tmp_path), gh.labels.copy()
    commands.calls.clear()
    gh.calls.clear()
    for module in (specloop_intake, restarted_intake()):
        assert admission_poll(repo, tmp_path, gh, module=module, labels=labels) == []
        assert files_under(tmp_path) == snapshot and gh.labels == original_labels
        assert_rejection_only(gh, [], armed, kind="pr")
    rejection = deepcopy(gh.comments[-1])

    reapply(gh, armed, "maintainer")
    event_id = gh.events[-1]["id"]
    result, = admission_poll(repo, tmp_path, gh, commands=commands,
                             module=restarted_intake(), labels=labels)
    assert result["outcome"] == outcome
    receipt = json.loads(path.read_text())
    assert type(receipt["approval_event"]) is int and receipt["approval_event"] == event_id
    assert receipt["delivered"] and receipt["head"] == original["head"]
    assert gh.comments[0] == rejection and len(gh.comments) == 2
    if outcome == "admitted":
        # Status recovers the existing run without git or another admission.
        assert commands.calls == [["specloop", "status", "--json"]]
        assert commands.runs == [{"change": CHANGE, "pr": gh.pr_url, "status": "running"}]
        assert "build has started" in gh.comments[-1]["body"] and gh.labels == set()
    else:
        # A new approval retries a refused round, even with an unchanged head.
        assert len(commands.admissions) == 1 and not commands.runs
        assert commands.result["reasons"][0] in gh.comments[-1]["body"]
        assert gh.labels == {"spec:review", "needs-human"}
    assert admission_poll(repo, tmp_path, gh, labels=labels) == []


@pytest.mark.scenario(RECEIPT)
def test_unauthorized_approval_preserves_running_and_finished_admission_job(
    repo, tmp_path, monkeypatch,
):
    gh, commands, launches = ApprovalGh(), AdmissionCommands(), []
    job_module = specloop_intake.specloop_job
    monkeypatch.setattr(job_module, "_children", {})
    path = tmp_path / "admissions/owner__sample/8.json"
    with ExitStack() as locks:
        def launch(args, **kwargs):
            launches.append(args)
            locks.callback(os.close, os.dup(kwargs["pass_fds"][0]))
            return SimpleNamespace(poll=lambda: None)

        assert specloop_intake.admit_pending(
            repo, queue.Labels(), tmp_path, runner=gh, launcher=launch,
            git_runner=commands, status_runner=commands,
        ) == []
        assert len(launches) == 1
        saved = json.loads(path.read_text())
        directory = Path(launches[0][-2])
        assert directory.name == saved["job"]
        reapply(gh, "spec:approved", "outsider")
        gh.calls.clear()
        original_labels = gh.labels.copy()
        for finished in (False, True):
            if finished:
                job_module.finalize(directory, {"result": commands.result})
                commands.runs = [{"change": CHANGE, "pr": gh.pr_url, "status": "running"}]
                locks.close()
            snapshot = files_under(tmp_path)
            for module in (specloop_intake, restarted_intake()):
                assert admission_poll(repo, tmp_path, gh, module=module) == []
                assert files_under(tmp_path) == snapshot and gh.labels == original_labels
                assert_rejection_only(gh, [], "spec:approved", kind="pr")
            assert not (directory / "collected.json").exists()
        rejection = deepcopy(gh.comments[-1])
        reapply(gh, "spec:approved", "maintainer")
        commands.calls.clear()
        result, = admission_poll(repo, tmp_path, gh, commands=commands, module=restarted_intake())
        assert result["outcome"] == "admitted"
        assert json.loads(path.read_text())["delivered"]
        assert (directory / "collected.json").is_file()
        assert len(launches) == 1 and commands.calls == [["specloop", "status", "--json"]]
        assert gh.comments[0] == rejection and len(gh.comments) == 2 and gh.labels == set()


def revision_poll(repo, state, gh, *, module=specloop_intake,
                  commands=forbidden_recovery_work, labels=queue.Labels()):
    return module.revise_pending(
        repo, labels, state, runner=gh, specloop_runner=commands, git_runner=commands,
        launcher=forbidden_recovery_work, on_error=forbidden_recovery_work,
        on_pending=forbidden_recovery_work,
    )


@pytest.mark.scenario(RECEIPT)
@pytest.mark.scenario(TRUSTED)
@pytest.mark.scenario("pr-revision/revision-actor-gate/unauthorized-revision-ignored-at-either-stage")
@pytest.mark.parametrize("stage", ["spec-revision", "build-revision"])
@pytest.mark.parametrize("configured", [False, True])
def test_revision_receipt_survives_untrusted_reapplication(repo, tmp_path, stage, configured):
    gh, commands = revision_case(stage)
    labels = queue.Labels(revise="custom:revise") if configured else queue.Labels()
    if stage == "build-revision":
        commands.revise_label = labels.revise
    if configured:
        # Configure the trigger before the first poll; preserve fixture event IDs.
        gh.labels.remove("agent:revise")
        gh.labels.add(labels.revise)
        for event in gh.events:
            if event["label"]["name"] == "agent:revise":
                event["label"]["name"] = labels.revise

    def lost_delivery(args):
        if args[:2] == ["pr", "comment"]:
            raise queue.GhError("delivery unavailable", transient=True)
        return gh(args)

    # A completed result exists, but its delivery is still armed.
    with pytest.raises(queue.GhError):
        specloop_intake.revise_pending(
            repo, labels, tmp_path, runner=lost_delivery,
            specloop_runner=commands, git_runner=commands,
        )
    path = tmp_path / "revisions/owner__sample/8.json"
    original = json.loads(path.read_text())
    assert "result" in original and not original.get("delivered")
    reapply(gh, labels.revise, "outsider")
    rejected_event = gh.events[-1]["id"]
    snapshot, original_labels = files_under(tmp_path), gh.labels.copy()
    commands.calls.clear()
    gh.calls.clear()
    for module in (specloop_intake, restarted_intake()):
        assert revision_poll(repo, tmp_path, gh, module=module, labels=labels) == []
        assert files_under(tmp_path) == snapshot and gh.labels == original_labels
        assert_rejection_only(gh, [], labels.revise, kind="pr")
        assert_no_revision_reads(gh)
    rejection = deepcopy(gh.comments[-1])
    assert f"label-authorization-8:{rejected_event}" in rejection["body"]

    reapply(gh, labels.revise, "maintainer")
    authorized_event = gh.events[-1]["id"]
    commands.fetch_head = gh.head
    result, = revision_poll(repo, tmp_path, gh, module=restarted_intake(),
                            commands=commands, labels=labels)
    receipt = json.loads(path.read_text())
    # Revision event IDs define rounds: a new application requests a new round,
    # rather than delivering the older event's result as this event's work.
    assert receipt["event"] == authorized_event != original["event"]
    assert receipt["round_id"] == str(authorized_event) and receipt["delivered"]
    assert result["outcome"] == ("drafted" if stage == "spec-revision" else "revised")
    dispatch = "propose" if stage == "spec-revision" else "revise"
    assert sum(c[:2] == ["specloop", dispatch] for c in commands.calls) == 1
    assert gh.comments[0] == rejection and len(gh.comments) == 2
    assert labels.revise not in gh.labels
    assert all((tmp_path / name).read_bytes() == data for name, data in snapshot.items()
               if name != path.relative_to(tmp_path))
    calls = commands.calls.copy()
    assert revision_poll(repo, tmp_path, gh, labels=labels) == []
    assert commands.calls == calls


@pytest.mark.scenario(RECEIPT)
@pytest.mark.parametrize("stage", ["spec-revision", "build-revision"])
@pytest.mark.parametrize("recovery", ["receipt", "job"])
def test_revision_recovery_rechecks_membership_and_reuses_same_event(
    repo, tmp_path, monkeypatch, stage, recovery,
):
    gh, commands = revision_case(stage)
    job_module = specloop_intake.specloop_job
    monkeypatch.setattr(job_module, "_children", {})
    path = tmp_path / "revisions/owner__sample/8.json"
    launches = []
    trusted = True

    def github(args):
        if not trusted and args == ["api", "repos/owner/sample/collaborators/maintainer", "--include"]:
            gh.calls.append(args)
            return "HTTP/2.0 404 Not Found\r\n\r\n"
        return gh(args)

    with ExitStack() as locks:
        def launch(args, **kwargs):
            launches.append(args)
            locks.callback(os.close, os.dup(kwargs["pass_fds"][0]))
            return SimpleNamespace(poll=lambda: None)

        if recovery == "job":
            assert specloop_intake.revise_pending(
                repo, queue.Labels(), tmp_path, runner=github, launcher=launch,
                git_runner=commands, status_runner=commands,
            ) == []
            assert len(launches) == 1
            directory = Path(launches[0][-2])
        else:
            def lost_delivery(args):
                if args[:2] == ["pr", "comment"]:
                    raise queue.GhError("delivery unavailable", transient=True)
                return github(args)

            with pytest.raises(queue.GhError):
                specloop_intake.revise_pending(
                    repo, queue.Labels(), tmp_path, runner=lost_delivery,
                    git_runner=commands, specloop_runner=commands,
                )
        original = json.loads(path.read_text())
        original_labels = gh.labels.copy()
        trusted = False  # Current membership is rechecked even for this same event.
        commands.calls.clear()
        gh.calls.clear()
        for finished in (False, True) if recovery == "job" else (True,):
            if recovery == "job" and finished:
                job_module.finalize(directory, {"result": commands.result})
                locks.close()
            snapshot = files_under(tmp_path)
            for module in (specloop_intake, restarted_intake()):
                assert revision_poll(repo, tmp_path, github, module=module) == []
                assert files_under(tmp_path) == snapshot and gh.labels == original_labels
                assert len(gh.comments) == 1
                assert "label-authorization-8:" in gh.comments[0]["body"]
                assert_no_revision_reads(gh)
                assert not any("PATCH" in c or c[:2] == ["pr", "edit"] for c in gh.calls)
            if recovery == "job":
                assert not (directory / "collected.json").exists()
        rejection = deepcopy(gh.comments[0])
        assert sum(c[:2] == ["pr", "comment"] for c in gh.calls) == 1

        trusted = True
        gh.calls.clear()
        # A pause at the event read must also leave collection pending: actor
        # authorization has not completed, even if the job already finished.
        monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
        before_pause = files_under(tmp_path)

        def pause_after_events(args):
            response = github(args)
            if args[0] == "api" and args[1].endswith("/events"):
                daemon.PAUSE_FILE.touch()
            return response

        health = daemon.RepoHealth()
        daemon.poll_specloop(Config(repos=[repo]), health,
                             runner=pause_after_events, specloop_runner=commands)
        assert daemon.PAUSE_FILE.exists()
        assert gh.calls[-1][1].endswith("/events")
        assert files_under(tmp_path) == before_pause
        assert not health.failures and not health.item_failures and not health.announced
        assert not commands.calls and gh.comments == [rejection] and gh.labels == original_labels
        daemon.PAUSE_FILE.unlink()
        gh.calls.clear()
        result, = revision_poll(repo, tmp_path, github, commands=commands, module=restarted_intake())
        receipt = json.loads(path.read_text())
        assert receipt["event"] == original["event"] and receipt["delivered"]
        assert result == commands.result
        assert_no_revision_reads(gh)
        assert not any(c[0] == "specloop" for c in commands.calls)
        if stage == "spec-revision":
            assert len(commands.calls) == 1 and commands.calls[0][3] == "push"
            assert gh.head == REVISED
        else:
            assert not commands.calls
        if recovery == "job":
            assert receipt["job"] == original["job"] == directory.name
            assert len(launches) == 1 and (directory / "collected.json").exists()
        assert gh.comments[0] == rejection and len(gh.comments) == 2
        assert "agent:revise" not in gh.labels
