"""Label event selection and real actor relationship resolution."""

import json
import logging
import subprocess
from copy import deepcopy

import pytest

from manifest_fixtures import manifest_text

from nightshift import daemon, queue, specloop_intake
from nightshift.config import Config, Repo
from test_engine_routing import DraftGh
from test_specloop_approval import ApprovalGh, Commands, poll as poll_admission
from test_specloop_intake import BlockedDraftGh, Commands as DraftCommands, poll as poll_draft, repo
from test_specloop_intake import idea_file
from test_specloop_spec_revision import RevisionGh, Commands as RevisionCommands, HEAD, REVISED, TIP_DATE, seed_consumed
from test_specloop_build_revision import BuildCommands


pytestmark = pytest.mark.real_trust


def label_event(event_id=1, *, label="spec:approved", event="labeled", actor="owner"):
    return {"id": event_id, "event": event, "label": {"name": label},
            "actor": {"login": actor}, "created_at": "2026-10-01T00:00:00Z"}


class EventHistory:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        assert args == ["api", "repos/owner/sample/issues/8/events", "--paginate", "--slurp"]
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/latest-application-wins")
@pytest.mark.parametrize("label", ["spec:draft", "agent:ready", "spec:approved", "agent:revise", "custom:trigger"])
def test_latest_application_retains_new_actor_across_pages(label):
    trusted = label_event(1, label=label)
    removed = label_event(2, label=label, event="unlabeled")
    untrusted = label_event(3, label=label, actor="outsider")
    gh = EventHistory(json.dumps([
        [trusted], [removed], [],
        [untrusted, label_event(4, label="unrelated"), {"id": 5, "event": "commented"}],
    ]))

    selected = specloop_intake._latest_label_events("owner/sample", 8, {label}, runner=gh)

    # The actual principal and receipt ID come from the same newer event, even
    # though the older event was applied by the repository owner.
    assert selected == {label: untrusted}
    assert selected[label]["actor"]["login"] == "outsider"
    assert len(gh.calls) == 1


def test_selects_each_requested_label_and_preserves_id_only_interfaces():
    draft = label_event(1, label="spec:draft")
    ready = label_event(2, label="agent:ready")
    approval = label_event(4)
    gh = EventHistory(json.dumps([
        [draft, label_event(3)],
        [ready, label_event(5, event="unlabeled"), approval],
    ]))
    labels = {"spec:draft", "agent:ready", "spec:approved"}

    assert specloop_intake._latest_label_events("owner/sample", 8, labels, runner=gh) == {
        "spec:draft": draft, "agent:ready": ready, "spec:approved": approval,
    }
    # Chronological page order controls selection, not sorting by event IDs.
    ids = specloop_intake._label_events("owner/sample", 8, labels, runner=gh)
    assert ids == {"spec:draft": 1, "agent:ready": 2, "spec:approved": 4}
    assert all(type(event_id) is int for event_id in ids.values())
    assert specloop_intake._approval_event("owner/sample", 8, "spec:approved", runner=gh) == 4
    assert len(gh.calls) == 3


INVALID_IDS = [None, False, True, 0, -1, 1.5, 1.0, "2", "", [], {}]


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("event_id", INVALID_IDS)
@pytest.mark.parametrize("interface", ["_latest_label_events", "_label_events", "_approval_event"])
def test_invalid_latest_id_never_falls_back_to_an_older_application(event_id, interface):
    gh = EventHistory(json.dumps([[label_event()], [label_event(event_id)]]))
    label = "spec:approved" if interface == "_approval_event" else {"spec:approved"}
    with pytest.raises(RuntimeError, match="could not confirm spec:approved event for #8"):
        getattr(specloop_intake, interface)("owner/sample", 8, label, runner=gh)


