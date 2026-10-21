"""Label event selection and real actor relationship resolution."""

import json
import subprocess

import pytest

from nightshift import queue, specloop_intake
from nightshift.config import Repo
from test_specloop_approval import ApprovalGh, Commands


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
    manifest.write_text('[run]\nrepo = "/unused/source"\n')
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
