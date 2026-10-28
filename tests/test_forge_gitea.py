"""Foundation contracts through the real transport, without a service or sockets."""

import json
import socket
import traceback

import pytest

from fake_gitea import FakeGitea, Reply
from nightshift.forge import ForgeError, http
from nightshift.forge.gitea import GiteaForge


SECRET = "private-token-or-response-value"
PATH = "/api/v1/repos/owner/repo/labels"
WIRE_PATH = "/gitea" + PATH
pytestmark = pytest.mark.real_trust


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Gitea adapter tests must not open sockets")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    delays = []
    monkeypatch.setattr(http, "sleep", delays.append)
    return delays


@pytest.fixture
def fake():
    return FakeGitea()


def make_forge(fake, **kwargs):
    options = dict(trusted_logins=["Alice"], bot_login="Robot", opener=fake)
    options.update(kwargs)
    return GiteaForge(fake.base_url, SECRET, **options)


@pytest.mark.scenario("gitea-forge/standalone-adapter/construction-and-interface")
def test_constructor_copies_allowlist_without_dispatch(fake):
    logins = ["Alice", "ALICE", "Robot"]
    forge = make_forge(fake, trusted_logins=logins)
    logins[:] = ["Mallory"]
    assert forge._trusted_logins == frozenset({"alice", "robot"})
    assert forge._bot_login == "robot" and forge._wip_prefix == "WIP: "
    assert make_forge(fake, trusted_logins=[], wip_prefix="Draft: ")._wip_prefix == "Draft: "
    assert fake.calls == []
    assert SECRET not in repr(forge) + repr(forge._transport)


@pytest.mark.scenario("gitea-forge/standalone-adapter/construction-and-interface")
@pytest.mark.parametrize("options", [
    {"base_url": None}, {"base_url": "https://host/" + SECRET + "/../"},
    {"base_url": "https://host/?" + SECRET}, {"base_url": "http://remote.example"},
    {"token": None}, {"token": ""}, {"token": SECRET + "\n"},
    {"trusted_logins": None}, {"trusted_logins": SECRET},
    {"trusted_logins": [SECRET, []]}, {"trusted_logins": [""]},
    {"trusted_logins": {SECRET: True}}, {"bot_login": None}, {"bot_login": ""},
    {"wip_prefix": ""}, {"wip_prefix": []}, {"guard": SECRET}, {"opener": SECRET},
    *({"timeout": value} for value in (None, SECRET, 0, -1, True, float("nan"), float("inf"))),
])
def test_invalid_constructor_values_are_not_disclosed(fake, options):
    arguments = dict(base_url=fake.base_url, token=SECRET,
                     trusted_logins=["Alice"], bot_login="Robot", opener=fake)
    arguments.update(options)
    with pytest.raises(ValueError) as caught:
        GiteaForge(**arguments)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert not fake.calls


@pytest.mark.scenario("gitea-forge/offline-fake-and-optional-live-contracts/fake-exercises-transport-behavior")
def test_real_encoding_dispatch_and_response_parsing(fake):
    events, stored = [], []
    forge = make_forge(fake, guard=lambda: events.append("guard"), timeout=2.5)
    path = forge._repo_path("a&b/r#x", "branches", "topic/a?#")
    assert path == "/api/v1/repos/a%26b/r%23x/branches/topic%2Fa%3F%23"

    def accept(call):
        events.append("open")
        stored.append(call.body)
        return Reply(call.body, status=201)

    fake.route("POST", "/gitea" + path, accept)
    body, query = {"title": "caf\u00e9 \u2603"}, {"q": ["a&b", "x y"]}
    status, _, result = forge._request("POST", path, query=query, json_body=body)
    assert status == 201 and result == body and stored == [body]
    assert events == ["guard", "open"]
    assert fake.calls[0].query == {"q": ["a&b", "x y"]}
    assert fake.calls[0].timeout == 2.5 and fake.responses[0].closed
    assert SECRET not in repr(fake.calls)
    with pytest.raises(AssertionError, match="Unexpected"):
        forge._request("GET", PATH)


@pytest.mark.scenario("gitea-forge/standalone-adapter/construction-and-interface")
@pytest.mark.parametrize("path", ["https://foreign/api", "//foreign/api", "/../outside",
                                  "/api/v1/%2e%2e/outside", "/api/v1/labels?token=secret"])
def test_transport_confinement_is_preserved(fake, path):
    with pytest.raises(ValueError):
        make_forge(fake)._request("GET", path)
    assert not fake.calls


@pytest.mark.scenario("gitea-forge/offline-fake-and-optional-live-contracts/fake-exercises-transport-behavior")
def test_fake_can_ignore_filters_and_lose_an_accepted_write(fake):
    stored = []

    def lose_response(call):
        stored.append(call.body)
        return OSError(SECRET)

    fake.route("POST", WIRE_PATH, lose_response)
    fake.route("GET", WIRE_PATH, lambda call: Reply(stored))
    forge = make_forge(fake)
    body = {"id": 1, "name": "unfiltered"}
    with pytest.raises(ForgeError) as caught:
        forge._request("POST", PATH, json_body=body)
    assert caught.value.transient and stored == [body]
    _, _, rows = forge._request("GET", PATH, query={"labels": "absent", "head": "absent"})
    assert rows == [body]
    assert [call.method for call in fake.calls] == ["POST", "GET"]
    assert fake.calls[-1].query == {"labels": ["absent"], "head": ["absent"]}


@pytest.mark.scenario("gitea-forge/offline-fake-and-optional-live-contracts/fake-exercises-transport-behavior")
@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/transport-failure-classification")
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("outcome, transient", [
    (Reply(status=503, raw=SECRET.encode()), True), (OSError(SECRET), True),
    (Reply(status=403, raw=SECRET.encode()), False),
    (Reply(status=302, headers={"Location": "https://foreign/" + SECRET}), False),
    (Reply(raw=SECRET.encode()), False),
])
def test_only_transport_retries_and_safe_classification(fake, offline, method, outcome, transient):
    attempts = 3 if method == "GET" and transient else 1
    fake.route(method, WIRE_PATH, *([outcome] * attempts))
    with pytest.raises(ForgeError) as caught:
        make_forge(fake)._request(method, PATH)
    assert caught.value.transient is transient
    assert len(fake.calls) == attempts and offline == [0.1, 0.2][:attempts - 1]
    assert all(response.closed for response in fake.responses)
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def guard_error(kind):
    return {"http": lambda: http.GiteaHTTPError(503, SECRET, True),
            "forge": lambda: ForgeError(SECRET, transient=True),
            "os": lambda: OSError(SECRET), "pause": lambda: RuntimeError(SECRET)}[kind]()


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/guard-exception-identity")
@pytest.mark.parametrize("kind", ["http", "forge", "os", "pause"])
@pytest.mark.parametrize("stage", ["read", "write", "retry", "later-page"])
def test_guard_identity_stops_all_dispatch(fake, offline, kind, stage):
    error, guards = guard_error(kind), []
    stop = 2 if stage in ("retry", "later-page") else 1

    def guard():
        guards.append(True)
        if len(guards) == stop:
            raise error

    forge = make_forge(fake, guard=guard)
    if stage == "later-page":
        fake.route("GET", WIRE_PATH, Reply([{"id": 1}]), page=1)
    elif stage == "retry":
        fake.route("GET", WIRE_PATH, Reply(status=503))
    with pytest.raises(type(error)) as caught:
        if stage == "later-page":
            forge._pages(PATH)
        else:
            forge._request("POST" if stage == "write" else "GET", PATH)
    assert caught.value is error
    assert len(fake.calls) == stop - 1
    assert len(guards) == stop and forge._guard_error.get() is None
    assert offline == ([0.1] if stage == "retry" else [])


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/guard-exception-identity")
@pytest.mark.parametrize("nested", [False, True])
def test_guard_origin_does_not_leak_to_later_transport_error(fake, nested):
    error = http.GiteaHTTPError(503, SECRET, True)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == (2 if nested else 1):
            raise error
        if nested and calls == 1:
            with pytest.raises(http.GiteaHTTPError) as caught:
                forge._request("GET", PATH)
            assert caught.value is error

    forge = make_forge(fake, guard=guard)
    if not nested:
        with pytest.raises(http.GiteaHTTPError) as caught:
            forge._request("GET", PATH)
        assert caught.value is error and not fake.calls
    # Deliberately reuse the very same object, now from the opener.
    fake.route("GET", WIRE_PATH, error, error, error)
    with pytest.raises(ForgeError) as translated:
        forge._request("GET", PATH)
    assert translated.value.transient and len(fake.calls) == 3
    assert forge._guard_error.get() is None


