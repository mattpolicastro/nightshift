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
