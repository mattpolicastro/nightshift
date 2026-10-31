"""Gitea allowlist authorization through real adapter reads and intake gates."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from fake_gitea import FakeGitea, Reply
from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo
from nightshift.forge import http
from test_forge_gitea import ROOT, candidate, label_event, make_forge
from test_specloop_label_authorization_recovery import restarted_intake


pytestmark = pytest.mark.real_trust
REPO = "owner/repo"
ALLOWLIST = "issue-intake/actor-relationship-resolution/gitea-allowlist-alone-authorizes-actors"


def forbidden(*args, **kwargs):
    pytest.fail("Authorization must not read relationships or dispatch denied work")


@pytest.fixture
def gitea(monkeypatch, tmp_path):
    fake = FakeGitea()
    monkeypatch.setattr(http, "sleep", lambda _: None)

    def new_forge():
        forge = make_forge(fake, trusted_logins=["Alice", "a.user_name", "Straße", "Robot"])
        for method in ("repository", "collaborator_response", "organization_member_response"):
            monkeypatch.setattr(forge, method, forbidden)
        return forge

    labels = [{"id": i, "name": name} for i, name in enumerate(
        ("spec:draft", "agent:ready", "unrelated"), 1)]
    issues = [candidate(n, labels, user={"login": "Alice"}, comments=[], timeline=[])
              for n in (1, 2)]
    for issue in issues:
        issue["timeline"] = [label_event(i, label=label) for i, label in enumerate(labels[:2], 1)]
    fake.repository(REPO, labels=labels, issues=issues)
    receipts = tmp_path / "proposals" / "owner__repo"
    receipts.mkdir(parents=True)
    for number in (1, 2):
        (receipts / f"{number}.json").write_text('{"job": {"id": "pending"}, "retained": true}')
    saved = {path: path.read_bytes() for path in receipts.iterdir()}
    collected = []

    def poll(module=intake):
        # Stage forge forwarding is a separate task; inject only construction here.
        monkeypatch.setattr(module, "GitHubForge", lambda runner: new_forge())
        monkeypatch.setattr(module, "_collect", lambda path, *args: collected.append(path.stem) or False)
        return module.propose_pending(Repo(REPO, engine="specloop"), queue.Labels(), tmp_path,
                                      specloop_runner=forbidden, git_runner=forbidden,
                                      launcher=forbidden)

    def preserved():
        assert all(issue["labels"] == labels for issue in issues)
        assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == saved
        assert all(call.method == "GET" or (
            call.method == "POST" and call.path.endswith("/comments")) for call in fake.calls)

    return SimpleNamespace(fake=fake, forge=new_forge(), issues=issues, poll=poll,
                           collected=collected, preserved=preserved)


@pytest.mark.scenario(ALLOWLIST)
@pytest.mark.parametrize("actor,expected", [
    ({"login": "aLiCe"}, "OWNER"), ({"login": "A.USER_NAME"}, "OWNER"),
    ({"login": "STRASSE"}, "OWNER"),
    ({"login": "owner", "type": "User"}, None),
    ({"login": "outsider", "site_admin": True, "author_association": "OWNER"}, None),
    ({"login": "ROBOT"}, None), ({"login": " Alice"}, None),
    ({"login": "Alice "}, None), ({"login": "Alice.extra"}, None),
    (None, None), ({}, None), ({"login": ""}, None), ({"login": " \t"}, None),
    ({"login": False}, None), ({"login": 42}, None), ("Alice", None),
])
def test_gitea_label_actor_trusted_by_allowlist(gitea, actor, expected):
    event = {"id": 1, "actor": actor, "authorAssociation": "OWNER", "author_association": "OWNER"}
    assert intake._label_actor_association(REPO, event, forge=gitea.forge) == expected
    assert intake._label_actor_association(REPO, {"id": 2}, forge=gitea.forge) is None
    assert gitea.fake.calls == []


@pytest.mark.scenario(ALLOWLIST)
@pytest.mark.scenario("issue-intake/actor-relationship-resolution/other-authors-cannot-authorize")
@pytest.mark.scenario("issue-intake/rejection-comment-idempotency/repeated-rejection-comments-once")
@pytest.mark.scenario("issue-intake/rejection-comment-idempotency/new-rejection-has-separate-explanation")
@pytest.mark.parametrize("alias", ["spec:draft", "agent:ready"])
@pytest.mark.parametrize("denied_actor", ["outside.user", "ROBOT"])
def test_gitea_latest_alias_denial_restart_and_trusted_reapplication(gitea, alias, denied_actor):
    issue = gitea.issues[0]

    def apply(identity, login, body="1"):
        issue["timeline"].append(label_event(identity, label={"name": alias},
                                           user={"login": login}, body=body,
                                           author_association="OWNER"))

    apply(3, "Alice", "")
    apply(4, denied_actor)
    assert gitea.poll() == []
    assert gitea.collected == ["2"]  # A later eligible issue still reaches collection.
    rejection = deepcopy(issue["comments"])
    assert len(rejection) == 1
    body = rejection[0]["body"]
    assert f"`{alias}`" in body and f"`{denied_actor}`" in body
    assert "Gitea trusted_logins allowlist" in body and "bot is always excluded" in body
    assert "OWNER, MEMBER, or COLLABORATOR" not in body
    assert "trusted actor must remove and reapply" in body
    assert body.endswith("<!-- nightshift:draft:label-authorization-1:4 -->")
    for module in (intake, restarted_intake()):
        assert gitea.poll(module) == []
        assert issue["comments"] == rejection
    assert gitea.collected == ["2"] * 3
    assert len([call for call in gitea.fake.calls if call.method != "GET"]) == 1
    gitea.preserved()

    apply(5, denied_actor, "")
    apply(6, "owner")  # Repository ownership never substitutes for the allowlist.
    assert gitea.poll() == []
    assert issue["comments"][:1] == rejection and len(issue["comments"]) == 2
    assert issue["comments"][1]["body"].endswith("<!-- nightshift:draft:label-authorization-1:6 -->")
    saved_comments = deepcopy(issue["comments"])
    assert gitea.poll(restarted_intake()) == []
    assert issue["comments"] == saved_comments
    apply(7, "owner", "")
    apply(8, "A.USER_NAME")
    assert gitea.poll(restarted_intake()) == []
    assert gitea.collected[-2:] == ["1", "2"]
    assert issue["comments"] == saved_comments
    gitea.preserved()


@pytest.mark.scenario("issue-intake/rejection-comment-idempotency/lost-comment-response-reconciled")
@pytest.mark.parametrize("lost_response", [False, True])
def test_gitea_rejection_failed_lookup_and_lost_response(gitea, lost_response):
    gitea.issues[0]["timeline"][-1]["user"] = {"login": "outsider"}
    if lost_response:
        gitea.fake.comment_create_outcome = OSError("accepted response lost")
        assert gitea.poll() == []
    else:
        # issue_for_draft first reads factual comments; the denial lookup then fails.
        path = ROOT + "/issues/1/comments"
        gitea.fake.route("GET", path, Reply([]), Reply(status=403))
        with pytest.raises(RuntimeError):
            gitea.poll()
        assert not gitea.issues[0]["comments"]
        assert all(call.method == "GET" for call in gitea.fake.calls)
        del gitea.fake.routes["GET", path, None]
    assert gitea.poll(restarted_intake()) == []
    assert len(gitea.issues[0]["comments"]) == 1
    assert len([call for call in gitea.fake.calls if call.method != "GET"]) == 1
    assert "1" not in gitea.collected
    gitea.preserved()


@pytest.mark.scenario("issue-intake/actor-relationship-resolution/lookup-failure-defers-action")
@pytest.mark.parametrize("failure", [401, 403, 429, 503, "transport", "malformed", "id", "unlabel", "identity"])
def test_gitea_unknown_timeline_preserves_receipts_without_rejection(gitea, failure):
    if failure in ("id", "unlabel", "identity"):
        event = gitea.issues[0]["timeline"][-1]
        if failure == "id":
            del event["id"]
        elif failure == "unlabel":
            event["body"] = ""
        else:
            event["user"] = {}
    else:
        reply = (Reply(status=failure) if isinstance(failure, int) else
                 OSError("offline") if failure == "transport" else Reply({}))
        gitea.fake.route("GET", ROOT + "/issues/1/timeline", *[reply] * 3, page=1)
    with pytest.raises(RuntimeError):
        gitea.poll()
    assert gitea.collected == ["2"]
    assert all(not issue["comments"] for issue in gitea.issues)
    assert all(call.method == "GET" for call in gitea.fake.calls)
    gitea.preserved()


@pytest.mark.scenario(ALLOWLIST)
@pytest.mark.scenario("issue-intake/actor-relationship-resolution/other-authors-cannot-authorize")
def test_gitea_issue_author_and_feedback_trust_remain_independent(gitea):
    issue = gitea.issues[0]
    gitea.fake.repositories[REPO]["pulls"] = [issue]
    issue["comments"] = [dict(id=n, user={"login": login}, body=login,
                              author_association="OWNER")
                         for n, login in enumerate(("Alice", "outsider", "Robot"), 1)]
    assert intake._issue_author_trusted(REPO, 1, forge=gitea.forge)
    assert [row["body"] for _, row in gitea.forge.pr_feedback_records(REPO, 1)] == ["Alice"]
    assert not intake._authorize_label_events(REPO, 1, {
        "spec:draft": {"id": 10, "actor": {"login": "outsider"}}}, forge=gitea.forge)
    issue["user"] = {"login": "outsider"}
    issue["author_association"] = "OWNER"
    assert intake._authorize_label_events(REPO, 1, {
        "spec:draft": {"id": 11, "actor": {"login": "Alice"}}}, forge=gitea.forge)
    assert not intake._issue_author_trusted(REPO, 1, forge=gitea.forge)
    assert [row["body"] for _, row in gitea.forge.pr_feedback_records(REPO, 1)] == ["Alice"]


@pytest.mark.parametrize("login,display", [
    ("outside.user_name-2", "`outside.user_name-2`"),
    ("bad`\n@Alice", "an unavailable actor identity"),
    ("Straße", "an unavailable actor identity"), (None, "an unavailable actor identity"),
])
def test_gitea_rejection_display_is_safe_and_separate_from_authorization(gitea, login, display):
    event = {"id": 20, "actor": {"login": login}}
    intake._comment_label_rejection(REPO, 1, "spec:draft", event, forge=gitea.forge)
    assert f"applied by {display}." in gitea.issues[0]["comments"][-1]["body"]
    if login == "Straße":
        assert intake._label_actor_association(REPO, event, forge=gitea.forge) == "OWNER"