STAMP = "2026-10-20T12:00:00Z"
ROOT = "/gitea/api/v1/repos/owner/repo"


def authored(identity=1, login="Alice", body="Change λ\n\nKeep 🦉", **extra):
    return dict(id=identity, user={"login": login} if login is not None else None,
                body=body, author_association="OWNER", authorAssociation="OWNER",
                created_at=STAMP, updated_at=STAMP, **extra)


@pytest.mark.scenario("gitea-forge/standalone-adapter/membership-is-unsupported")
@pytest.mark.parametrize("method", ["collaborator_response", "organization_member_response"])
def test_membership_is_explicitly_unsupported(fake, method):
    def guard():
        pytest.fail("Unsupported membership must not dispatch")
    with pytest.raises(ForgeError, match="unsupported") as caught:
        getattr(make_forge(fake, guard=guard), method)("owner/repo", "Alice")
    assert not caught.value.transient and not fake.calls


@pytest.mark.scenario("gitea-forge/allowlist-identity-projection/allowlist-alone-determines-trust")
@pytest.mark.parametrize("login, association", [("aLiCe", "OWNER"), ("Mallory", "NONE"),
                                               (None, "NONE"), ("Robot", "NONE"),
                                               ("rObOt", "NONE"), ("ROBOT", "NONE")])
def test_gitea_trust_from_allowlist_only(fake, login, association):
    row = authored(login=login, state="APPROVED", submitted_at=STAMP)
    server_claim = "NONE" if association == "OWNER" else "OWNER"
    row.update(author_association=server_claim, authorAssociation=server_claim)
    issue = candidate(1, [], user=row["user"], author_association=server_claim,
                      authorAssociation=server_claim, comments=[row], reviews=[{**row, "comments": [row]}])
    fake.repository("owner/repo", labels=[], pulls=[issue],
                    facts={"owner": {"login": "Mallory", "type": "User"}, "permissions": {"admin": True}})
    forge = make_forge(fake, trusted_logins=["Alice", "Robot"])
    assert forge.repository("owner/repo")["owner"]["login"] == "Mallory"
    assert forge.issue_author("owner/repo", 1)["author_association"] == association
    comment = forge.comment_pages("owner/repo", 1)[0][0]
    assert comment["author_association"] == association
    assert "authorAssociation" not in comment
    draft = forge.issue_for_draft("owner/repo", 1)
    assert draft["authorAssociation"] == association
    if login is not None and login.casefold() == "robot":
        assert draft["comments"] == []  # No daemon body prefix was supplied.
    else:
        assert draft["comments"][0]["authorAssociation"] == association
    records = forge.pr_feedback_records("owner/repo", 1)
    assert [source for source, _ in records] == (["conversation", "review", "inline"]
                                               if association == "OWNER" else [])
    assert all(record["author_association"] == "OWNER" for _, record in records)


@pytest.mark.scenario("gitea-forge/allowlist-identity-projection/allowlist-alone-determines-trust")
def test_absent_user_cannot_borrow_server_author_identity(fake):
    row = authored(author={"login": "Alice"})
    del row["user"]
    issue = candidate(1, [], author={"login": "Alice"}, author_association="OWNER", comments=[row])
    fake.repository("owner/repo", labels=[], issues=[issue])
    forge = make_forge(fake)
    assert forge.issue_author("owner/repo", 1)["author_association"] == "NONE"
    assert forge.comment_pages("owner/repo", 1)[0][0]["author_association"] == "NONE"
    assert forge.issue_for_draft("owner/repo", 1)["comments"][0]["authorAssociation"] == "NONE"


def label_event(identity=1, **extra):
    return dict(id=identity, type="label", label={"name": "ready"}, user={"login": "Alice"},
                removed=False, created_at=STAMP) | extra


@pytest.mark.scenario("gitea-forge/label-timeline-evidence/label-actions-and-actors")
def test_timeline_actions_sorted_and_projected(fake):
    rows = [label_event(4, user={"login": "ROBOT"}),
            {"id": 2, "type": "comment", "body": "Alice applied ready"},
            label_event(3, removed=True, user={"login": "Mallory"}, author_association="OWNER"),
            label_event(1, user={"login": "aLiCe"})]
    fake.repository("owner/repo", labels=[], issues=[candidate(1, [], timeline=rows)], page_size=2)
    path = ROOT + "/issues/1/timeline"
    fake.route("GET", path, Reply([rows[0], *rows[2:]]), page=2)
    pages = make_forge(fake, trusted_logins=["Alice", "Robot"]).label_event_pages("owner/repo", 1)
    assert [row for page in pages for row in page] == [
        {"id": identity, "event": action, "actor": {"login": actor},
         "author_association": trust, "label": {"name": "ready"}, "created_at": STAMP}
        for identity, action, actor, trust in [(1, "labeled", "aLiCe", "OWNER"),
                                               (3, "unlabeled", "Mallory", "NONE"),
                                               (4, "labeled", "ROBOT", "NONE")]]
    assert [call.query for call in fake.calls] == [
        {"page": [str(page)], "limit": ["50"]} for page in (1, 2, 3)]
    assert all(call.path == path for call in fake.calls)


@pytest.mark.scenario("gitea-forge/label-timeline-evidence/unknown-label-evidence")
@pytest.mark.parametrize("patch", [{"id": None}, {"id": True}, {"id": 0}, {"id": "2"},
    {"user": None}, {"user": {}}, {"user": {"login": ""}}, {"user": {"login": True}},
    {"label": None}, {"label": {}}, {"label": {"name": ""}}, {"label": {"name": 1}},
    {"removed": None}, {"removed": "false"}, {"removed": 0}, {"created_at": None},
    {"id": 1, "removed": True}])
def test_invalid_timeline_evidence_refused(fake, patch):
    rows = [label_event(), label_event(2, **patch)]
    fake.repository("owner/repo", labels=[], issues=[candidate(1, [], timeline=rows)])
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).label_event_pages("owner/repo", 1)
    assert not caught.value.transient and len(fake.calls) in (2, 3)


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/draft-facts-and-blockers")
def test_issue_draft_repository_and_blocker_facts(fake):
    labels = [label_row(1, "ready")]
    comments = [authored(), authored(2, "Robot", "Bot without prefix"), authored(3, None)]
    issue = candidate(1, labels, user={"login": "Alice"}, comments=comments)
    facts = {"id": 80, "name": "repo", "owner": {"login": "Mallory"}, "default_branch": "main"}
    fake.repository("owner/repo", labels=labels, issues=[issue, candidate(2, []),
                     {**candidate(3, []), "state": "closed"}], facts=facts)
    forge = make_forge(fake)
    assert forge.open_blockers("owner/repo", []) == [] and not fake.calls
    assert forge.repository("owner/repo") == facts
    draft = forge.issue_for_draft("owner/repo", 1)
    assert {key: draft[key] for key in ("state", "title", "body", "labels")} == {
        "state": "OPEN", "title": "Issue 1", "body": "Details", "labels": labels}
    assert [(c["id"], c["authorAssociation"], c["createdAt"]) for c in draft["comments"]] == [
        (1, "OWNER", STAMP), (3, "NONE", STAMP)]
    assert forge.open_blockers("owner/repo", [3, 2, 1, 2]) == [2, 1, 2]


