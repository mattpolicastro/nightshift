"""Foundation contracts through the real transport, without a service or sockets."""

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