def failure_responses():
    missing_id = label_event()
    del missing_id["id"]
    return [
        pytest.param(queue.GhError("connection reset", transient=True), id="transport"),
        pytest.param(queue.GhError("HTTP 401: unauthorized", transient=False), id="unauthorized"),
        pytest.param(queue.GhError("HTTP 403: forbidden", transient=False), id="forbidden"),
        pytest.param(queue.GhError("HTTP 429: rate limit", transient=True), id="rate-limit"),
        pytest.param("not json", id="invalid-json"),
        pytest.param("null", id="null-history"),
        pytest.param("{}", id="object-history"),
        pytest.param(json.dumps([[label_event()], {}]), id="malformed-later-page"),
        pytest.param(json.dumps([[label_event()], [None]]), id="malformed-later-event"),
        pytest.param(json.dumps([[label_event()], [{"event": "labeled", "label": None}]]),
                     id="malformed-label"),
        pytest.param(json.dumps([[label_event()], [{"event": "labeled", "label": {"name": []}}]]),
                     id="malformed-label-name"),
        pytest.param("[]", id="empty-history"),
        pytest.param(json.dumps([[label_event(label="unrelated")]]), id="missing-label"),
        pytest.param(json.dumps([[label_event()], [missing_id]]), id="missing-latest-id"),
        pytest.param(json.dumps([[label_event()], [label_event(2, event="unlabeled")]]),
                     id="latest-is-unlabel"),
        *[pytest.param(json.dumps([[label_event()], [label_event(event_id)]]),
                       id=f"invalid-id-{event_id!r}") for event_id in INVALID_IDS],
    ]


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("response", failure_responses())
def test_event_lookup_failure_preserves_admission_state_for_retry(tmp_path, response):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text("/unused/source"))
    repo = Repo("owner/sample", engine="specloop", manifest=manifest)
    gh, commands = ApprovalGh(), Commands()
    history = EventHistory(response)
    labels_before = gh.labels.copy()
    saved = {manifest: manifest.read_bytes()}
    for stage, receipt in [
        ("approvals", {"event": 1, "head": "previous-head"}),
        ("admissions", {"approval_event": 1, "result": {"outcome": "refused"}}),
    ]:
        path = tmp_path / stage / "owner__sample/8.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(receipt))
        saved[path] = path.read_bytes()

    def runner(args):
        if args[0] == "api" and args[1].endswith("/events"):
            return history(args)
        return gh(args)

    errors = []
    for _ in range(2):
        assert specloop_intake.admit_pending(
            repo, queue.Labels(), tmp_path, runner=runner,
            specloop_runner=commands, git_runner=commands, launcher=commands,
            on_error=lambda number, exc: errors.append((number, exc)),
        ) == []

    assert len(errors) == 2
    assert all(number == 8 and isinstance(exc, (RuntimeError, ValueError)) for number, exc in errors)
    if isinstance(response, Exception):
        assert all(exc is response for _, exc in errors)
    assert not commands.calls
    assert not gh.comments
    assert gh.labels == labels_before
    assert all(args[:2] in (["pr", "list"], ["pr", "view"]) for args in gh.calls)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == saved


NO_CONTENT = "HTTP/2.0 204 No Content\r\nX-Github-Request-Id: test\r\n\r\n"
NOT_FOUND = 'HTTP/2.0 404 Not Found\r\nContent-Type: application/json\r\n\r\n{"message":"Not Found"}'


