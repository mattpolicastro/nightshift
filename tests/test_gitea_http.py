"""Socket-free checks of the Gitea HTTP transport and JSON helpers."""

from email.message import Message
from http.client import HTTPConnection, HTTPResponse
from io import BytesIO
import json
import socket
import traceback
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener
from urllib.response import addinfourl

import pytest

from nightshift.forge import http


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("A transport test attempted network access")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def response():
    def make(status=200, body=b'{}', location=None):
        headers = Message()
        if location is not None:
            headers["Location"] = location
        result = addinfourl(BytesIO(body), headers, "https://forge.example/api", status)
        result.msg = "remote reason"
        return result
    return make


@pytest.fixture
def opener(response):
    class FakeOpener:
        def __init__(self):
            self.calls, self.handlers = [], []
            self.result = response()

        def add_handler(self, handler):
            self.handlers.append(handler)

        def open(self, request, *, timeout):
            self.calls.append((request, timeout))
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
    return FakeOpener()


@pytest.mark.scenario("gitea-http/configured-origin/https-and-loopback-bases")
@pytest.mark.scenario("gitea-http/configured-origin/unsafe-bases-refused")
def test_refuses_http_except_loopback(opener):
    for base in ("https://forge.example", "https://forge.example:8443/gitea/",
                 "http://localhost", "http://LOCALHOST:3000", "http://127.0.0.1",
                 "http://127.0.0.1:3000", "http://[::1]", "http://[::1]:80"):
        http.GiteaTransport(base, "secret", opener=opener)
    for base in ("http://forge.example", "http://127.0.0.2", "http://localhost.evil",
                 "ftp://localhost", "https:///missing", "https://host:wrong",
                 "https://host:65536", "https://host:", "https://user:pw@host",
                 "https://host?", "https://host#", "https://host/white space",
                 "https://host/\npath", "https://host/\\path", "https://[bad]/",
                 "https://host/a/../b", "https://host/a%2f%2E%2e%2fb",
                 "https://host/%5cpath", "https://host/%00", "https://host/%20"):
        with pytest.raises(ValueError):
            http.GiteaTransport(base, "secret", opener=opener)
    assert not opener.calls


@pytest.mark.scenario("gitea-http/api-path-confinement/unsafe-paths-refused")
def test_rejects_absolute_and_traversal_paths(opener):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    for path in ("https://forge.example/api", "https://evil/api", "//evil/api", "api", "",
                 "/a/../b", "/a/%2e%2E/b", "/a%2f..%2fb", "/../b", "/a/..",
                 "/api?q=1", "/api#part", "/a\\b", "/a%5Cb", "/a b", "/a%20b",
                 "/a\nb", "/a%0Db", "/a\x00b", "/a%7fb"):
        with pytest.raises(ValueError):
            client.request("GET", path)
    assert not opener.calls


@pytest.mark.scenario("gitea-http/api-path-confinement/authorization-stays-on-origin")
@pytest.mark.parametrize("prefix", ["", "/gitea/"])
def test_authorization_only_to_configured_origin(opener, prefix, monkeypatch):
    client = http.GiteaTransport("https://FORGE.example:8443" + prefix, "secret", opener=opener)
    query = {"next": ["https://evil/path", "//evil/path"], "q": "a&b#c"}
    client.request("get", "/api/v1/a%2Fb", query=query)
    request, timeout = opener.calls[0]
    url = urlsplit(request.full_url)
    assert (url.scheme, url.hostname, url.port) == ("https", "forge.example", 8443)
    assert url.path == prefix.rstrip("/") + "/api/v1/a%2Fb"
    assert parse_qs(url.query) == {**query, "q": [query["q"]]}
    assert request.get_header("Authorization") == "token secret"
    assert timeout == 30
    def forbidden_request(*args, **kwargs):
        pytest.fail("Origin mismatch must be rejected before creating an authorized request")
    monkeypatch.setattr(http, "Request", forbidden_request)
    for base in ("http://forge.example:8443", "https://evil:8443", "https://forge.example:443"):
        client._base = base  # Exercise the origin check independently of path validation.
        with pytest.raises(ValueError):
            client.request("GET", "/api")
    assert len(opener.calls) == 1


