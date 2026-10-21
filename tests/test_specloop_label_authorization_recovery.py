"""Shared label authorization and rejection recovery at injected GitHub boundaries.

Stage-specific placement of the gate is covered by the intake integration tasks.
Here the real selection, membership, authorization and per-item loop run together.
"""

import importlib.util
import json
import subprocess
from copy import deepcopy
from functools import partial

import pytest

from nightshift import queue, specloop_intake


pytestmark = pytest.mark.real_trust
REPO = "owner/sample"
REPEATED = "issue-intake/rejection-comment-idempotency/repeated-rejection-comments-once"
LOST = "issue-intake/rejection-comment-idempotency/lost-comment-response-reconciled"
NEW = "issue-intake/rejection-comment-idempotency/new-rejection-has-separate-explanation"


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