class MembershipGh:
    def __init__(self, *, owner_type="User", collaborator=NOT_FOUND, member=NOT_FOUND):
        self.repository = json.dumps({"owner": {"login": "owner", "type": owner_type},
                                      "private": False, "permissions": {"pull": True}})
        self.collaborator = collaborator
        self.member = member
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args == ["api", "repos/owner/sample"]:
            response = self.repository
        else:
            assert args[0] == "api" and args[2:] == ["--include"]
            if args[1].startswith("repos/owner/sample/collaborators/"):
                response = self.collaborator
            else:
                assert args[1].startswith("orgs/owner/members/")
                response = self.member
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/identity-without-association-resolved")
@pytest.mark.parametrize("owner_type,actor,collaborator,member,expected,queries", [
    ("User", "OwNeR", NOT_FOUND, NOT_FOUND, "OWNER", 1),
    ("User", "helper", NO_CONTENT, NOT_FOUND, "COLLABORATOR", 2),
    ("User", "alice_acme", NO_CONTENT, NOT_FOUND, "COLLABORATOR", 2),
    ("User", "Alice_Acme", NO_CONTENT, NOT_FOUND, "COLLABORATOR", 2),
    ("Organization", "helper", NO_CONTENT, NO_CONTENT, "COLLABORATOR", 2),
    ("Organization", "helper", NOT_FOUND, NO_CONTENT, "MEMBER", 3),
    ("Organization", "alice_acme", NOT_FOUND, NO_CONTENT, "MEMBER", 3),
    ("User", "outsider", NOT_FOUND, NO_CONTENT, None, 2),
    ("Organization", "outsider", NOT_FOUND, NOT_FOUND, None, 3),
    ("Organization", "alice_acme", NOT_FOUND, NOT_FOUND, None, 3),
    ("Organization", "owner", NOT_FOUND, NOT_FOUND, None, 3),
])
def test_identity_is_resolved_from_positive_membership_only(
        owner_type, actor, collaborator, member, expected, queries):
    gh = MembershipGh(owner_type=owner_type, collaborator=collaborator, member=member)
    event = label_event(actor=actor)
    assert "author_association" not in event

    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) == expected
    assert len(gh.calls) == queries
    assert gh.calls[0] == ["api", "repos/owner/sample"]
    if queries > 1:
        assert gh.calls[1] == ["api", f"repos/owner/sample/collaborators/{actor}", "--include"]
    if queries > 2:
        assert gh.calls[2] == ["api", f"orgs/owner/members/{actor}", "--include"]


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/other-authors-cannot-authorize")
@pytest.mark.parametrize("author", ["issue", "pull_request", "feedback"])
def test_trusted_other_authors_do_not_supply_label_actor_trust(author):
    gh = MembershipGh(owner_type="Organization")
    event = label_event(actor="outsider")
    trusted_author = {"login": "owner", "author_association": "OWNER"}
    assert queue.trusted(trusted_author, "author_association")
    event[author] = trusted_author
    event.update(author=trusted_author, author_association="OWNER", authorAssociation="OWNER")
    event["actor"].update(type="User", site_admin=True, author_association="OWNER")

    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) is None
    assert gh.calls[1:] == [
        ["api", "repos/owner/sample/collaborators/outsider", "--include"],
        ["api", "orgs/owner/members/outsider", "--include"],
    ]


@pytest.mark.parametrize("actor", [
    None, {}, "owner", [], {"login": None}, {"login": False}, {"login": 42},
    {"login": ""}, {"login": "ghost"}, {"login": "Ghost"}, {"login": "../owner"},
    {"login": "owner/member"}, {"login": "owner?admin=true"}, {"login": "owner%2Fmember"},
    {"login": "owner\n"}, {"login": " owner"}, {"login": "-owner"}, {"login": "owner-"},
    {"login": "bad--login"}, {"login": "a" * 40}, {"login": "é"},
])
def test_missing_deleted_or_malformed_actor_is_untrusted_without_lookup(actor):
    gh = MembershipGh(collaborator=NO_CONTENT, member=NO_CONTENT)
    event = label_event()
    event["actor"] = actor
    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) is None
    del event["actor"]
    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) is None
    assert gh.calls == []


