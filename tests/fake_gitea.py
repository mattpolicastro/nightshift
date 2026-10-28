"""An in-process recording urllib opener; routes are installed explicitly."""

from collections import deque
from dataclasses import dataclass, field
from email.message import Message
from io import BytesIO
import json
from urllib.parse import parse_qs, quote, urlsplit
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
        self.repositories = {}
        self.silent_label_writes = set()

    def repository(self, repo, *, labels, issues=(), pulls=(), page_size=1, facts=None):
        """Install factual read/write routes; deliberately ignore label/head filters.

        Issues may carry comments and timeline arrays; PRs may additionally
        carry reviews with nested comments. Each list paginates independently.
        """
        self.repositories[repo] = dict(labels=labels, issues=list(issues),
                                       pulls=list(pulls), page_size=page_size,
                                       facts=facts if facts is not None else {"name": repo.split("/")[1]})

    def _state_reply(self, call, page):
        for repo, state in self.repositories.items():
            root = urlsplit(self.base_url).path + "/api/v1/repos/" + "/".join(
                quote(part, safe="") for part in repo.split("/"))
            rows = None
            if call.method == "GET" and call.path == root:
                return Reply(state["facts"])
            if call.method == "POST" and call.path == root + "/pulls":
                assert set(call.body) == {"title", "body", "head", "base"}
                number = max((row["number"] for row in [*state["issues"], *state["pulls"]]), default=0) + 1
                pr_id = max((row["id"] for row in state["pulls"]), default=0) + 1
                identity = {"id": state["facts"].get("id", 1)}
                web_path = "/".join(quote(part, safe="") for part in repo.split("/"))
                created = dict(id=pr_id, number=number, state="open", merged=False, labels=[],
                               title=call.body["title"], body=call.body["body"],
                               head={"ref": call.body["head"], "sha": "a" * 40, "repo": identity},
                               base={"ref": call.body["base"], "repo": identity},
                               html_url=self.base_url + "/" + web_path + f"/pulls/{number}")
                state["pulls"].append(created)
                return Reply(created, status=201)
            for endpoint in ("labels", "issues", "pulls"):
                if call.path == root + "/" + endpoint and call.method == "GET":
                    rows = state[endpoint]
                    if endpoint != "labels" and call.query.get("state", ["all"])[0] != "all":
                        rows = [row for row in rows if row["state"] == call.query["state"][0]]
            for pr in state["pulls"]:
                if call.method == "GET" and call.path == root + f'/pulls/{pr["number"]}':
                    return Reply(pr)
            for issue in [*state["issues"], *state["pulls"]]:
                detail_path = root + f'/issues/{issue["number"]}'
                if call.method == "GET":
                    if call.path == detail_path:
                        return Reply(issue)
                    for endpoint in ("comments", "timeline"):
                        if call.path == detail_path + "/" + endpoint:
                            rows = issue.get(endpoint, [])
                    review_path = root + f'/pulls/{issue["number"]}/reviews'
                    if call.path == review_path:
                        rows = issue.get("reviews", [])
                    for review in issue.get("reviews", []):
                        if call.path == review_path + f'/{review["id"]}/comments':
                            rows = review.get("comments", [])
                path = root + f'/issues/{issue["number"]}/labels'
                if call.method == "GET" and call.path == path:
                    rows = issue["labels"]
                elif call.method == "POST" and call.path == path:
                    assert set(call.body) == {"labels"}
                    if "POST" not in self.silent_label_writes:
                        current = {label["id"] for label in issue["labels"]}
                        issue["labels"].extend(label for label in state["labels"]
                                               if label["id"] in call.body["labels"]
                                               and label["id"] not in current)
                    return Reply(issue["labels"])
                elif call.method == "DELETE" and call.path.startswith(path + "/"):
                    label_id = int(call.path[len(path) + 1:])
                    if "DELETE" not in self.silent_label_writes:
                        issue["labels"][:] = [label for label in issue["labels"]
                                               if label["id"] != label_id]
                    return Reply(status=204, raw=b"")
            if rows is not None:
                assert page is not None
                size = min(state["page_size"], int(call.query["limit"][0]))
                return Reply(rows[(page - 1) * size:page * size])
        raise AssertionError("Unexpected fake Gitea route")

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
        key = (call.method, call.path, page)
        if key in self.routes:
            pending = self.routes[key]
            assert pending, "Unexpected or exhausted fake Gitea route"
            outcome = pending.popleft()
        else:
            outcome = self._state_reply(call, page)
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
