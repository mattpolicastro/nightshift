"""An in-process recording urllib opener; routes are installed explicitly."""

from collections import deque
from dataclasses import dataclass, field
from email.message import Message
from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit
from urllib.response import addinfourl


@dataclass
class Reply:
    payload: object = None
    status: int = 200
    headers: dict[str, str] | list[tuple[str, str]] = field(default_factory=dict)
    raw: bytes | None = None


@dataclass
class Call:
    method: str
    path: str
    query: dict[str, list[str]]
    body: object
    timeout: float


class FakeGitea:
    def __init__(self, base_url="https://forge.example/gitea"):
        self.base_url = base_url
        self.calls: list[Call] = []
        self.handlers = []
        self.responses = []
        self.routes = {}

    def add_handler(self, handler):
        self.handlers.append(handler)

    def route(self, method, path, *outcomes, page=None):
        """Queue replies, exceptions or callbacks for one explicit route/page."""
        self.routes[method, path, page] = deque(outcomes)

    def open(self, request, *, timeout):
        url, base = urlsplit(request.full_url), urlsplit(self.base_url)
        assert (url.scheme, url.netloc) == (base.scheme, base.netloc), "Foreign origin"
        assert url.path.startswith(base.path + "/api/v1/"), "Foreign API prefix"
        query = parse_qs(url.query, keep_blank_values=True)
        call = Call(request.get_method(), url.path, query,
                    json.loads(request.data) if request.data is not None else None, timeout)
        self.calls.append(call)  # Deliberately never retain authorization headers.
        page = int(query["page"][0]) if "page" in query else None
        pending = self.routes.get((call.method, call.path, page))
        assert pending, "Unexpected or exhausted fake Gitea route"
        outcome = pending.popleft()
        if callable(outcome):
            outcome = outcome(call)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, Reply), "Expected a fake Gitea reply"
        headers = Message()
        fields = outcome.headers.items() if isinstance(outcome.headers, dict) else outcome.headers
        for name, value in fields:
            headers[name] = value
        body = outcome.raw if outcome.raw is not None else json.dumps(outcome.payload).encode()
        response = addinfourl(BytesIO(body), headers, request.full_url, outcome.status)
        self.responses.append(response)
        for handler in self.handlers:
            response = handler.http_response(request, response)
        return response