def test_bot_login_is_encoded_and_type_alone_does_not_authorize():
    gh = MembershipGh()
    event = label_event(actor="helper[bot]")
    event["actor"]["type"] = "Bot"
    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) is None
    assert gh.calls[-1] == ["api", "repos/owner/sample/collaborators/helper%5Bbot%5D", "--include"]
    gh.collaborator = NO_CONTENT
    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) == "COLLABORATOR"
    gh.collaborator = NOT_FOUND
    assert specloop_intake._label_actor_association("owner/sample", event, runner=gh) is None


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("endpoint", ["repository", "collaborator", "member"])
@pytest.mark.parametrize("failure", [
    queue.GhError("connection reset", transient=True),
    queue.GhError("gh: Bad credentials (HTTP 401)", transient=False),
    queue.GhError("gh: Forbidden (HTTP 403)", transient=False),
    queue.GhError("gh: API rate limit exceeded (HTTP 403)", transient=False),
    queue.GhError("gh: Too Many Requests (HTTP 429)", transient=False),
    queue.GhError("gh: Service Unavailable (HTTP 503)", transient=True),
    queue.GhError("lookup failed for issue 404", transient=False),
    queue.GhError("HTTP 4040: not a status", transient=False),
    queue.GhError("connection reset (HTTP 404)", transient=True),
    RuntimeError("unrecognized runner failure"),
])
def test_relationship_lookup_errors_propagate_unchanged(endpoint, failure):
    gh = MembershipGh(owner_type="Organization")
    setattr(gh, endpoint, failure)
    with pytest.raises(type(failure)) as raised:
        specloop_intake._label_actor_association("owner/sample", label_event(actor="helper"), runner=gh)
    assert raised.value is failure
    assert len(gh.calls) == ["repository", "collaborator", "member"].index(endpoint) + 1


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("response", [
    "", "not json", "null", "[]", "{}", '{"owner": null}', '{"owner": []}',
    '{"owner": {"login": "owner"}}', '{"owner": {"login": "owner", "type": "Bot"}}',
    '{"owner": {"login": "../owner", "type": "Organization"}}',
])
def test_malformed_repository_metadata_defers_relationship_resolution(response):
    gh = MembershipGh(collaborator=NO_CONTENT)
    gh.repository = response
    with pytest.raises((RuntimeError, ValueError)):
        specloop_intake._label_actor_association("owner/sample", label_event(), runner=gh)
    assert gh.calls == [["api", "repos/owner/sample"]]


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("endpoint", ["collaborator", "member"])
@pytest.mark.parametrize("response", [
    None, "", "{}", '{"permission": "read"}', "204", "HTTP/2.0 204", "HTTP/2.0 204\n",
    'garbage\nHTTP/2.0 204 No Content\n\n', '{"message": "HTTP/2.0 204 No Content\\n\\n"}',
    *[f"HTTP/2.0 {code} Unexpected\n\n" for code in [200, 301, 401, 403, 429, 500, 503]],
])
def test_malformed_or_unexpected_membership_response_defers_resolution(endpoint, response):
    gh = MembershipGh(owner_type="Organization")
    setattr(gh, endpoint, response)
    with pytest.raises(RuntimeError, match="unconfirmed membership response"):
        specloop_intake._label_actor_association("owner/sample", label_event(actor="helper"), runner=gh)
    assert len(gh.calls) == (2 if endpoint == "collaborator" else 3)


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/identity-without-association-resolved")
@pytest.mark.parametrize("status_line", ["HTTP/1.1 204 No Content", "HTTP/2.0 204 No Content", "HTTP/2 204"])
@pytest.mark.parametrize("stderr", ["gh: Not Found (HTTP 404)", "HTTP 404: Not Found (https://api.github.com/)"])
def test_membership_status_through_real_gh_error_handling(monkeypatch, status_line, stderr):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        if args == ["gh", "api", "repos/owner/sample"]:
            return subprocess.CompletedProcess(args, 0, MembershipGh(owner_type="Organization").repository, "")
        if args == ["gh", "api", "repos/owner/sample/collaborators/helper", "--include"]:
            # _gh discards this stdout on a nonzero exit: the status must come
            # from its GhError, not from a fabricated successful API result.
            return subprocess.CompletedProcess(args, 1, NOT_FOUND, stderr)
        assert args == ["gh", "api", "orgs/owner/members/helper", "--include"]
        return subprocess.CompletedProcess(args, 0, status_line + "\nX-Test: yes\n\n", "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    assert specloop_intake._label_actor_association(
        "owner/sample", label_event(actor="helper"), runner=queue._gh,
    ) == "MEMBER"
    assert len(calls) == 3


def test_repository_404_is_a_lookup_failure_not_negative_membership():
    gh = MembershipGh()
    failure = queue.GhError("gh: Not Found (HTTP 404)", transient=False)
    gh.repository = failure
    with pytest.raises(queue.GhError) as raised:
        specloop_intake._label_actor_association("owner/sample", label_event(), runner=gh)
    assert raised.value is failure


def prepare_drafting_round(repo, tmp_path, gh, commands, armed, previous):
    """Rearm real delivered work, keeping its earlier receipt on disk."""
    if previous is None:
        return
    commands.result["outcome"] = previous
    poll_draft(repo, tmp_path, gh, commands)
    if previous == "drafted":
        gh.prs[0]["state"] = "CLOSED"
    gh.label_event("needs-human", "unlabeled")
    gh.label_event(armed, "labeled")
    commands.result["outcome"] = "drafted"
    commands.calls.clear()
    gh.calls.clear()


def revision_case(stage, *, empty=False):
    gh = RevisionGh()
    commands = BuildCommands(gh) if stage == "build-revision" else RevisionCommands(gh)
    if stage == "build-revision":
        gh.label_event("spec:review", "unlabeled")
    if empty:
        gh.feedback = []
    return gh, commands


def assert_no_revision_reads(gh):
    assert not any(
        c[:2] == ["pr", "view"] and c[-1] == "comments,reviews"
        or c[0] == "api" and ("/commits/" in c[1] or "/pulls/" in c[1])
        for c in gh.calls
    )


