"""Standalone Gitea operations with complete reads and verified label writes."""

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
import json
import re
from urllib.parse import quote

from nightshift.forge import ForgeError
from nightshift.forge.http import GiteaHTTPError, GiteaTransport
from nightshift.queue import Issue


class GiteaForge:
    def __init__(self, base_url: str, token: str, *, trusted_logins: Iterable[str],
                 bot_login: str, wip_prefix: str = "WIP: ", opener=None,
                 guard: Callable[[], None] | None = None, timeout: float = 30):
        if not isinstance(base_url, str):
            raise ValueError("Invalid Gitea base URL")
        if not isinstance(token, str):
            raise ValueError("Invalid Gitea token")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < timeout < float("inf")):
            raise ValueError("Invalid Gitea timeout")
        if not isinstance(bot_login, str) or not bot_login.strip():
            raise ValueError("Invalid Gitea bot login")
        if not isinstance(wip_prefix, str) or not wip_prefix:
            raise ValueError("Invalid Gitea WIP prefix")
        if isinstance(trusted_logins, (str, bytes, Mapping)):
            raise ValueError("Invalid Gitea trusted logins")
        try:
            logins = tuple(trusted_logins)
        except TypeError:
            raise ValueError("Invalid Gitea trusted logins") from None
        if any(not isinstance(login, str) or not login.strip() for login in logins):
            raise ValueError("Invalid Gitea trusted logins")
        if guard is not None and not callable(guard):
            raise ValueError("Invalid Gitea guard")
        if opener is not None and any(not callable(getattr(opener, name, None))
                                      for name in ("open", "add_handler")):
            raise ValueError("Invalid Gitea opener")
        self._trusted_logins = frozenset(login.casefold() for login in logins)
        self._bot_login, self._wip_prefix = bot_login.casefold(), wip_prefix
        self._guard_error: ContextVar[BaseException | None] = ContextVar(
            "gitea_guard_error", default=None)

        def observed_guard():
            # Clear on each attempt too: the transport owns GET retries.
            self._guard_error.set(None)
            try:
                if guard is not None:
                    guard()
            except BaseException as error:
                self._guard_error.set(error)
                raise

        self._transport = GiteaTransport(base_url, token, opener=opener,
                                         guard=observed_guard, timeout=timeout)

    @staticmethod
    def _repo_path(repo: str, *components: str) -> str:
        if (not isinstance(repo, str) or len(repo.split("/")) != 2
                or not all(repo.split("/"))):
            raise ValueError("Invalid Gitea repository")
        return "/api/v1/repos/" + "/".join(
            quote(part, safe="") for part in (*repo.split("/"), *components))

    def _request(self, method: str, path: str, *, query=None, json_body=None):
        """The sole transport boundary; guard origin takes priority over type.

        Future suppression/reconciliation belongs in the genuine HTTP-error
        branch below, before translation, never around a broad exception catch.
        """
        scope = self._guard_error.set(None)
        try:
            return self._transport.request(method, path, query=query, json_body=json_body)
        except GiteaHTTPError as error:
            if self._guard_error.get() is error:
                raise
            raise ForgeError("Gitea request failed", transient=error.transient) from None
        finally:
            self._guard_error.reset(scope)

    def _pages(self, path: str, query=None) -> list[list[dict]]:
        """Read through an empty page or refuse the entire listing.

        Keep queries endpoint-local and treat response links only as evidence,
        never as request destinations. Identical IDs are deduplicated.
        """
        fixed_query = dict(query or {})
        pages: list[list[dict]] = []
        seen: dict[int, dict] = {}
        signatures: set[frozenset[int]] = set()
        total = None
        declared_end = False
        for page in range(1, 1001):
            _, headers, rows = self._request(
                "GET", path, query={**fixed_query, "page": page, "limit": 50})
            if not isinstance(rows, list):
                raise ForgeError("Invalid Gitea page", transient=False)
            count = headers.get("X-Total-Count")
            if count is not None:
                if not re.fullmatch(r"[0-9]+", count):
                    raise ForgeError("Invalid Gitea pagination metadata", transient=False)
                try:
                    current_total = int(count)
                except ValueError:
                    raise ForgeError("Invalid Gitea pagination metadata", transient=False) from None
                if total is not None and current_total != total:
                    raise ForgeError("Inconsistent Gitea total", transient=False)
                total = current_total
            next_page = headers.get("X-Next-Page")
            if next_page is not None and next_page not in ("", "0", str(page + 1)):
                raise ForgeError("Invalid Gitea next page", transient=False)
            # Link is repeatable; get() would silently ignore later fields.
            has_next = (next_page == str(page + 1) or any(re.search(
                r';\s*rel\s*=\s*(?:"[^\"]*\bnext\b[^\"]*"|next\b)',
                link, re.IGNORECASE) is not None
                for link in headers.get_all("Link", [])))
            if not rows:
                if has_next or (total is not None and len(seen) != total):
                    raise ForgeError("Incomplete Gitea listing", transient=False)
                return pages
            if declared_end:
                raise ForgeError("Inconsistent Gitea termination", transient=False)
            declared_end = next_page in ("", "0")
            if declared_end and has_next:
                raise ForgeError("Inconsistent Gitea termination", transient=False)
            unique = []
            ids = set()
            for row in rows:
                if (not isinstance(row, dict) or type(row.get("id")) is not int
                        or row["id"] <= 0):
                    raise ForgeError("Invalid Gitea record", transient=False)
                key = row["id"]
                ids.add(key)
                if key in seen:
                    if json.dumps(seen[key], sort_keys=True) != json.dumps(row, sort_keys=True):
                        raise ForgeError("Conflicting Gitea record", transient=False)
                else:
                    seen[key] = row
                    unique.append(row)
            signature = frozenset(ids)
            if signature in signatures:
                raise ForgeError("Repeated Gitea page", transient=False)
            signatures.add(signature)
            if total is not None and len(seen) > total:
                raise ForgeError("Inconsistent Gitea total", transient=False)
            if unique:
                pages.append(unique)
        raise ForgeError("Gitea pagination limit exceeded", transient=False)

    @staticmethod
    def _label_map(rows: object) -> dict[str, int]:
        if not isinstance(rows, list):
            raise ForgeError("Invalid Gitea labels", transient=False)
        labels: dict[str, int] = {}
        ids: set[int] = set()
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("id")) is not int
                    or row["id"] <= 0 or not isinstance(row.get("name"), str)
                    or not row["name"].strip()):
                raise ForgeError("Invalid Gitea label", transient=False)
            if row["name"] in labels or row["id"] in ids:
                raise ForgeError("Ambiguous Gitea labels", transient=False)
            labels[row["name"]] = row["id"]
            ids.add(row["id"])
        return labels

    def _read_labels(self, path: str) -> dict[str, int]:
        return self._label_map([row for page in self._pages(path) for row in page])

    def _resolve_labels(self, repo: str, names: Iterable[str]) -> dict[str, int]:
        labels = self._read_labels(self._repo_path(repo, "labels"))
        for name in names:
            if not isinstance(name, str) or name not in labels:
                raise ForgeError("Requested Gitea label unavailable", transient=False)
        return labels

    @staticmethod
    def _number(number: object) -> str:
        if type(number) is not int or number <= 0:
            raise ForgeError("Invalid Gitea issue or PR number", transient=False)
        return str(number)

    def _label_candidates(self, repo: str, label: str, endpoint: str) -> list[dict]:
        label_id = self._resolve_labels(repo, [label])[label]
        pages = self._pages(self._repo_path(repo, endpoint), {"state": "open"})
        candidates = []
        for page in pages:
            for row in page:
                if endpoint == "issues" and row.get("pull_request") is not None:
                    continue
                if label_id in self._label_map(row.get("labels")).values():
                    self._number(row.get("number"))
                    candidates.append(row)
        return candidates

    def list_issues(self, repo: str, label: str) -> list[Issue]:
        issues = []
        for row in self._label_candidates(repo, label, "issues"):
            if (not isinstance(row.get("title"), str)
                    or (row.get("body") is not None and not isinstance(row["body"], str))):
                raise ForgeError("Invalid Gitea issue text", transient=False)
            issues.append(Issue(repo, row["number"], row["title"], row.get("body") or ""))
        return issues

    def list_open_prs(self, repo: str, label: str) -> list[dict]:
        return [{"number": row["number"]}
                for row in self._label_candidates(repo, label, "pulls")]

    def issue_labels(self, repo: str, number: int) -> set[str]:
        path = self._repo_path(repo, "issues", self._number(number), "labels")
        return set(self._read_labels(path))

    def relabel_issue(self, repo: str, number: int, *, add: list[str], remove: list[str]) -> None:
        path = self._repo_path(repo, "issues", self._number(number), "labels")
        labels = self._resolve_labels(repo, [*add, *remove])
        if set(add) & set(remove):
            raise ForgeError("Overlapping Gitea label changes", transient=False)
        current = set(self._read_labels(path).values())
        for adding, names in ((False, remove), (True, add)):
            for name in sorted(set(names)):
                label_id = labels[name]
                if (label_id in current) == adding:
                    continue
                if adding:
                    self._request("POST", path, json_body={"labels": [label_id]})
                else:
                    self._request("DELETE", path + "/" + str(label_id))
                current = set(self._read_labels(path).values())
                if (label_id in current) != adding:
                    raise ForgeError("Gitea label write verification failed", transient=False)

    def relabel_pr(self, repo: str, target: str | int, *, add: list[str], remove: list[str]) -> None:
        # Branch and URL resolution is added with the PR operations.
        if isinstance(target, str) and re.fullmatch(r"[0-9]+", target):
            try:
                target = int(target)
            except ValueError:
                raise ForgeError("Invalid Gitea PR number", transient=False) from None
        if type(target) is not int:
            raise ForgeError("Invalid Gitea PR number", transient=False)
        self.relabel_issue(repo, target, add=add, remove=remove)