@pytest.mark.scenario("gitea-http/redirect-refusal/redirect-is-never-followed")
@pytest.mark.parametrize("scheme", ["http://localhost", "https://forge.example"])
@pytest.mark.parametrize("location", ["/next", "https://foreign/next", "//foreign/next", None])
@pytest.mark.parametrize("default_factory", [False, True])
def test_redirect_is_an_error_and_never_followed(opener, response, monkeypatch,
                                               scheme, location, default_factory):
    class FakeHTTP(HTTPHandler):
        def http_open(self, request):
            return opener.open(request, timeout=request.timeout)

    class FakeHTTPS(HTTPSHandler):
        https_open = FakeHTTP.http_open

    pipeline = build_opener(ProxyHandler({}), FakeHTTP(), FakeHTTPS(), HTTPRedirectHandler())
    monkeypatch.setattr(http, "build_opener", lambda: pipeline)
    client = http.GiteaTransport(scheme, "secret", opener=None if default_factory else pipeline)
    for status in range(300, 400):
        opener.result = response(status, location=location)
        with pytest.raises(http.GiteaHTTPError) as failure:
            client.request("GET", "/api")
        assert failure.value.status == status and failure.value.transient is False
        assert len(opener.calls) == status - 299
        assert opener.calls[-1][0].full_url == scheme + "/api"
        assert opener.result.closed


@pytest.mark.scenario("gitea-http/token-secrecy/echoed-token-remains-secret")
def test_token_never_in_errors_or_repr(opener, response, caplog):
    token = "sensitive-token-123"
    client = http.GiteaTransport("https://" + token + ".example", token, opener=opener)

    def secret_free(call, error_type):
        with pytest.raises(error_type) as failure:
            call()
        error = failure.value
        diagnostics = str(error) + repr(error) + repr(error.args) + repr(client)
        diagnostics += "".join(traceback.format_exception(error)) + caplog.text
        assert token not in diagnostics
        return error

    for result, status in ((response(403, token.encode()), 403),
                           (response(200, token.encode()), 200),
                           (response(200, token.encode() + b'\xff'), 200),
                           (response(201, b'{"' + token.encode() + b'":}'), 201),
                           (response(302, location="https://evil/" + token), 302),
                           (HTTPError("https://evil/" + token, 401, token, Message(), BytesIO()), 401),
                           (URLError(token), None), (OSError(token), None)):
        opener.result = result
        error = secret_free(lambda: client.request("GET", "/api"), http.GiteaHTTPError)
        assert error.status == status
        assert error.transient is (status is None)
        if not isinstance(result, Exception):
            assert result.closed
    opener.result = response(200, json.dumps({"echo": token}).encode())
    error = secret_free(lambda: client.get_page("/api", {}, 1, 10), http.GiteaHTTPError)
    assert error.status == 200 and error.transient is False
    assert opener.result.closed
    calls = len(opener.calls)
    secret_free(lambda: client.request("GET", "//" + token), ValueError)
    secret_free(lambda: client.get_page("/api", {}, token, 10), ValueError)
    secret_free(lambda: http.GiteaTransport("https://host:" + token, token, opener=opener), ValueError)
    for bad_token in ("", token + "\r", token + "\n"):
        secret_free(lambda: http.GiteaTransport("https://host", bad_token, opener=opener), ValueError)
    for timeout in (0, -1):
        secret_free(lambda: http.GiteaTransport("https://host", token, opener=opener,
                                               timeout=timeout), ValueError)
    assert len(opener.calls) == calls


@pytest.mark.scenario("gitea-http/json-requests/json-request-and-response")
@pytest.mark.parametrize("timeout", [None, 2.5])
@pytest.mark.parametrize("body", [{"title": "caf\u00e9 \u2603"}, [], {}, False, 0, "", None])
def test_json_request_and_response(opener, response, timeout, body):
    options = {} if timeout is None else {"timeout": timeout}
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener, **options)
    query = {"label": ["a&b", "caf\u00e9"], "q": "with space", "page": 2}
    for _ in range(2):  # Each open receives the configured timeout.
        opener.result = response(201, '{"title": "caf\u00e9"}'.encode("utf-8"))
        opener.result.headers["lInK"] = '</api?page=3>; rel="next"'
        opener.result.headers["X-Total-Count"] = "12"
        status, headers, result = client.request("post", "/api", query=query, json_body=body)
        request, observed_timeout = opener.calls[-1]
        assert request.get_method() == "POST"
        assert request.full_url == (
            "https://forge.example/api?label=a%26b&label=caf%C3%A9&q=with+space&page=2")
        assert request.get_header("Authorization") == "token secret"
        assert request.get_header("Accept") == "application/json"
        assert observed_timeout == (30 if timeout is None else timeout)
        if body is None:
            assert request.data is None
            assert request.get_header("Content-type") is None
        else:
            assert isinstance(request.data, bytes)
            assert json.loads(request.data.decode("utf-8")) == body
            assert request.get_header("Content-type") == "application/json"
        assert status == 201 and result == {"title": "caf\u00e9"}
        assert headers is opener.result.headers
        assert headers["Link"] == headers["link"] == '</api?page=3>; rel="next"'
        assert headers["x-total-count"] == "12"
        assert opener.result.closed
    assert len(opener.calls) == 2