@pytest.mark.scenario("pr-revision/revision-actor-gate/trusted-label-does-not-trust-feedback")
@pytest.mark.parametrize("stage", ["spec-revision", "build-revision"])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/dependencies-and-label-actors-gate-work"
)
def test_revision_label_trust_keeps_feedback_author_and_consumption_filters(repo, tmp_path, stage):
    gh, commands = revision_case(stage)
    # Exercise each GitHub feedback shape with trusted, untrusted, absent
    # association and consumed entries. A trusted label cannot promote any text.
    seed_consumed(tmp_path, [f"{kind}:{i}" for kind in ("conversation", "review", "inline") for i in (4, 5)])
    for target, association, timestamp, author in (
        (gh.feedback, "author_association", "created_at", "user"),
        (gh.reviews, "author_association", "submitted_at", "user"),
        (gh.inline, "author_association", "created_at", "user"),
    ):
        target.clear()
        for trust, date, body in (
            ("MEMBER", "2026-10-20T13:00:00Z", "Keep the eligible instruction."),
            ("NONE", "2026-10-20T14:00:00Z", "Ignore this outsider instruction."),
            (None, "2026-10-20T14:00:00Z", "Ignore missing association."),
            ("OWNER", TIP_DATE, "Ignore consumed instruction at the branch tip."),
            ("COLLABORATOR", "2026-10-19T12:00:00Z", "Ignore consumed older instruction."),
            ("MEMBER", "2026-10-20T14:00:00Z", queue.SELF_COMMENT_PREFIX + " Ignore self comment."),
        ):
            item = {"id": len(target) + 1, author: {"login": "reviewer"}, timestamp: date, "body": body}
            if trust is not None:
                item[association] = trust
            if target is gh.inline:
                item.update(path="src/gate.py", line=12)
            target.append(item)

    result, = specloop_intake.revise_pending(
        repo, queue.Labels(), tmp_path, runner=gh, specloop_runner=commands, git_runner=commands,
    )
    expected = ["**reviewer:**\n\nKeep the eligible instruction."] * 2 + [
        "**reviewer on `src/gate.py:12`:**\n\nKeep the eligible instruction.",
    ]
    receipt = json.loads((tmp_path / "revisions/owner__sample/8.json").read_text())
    assert receipt["feedback"] == expected and receipt["delivered"]
    if stage == "spec-revision":
        assert result["outcome"] == "drafted" and gh.head == REVISED
        proposal, = commands.proposals
        idea = idea_file(proposal).read_text()
        assert idea.endswith("\n\n---\n\n".join(expected)) and "Ignore" not in idea
    else:
        assert result["outcome"] == "revised"
        assert [json.loads(blocks) for blocks in commands.feedback] == [expected]
    assert "agent:revise" not in gh.labels
    assert not any("label-authorization" in c["body"] for c in gh.comments)


@pytest.mark.scenario("pr-revision/no-instruction-no-revision/empty-revise-refused")
@pytest.mark.parametrize("stage", ["spec-revision", "build-revision"])
def test_authorized_empty_revision_refuses_without_status_or_dispatch(repo, tmp_path, stage):
    gh, commands = revision_case(stage)
    seed_consumed(tmp_path, ("conversation:101", "conversation:102"))

    def forbidden(*args, **kwargs):
        pytest.fail("empty revision must not read status, dispatch, or invoke git")

    result, = specloop_intake.revise_pending(
        repo, queue.Labels(), tmp_path, runner=gh, specloop_runner=forbidden,
        git_runner=forbidden, status_runner=forbidden, launcher=forbidden,
    )
    assert result == {"outcome": "empty"}
    assert gh.head == HEAD and "agent:revise" not in gh.labels
    comment, = gh.comments
    assert "nothing to revise" in comment["body"] and "no unconsumed review feedback" in comment["body"]
    assert "label-authorization" not in comment["body"]
    receipt = json.loads((tmp_path / "revisions/owner__sample/8.json").read_text())
    assert receipt["feedback"] == [] and receipt["delivered"]


@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/untrusted-actor-ignored")
@pytest.mark.parametrize("armed,previous", [
    (armed, previous) for armed in ("spec:draft", "agent:ready")
    for previous in (None, "needs_input", "rejected", "drafted")
] + [("spec:approved", previous) for previous in (None, "admitted", "refused")]
  + [pytest.param(stage, feedback, marks=pytest.mark.scenario(
      "pr-revision/revision-actor-gate/unauthorized-revision-ignored-at-either-stage"))
     for stage in ("spec-revision", "build-revision") for feedback in (None, "empty")])
