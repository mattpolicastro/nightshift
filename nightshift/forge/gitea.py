"""Standalone Gitea operations with complete reads and verified label writes."""

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
import json
import re
from typing import Literal
from urllib.parse import quote, urlsplit

from nightshift.forge import ForgeError
from nightshift.forge.http import GiteaHTTPError, GiteaTransport
from nightshift.forge.types import ResponseText
from nightshift.queue import FeedbackItem, Issue, _block, _has_final_marker, has_instructions


class GiteaForge:
    def __init__(self, base_url: str, token: str, *, trusted_logins: Iterable[str],
                 bot_login: str, wip_prefix: str = "WIP: ", opener=None,
                 guard: Callable[[], None] | None = None, timeout: float = 30,
                 after_request: Callable[[], None] | None = None):
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
        if after_request is not None and not callable(after_request):
            raise ValueError("Invalid Gitea completion guard")
        self._after_request = after_request
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
        self._base_url = urlsplit(base_url.rstrip("/"))

    @staticmethod
    def _repo_path(repo: str, *components: str) -> str:
        if (not isinstance(repo, str) or len(repo.split("/")) != 2
                or not all(repo.split("/"))):
            raise ValueError("Invalid Gitea repository")
        return "/api/v1/repos/" + "/".join(
            quote(part, safe="") for part in (*repo.split("/"), *components))

    def _request(self, method: str, path: str, *, query=None, json_body=None,
                 blocking_fallback: bool = False,
                 reconcile: Callable[[], bool] | None = None):
        """The sole transport boundary; guard origin takes priority over type.

        Classify genuine HTTP failures before translation. Reconciliation runs
        outside the handler so failures do not inherit the write error context.
        """
        scope = self._guard_error.set(None)
        try:
            result = self._transport.request(method, path, query=query, json_body=json_body)
        except GiteaHTTPError as error:
            if self._guard_error.get() is error:
                raise
            transient = error.transient
            ambiguous = transient or (error.status is not None and 200 <= error.status < 300)
        else:
            # Completion guards are not transport failures, even if their
            # exception type matches one. Never reconcile or suppress them.
            if self._after_request is not None:
                self._after_request()
            return result
        finally:
            self._guard_error.reset(scope)
        if reconcile is not None and ambiguous and reconcile():
            return None
        if blocking_fallback:
            return None
        raise ForgeError("Gitea request failed", transient=transient) from None

    @staticmethod
    def _login(user: object) -> str | None:
        login = user.get("login") if isinstance(user, dict) else None
        return login if isinstance(login, str) and login.strip() else None

    def _association(self, user: object) -> str:
        login = self._login(user)
        return "OWNER" if (login is not None and login.casefold() in self._trusted_logins
                           and login.casefold() != self._bot_login) else "NONE"

    def _is_bot(self, user: object) -> bool:
        login = self._login(user)
        return login is not None and login.casefold() == self._bot_login

    def _record(self, row: dict) -> dict:
        record = dict(row)
        record.pop("authorAssociation", None)
        record["author_association"] = self._association(row.get("user"))
        record["user"] = {"login": self._login(row.get("user"))}
        if row.get("body") is not None and not isinstance(row["body"], str):
            raise ForgeError("Invalid Gitea record body", transient=False)
        record["body"] = row.get("body") or ""
        return record

    def _detail(self, path: str) -> dict:
        _, _, row = self._request("GET", path)
        if not isinstance(row, dict):
            raise ForgeError("Invalid Gitea detail", transient=False)
        return row

    def issue_author(self, repo: str, number: int) -> dict:
        return self._record(self._detail(self._repo_path(repo, "issues", self._number(number))))

    def comment_pages(self, repo: str, number: int) -> list[list[dict]]:
        return self._pages(self._repo_path(repo, "issues", self._number(number), "comments"))

    def bot_comment_pages(self, repo: str, number: int) -> list[list[dict]]:
        """Validated daemon evidence, separate from factual human comments."""
        pages = []
        markers: set[str] = set()
        for page in self.comment_pages(repo, number):
            bots = [row for row in page if self._is_bot(row["user"])]
            for row in bots:
                marker = self._comment_marker(row["body"])
                if marker is not None:
                    if marker in markers:
                        raise ForgeError("Ambiguous Gitea comment marker", transient=False)
                    markers.add(marker)
            pages.append(bots)
        return pages

    @staticmethod
    def _comment_marker(body: str) -> str | None:
        if not isinstance(body, str):
            raise ForgeError("Invalid Gitea comment body", transient=False)
        token = "<!-- nightshift:"
        if token not in body:
            return None
        # Only CRLF and a single trailing newline are normalized for matching.
        # Markdown/HTML context and indentation have no special meaning.
        lines = body.replace("\r\n", "\n").removesuffix("\n").split("\n")
        if (sum(line.startswith(token) for line in lines) != 1 or
                re.fullmatch(r"<!-- nightshift:(?:(?:draft|admit|publish|revise)(?:-error)?|approval):[^\s<>]+ -->",
                             lines[-1]) is None):
            raise ForgeError("Invalid Gitea comment marker", transient=False)
        return lines[-1]

    def _upsert_marked_comment(self, repo: str, number: int, body: str, marker: str) -> bool:
        matches = [row for page in self.bot_comment_pages(repo, number) for row in page
                   if _has_final_marker(row["body"], marker)]
        if not matches:
            return False
        if matches[0]["body"] != body:
            self.update_comment(repo, matches[0]["id"], body)
        return True

    def create_comment(self, repo: str, number: int, kind: Literal["issue", "pr"],
                       body: str) -> None:
        marker = self._comment_marker(body)
        path = self._repo_path(repo, "issues", self._number(number), "comments")
        reconcile = None
        if marker is not None:
            def reconcile() -> bool:
                return self._upsert_marked_comment(repo, number, body, marker)
            if reconcile():
                return
        self._request("POST", path, json_body={"body": body}, reconcile=reconcile)

    def update_comment(self, repo: str, comment_id: int, body: str) -> None:
        self._comment_marker(body)
        path = self._repo_path(repo, "issues", "comments", self._number(comment_id))
        row = self._detail(path)
        if type(row.get("id")) is not int or row["id"] != comment_id or not self._is_bot(row.get("user")):
            raise ForgeError("Gitea comment is not bot-owned", transient=False)
        self._request("PATCH", path, json_body={"body": body})

    def issue_for_draft(self, repo: str, number: int) -> dict:
        row = self.issue_author(repo, number)
        if row.get("state") not in ("open", "closed") or not isinstance(row.get("title"), str):
            raise ForgeError("Invalid Gitea issue facts", transient=False)
        self._label_map(row.get("labels"))
        comments = [{"id": comment["id"], "body": comment["body"],
                     "author": comment["user"],
                     "authorAssociation": comment["author_association"],
                     "createdAt": comment.get("created_at"),
                     "updatedAt": comment.get("updated_at")}
                    for page in self.comment_pages(repo, number) for comment in page
                    if not self._is_bot(comment["user"])]
        return {"state": row["state"].upper(), "title": row["title"], "body": row["body"],
                "labels": row["labels"], "author": row["user"],
                "authorAssociation": row["author_association"], "comments": comments}

    def repository(self, repo: str) -> dict:
        return self._detail(self._repo_path(repo))

    def collaborator_response(self, repo: str, login: str) -> ResponseText:
        raise ForgeError("Gitea membership operations are unsupported", transient=False)

    def organization_member_response(self, owner: str, login: str) -> ResponseText:
        raise ForgeError("Gitea membership operations are unsupported", transient=False)

    def open_blockers(self, repo: str, numbers: list[int]) -> list[int]:
        blocking = []
        for number in numbers:
            result = self._request("GET", self._repo_path(repo, "issues", self._number(number)),
                                   blocking_fallback=True)
            row = result[2] if result is not None else None
            if not isinstance(row, dict) or row.get("state") != "closed":
                blocking.append(number)
        return blocking

    def label_event_pages(self, repo: str, number: int) -> list[list[dict]]:
        pages = self._pages(self._repo_path(repo, "issues", self._number(number), "timeline"))
        events = []
        for page in pages:
            for row in page:
                if row.get("type") != "label":
                    continue
                label = row.get("label")
                # Gitea encodes label actions in body; removed is not action evidence.
                body = row.get("body")
                if (not isinstance(label, dict) or not isinstance(label.get("name"), str)
                        or not label["name"].strip() or self._login(row.get("user")) is None
                        or not isinstance(body, str) or body not in ("1", "")
                        or not isinstance(row.get("created_at"), str) or not row["created_at"]):
                    raise ForgeError("Invalid Gitea label event", transient=False)
                events.append({"id": row["id"], "event": "labeled" if body == "1" else "unlabeled",
                               "actor": {"login": self._login(row["user"])},
                               "author_association": self._association(row["user"]),
                               "label": label, "created_at": row["created_at"]})
        return [sorted(events, key=lambda event: event["id"])] if events else []

    def pr_feedback_records(self, repo: str, number: int) -> list[tuple[str, dict]]:
        conversation = [row for page in self.comment_pages(repo, number) for row in page]
        path = self._repo_path(repo, "pulls", self._number(number), "reviews")
        reviews = [self._record(row) for page in self._pages(path) for row in page]
        inline = [row for review in reviews
                  for page in self._pages(path + "/" + str(review["id"]) + "/comments")
                  for row in page]
        records = []
        seen: dict[tuple[str, int], dict] = {}
        for source, rows in (("conversation", conversation), ("review", reviews), ("inline", inline)):
            for row in rows:
                key = (source, row["id"])
                if key in seen:
                    if json.dumps(seen[key], sort_keys=True) != json.dumps(row, sort_keys=True):
                        raise ForgeError("Conflicting Gitea feedback", transient=False)
                    continue
                seen[key] = dict(row)
                if source == "inline":
                    row = self._record(row)
                if row["author_association"] != "OWNER":
                    continue
                if source == "review":
                    state = row.get("state")
                    if not isinstance(state, str) or not state:
                        raise ForgeError("Invalid Gitea review state", transient=False)
                    row["state"] = {"COMMENT": "COMMENTED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}.get(
                        state.upper(), state.upper())
                elif source == "inline":
                    if row.get("path") is not None and not isinstance(row["path"], str):
                        raise ForgeError("Invalid Gitea inline path", transient=False)
                    for field in ("line", "original_line", "position"):
                        value = row.get(field)
                        if value is not None and (type(value) is not int or value < 0):
                            raise ForgeError("Invalid Gitea inline location", transient=False)
                    # Gitea calls its inline location position; retain the wire field too.
                    if "line" not in row and "position" in row:
                        row["line"] = row["position"]
                records.append((source, row))
        return records

    def pr_feedback_items(self, repo: str, number: int) -> list[FeedbackItem]:
        items = []
        for source, row in self.pr_feedback_records(repo, number):
            suffix = ""
            if source == "review":
                state = row["state"].replace("_", " ").lower()
                suffix = f" ({state})" if state and state != "commented" else ""
            elif source == "inline":
                where = row.get("path") or ""
                line = row.get("line") or row.get("original_line")
                if where and line:
                    where = f"{where}:{line}"
                suffix = f" on `{where}`" if where else ""
            block = _block(row["user"], row["body"], suffix=suffix)
            if block and has_instructions([block]):
                items.append(FeedbackItem(f'{source}:{row["id"]}', block))
        return items

    def _read_comments(self, path: str, *, inline: bool = False) -> list[list[dict]]:
        """Validate a complete comment response; never follow continuation hints."""
        _, headers, rows = self._request("GET", path)
        if not isinstance(rows, list):
            raise ForgeError("Invalid Gitea comment listing", transient=False)
        count = headers.get("X-Total-Count")
        if count is not None:
            if not re.fullmatch(r"[0-9]+", count):
                raise ForgeError("Invalid Gitea comment total", transient=False)
            try:
                total = int(count)
            except ValueError:
                raise ForgeError("Invalid Gitea comment total", transient=False) from None
            if total != len(rows):
                raise ForgeError("Incomplete Gitea comment listing", transient=False)
        if headers.get("X-Next-Page") not in (None, "", "0") or any(re.search(
                r';\s*rel\s*=\s*(?:"[^\"]*\bnext\b[^\"]*"|next\b)',
                link, re.IGNORECASE) is not None for link in headers.get_all("Link", [])):
            raise ForgeError("Incomplete Gitea comment listing", transient=False)
        records = []
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("id")) is not int
                    or row["id"] <= 0 or row["id"] in seen):
                raise ForgeError("Invalid or duplicate Gitea comment ID", transient=False)
            seen.add(row["id"])
            record = self._record(row)
            # Inline aggregation compares raw identities before user projection.
            records.append(row if inline else record)
        return [records] if records else []

    def _pages(self, path: str, query=None, *, strict: bool = True,
               unpack: Callable[[object], list] | None = None) -> list[list[dict]]:
        """Read complete comments or paginate through an empty terminal page.

        Keep queries endpoint-local and treat response links only as evidence,
        never as request destinations. Paginated listings deduplicate identical IDs.
        """
        comments = re.fullmatch(
            r"/api/v1/repos/[^/?#]+/[^/?#]+/(?:issues/[1-9][0-9]*|"
            r"(?P<inline>pulls/[1-9][0-9]*/reviews/[1-9][0-9]*))/comments", path)
        if comments:
            return self._read_comments(path, inline=comments.group("inline") is not None)
        timeline = re.fullmatch(
            r"/api/v1/repos/[^/?#]+/[^/?#]+/issues/[1-9][0-9]*/timeline", path) is not None
        fixed_query = dict(query or {})
        pages: list[list[dict]] = []
        seen: dict[int, dict] = {}
        signatures: set[frozenset[int]] = set()
        total = None
        declared_end = False
        for page in range(1, 1001):
            result = self._request(
                "GET", path, query={**fixed_query, "page": page, "limit": 50},
                blocking_fallback=not strict)
            if result is None:
                # Only the transport boundary may suppress a failure. Discard
                # partial evidence; guard and malformed-page errors still escape.
                return []
            _, headers, rows = result
            if unpack is not None:
                rows = unpack(rows)
            if timeline and rows is None:
                rows = []
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
                # Timeline counts describe individual pages, not the whole listing.
                if not timeline:
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
    def _sha(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
            raise ForgeError("Invalid Gitea commit SHA", transient=False)
        return value

    def checks_for_sha(self, repo: str, sha: str) -> ResponseText:
        sha = self._sha(sha)
        buckets = {"success": "pass", "pending": "pending", "failure": "fail",
                   "error": "fail", "warning": "fail", "skipped": "skipping"}
        snapshot: tuple[str, int] | None = None

        def unpack(payload: object) -> list:
            nonlocal snapshot
            if not isinstance(payload, dict) or not {"sha", "state", "total_count", "statuses"} <= payload.keys():
                raise ForgeError("Invalid Gitea combined status", transient=False)
            state, count, rows = (payload.get(key) for key in ("state", "total_count", "statuses"))
            if (self._sha(payload.get("sha")).lower() != sha.lower()
                    or not isinstance(state, str) or state not in buckets
                    or type(count) is not int or count < 0):
                raise ForgeError("Invalid Gitea combined status", transient=False)
            if rows is None:
                if count != 0:
                    raise ForgeError("Invalid Gitea combined status", transient=False)
                return []  # Terminal envelopes do not describe the nonempty snapshot.
            if not isinstance(rows, list):
                raise ForgeError("Invalid Gitea combined status", transient=False)
            current = (state, count)
            if snapshot is not None and snapshot != current:
                raise ForgeError("Inconsistent Gitea combined status", transient=False)
            if not rows and snapshot is None and count:
                raise ForgeError("Incomplete Gitea combined status", transient=False)
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get("status"), str)
                        or row["status"] not in buckets):
                    raise ForgeError("Invalid Gitea status record", transient=False)
            if rows:
                snapshot = current
            return rows

        pages = self._pages(self._repo_path(repo, "commits", sha, "status"), unpack=unpack)
        if snapshot is None:
            return ResponseText("[]")
        state, count = snapshot
        if sum(len(page) for page in pages) != count:
            raise ForgeError("Incomplete Gitea combined status", transient=False)
        if state == "success" and count:
            # Skipped checks are neutral, not successful evidence. Inspect the
            # complete set so a later pending/failing row cannot become green.
            states = {row["status"] for page in pages for row in page}
            if "success" not in states or states - {"success", "skipped"}:
                raise ForgeError("Contradictory Gitea combined status", transient=False)
        checks = ([{"name": "Gitea combined status", "bucket": buckets[state], "state": state}]
                  if count else [])
        return ResponseText(json.dumps(checks))

    def pr_checks(self, repo: str, number: int) -> ResponseText:
        row = self._detail(self._repo_path(repo, "pulls", self._number(number)))
        head = row.get("head")
        if not isinstance(head, dict):
            raise ForgeError("Invalid Gitea PR head", transient=False)
        return self.checks_for_sha(repo, self._sha(head.get("sha")))

    def commit(self, repo: str, head: str) -> dict:
        head = self._sha(head)
        row = self._detail(self._repo_path(repo, "git", "commits", head))
        if self._sha(row.get("sha")).lower() != head.lower():
            raise ForgeError("Mismatching Gitea commit SHA", transient=False)
        parents = row.get("parents")
        if not isinstance(parents, list) or any(not isinstance(p, dict) for p in parents):
            raise ForgeError("Invalid Gitea commit parents", transient=False)
        normalized = [{"sha": self._sha(parent.get("sha"))} for parent in parents]
        metadata = row.get("commit", {})
        if not isinstance(metadata, dict):
            raise ForgeError("Invalid Gitea commit metadata", transient=False)
        for role in ("author", "committer"):
            person = metadata.get(role)
            if person is not None and (not isinstance(person, dict)
                    or (person.get("date") is not None and not isinstance(person["date"], str))):
                raise ForgeError("Invalid Gitea commit date", transient=False)
        return {"sha": row["sha"], "parents": normalized, "commit": metadata}

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

    def _read_issue_labels(self, path: str) -> dict[str, int]:
        # Gitea returns all issue labels and ignores pagination parameters.
        _, _, rows = self._request("GET", path)
        return self._label_map(rows)

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
        return set(self._read_issue_labels(path))

    def relabel_issue(self, repo: str, number: int, *, add: list[str], remove: list[str]) -> None:
        path = self._repo_path(repo, "issues", self._number(number), "labels")
        labels = self._resolve_labels(repo, [*add, *remove])
        if set(add) & set(remove):
            raise ForgeError("Overlapping Gitea label changes", transient=False)
        current = set(self._read_issue_labels(path).values())
        # Confirm every destination before removing any source, so interrupted
        # transitions retain a discoverable label (or a needs-human hold).
        for adding, names in ((True, add), (False, remove)):
            for name in sorted(set(names)):
                label_id = labels[name]
                if (label_id in current) == adding:
                    continue
                if adding:
                    self._request("POST", path, json_body={"labels": [label_id]})
                else:
                    self._request("DELETE", path + "/" + str(label_id))
                current = set(self._read_issue_labels(path).values())
                if (label_id in current) != adding:
                    raise ForgeError("Gitea label write verification failed", transient=False)

    def relabel_pr(self, repo: str, target: str | int, *, add: list[str], remove: list[str]) -> None:
        self.relabel_issue(repo, self._pr_number(repo, target), add=add, remove=remove)

    def latest_pr_number(self, repo: str, branch: str, *, strict: bool = True) -> int | None:
        if not isinstance(branch, str) or not branch:
            raise ForgeError("Invalid Gitea branch", transient=False)
        pages = self._pages(self._repo_path(repo, "pulls"), {"state": "all"}, strict=strict)
        matches = []
        for page in pages:
            for row in page:
                self._number(row.get("number"))
                head = row.get("head")
                if (not isinstance(head, dict) or not isinstance(head.get("ref"), str)
                        or not head["ref"]):
                    raise ForgeError("Invalid Gitea PR head", transient=False)
                if head["ref"] == branch:
                    matches.append(row["number"])
        return max(matches, default=None)

    def _pr_url_number(self, repo: str, target: str) -> int:
        """Accept only an unambiguous web PR URL at our configured repository."""
        self._repo_path(repo)  # Validate before building the expected web path.
        try:
            parts, base = urlsplit(target), self._base_url
            origin = lambda url: (url.scheme.lower(), url.hostname,
                                  url.port or (443 if url.scheme == "https" else 80))
            prefix = base.path + "/" + "/".join(quote(part, safe="") for part in repo.split("/"))
            match = re.fullmatch(re.escape(prefix) + r"/pulls/([1-9][0-9]*)", parts.path)
            if (any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 or c == "\\"
                    for c in target) or "?" in target or "#" in target
                    or parts.username is not None or parts.password is not None
                    or parts.netloc.endswith(":") or parts.port == 0
                    or origin(parts) != origin(base) or match is None):
                raise ValueError
            return int(match[1])
        except ValueError:
            raise ForgeError("Invalid Gitea PR URL", transient=False) from None

    def _pr_number(self, repo: str, target: str | int) -> int:
        if type(target) is int:
            self._number(target)
            return target
        if not isinstance(target, str) or not target:
            raise ForgeError("Invalid Gitea PR target", transient=False)
        if re.fullmatch(r"[0-9]+", target):
            try:
                number = int(target)
            except ValueError:
                raise ForgeError("Invalid Gitea PR number", transient=False) from None
            self._number(number)
            return number
        if ":" in target or target.startswith("/"):
            return self._pr_url_number(repo, target)
        # Branches are compared as exact refs, never used as URL destinations.
        # Git permits hashes, Unicode whitespace and C1 characters in refs;
        # retain the stricter URL validation only in _pr_url_number.
        if any(ord(c) <= 32 or ord(c) == 127 or c in "\\?" for c in target):
            raise ForgeError("Invalid Gitea PR target", transient=False)
        found = self.latest_pr_number(repo, target)
        if found is None:
            raise ForgeError("Gitea PR not found", transient=False)
        return found

    def _pr_view(self, repo: str, number: int) -> dict:
        row = self._detail(self._repo_path(repo, "pulls", self._number(number)))
        head, base = row.get("head"), row.get("base")
        if (row.get("state") not in ("open", "closed") or type(row.get("merged")) is not bool
                or not isinstance(row.get("title"), str)
                or (row.get("body") is not None and not isinstance(row["body"], str))
                or not isinstance(row.get("html_url"), str) or not row["html_url"]
                or not isinstance(head, dict) or not isinstance(base, dict)):
            raise ForgeError("Invalid Gitea PR facts", transient=False)
        for ref in (head, base):
            identity = ref.get("repo")
            if (not isinstance(identity, dict) or type(identity.get("id")) is not int
                    or identity["id"] <= 0):
                raise ForgeError("Missing Gitea PR repository identity", transient=False)
        if (not isinstance(head.get("ref"), str) or not head["ref"]
                or not isinstance(head.get("sha"), str)
                or not re.fullmatch(r"[0-9a-fA-F]{40}", head["sha"])):
            raise ForgeError("Invalid Gitea PR head", transient=False)
        self._label_map(row.get("labels"))
        return {"state": "MERGED" if row["merged"] else row["state"].upper(),
                "title": row["title"], "body": row.get("body") or "", "labels": row["labels"],
                "headRefName": head["ref"], "headRefOid": head["sha"],
                "isCrossRepository": head["repo"]["id"] != base["repo"]["id"],
                "url": row["html_url"], "isDraft": row["title"].startswith(self._wip_prefix)}

    def pr_for_recovery(self, repo: str, number: int) -> dict:
        return self._pr_view(repo, number)

    def pr_for_admission(self, repo: str, number: int) -> dict:
        return self._pr_view(repo, number)

    def pr_for_revision(self, repo: str, number: int) -> dict:
        return self._pr_view(repo, number)

    def pr_for_publication(self, repo: str, number: int) -> dict:
        return self._pr_view(repo, number)

    def pr_for_readiness(self, repo: str, number: int) -> dict:
        return self._pr_view(repo, number)

    def pr_state_url(self, repo: str, target: str | int) -> dict:
        view = self._pr_view(repo, self._pr_number(repo, target))
        return {key: view[key] for key in ("state", "url")}

    def create_draft_pr(self, repo: str, branch: str, base: str, title: str, body: str) -> ResponseText:
        if (not all(isinstance(value, str) for value in (branch, base, title, body))
                or not branch or not base):
            raise ForgeError("Invalid Gitea draft input", transient=False)
        if not title.startswith(self._wip_prefix):
            title = self._wip_prefix + title
        _, _, row = self._request("POST", self._repo_path(repo, "pulls"),
                                  json_body={"title": title, "body": body, "head": branch, "base": base})
        if not isinstance(row, dict) or not isinstance(row.get("html_url"), str) or not row["html_url"]:
            raise ForgeError("Invalid Gitea created PR URL", transient=False)
        return ResponseText(row["html_url"])