@pytest.fixture
def feedback_repo(fake):
    rows = [authored(i, body=body, state="COMMENT", submitted_at=STAMP, path="src/λ.py", position=4)
            for i, body in enumerate(("Change λ\n\nKeep 🦉", "  ", "**nightshift** — escalation"), 1)]
    rows += [authored(4, "Mallory"), authored(5, "rObOt"), authored(6, None)]
    rows[0]["state"] = "REQUEST_CHANGES"
    reviews = [dict(row, comments=[dict(item) for item in rows] if row["id"] == 1 else []) for row in rows]
    # Untrusted reviewers can have trusted inline replies; read every review.
    reviews[3]["comments"] = [dict(rows[0]), authored(7, path="old.py", line=None, original_line=9)]
    issue = candidate(1, [], comments=rows, reviews=reviews)
    fake.repository("owner/repo", labels=[], pulls=[issue], page_size=2)
    return issue


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/complete-identified-feedback")
def test_complete_feedback_records_and_existing_format(fake, feedback_repo):
    from nightshift.queue import FeedbackItem, _feedback_id

    forge = make_forge(fake, trusted_logins=["Alice", "Robot"])
    records = forge.pr_feedback_records("owner/repo", 1)
    assert [_feedback_id(source, row) for source, row in records] == [
        "conversation:1", "conversation:2", "conversation:3", "review:1", "review:2", "review:3",
        "inline:1", "inline:2", "inline:3", "inline:7"]
    assert all(row["submitted_at" if source == "review" else "created_at"] == STAMP
               and row["updated_at"] == STAMP for source, row in records)
    assert [row["body"] for _, row in records].count("  ") == 3
    assert forge.pr_feedback_items("owner/repo", 1) == [
        FeedbackItem("conversation:1", "**Alice:**\n\nChange λ\n\nKeep 🦉"),
        FeedbackItem("review:1", "**Alice (changes requested):**\n\nChange λ\n\nKeep 🦉"),
        FeedbackItem("inline:1", "**Alice on `src/λ.py:4`:**\n\nChange λ\n\nKeep 🦉"),
        FeedbackItem("inline:7", "**Alice on `old.py:9`:**\n\nChange λ\n\nKeep 🦉")]
    assert all(call.method == "GET" and call.query["limit"] == ["50"] for call in fake.calls)
    assert {call.path for call in fake.calls} == {ROOT + "/issues/1/comments", ROOT + "/pulls/1/reviews",
                     *(ROOT + f"/pulls/1/reviews/{i}/comments" for i in range(1, 7))}
    assert all(any(call.path == path and call.query["page"] != ["1"] for call in fake.calls)
               for path in (ROOT + "/issues/1/comments", ROOT + "/pulls/1/reviews",
                            ROOT + "/pulls/1/reviews/1/comments", ROOT + "/pulls/1/reviews/4/comments"))


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/complete-identified-feedback")
@pytest.mark.parametrize("endpoint", ["issues/1/comments", "pulls/1/reviews", "pulls/1/reviews/1/comments"])
@pytest.mark.parametrize("failure", ["transport", "missing-id", "conflict", "malformed-page"])
@pytest.mark.parametrize("method", ["pr_feedback_records", "pr_feedback_items"])
def test_feedback_partial_reads_refused(fake, feedback_repo, endpoint, failure, method):
    row = dict(feedback_repo["comments"][0])
    if failure == "missing-id":
        row.pop("id")
        reply = Reply([row])
    elif failure == "conflict":
        row["body"] = "Edited"
        reply = Reply([row])
    elif failure == "malformed-page":
        reply = Reply({"secret": SECRET})
    else:
        reply = Reply(status=503, raw=SECRET.encode())
    fake.route("GET", ROOT + "/" + endpoint, reply, reply, reply, page=2)
    with pytest.raises(ForgeError) as caught:
        getattr(make_forge(fake), method)("owner/repo", 1)
    assert caught.value.transient is (failure == "transport")
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert fake.calls[-1].path == ROOT + "/" + endpoint


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/complete-identified-feedback")
@pytest.mark.parametrize("change", [{"body": "different"}, {"created_at": "later"},
                                    {"user": {"login": "Alice", "id": 900}}])
def test_conflicting_inline_identity_across_reviews_refused(fake, feedback_repo, change):
    feedback_repo["reviews"][3]["comments"][0].update(change)
    with pytest.raises(ForgeError, match="Conflicting"):
        make_forge(fake).pr_feedback_records("owner/repo", 1)


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/complete-identified-feedback")
@pytest.mark.parametrize("body", [None, "", "  ", "**nightshift** — escalation", "Act"])
def test_blank_feedback_baselines_and_commented_review_format(fake, body):
    row = authored(body=body, state="COMMENT", submitted_at=STAMP, path="file.py", line=2)
    issue = candidate(1, [], comments=[row], reviews=[{**row, "comments": [row]}])
    fake.repository("owner/repo", labels=[], pulls=[issue])
    forge = make_forge(fake)
    records = forge.pr_feedback_records("owner/repo", 1)
    assert [source for source, _ in records] == ["conversation", "review", "inline"]
    assert all(record["body"] == (body or "") for _, record in records)
    items = forge.pr_feedback_items("owner/repo", 1)
    assert [item.id for item in items] == (["conversation:1", "review:1", "inline:1"] if body == "Act" else [])
    if items:
        assert items[1].block == "**Alice:**\n\nAct"


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/complete-identified-feedback")
@pytest.mark.parametrize("source, patch", [("conversation", {"body": []}),
    ("review", {"state": None}), ("review", {"state": []}),
    ("inline", {"path": []}), ("inline", {"position": True}), ("inline", {"line": "2"})])
def test_malformed_eligible_feedback_refused(fake, feedback_repo, source, patch):
    row = {"conversation": feedback_repo["comments"][0], "review": feedback_repo["reviews"][0],
           "inline": feedback_repo["reviews"][0]["comments"][0]}[source]
    row.update(patch)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).pr_feedback_items("owner/repo", 1)
    assert not caught.value.transient


@pytest.mark.scenario("gitea-forge/issue-and-feedback-records/draft-facts-and-blockers")
def test_drafting_requires_complete_comment_pages(fake):
    fake.repository("owner/repo", labels=[], issues=[candidate(1, [], comments=[authored()])])
    fake.route("GET", ROOT + "/issues/1/comments", Reply(status=403), page=2)
    with pytest.raises(ForgeError):
        make_forge(fake).issue_for_draft("owner/repo", 1)


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/transport-failure-classification")
@pytest.mark.parametrize("reply, attempts", [(Reply(status=503, raw=SECRET.encode()), 3),
    (Reply(status=403, raw=SECRET.encode()), 1), (OSError(SECRET), 3),
    (Reply(raw=SECRET.encode()), 1), (Reply({}), 1), (Reply({"state": True}), 1)])
def test_unreadable_blockers_retained_in_input_order(fake, offline, reply, attempts):
    fake.repository("owner/repo", labels=[], issues=[{**candidate(1, []), "state": "closed"}])
    for number in (3, 2):
        fake.route("GET", ROOT + f"/issues/{number}", *([reply] * attempts))
    assert make_forge(fake).open_blockers("owner/repo", [3, 1, 2]) == [3, 2]
    assert [call.path for call in fake.calls] == [ROOT + "/issues/3"] * attempts + [
        ROOT + "/issues/1"] + [ROOT + "/issues/2"] * attempts
    assert offline == ([0.1, 0.2] * 2 if attempts == 3 else [])


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/guard-exception-identity")
@pytest.mark.parametrize("kind", ["http", "forge", "os", "pause"])
@pytest.mark.parametrize("stop", [1, 2, 3])
def test_blocker_guards_never_become_fallback(fake, kind, stop):
    error = guard_error(kind)
    calls = []

    def guard():
        calls.append(True)
        if len(calls) == stop:
            raise error

    fake.route("GET", ROOT + "/issues/1", Reply(status=503), Reply(status=503))
    forge = make_forge(fake, guard=guard)
    with pytest.raises(type(error)) as caught:
        forge.open_blockers("owner/repo", [1, 2])
    assert caught.value is error and len(fake.calls) == stop - 1
    assert forge._guard_error.get() is None
    fake.route("GET", ROOT + "/issues/2", *([error if kind == "http" else Reply(status=503)] * 3))
    assert forge.open_blockers("owner/repo", [2]) == [2]


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/short-intermediate-page")
def test_short_pages_and_queries_remain_endpoint_local(fake):
    forge = make_forge(fake)
    fixed = {"state": "all", "page": 99, "limit": 1}
    for path in (PATH, PATH + "/other"):
        for page, rows in enumerate(([{"id": 2}], [{"id": 1}], []), 1):
            headers = {"X-Total-Count": "2"}
            if rows:
                headers["Link"] = '<https://foreign/secret?page=90>; rel="next"'
            fake.route("GET", "/gitea" + path, Reply(rows, headers=headers), page=page)
        assert forge._pages(path, fixed) == [[{"id": 2}], [{"id": 1}]]
    assert fixed == {"state": "all", "page": 99, "limit": 1}
    assert [call.query for call in fake.calls] == [
        {"state": ["all"], "page": [str(page)], "limit": ["50"]}
        for page in (1, 2, 3, 1, 2, 3)]
    assert all(response.closed for response in fake.responses)


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/incomplete-listing-refused")
@pytest.mark.parametrize("second", [
    Reply({"body": SECRET}), Reply(None), Reply([None]), Reply([{"id": True}]),
    Reply([{"id": 0}]), Reply([{"id": "2"}]), Reply([{"name": SECRET}]),
    Reply([{"id": 1, "body": SECRET}]), Reply([{"id": 2}, {"id": 1}]),
    Reply([], headers={"X-Total-Count": "3"}),
    Reply([], headers={"X-Next-Page": "3"}),
    Reply([], headers={"Link": '<https://foreign/>; rel="next"'}),
    Reply([], headers={"X-Total-Count": SECRET}),
    Reply([], headers={"X-Total-Count": "9" * 5000}),
    Reply([], headers={"X-Next-Page": "1"}),
    Reply([{"id": 3}], headers={"X-Total-Count": "2"}),
    Reply(status=503, raw=SECRET.encode()),
])
def test_later_page_failure_never_returns_partial_results(fake, second):
    fake.route("GET", WIRE_PATH, Reply([{"id": 1}, {"id": 2}]), page=1)
    fake.route("GET", WIRE_PATH, second, second, second, page=2)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake)._pages(PATH)
    assert caught.value.transient is (second.status == 503)
    assert [call.query["page"] for call in fake.calls] == [["1"]] + (
        [["2"]] * (3 if second.status == 503 else 1))
    assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/incomplete-listing-refused")