@pytest.mark.parametrize("actor", [{"login": "outsider"}, None, {}, {"login": "bad/login"}])
def test_label_from_non_collaborator_is_ignored(repo, tmp_path, armed, previous, actor):
    revision = armed in ("spec-revision", "build-revision")
    if revision:
        gh, commands = revision_case(armed, empty=previous == "empty")
        action, number = specloop_intake.revise_pending, 8
    elif armed == "spec:approved":
        gh, commands = ApprovalGh((armed,)), Commands(previous or "admitted")
        if previous:
            poll_admission(repo, tmp_path, gh, commands)
            gh.label_event("needs-human", "unlabeled")
            gh.label_event(armed, "labeled")
            commands.calls.clear()
            gh.calls.clear()
        action, number = specloop_intake.admit_pending, 8
    else:
        gh, commands = DraftGh((armed,)), DraftCommands()
        prepare_drafting_round(repo, tmp_path, gh, commands, armed, previous)
        action, number = specloop_intake.propose_pending, 7
    trigger = "agent:revise" if revision else armed
    event = next(e for e in reversed(gh.events) if e["label"]["name"] == trigger)
    event["actor"] = actor
    labels, comments, prs = gh.labels.copy(), deepcopy(gh.comments), deepcopy(gh.prs)
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized work must not dispatch, deliver, or report pending/error")

    for _ in range(2):
        assert action(
            repo, queue.Labels(), tmp_path, runner=gh, specloop_runner=forbidden,
            git_runner=forbidden, launcher=forbidden, on_error=forbidden, on_pending=forbidden,
        ) == []
        assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files
        assert gh.labels == labels and gh.prs == prs
        assert gh.comments[:-1] == comments
        assert len(gh.comments) == len(comments) + 1
    assert f"label-authorization-{number}:{event['id']}" in gh.comments[-1]["body"]
    if revision:
        assert_no_revision_reads(gh)
    assert not commands.calls
    assert not any("PATCH" in c or c[:2] in (
        ["issue", "edit"], ["pr", "edit"], ["pr", "create"],
    ) for c in gh.calls)