@pytest.mark.scenario("gitea-http/json-requests/empty-and-invalid-bodies")
@pytest.mark.parametrize("status", [200, 204])
@pytest.mark.parametrize("body", [b"", b"null", b"\xff", b'{"broken":}', b" "])
def test_empty_and_invalid_bodies(opener, response, status, body):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    opener.result = response(status, body)
    if body in (b"", b"null"):
        received, headers, result = client.request("GET", "/api")
        assert received == status and result is None
        assert headers is opener.result.headers
    else:
        with pytest.raises(http.GiteaHTTPError) as failure:
            client.request("GET", "/api")
        assert failure.value.status == status
        assert failure.value.transient is False
    assert opener.result.closed
    assert len(opener.calls) == 1


@pytest.mark.scenario("gitea-http/json-and-page-helpers/get-json-result")
@pytest.mark.parametrize("query", [None, {"label": ["one", "two"]}])
@pytest.mark.parametrize("body, expected", [(b'{"id": 1}', {"id": 1}), (b"[]", []),
                                           (b"false", False), (b"null", None), (b"", None)])
def test_get_json_result(opener, response, query, body, expected):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    opener.result = response(200, body)
    result = client.get_json("/api") if query is None else client.get_json("/api", query)
    assert result == expected
    assert len(opener.calls) == 1 and opener.result.closed
    request, _ = opener.calls[0]
    assert request.get_method() == "GET" and request.data is None
    assert urlsplit(request.full_url).path == "/api"
    assert parse_qs(urlsplit(request.full_url).query) == (query or {})


@pytest.mark.scenario("gitea-http/json-and-page-helpers/get-json-result")
@pytest.mark.parametrize("status, body", [(403, b"denied"), (200, b"invalid")])
def test_get_json_preserves_request_errors(opener, response, status, body):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    errors = []
    for call in (lambda: client.request("GET", "/api"), lambda: client.get_json("/api")):
        opener.result = response(status, body)
        with pytest.raises(http.GiteaHTTPError) as failure:
            call()
        errors.append(failure.value)
        assert failure.value.status == status and failure.value.transient is False
        assert opener.result.closed
    assert errors[0].args == errors[1].args
    assert len(opener.calls) == 2
    with pytest.raises(ValueError):
        client.get_json("//foreign/api")
    assert len(opener.calls) == 2


@pytest.mark.scenario("gitea-http/json-and-page-helpers/single-page-without-completeness-policy")
@pytest.mark.parametrize("items", [[], [{"id": 1}], [{"id": 1}, {"id": 2}]])
@pytest.mark.parametrize("pagination", [False, True])
def test_get_page_returns_one_array_and_headers(opener, response, items, pagination):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    query = {"page": 99, "limit": 100, "label": ["a", "b"]}
    opener.result = response(200, json.dumps(items).encode())
    if pagination:
        opener.result.headers["Link"] = '<https://foreign/api?page=4>; rel="next"'
        opener.result.headers["X-Total-Count"] = "100"
    result, headers = client.get_page("/api", query, page=3, limit=2)
    assert result == items and headers is opener.result.headers
    assert headers.get("link") == (
        '<https://foreign/api?page=4>; rel="next"' if pagination else None)
    assert headers.get("x-total-count") == ("100" if pagination else None)
    assert query == {"page": 99, "limit": 100, "label": ["a", "b"]}
    assert len(opener.calls) == 1 and opener.result.closed
    request, _ = opener.calls[0]
    assert request.get_method() == "GET" and request.data is None
    assert urlsplit(request.full_url).path == "/api"
    assert parse_qs(urlsplit(request.full_url).query) == {
        "page": ["3"], "limit": ["2"], "label": ["a", "b"]}


@pytest.mark.scenario("gitea-http/json-and-page-helpers/invalid-page-inputs-and-results")
@pytest.mark.parametrize("field", ["page", "limit"])
@pytest.mark.parametrize("value", [0, -1, 1.5, 1.0, "2", None, True, False, [], {}])
def test_get_page_rejects_invalid_arguments(opener, field, value):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    arguments = {"page": 1, "limit": 2, field: value}
    query = {"label": "a"}
    with pytest.raises(ValueError):
        client.get_page("/api", query, **arguments)
    assert not opener.calls
    assert query == {"label": "a"}