@pytest.mark.parametrize("first, second", [
    ({"X-Total-Count": "2"}, {"X-Total-Count": "3"}),
    ({"X-Next-Page": "0"}, {}), ({"X-Next-Page": ""}, {}),
    ({"X-Next-Page": "0", "Link": '<https://foreign/>; rel="next"'}, {}),
])
def test_contradictory_completion_metadata(fake, first, second):
    fake.route("GET", WIRE_PATH, Reply([{"id": 1}], headers=first), page=1)
    fake.route("GET", WIRE_PATH, Reply([{"id": 2}], headers=second), page=2)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake)._pages(PATH)
    assert not caught.value.transient


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/incomplete-listing-refused")
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("rows", [[], [{"id": 2}]])
def test_all_link_fields_are_checked_for_contradictory_completion(fake, reverse, rows):
    links = [("Link", '<https://foreign/previous>; rel="prev"'),
             ("link", '<https://foreign/next>; rel="next"')]
    if reverse:
        links.reverse()
    # A next link contradicts both an empty page and an explicit end marker.
    headers = links + ([("X-Next-Page", "0")] if rows else [])
    fake.route("GET", WIRE_PATH, Reply([{"id": 1}]), page=1)
    fake.route("GET", WIRE_PATH, Reply(rows, headers=headers), page=2)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake)._pages(PATH)
    assert not caught.value.transient
    assert [call.query["page"] for call in fake.calls] == [["1"], ["2"]]
    assert len(fake.responses[-1].headers.get_all("Link")) == 2
    assert all(response.closed for response in fake.responses)


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/short-intermediate-page")
def test_multiple_link_fields_without_next_allow_empty_termination(fake):
    headers = [("Link", '<https://foreign/previous>; rel="prev"'),
               ("Link", '<https://foreign/first>; rel="first"')]
    fake.route("GET", WIRE_PATH, Reply([{"id": 1}]), page=1)
    fake.route("GET", WIRE_PATH, Reply([], headers=headers), page=2)
    assert make_forge(fake)._pages(PATH) == [[{"id": 1}]]
    assert [call.query["page"] for call in fake.calls] == [["1"], ["2"]]


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/incomplete-listing-refused")
@pytest.mark.parametrize("terminal", [False, True])
def test_thousand_page_bound_requires_empty_termination(fake, terminal):
    for page in range(1, 1001):
        rows = [] if terminal and page == 1000 else [{"id": page}]
        fake.route("GET", WIRE_PATH, Reply(rows), page=page)
    forge = make_forge(fake)
    if terminal:
        assert len(forge._pages(PATH)) == 999
    else:
        with pytest.raises(ForgeError, match="limit exceeded") as caught:
            forge._pages(PATH)
        assert not caught.value.transient
    assert len(fake.calls) == 1000


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/short-intermediate-page")
def test_identical_records_deduplicate_without_stopping(fake):
    for page, rows in enumerate(([{"id": 1}], [{"id": 1}, {"id": 2}], []), 1):
        fake.route("GET", WIRE_PATH, Reply(rows, headers={"X-Total-Count": "2"}), page=page)
    assert make_forge(fake)._pages(PATH) == [[{"id": 1}], [{"id": 2}]]
    assert len(fake.calls) == 3


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/incomplete-listing-refused")
def test_duplicate_ids_with_different_json_types_conflict(fake):
    fake.route("GET", WIRE_PATH, Reply([{"id": 1, "value": True}]), page=1)
    fake.route("GET", WIRE_PATH, Reply([{"id": 1, "value": 1}, {"id": 2}]), page=2)
    with pytest.raises(ForgeError, match="Conflicting"):
        make_forge(fake)._pages(PATH)
    assert len(fake.calls) == 2


@pytest.mark.scenario("gitea-forge/complete-endpoint-pagination/short-intermediate-page")
@pytest.mark.parametrize("empty", [False, True])
def test_consistent_completion_metadata_still_requires_empty_page(fake, empty):
    rows = [] if empty else [{"id": 1}]
    headers = {"X-Total-Count": str(len(rows)), "X-Next-Page": "0"}
    fake.route("GET", WIRE_PATH, Reply(rows, headers=headers), page=1)
    if rows:
        fake.route("GET", WIRE_PATH, Reply([], headers=headers), page=2)
    assert make_forge(fake)._pages(PATH) == ([] if empty else [rows])
    assert len(fake.calls) == (1 if empty else 2)


def label_row(number, name):
    return {"id": number, "name": name}


def candidate(number, labels, **extra):
    return dict(id=100 + number, number=number, title=f"Issue {number}",
                body="Details", state="open", labels=list(labels), **extra)


@pytest.fixture
def label_repo(fake):
    labels = [label_row(i, name) for i, name in enumerate(
        ("ready", "working", "keep", "other", "later"), 1)]
    issue = candidate(1, [labels[0], labels[2], labels[3]])
    pr = candidate(2, issue["labels"])
    fake.repository("owner/repo", labels=labels, issues=[issue], pulls=[pr])
    return labels, issue, pr


@pytest.mark.scenario("gitea-forge/fresh-label-resolution/client-side-filtering")
@pytest.mark.parametrize("operation", ["list_issues", "list_open_prs"])
def test_gitea_label_filter_never_server_side(fake, operation):
    from nightshift.queue import Issue

    ready, other = label_row(1, "ready"), label_row(2, "other")
    rows = [candidate(1, [other]), candidate(2, [ready], pull_request={}),
            candidate(3, [label_row(99, "ready")]), candidate(4, [ready]),
            candidate(5, [])]
    fake.repository("owner/repo", labels=[other, ready], issues=rows, pulls=rows)
    result = getattr(make_forge(fake), operation)("owner/repo", "ready")
    assert result == ([Issue("owner/repo", 4, "Issue 4", "Details")]
                      if operation == "list_issues" else [{"number": 2}, {"number": 4}])
    assert all(not ({"label", "labels"} & call.query.keys()) for call in fake.calls)
    assert all(call.method == "GET" for call in fake.calls)
    assert [call.query for call in fake.calls[3:]] == [
        {"state": ["open"], "page": [str(page)], "limit": ["50"]} for page in range(1, 7)]


@pytest.mark.scenario("gitea-forge/fresh-label-resolution/poll-refreshes-label-map")
@pytest.mark.parametrize("operation", ["list_issues", "list_open_prs"])
@pytest.mark.parametrize("change", ["recreated", "renamed"])
def test_poll_refreshes_label_map(fake, operation, change):
    old, new = label_row(1, "ready"), label_row(2, "ready")
    rows = [candidate(1, [old]), candidate(2, [new])]
    fake.repository("owner/repo", labels=[old], issues=rows, pulls=rows)
    poll = getattr(make_forge(fake), operation)
    first = poll("owner/repo", "ready")
    assert (first[0].number if operation == "list_issues" else first[0]["number"]) == 1
    fake.repositories["owner/repo"]["labels"] = (
        [new] if change == "recreated" else [label_row(1, "renamed")])
    before = len(fake.calls)
    if change == "recreated":
        second = poll("owner/repo", "ready")
        assert (second[0].number if operation == "list_issues" else second[0]["number"]) == 2
    else:
        with pytest.raises(ForgeError):
            poll("owner/repo", "ready")
        assert all(call.path == WIRE_PATH for call in fake.calls[before:])
    assert [call.query["page"] for call in fake.calls if call.path == WIRE_PATH] == [
        ["1"], ["2"], ["1"], ["2"]]


@pytest.mark.scenario("gitea-forge/fresh-label-resolution/missing-or-duplicate-label")
@pytest.mark.parametrize("operation", ["list_issues", "list_open_prs", "relabel_issue", "relabel_pr"])
@pytest.mark.parametrize("bad", [None, label_row(9, "ready"), label_row(9, "keep"),
                                label_row(9, ""), label_row(True, "bad"), {"id": 9},
                                label_row(9, None)])