@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/trusted-actor-accepted")
@pytest.mark.parametrize("armed,outcome", [
    ("spec:draft", "drafted"), ("agent:ready", "drafted"),
    pytest.param("spec:approved", "admitted", marks=pytest.mark.scenario(
        "issue-intake/approval-admits/approved-spec-admitted")),
    pytest.param("spec:approved", "refused", marks=pytest.mark.scenario(
        "issue-intake/approval-admits/refused-admission-explained")),
    pytest.param("spec-revision", "drafted", marks=pytest.mark.scenario(
        "pr-revision/spec-stage-revision/spec-revised-from-review")),
    pytest.param("build-revision", "revised", marks=pytest.mark.scenario(
        "pr-revision/build-stage-revision/review-comments-become-tasks")),
])
@pytest.mark.parametrize("relationship", ["OWNER", "COLLABORATOR", "MEMBER"])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/dependencies-and-label-actors-gate-work"
)
def test_label_from_collaborator_is_acted_on(repo, tmp_path, armed, outcome, relationship):
    admission = armed == "spec:approved"
    revision = armed in ("spec-revision", "build-revision")
    gh, commands = (ApprovalGh((armed,)), Commands(outcome)) if admission else (
        DraftGh((armed,)), DraftCommands())
    if revision:
        gh, commands = revision_case(armed)
        gh.feedback = []
        gh.inline = [
            {"id": line, "user": {"login": "reviewer"}, "author_association": "COLLABORATOR",
             "path": path, "line": line, "body": body, "created_at": "2026-10-20T13:00:00Z"}
            for path, line, body in [("src/gate.py", 12, "Handle empty input."),
                                     ("tests/test_gate.py", 42, "Cover the empty case.")]
        ]
    actor = "owner" if relationship == "OWNER" else "helper"
    trigger = "agent:revise" if revision else armed
    event = next(e for e in reversed(gh.events) if e["label"]["name"] == trigger)
    event["actor"] = {"login": actor}
    membership = MembershipGh(
        owner_type="Organization" if relationship == "MEMBER" else "User",
        collaborator=NO_CONTENT if relationship == "COLLABORATOR" else NOT_FOUND,
        member=NO_CONTENT if relationship == "MEMBER" else NOT_FOUND,
    )

    def github(args):
        if args == ["api", "repos/owner/sample"] or args[-1] == "--include":
            return membership(args)
        return gh(args)

    if revision:
        result, = specloop_intake.revise_pending(
            repo, queue.Labels(), tmp_path, runner=github,
            specloop_runner=commands, git_runner=commands,
        )
        assert result["outcome"] == outcome
        receipt = json.loads((tmp_path / "revisions/owner__sample/8.json").read_text())
        assert type(receipt["event"]) is int and receipt["event"] == event["id"]
        expected = ["**reviewer on `src/gate.py:12`:**\n\nHandle empty input.",
                    "**reviewer on `tests/test_gate.py:42`:**\n\nCover the empty case."]
        assert receipt["feedback"] == expected
        if armed == "spec-revision":
            proposal, = commands.proposals
            assert idea_file(proposal).read_text().endswith("\n\n---\n\n".join(expected))
            assert gh.head == REVISED and gh.labels == {"spec:review"}
            assert "Revised the spec" in gh.comments[-1]["body"]
        else:
            assert [json.loads(blocks) for blocks in commands.feedback] == [expected]
            assert gh.head == HEAD and gh.labels == set()
            assert "Queued review tasks" in gh.comments[-1]["body"]
    elif admission:
        result, = poll_admission(repo, tmp_path, github, commands)
        assert result["outcome"] == outcome
        receipt = json.loads((tmp_path / "admissions/owner__sample/8.json").read_text())
        approval = json.loads((tmp_path / "approvals/owner__sample/8.json").read_text())
        assert type(receipt["approval_event"]) is int and receipt["approval_event"] == 1
        assert type(approval["event"]) is int and approval["event"] == 1
        assert receipt["head"] == approval["head"] == gh.head
        assert len(commands.admissions) == 1
        assert commands.admissions[0][commands.admissions[0].index("--pr") + 1] == gh.pr_url
        if outcome == "admitted":
            assert gh.labels == set()
            assert "build has started" in gh.comments[-1]["body"]
        else:
            assert gh.labels == {"needs-human"}
            assert "Admission refused" in gh.comments[-1]["body"]
            assert commands.result["reasons"][0] in gh.comments[-1]["body"]
    else:
        receipt, = poll_draft(repo, tmp_path, github, commands)
        assert receipt["drafting_events"] == {armed: 1}
        assert [c[0] for c in commands.calls] == ["specloop", "git"]
        assert gh.labels == set() and len(gh.prs) == 1
        assert gh.pr_url in gh.comments[-1]["body"]
    assert receipt["delivered"] is True
    assert not any("label-authorization" in c["body"] for c in gh.comments)
    assert len(membership.calls) == {"OWNER": 1, "COLLABORATOR": 2, "MEMBER": 3}[relationship]
    assert sum(c[0] == "api" and c[1].endswith("/events") for c in gh.calls) == (2 if revision else 1)


@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/trusted-actor-accepted")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("previous", [None, "needs_input", "rejected", "drafted"])
def test_pause_after_draft_label_read_preserves_receipts(
        repo, tmp_path, monkeypatch, armed, previous):
    gh, commands = DraftGh((armed,)), DraftCommands()
    prepare_drafting_round(repo, tmp_path, gh, commands, armed, previous)
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    labels, comments, prs = gh.labels.copy(), deepcopy(gh.comments), deepcopy(gh.prs)
    paused_after = []

    def pause_after_events(args):
        result = gh(args)
        if args[0] == "api" and args[1].endswith("/events"):
            daemon.PAUSE_FILE.touch()
            paused_after.append(args)
        return result

    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health,
                         runner=pause_after_events, specloop_runner=commands)
    assert len(paused_after) == 1 and gh.calls[-1] == paused_after[0]
    assert not commands.calls
    assert not health.failures and not health.item_failures and not health.announced
    assert gh.labels == labels and gh.comments == comments and gh.prs == prs
    # A paused event read cannot create an initial receipt or replace the
    # previous round's receipt before the actor has been authorized.
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files

    daemon.PAUSE_FILE.unlink()
    receipt, = poll_draft(repo, tmp_path, gh, commands)
    assert receipt["delivered"] is True
    assert sum(c[0] == "specloop" for c in commands.calls) == 1
    assert not any("label-authorization" in c["body"] for c in gh.comments)