@pytest.mark.scenario("gitea-http/json-and-page-helpers/invalid-page-inputs-and-results")
@pytest.mark.parametrize("status, body", [(200, b"{}"), (201, b'"items"'), (200, b"1"),
                                       (200, b"false"), (200, b"null"), (204, b"")])
def test_get_page_requires_an_array(opener, response, status, body):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    opener.result = response(status, body)
    with pytest.raises(http.GiteaHTTPError) as failure:
        client.get_page("/api", {}, 1, 2)
    assert failure.value.status == status and failure.value.transient is False
    assert len(opener.calls) == 1 and opener.result.closed


@pytest.mark.scenario("gitea-http/token-secrecy/echoed-token-remains-secret")
@pytest.mark.parametrize("suffix", ["\n", "\r", "\t", " ", "\x00", "\x7f", "/", ":",
                                    "(", ")", "<", ">", "@", ",", ";", "\\", '"',
                                    "[", "]", "?", "=", "{", "}", "\u017f"])
def test_token_never_in_invalid_method(opener, monkeypatch, caplog, suffix):
    token = "1234567890" * 4
    client = http.GiteaTransport("https://forge.example", token, opener=opener)

    def forbidden_request(*args, **kwargs):
        pytest.fail("Invalid methods must fail before request construction")

    monkeypatch.setattr(http, "Request", forbidden_request)
    for method in ("", "GET" + suffix + token):
        with pytest.raises(ValueError) as failure:
            client.request(method, "/api")
        error = failure.value
        diagnostics = str(error) + repr(error) + repr(error.args) + repr(client) + caplog.text
        assert token not in diagnostics + "".join(traceback.format_exception(error))
    assert not opener.calls


@pytest.mark.scenario("gitea-http/token-secrecy/echoed-token-remains-secret")
@pytest.mark.parametrize("method", ["get", "pAtCh", "custom-method", "!#$%&'*+-.^_`|~0123456789"])
def test_valid_extension_methods(opener, method):
    client = http.GiteaTransport("https://forge.example", "secret", opener=opener)
    client.request(method, "/api")
    assert len(opener.calls) == 1
    assert opener.calls[0][0].get_method() == method.upper()


@pytest.mark.scenario("gitea-http/token-secrecy/echoed-token-remains-secret")
@pytest.mark.parametrize("prefix", [b"", b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n"])
def test_token_never_in_protocol_errors(prefix, caplog):
    token = "sensitive-token-123"
    stream = BytesIO(prefix + token.encode())
    calls = []

    class FakeSocket:
        def makefile(self, *args):
            return stream

    class FakeHTTP(HTTPHandler):
        def http_open(self, request):
            calls.append(request)
            response = HTTPResponse(FakeSocket())
            try:
                response.begin()  # BadStatusLine during open; IncompleteRead during read.
            except Exception:
                response.close()
                raise
            return response

    opener = build_opener(ProxyHandler({}), FakeHTTP())
    client = http.GiteaTransport("http://localhost", token, opener=opener)
    with pytest.raises(http.GiteaHTTPError) as failure:
        client.request("GET", "/api")
    error = failure.value
    assert error.status is None and error.transient is True
    assert len(calls) == 1 and stream.closed
    diagnostics = str(error) + repr(error) + repr(error.args) + repr(client) + caplog.text
    assert token not in diagnostics + "".join(traceback.format_exception(error))


@pytest.mark.scenario("gitea-http/token-secrecy/echoed-token-remains-secret")
@pytest.mark.parametrize("field", ["token", "path", "query"])
def test_token_never_in_request_encoding_errors(field, caplog):
    token = "sensitive-token-123" + ("\N{SNOWMAN}" if field == "token" else "")
    calls = []

    class EncodingOnlyConnection(HTTPConnection):
        def connect(self):
            pytest.fail("Encoding must fail before connecting")

    class FakeHTTP(HTTPHandler):
        def http_open(self, request):
            calls.append(request)
            return self.do_open(EncodingOnlyConnection, request)

    opener = build_opener(ProxyHandler({}), FakeHTTP())
    client = http.GiteaTransport("http://localhost", token, opener=opener)
    path = "/api/" + token + "\N{SNOWMAN}" if field == "path" else "/api"
    query = {"q": token + "\ud800"} if field == "query" else None
    with pytest.raises(http.GiteaHTTPError) as failure:
        client.request("GET", path, query=query)
    error = failure.value
    assert error.status is None and error.transient is False
    assert len(calls) == (0 if field == "query" else 1)
    diagnostics = str(error) + repr(error) + repr(error.args) + repr(client) + caplog.text
    assert token not in diagnostics + "".join(traceback.format_exception(error))