def test_gitea_missing_or_duplicate_label_refused(fake, label_repo, operation, bad):
    labels, _, _ = label_repo
    if bad is None:
        labels.pop(0)
    else:
        labels.append(bad)  # Unrelated duplicate names on later pages are ambiguous too.
    method = getattr(make_forge(fake), operation)
    with pytest.raises(ForgeError) as caught:
        if operation.startswith("relabel"):
            method("owner/repo", 1, add=["working"], remove=["ready"])
        else:
            method("owner/repo", "ready")
    assert not caught.value.transient
    assert fake.calls and all(call.path == WIRE_PATH and call.method == "GET" for call in fake.calls)


@pytest.mark.scenario("gitea-forge/verified-label-mutations/write-is-read-back")
@pytest.mark.parametrize("target", [1, 2, "2"])
def test_gitea_label_write_is_read_back(fake, label_repo, target):
    _, issue, pr = label_repo
    forge = make_forge(fake)
    relabel = forge.relabel_issue if target == 1 else forge.relabel_pr
    relabel("owner/repo", target, add=["working", "later", "working"], remove=["ready", "other"])
    path = f"/gitea/api/v1/repos/owner/repo/issues/{target}/labels"
    calls = [call for call in fake.calls if call.path != WIRE_PATH]
    expected = [("GET", path, {"page": [str(page)], "limit": ["50"]}, None)
                for page in range(1, 5)]
    for method, suffix, body, pages in [("DELETE", "/4", None, 3), ("DELETE", "/1", None, 2),
                                       ("POST", "", {"labels": [5]}, 3),
                                       ("POST", "", {"labels": [2]}, 4)]:
        expected.append((method, path + suffix, {}, body))
        expected.extend(("GET", path, {"page": [str(page)], "limit": ["50"]}, None)
                        for page in range(1, pages + 1))
    assert [(call.method, call.path, call.query, call.body) for call in calls] == expected
    assert {label["name"] for label in (issue if target == 1 else pr)["labels"]} == {
        "keep", "working", "later"}
    before = len(fake.calls)
    relabel("owner/repo", target, add=["later", "working"], remove=["ready", "other"])
    assert all(call.method == "GET" for call in fake.calls[before:])
    assert fake.calls[before].path == WIRE_PATH  # Every mutation refreshes the map.
    assert forge.issue_labels("owner/repo", int(target)) == {"keep", "working", "later"}


@pytest.mark.scenario("gitea-forge/verified-label-mutations/write-is-read-back")
def test_mutation_uses_recreated_label_and_refuses_overlap(fake, label_repo):
    labels, _, _ = label_repo
    forge = make_forge(fake)
    forge.relabel_issue("owner/repo", 1, add=["working"], remove=[])
    labels[1] = label_row(20, "working")
    # Gitea removes the old label from issues when deleting the repository label.
    fake.repositories["owner/repo"]["issues"][0]["labels"][:] = [labels[0], labels[2], labels[3]]
    forge.relabel_issue("owner/repo", 1, add=["working"], remove=[])
    assert [call.body for call in fake.calls if call.method == "POST"] == [
        {"labels": [2]}, {"labels": [20]}]
    before = len(fake.calls)
    with pytest.raises(ForgeError, match="Overlapping"):
        forge.relabel_issue("owner/repo", 1, add=["ready"], remove=["ready"])
    assert all(call.method == "GET" and call.path == WIRE_PATH for call in fake.calls[before:])


@pytest.mark.scenario("gitea-forge/verified-label-mutations/silent-write-failure")
@pytest.mark.parametrize("target", [1, 2])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("failure", ["silent", "readback-malformed", "readback-http", "write-http"])
def test_label_failure_stops_remaining_writes(fake, label_repo, target, method, failure):
    _, issue, _ = label_repo
    path = f"/gitea/api/v1/repos/owner/repo/issues/{target}/labels"
    if failure == "silent":
        fake.silent_label_writes.add(method)
    elif failure.startswith("readback"):
        reply = Reply({"unexpected": SECRET}) if failure == "readback-malformed" else Reply(status=503)
        fake.route("GET", path, Reply([issue["labels"][1]]), reply, reply, reply, page=2)
    else:
        fake.route(method, path + ("/4" if method == "DELETE" else ""), Reply(status=503))
    forge = make_forge(fake)
    relabel = forge.relabel_issue if target == 1 else forge.relabel_pr
    with pytest.raises(ForgeError) as caught:
        relabel("owner/repo", target, add=["working", "later"],
                remove=["ready", "other"] if method == "DELETE" else [])
    assert caught.value.transient is failure.endswith("http")
    assert [call.method for call in fake.calls if call.method != "GET"] == [method]
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    if failure == "write-http":
        assert fake.calls[-1].method == method


@pytest.mark.scenario("gitea-forge/verified-label-mutations/silent-write-failure")
@pytest.mark.parametrize("operation", ["issue_labels", "relabel_issue"])
def test_unreadable_current_labels_refused(fake, label_repo, operation):
    path = "/gitea/api/v1/repos/owner/repo/issues/1/labels"
    fake.route("GET", path, Reply([label_row(9, None)]), page=2)
    with pytest.raises(ForgeError):
        method = getattr(make_forge(fake), operation)
        method("owner/repo", 1, **({"add": ["working"], "remove": []}
                                   if operation == "relabel_issue" else {}))
    assert all(call.method == "GET" for call in fake.calls)


@pytest.mark.scenario("gitea-forge/fresh-label-resolution/client-side-filtering")
@pytest.mark.parametrize("operation, endpoint", [("list_issues", "issues"), ("list_open_prs", "pulls")])
@pytest.mark.parametrize("bad_page", ["labels", "candidates"])
def test_poll_later_page_failure_refuses_candidates(fake, label_repo, operation, endpoint, bad_page):
    path = WIRE_PATH if bad_page == "labels" else f"/gitea/api/v1/repos/owner/repo/{endpoint}"
    fake.route("GET", path, Reply(status=403), page=2)
    with pytest.raises(ForgeError) as caught:
        getattr(make_forge(fake), operation)("owner/repo", "ready")
    assert not caught.value.transient
    if bad_page == "labels":
        assert all(call.path == WIRE_PATH for call in fake.calls)


@pytest.mark.scenario("gitea-forge/verified-label-mutations/write-is-read-back")
def test_empty_issue_labels_are_proven_by_a_read(fake):
    fake.repository("owner/repo", labels=[], issues=[candidate(1, [])])
    assert make_forge(fake).issue_labels("owner/repo", 1) == set()
    assert len(fake.calls) == 1 and fake.calls[0].query == {"page": ["1"], "limit": ["50"]}


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/guard-exception-identity")
@pytest.mark.parametrize("kind", ["http", "forge", "os", "pause"])
@pytest.mark.parametrize("stage, stop", [("read", 1), ("write", 11), ("readback", 12),
                                         ("later-page", 13), ("retry", 13)])
def test_label_guard_identity(fake, label_repo, kind, stage, stop):
    labels, _, _ = label_repo
    error = guard_error(kind)
    guards = []

    def guard():
        guards.append(True)
        if len(guards) == stop:
            raise error

    if stage == "retry":
        fake.route("GET", "/gitea/api/v1/repos/owner/repo/issues/1/labels",
                   Reply([labels[0]]), Reply(status=503), page=1)
    forge = make_forge(fake, guard=guard)
    with pytest.raises(type(error)) as caught:
        forge.relabel_issue("owner/repo", 1, add=["working"], remove=["ready", "other"])
    assert caught.value is error
    assert len(fake.calls) == stop - 1 and len(guards) == stop
    assert sum(call.method != "GET" for call in fake.calls) == (1 if stop > 11 else 0)
    assert forge._guard_error.get() is None


PULLS_PATH = "/gitea/api/v1/repos/owner/repo/pulls"
PR_VIEWS = ["pr_for_" + purpose for purpose in
            ("recovery", "admission", "revision", "publication", "readiness")]