@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/alias-and-configured-names-protected")
@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/trusted-reapplication-recovers")
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("both", [False, True])
@pytest.mark.parametrize("trigger", ["spec_draft", "ready"])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/dependencies-and-label-actors-gate-work"
)
def test_intake_alias_requires_its_own_trusted_application(repo, tmp_path, configured, both, trigger):
    labels = queue.Labels(spec_draft="custom:draft", ready="custom:ready") if configured else queue.Labels()
    armed = getattr(labels, trigger)
    gh = DraftGh((labels.spec_draft, labels.ready) if both else (armed,))
    commands = DraftCommands()
    # The newer application must defeat the earlier trusted one, including
    # when the other intake label is still trusted on another event page.
    gh.label_event(armed, "unlabeled")
    gh.label_event(armed, "labeled", actor="outsider")
    rejected_id = gh.events[-1]["id"]
    original_labels = gh.labels.copy()
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for _ in range(2):
        assert poll_draft(repo, tmp_path, gh, commands, labels) == []
    assert not commands.calls and not gh.prs
    assert gh.labels == original_labels and len(gh.comments) == 2
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files
    rejection = deepcopy(gh.comments[-1])
    assert f"`{armed}`" in rejection["body"]
    assert f"label-authorization-7:{rejected_id}" in rejection["body"]

    gh.label_event(armed, "unlabeled")
    gh.label_event(armed, "labeled")
    expected_events = {label: next(e["id"] for e in reversed(gh.events)
                                  if e["label"]["name"] == label) for label in gh.labels}
    gh.calls.clear()
    receipt, = poll_draft(repo, tmp_path, gh, commands, labels)
    assert receipt["drafting_events"] == expected_events
    assert gh.comments[1] == rejection
    assert gh.labels == set() and len(gh.prs) == 1
    assert [c[0] for c in commands.calls] == ["specloop", "git"]
    assert not any("PATCH" in c for c in gh.calls)
    assert sum(c[0] == "api" and c[1].endswith("/events") for c in gh.calls) == 1


@pytest.mark.scenario("issue-intake/authorization-preserves-intake-recovery/blocked-draft-remains-silent")
@pytest.mark.parametrize("armed", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("previous", [None, "needs_input", "rejected", "drafted"])
@pytest.mark.parametrize("unreadable", [False, True])
def test_blocked_untrusted_draft_waits_before_authorization(
        repo, tmp_path, caplog, armed, previous, unreadable):
    gh, commands = BlockedDraftGh(armed, {6: "CLOSED"}), DraftCommands()
    prepare_drafting_round(repo, tmp_path, gh, commands, armed, previous)
    gh.events[-1]["actor"] = {"login": "outsider"}
    gh.states[6] = queue.GhError("unavailable", transient=True) if unreadable else "OPEN"
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    labels, comments, prs = gh.labels.copy(), deepcopy(gh.comments), deepcopy(gh.prs)
    caplog.set_level(logging.INFO, logger=specloop_intake.__name__)
    for _ in range(2):
        assert poll_draft(repo, tmp_path, gh, commands) == []
        assert gh.comments == comments and gh.labels == labels and gh.prs == prs
        assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files
    assert caplog.record_tuples == [
        (specloop_intake.__name__, logging.INFO, "#7 is blocked by #6 — not drafting"),
    ] * 2
    assert not commands.calls
    assert not any(c == ["api", "repos/owner/sample"] for c in gh.calls)

    gh.states[6] = "CLOSED"
    assert poll_draft(repo, tmp_path, gh, commands) == []
    assert not commands.calls and gh.labels == labels and gh.prs == prs
    assert len(gh.comments) == len(comments) + 1
    assert "label-authorization-7:" in gh.comments[-1]["body"]
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files
    rejection = deepcopy(gh.comments[-1])
    gh.label_event(armed, "unlabeled")
    gh.label_event(armed, "labeled")
    authorized_event = gh.events[-1]["id"]
    gh.calls.clear()
    receipt, = poll_draft(repo, tmp_path, gh, commands)
    assert receipt["delivered"] is True
    assert receipt["drafting_events"] == {armed: authorized_event}
    assert sum(c[0] == "api" and c[1].endswith("/events") for c in gh.calls) == 1
    assert [c[0] for c in commands.calls] == (
        ["specloop", "git", "git"] if previous == "drafted" else ["specloop", "git"]
    )
    assert gh.comments[-2] == rejection
    assert gh.labels == set()