def pull(number=7, branch="topic/work", **changes):
    row = candidate(number, [label_row(1, "keep")], merged=False,
                    head={"ref": branch, "sha": "a" * 40, "repo": {"id": 10}},
                    base={"ref": "main", "repo": {"id": 10}},
                    html_url=f"https://forge.example/gitea/owner/repo/pulls/{number}")
    row.update(changes)
    return row


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/highest-branch-pr")
@pytest.mark.parametrize("merged", [False, True])
def test_gitea_pr_by_branch_takes_highest_number(fake, merged):
    rows = [pull(8), pull(99, "topic/work-more"), pull(3, state="closed"),
            pull(24, state="closed", merged=merged), pull(50, "Topic/work"), pull(1)]
    fake.repository("owner/repo", labels=[], pulls=rows)
    forge = make_forge(fake)
    assert forge.latest_pr_number("owner/repo", "topic/work") == 24
    assert forge.latest_pr_number("owner/repo", "absent") is None
    expected = [{"state": ["all"], "page": [str(page)], "limit": ["50"]}
                for page in range(1, 8)]
    assert [call.query for call in fake.calls] == expected * 2
    assert all(call.path == PULLS_PATH and call.method == "GET" for call in fake.calls)


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/transport-failure-classification")
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("status", [403, 503])
def test_pr_lookup_transport_failure_discards_partial_match(fake, offline, strict, status):
    fake.repository("owner/repo", labels=[], pulls=[pull()])
    attempts = 3 if status == 503 else 1
    fake.route("GET", PULLS_PATH, *[Reply(status=status, raw=SECRET.encode())] * attempts, page=2)
    forge = make_forge(fake)
    if strict:
        with pytest.raises(ForgeError) as caught:
            forge.latest_pr_number("owner/repo", "topic/work", strict=strict)
        assert caught.value.transient is (status == 503)
        assert SECRET not in "".join(traceback.format_exception(caught.value))
    else:
        assert forge.latest_pr_number("owner/repo", "topic/work", strict=strict) is None
    assert len(fake.calls) == 1 + attempts
    assert offline == ([0.1, 0.2] if status == 503 else [])


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/highest-branch-pr")
@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/transport-failure-classification")
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("second", [None, [pull(0)], [pull(2, head=None)],
                                   [pull(2, head={"ref": None})], [pull()],
                                   [pull(title="conflict")]])
def test_pr_lookup_malformed_evidence_is_never_absence(fake, strict, second):
    fake.repository("owner/repo", labels=[], pulls=[pull()])
    fake.route("GET", PULLS_PATH, Reply(second), page=2)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).latest_pr_number("owner/repo", "topic/work", strict=strict)
    assert not caught.value.transient


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("operation", PR_VIEWS)
@pytest.mark.parametrize("state, merged, cross", [("open", False, False),
                                                  ("closed", False, True), ("closed", True, False)])
def test_pr_views_share_normalized_facts(fake, operation, state, merged, cross):
    row = pull(state=state, merged=merged, title="WIP: café", body="Text\r\nkept")
    row["head"]["repo"] = {"id": 20 if cross else 10, "full_name": "misleading/name"}
    fake.repository("owner/repo", labels=[], pulls=[row])
    assert getattr(make_forge(fake), operation)("owner/repo", 7) == {
        "state": "MERGED" if merged else state.upper(), "title": row["title"], "body": row["body"],
        "labels": row["labels"], "headRefName": "topic/work", "headRefOid": "a" * 40,
        "isCrossRepository": cross, "url": row["html_url"], "isDraft": True}
    assert [(call.method, call.path, call.query) for call in fake.calls] == [
        ("GET", PULLS_PATH + "/7", {})]


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("side", ["head", "base"])
@pytest.mark.parametrize("identity", [None, {}, {"id": None}, {"id": True}, {"id": 0},
                                       {"id": "10"}, {"full_name": "owner/repo"}])
def test_pr_missing_repository_identity_is_refused(fake, side, identity):
    row = pull()
    row[side]["repo"] = identity
    row["isCrossRepository"] = False  # Server hints cannot replace actual IDs.
    fake.repository("owner/repo", labels=[], pulls=[row])
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).pr_for_admission("owner/repo", 7)
    assert not caught.value.transient and len(fake.calls) == 1


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("changes", [{"state": "unknown"}, {"merged": None}, {"title": None},
    {"body": {}}, {"labels": None}, {"head": None}, {"base": None}, {"html_url": None},
    {"head": {"ref": "x", "sha": "short", "repo": {"id": 10}}}])
def test_malformed_pr_details_refused(fake, changes):
    fake.repository("owner/repo", labels=[], pulls=[pull(**changes)])
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).pr_for_recovery("owner/repo", 7)
    assert not caught.value.transient


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("target", [7, "7", "topic/work",
    "https://forge.example/gitea/owner/repo/pulls/7",
    "https://FORGE.example:443/gitea/owner/repo/pulls/7"])
def test_pr_targets_resolve_for_views_and_verified_labels(fake, target):
    keep, ready = label_row(1, "keep"), label_row(2, "ready")
    row = pull()
    fake.repository("owner/repo", labels=[keep, ready], pulls=[pull(3), row])
    forge = make_forge(fake)
    assert forge.pr_state_url("owner/repo", target) == {"state": "OPEN", "url": row["html_url"]}
    forge.relabel_pr("owner/repo", target, add=["ready"], remove=[])
    assert row["labels"] == [keep, ready]
    writes = [call for call in fake.calls if call.method != "GET"]
    assert [(call.method, call.path, call.body) for call in writes] == [
        ("POST", "/gitea/api/v1/repos/owner/repo/issues/7/labels", {"labels": [2]})]
    assert fake.calls[-1].path.endswith("/issues/7/labels")
    assert fake.calls[-1].query["page"] == ["3"]  # Complete verification after the write.
    if target == "topic/work":
        assert sum(call.path == PULLS_PATH for call in fake.calls) == 6


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("branch", ["topic#42", "#42", "topic/work#42",
    "topic/hello\u00a0world", "topic/hello\u2003world", "topic/hello\u2028world",
    "topic/hello\u0085world", "topic/hello\u009fworld", "topic/café"])
def test_pr_branch_targets_allow_git_valid_characters_for_views_and_labels(fake, branch):
    keep, ready = label_row(1, "keep"), label_row(2, "ready")
    row, unrelated = pull(branch=branch), pull(99, branch="topic")
    fake.repository("owner/repo", labels=[keep, ready], pulls=[unrelated, row])
    forge = make_forge(fake)
    assert forge.latest_pr_number("owner/repo", branch) == 7
    assert forge.pr_state_url("owner/repo", branch) == {"state": "OPEN", "url": row["html_url"]}
    forge.relabel_pr("owner/repo", branch, add=["ready"], remove=[])
    assert row["labels"] == [keep, ready] and unrelated["labels"] == [keep]
    assert [(call.method, call.path, call.body) for call in fake.calls if call.method != "GET"] == [
        ("POST", ROOT + "/issues/7/labels", {"labels": [2]})]
    listings = [call for call in fake.calls if call.path == PULLS_PATH]
    assert [call.query for call in listings] == [
        {"state": ["all"], "page": [str(page)], "limit": ["50"]}
        for page in range(1, 4)] * 3
    assert fake.calls[-1].path == ROOT + "/issues/7/labels"
    assert fake.calls[-1].query["page"] == ["3"]


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("target", [0, -1, True, None, "", "0", "9" * 5000,
    "https://foreign.example/gitea/owner/repo/pulls/7",
    "http://forge.example/gitea/owner/repo/pulls/7",
    "https://forge.example:444/gitea/owner/repo/pulls/7",
    *["https://forge.example" + path for path in (
        "/owner/repo/pulls/7", "/gitea/owner/other/pulls/7", "/gitea/owner/repo/issues/7",
        "/gitea/owner/repo/pulls/7/", "/gitea/owner/repo/pulls/07", "/gitea/owner/repo/pulls/0",
        "/gitea/owner/repo/pulls/7?", "/gitea/owner/repo/pulls/7#",
        "/gitea/owner/repo/pulls/7#42",
        "/gitea/owner/repo/pulls/%37", "/gitea/owner/repo/pulls/../7",
        "/gitea/owner/repo/pulls/%2e%2e/7", "/gitea/owner/repo/pulls/7/files")],
    "https://user@forge.example/gitea/owner/repo/pulls/7",
    "https://forge.example:/gitea/owner/repo/pulls/7", "https://[broken/",
    "https:/forge.example/gitea/owner/repo/pulls/7", "//forge.example/gitea/owner/repo/pulls/7",
    " https://forge.example/gitea/owner/repo/pulls/7",
    "\u00a0https://forge.example/gitea/owner/repo/pulls/7",
    "https://forge.example/gitea/owner/repo/pulls/7\u0085",
    "topic\\work", "topic\nwork", "topic work", "topic\x7fwork"])
@pytest.mark.parametrize("operation", ["pr_state_url", "relabel_pr"])
def test_invalid_pr_targets_never_dispatch(fake, target, operation):
    with pytest.raises(ForgeError) as caught:
        getattr(make_forge(fake), operation)("owner/repo", target,
            **({"add": ["ready"], "remove": []} if operation == "relabel_pr" else {}))
    assert not caught.value.transient and not fake.calls


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
def test_absent_branch_cannot_authorize_label_writes(fake):
    fake.repository("owner/repo", labels=[], pulls=[pull()])
    with pytest.raises(ForgeError, match="not found"):
        make_forge(fake).relabel_pr("owner/repo", "absent", add=["ready"], remove=[])
    assert len(fake.calls) == 2 and all(call.path == PULLS_PATH for call in fake.calls)


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/pr-views-and-targets")
@pytest.mark.parametrize("prefix", ["", "/custom/forge"])
def test_pr_paths_encode_repository_components_under_configured_prefix(prefix):
    fake = FakeGitea("https://forge.example" + prefix)
    fake.repository("a&b/r#x", labels=[])
    forge = make_forge(fake)
    url = forge.create_draft_pr("a&b/r#x", "topic/é", "main", "Title", "").stdout
    assert url == fake.base_url + "/a%26b/r%23x/pulls/1"
    assert forge.pr_state_url("a&b/r#x", url) == {"state": "OPEN", "url": url}
    assert forge.latest_pr_number("a&b/r#x", "topic/é") == 1
    assert [call.path for call in fake.calls] == [
        prefix + "/api/v1/repos/a%26b/r%23x/pulls" + suffix for suffix in ("", "/1", "", "")]


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/draft-title-prefix")
@pytest.mark.parametrize("prefix", ["WIP: ", "[Draft] ", "🚧 "])
@pytest.mark.parametrize("prefixed", [False, True, "wrong-case"])
def test_draft_prefix_is_case_sensitive_and_creation_preserves_text(fake, prefix, prefixed):
    # The default is exercised without supplying the constructor option.
    forge = make_forge(fake, **({} if prefix == "WIP: " else {"wip_prefix": prefix}))
    title = (prefix if prefixed is True else prefix.lower() if prefixed else "") + "Café"
    expected = title if title.startswith(prefix) else prefix + title
    row = pull(title=title)
    fake.repository("owner/repo", labels=[], pulls=[row])
    assert forge.pr_for_revision("owner/repo", 7)["isDraft"] is title.startswith(prefix)
    body = "Description\r\n\n  untouched ☃"
    created = forge.create_draft_pr("owner/repo", "topic/new", "release/main", title, body)
    assert created.stdout == "https://forge.example/gitea/owner/repo/pulls/8"
    assert fake.calls[-1].body == {"title": expected, "body": body,
                                    "head": "topic/new", "base": "release/main"}
    view = forge.pr_for_revision("owner/repo", 8)
    assert view["isDraft"] and view["title"] == expected and view["body"] == body
    assert sum(call.method == "POST" for call in fake.calls) == 1


@pytest.mark.scenario("gitea-forge/pr-identity-and-drafts/draft-title-prefix")
@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/transport-failure-classification")
@pytest.mark.parametrize("outcome, transient", [(Reply(status=503, raw=SECRET.encode()), True),
    (OSError(SECRET), True), (Reply(status=403, raw=SECRET.encode()), False),
    (Reply(status=201, raw=SECRET.encode()), False), (Reply({}), False)])
def test_draft_failure_never_repeats_create(fake, offline, outcome, transient):
    fake.route("POST", PULLS_PATH, outcome)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).create_draft_pr("owner/repo", "topic", "main", "Title", "Body")
    assert caught.value.transient is transient
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert len(fake.calls) == 1 and fake.calls[0].method == "POST" and not offline


@pytest.mark.scenario("gitea-forge/failure-and-guard-boundaries/guard-exception-identity")
@pytest.mark.parametrize("kind", ["http", "forge", "os", "pause"])
@pytest.mark.parametrize("stage", ["lookup", "later-page", "retry", "view", "create"])
@pytest.mark.parametrize("strict", [False, True])
def test_pr_guard_identity_prevents_fallback(fake, kind, stage, strict):
    error, guards = guard_error(kind), []
    stop = 2 if stage in ("later-page", "retry") else 1

    def guard():
        guards.append(True)
        if len(guards) == stop:
            raise error

    fake.repository("owner/repo", labels=[], pulls=[pull()])
    if stage == "retry":
        fake.route("GET", PULLS_PATH, Reply(status=503), page=1)
    forge = make_forge(fake, guard=guard)
    with pytest.raises(type(error)) as caught:
        if stage == "create":
            forge.create_draft_pr("owner/repo", "topic", "main", "Title", "Body")
        elif stage == "view":
            forge.pr_for_readiness("owner/repo", 7)
        else:
            forge.latest_pr_number("owner/repo", "topic/work", strict=strict)
    assert caught.value is error and len(fake.calls) == stop - 1
    assert len(guards) == stop and forge._guard_error.get() is None
    # Reusing even the identical exception as a transport failure must not
    # inherit the previous request's guard origin.
    failure = error if kind == "http" else http.GiteaHTTPError(503, SECRET, True)
    fake.route("GET", PULLS_PATH, failure, failure, failure, page=1)
    assert forge.latest_pr_number("owner/repo", "topic/work", strict=False) is None
    assert len(fake.calls) == stop + 2


SHA = "a" * 40
COMMIT_PATH = ROOT + "/git/commits/" + SHA
STATUS_PATH = ROOT + "/commits/" + SHA + "/status"


def combined(state="success", count=1, **changes):
    return {"sha": SHA, "state": state, "total_count": count,
            "statuses": [{"id": n + 1, "state": state} for n in range(count)], **changes}


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/status-mapping")
@pytest.mark.parametrize("state,bucket", [("success", "pass"), ("pending", "pending"),
    ("failure", "fail"), ("error", "fail"), ("warning", "fail"), ("skipped", "skipping")])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_gitea_ci_status_mapping(fake, state, bucket, count):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined(state, count)})
    result = make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert json.loads(result.stdout) == ([
        {"name": "Gitea combined status", "bucket": bucket, "state": state}] if count else [])
    assert [(c.method, c.path, c.query) for c in fake.calls] == [
        ("GET", STATUS_PATH, {"page": [str(page)], "limit": ["50"]})
        for page in range(1, count + 2)]


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/status-mapping")
@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.parametrize("states", [
    ["skipped"], ["skipped", "skipped", "skipped"],
    *[[state] for state in ("pending", "failure", "error", "warning")],
    *[["success", state] for state in ("pending", "failure", "error", "warning")]])
def test_success_refuses_contradictory_status_rows(fake, states):
    rows = [{"id": n + 1, "state": state} for n, state in enumerate(states)]
    fake.repository("owner/repo", labels=[], combined_statuses={
        SHA: combined(count=len(rows), statuses=rows)})
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert not caught.value.transient
    assert [(c.path, c.query) for c in fake.calls] == [
        (STATUS_PATH, {"page": [str(page)], "limit": ["50"]})
        for page in range(1, len(rows) + 2)]


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/status-mapping")
@pytest.mark.parametrize("states", [("success", "skipped"), ("skipped", "success")])
def test_success_with_skipped_rows_requires_successful_evidence(fake, states):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined(
        count=2, statuses=[{"id": n + 1, "state": state} for n, state in enumerate(states)])})
    result = make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert json.loads(result.stdout) == [
        {"name": "Gitea combined status", "bucket": "pass", "state": "success"}]
    assert [c.query["page"] for c in fake.calls] == [["1"], ["2"], ["3"]]


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.scenario("gitea-forge/commit-parent-facts/complete-commit-parents")
@pytest.mark.parametrize("method", ["checks_for_sha", "commit"])
@pytest.mark.parametrize("sha", ["main", "refs/pull/7/merge", "a" * 7, "a" * 39,
    "a" * 41, "g" * 40, SHA + "\n", None, True, {}, SECRET])
def test_exact_sha_required_before_dispatch(fake, method, sha):
    with pytest.raises(ForgeError) as caught:
        getattr(make_forge(fake), method)("owner/repo", sha)
    assert not caught.value.transient and not fake.calls
    assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.parametrize("payload", [None, [], {},
    *[{k: v for k, v in combined().items() if k != field}
      for field in ("sha", "state", "total_count", "statuses")],
    *[combined(sha=sha) for sha in (None, "b" * 40, "short", [])],
    *[combined(state=state, statuses=[]) for state in (None, "", "unknown", "SUCCESS", [], {})],
    *[combined(total_count=count) for count in (None, True, -1, 1.0, "1", 0, 2)],
    *[combined(statuses=rows) for rows in (None, {}, [], [None], [{}],
        [{"id": True, "state": "success"}], [{"id": 0, "state": "success"}],
        [{"id": 1}], [{"id": 1, "state": []}], [{"id": 1, "state": "unknown"}])]])
def test_combined_status_malformed_evidence_refused(fake, payload):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined()})
    fake.route("GET", STATUS_PATH, Reply(payload), page=1)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert not caught.value.transient


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.parametrize("second", [
    combined(sha="b" * 40, statuses=[]), combined(state="pending", statuses=[]),
    combined(total_count=2, statuses=[]), combined(statuses=None), combined(),
    combined(statuses=[{"id": 1, "state": "failure"}]),
    combined(statuses=[{"id": 2, "state": "success"}])])
def test_combined_status_inconsistent_later_pages_refused(fake, second):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined()})
    fake.route("GET", STATUS_PATH, Reply(second), page=2)
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert not caught.value.transient


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.parametrize("page,headers", [
    (1, {"X-Total-Count": "0"}), (1, {"X-Total-Count": "invalid"}),
    (1, {"X-Total-Count": "2"}), (1, {"X-Next-Page": "3"}),
    (2, {"X-Next-Page": "3"}), (2, {"Link": '<https://foreign/>; rel="next"'})])
def test_combined_status_contradictory_metadata_refused(fake, page, headers):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined()})
    fake.route("GET", STATUS_PATH, Reply(combined(statuses=(combined()["statuses"]
               if page == 1 else [])), headers=headers), page=page)
    with pytest.raises(ForgeError):
        make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert all(call.path == STATUS_PATH for call in fake.calls)


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/status-mapping")
def test_status_pagination_deduplicates_identical_overlaps_without_following_links(fake):
    payload = combined(count=3)
    for page, rows in enumerate(([payload["statuses"][0]], payload["statuses"], []), 1):
        headers = {"X-Total-Count": "3"}
        if rows:
            headers["Link"] = '<https://foreign/status>; rel="next"'
        fake.route("GET", STATUS_PATH, Reply({**payload, "statuses": rows}, headers=headers), page=page)
    assert json.loads(make_forge(fake).checks_for_sha("owner/repo", SHA).stdout)[0]["bucket"] == "pass"
    assert [c.query for c in fake.calls] == [
        {"page": [str(p)], "limit": ["50"]} for p in (1, 2, 3)]
    assert all(c.path == STATUS_PATH for c in fake.calls)


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
def test_status_pagination_bound_refuses_without_empty_terminal_page(fake):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined(count=1000)})
    with pytest.raises(ForgeError, match="pagination limit"):
        make_forge(fake).checks_for_sha("owner/repo", SHA)
    assert len(fake.calls) == 1000


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
def test_pr_checks_captures_exact_sha_and_refreshes_on_next_call(fake):
    pr, next_sha = pull(), "b" * 40
    fake.repository("owner/repo", labels=[], pulls=[pr], combined_statuses={
        SHA: combined(), next_sha: combined("failure", sha=next_sha)})

    def change_head(call):
        pr["head"]["sha"] = next_sha
        return Reply(combined())

    fake.route("GET", STATUS_PATH, change_head, page=1)
    forge = make_forge(fake)
    assert json.loads(forge.pr_checks("owner/repo", 7).stdout)[0]["bucket"] == "pass"
    assert json.loads(forge.pr_checks("owner/repo", 7).stdout)[0]["bucket"] == "fail"
    assert [c.path for c in fake.calls] == [PULLS_PATH + "/7", STATUS_PATH, STATUS_PATH,
        PULLS_PATH + "/7", STATUS_PATH.replace(SHA, next_sha), STATUS_PATH.replace(SHA, next_sha)]
    assert all(c.method == "GET" for c in fake.calls)


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.parametrize("head", [None, {}, {"sha": "main"}, {"sha": "abc1234"}])
def test_pr_checks_refuses_missing_or_invalid_head(fake, head):
    fake.repository("owner/repo", labels=[], pulls=[pull(head=head)])
    with pytest.raises(ForgeError):
        make_forge(fake).pr_checks("owner/repo", 7)
    assert len(fake.calls) == 1


def commit_row(parents=()):
    return {"sha": SHA, "parents": [{"sha": sha, "url": "https://foreign/"} for sha in parents],
            "commit": {"message": "Preserved metadata", "author": {"date": "2026-10-01T12:30:01Z"},
                       "committer": {"date": "2026-10-02T12:30:02-05:00"}}}


@pytest.mark.scenario("gitea-forge/commit-parent-facts/complete-commit-parents")
@pytest.mark.parametrize("parents", [[], ["b" * 40], ["b" * 40, "c" * 40, "D" * 40]])
@pytest.mark.parametrize("metadata", [None, {}, {"author": {"date": None}}])
def test_commit_preserves_all_parents_and_available_dates(fake, parents, metadata):
    row = commit_row(parents)
    if metadata is not None:
        row["commit"] = metadata
    fake.repository("owner/repo", labels=[], commits=[row])
    assert make_forge(fake).commit("owner/repo", SHA) == {
        "sha": SHA, "parents": [{"sha": sha} for sha in parents], "commit": row["commit"]}
    assert [(c.method, c.path, c.query) for c in fake.calls] == [("GET", COMMIT_PATH, {})]


@pytest.mark.scenario("gitea-forge/commit-parent-facts/complete-commit-parents")
@pytest.mark.parametrize("changes", [
    {"sha": "b" * 40}, {"sha": None}, {"sha": "short"}, {"parents": None}, {"parents": {}},
    *[{"parents": [parent]} for parent in (None, {}, {"sha": "short"}, {"sha": "g" * 40},
        {"sha": None}, {"sha": []}, SHA)], {"commit": []}, {"commit": {"author": []}},
    {"commit": {"committer": {"date": 42}}}, "missing-parents"])
def test_commit_refuses_incomplete_or_mismatching_facts(fake, changes):
    row = commit_row()
    if changes == "missing-parents":
        del row["parents"]
    else:
        row.update(changes)
    fake.route("GET", COMMIT_PATH, Reply(row))
    with pytest.raises(ForgeError) as caught:
        make_forge(fake).commit("owner/repo", SHA)
    assert not caught.value.transient and len(fake.calls) == 1


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.scenario("gitea-forge/commit-parent-facts/complete-commit-parents")
@pytest.mark.parametrize("method", ["checks_for_sha", "commit"])
@pytest.mark.parametrize("status", [403, 503])
def test_commit_and_later_status_transport_failure(fake, method, status):
    fake.repository("owner/repo", labels=[], combined_statuses={SHA: combined()})
    path, page = (STATUS_PATH, 2) if method == "checks_for_sha" else (COMMIT_PATH, None)
    attempts = 3 if status == 503 else 1
    fake.route("GET", path, *[Reply(status=status, raw=SECRET.encode())] * attempts, page=page)
    with pytest.raises(ForgeError) as caught:
        getattr(make_forge(fake), method)("owner/repo", SHA)
    assert caught.value.transient is (status == 503)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert len(fake.calls) == attempts + (method == "checks_for_sha")


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.scenario("gitea-forge/commit-parent-facts/complete-commit-parents")
@pytest.mark.parametrize("kind", ["http", "forge", "os", "pause"])
@pytest.mark.parametrize("stage", ["commit", "status", "terminal", "retry", "after-pr"])
def test_commit_status_guard_identity(fake, kind, stage):
    error, guards = guard_error(kind), []
    stop = 1 if stage in ("commit", "status") else 2

    def guard():
        guards.append(True)
        if len(guards) == stop:
            raise error

    fake.repository("owner/repo", labels=[], pulls=[pull()], combined_statuses={SHA: combined()})
    if stage == "retry":
        fake.route("GET", STATUS_PATH, Reply(status=503), page=1)
    forge = make_forge(fake, guard=guard)
    method = "commit" if stage == "commit" else "pr_checks" if stage == "after-pr" else "checks_for_sha"
    with pytest.raises(type(error)) as caught:
        getattr(forge, method)("owner/repo", 7 if stage == "after-pr" else SHA)
    assert caught.value is error and len(fake.calls) == stop - 1
    assert len(guards) == stop and forge._guard_error.get() is None


@pytest.mark.scenario("gitea-forge/exact-sha-combined-status/sha-and-status-refusal")
@pytest.mark.scenario("gitea-forge/commit-parent-facts/complete-commit-parents")
@pytest.mark.parametrize("method", ["checks_for_sha", "commit"])
def test_full_hex_sha_case_preserves_identity(fake, method):
    path = (COMMIT_PATH if method == "commit" else STATUS_PATH).replace(SHA, SHA.upper())
    if method == "commit":
        fake.route("GET", path, Reply(commit_row()))
    else:
        fake.route("GET", path, Reply(combined(count=0)), page=1)
    result = getattr(make_forge(fake), method)("owner/repo", SHA.upper())
    assert (result["sha"] == SHA if method == "commit" else json.loads(result.stdout) == [])
    assert len(fake.calls) == 1 and fake.calls[0].path == path
