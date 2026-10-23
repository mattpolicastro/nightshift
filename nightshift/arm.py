"""Issue-body lint and bounded GitHub reads for issue arming."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from nightshift.queue import blockers
from specloop import critic, propose

REQUIRED_SECTIONS = ("Scope", "What to do", "Definition of done", "What CI cannot prove")
_SECTION_NAMES = {name.casefold(): name for name in REQUIRED_SECTIONS}
_MARKER = re.compile(r"^[ \t]*\*\*([^*\n]+):\*\*(.*)$")
_HEADING = re.compile(r"^[ \t]*#{1,6}(?:[ \t]|$)")
_SETEXT = re.compile(r"^[ \t]*(?:=+|-+)[ \t]*$")
_BLOCK_START = re.compile(r"^[ \t]*(?:>|(?:[-+*]|\d+[.)])(?:[ \t]|$))")
_RULE = re.compile(r"^[ \t]*(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
_NAMED_TEST = re.compile(
    r"`(?:[^\s`]+::)?test_[A-Za-z0-9_]+`"
    r"|\"test_[A-Za-z0-9_]+\"|'test_[A-Za-z0-9_]+'"
    r'''|\btest[ \t]+(?:"[^"\n]*[^"\s][^"\n]*"|'[^'\n]*[^'\s][^'\n]*')'''
)
_DISCOURAGED = re.compile(
    r"\b(?:no\s+existing\s+tests?\s+(?:(?:is|are|to\s+be)\s+)?"
    r"(?:modified|changed|edited)"
    r"|(?:do\s+not|don't)\s+(?:modify|change|edit)\s+existing\s+tests"
    r"|existing\s+tests\s+must\s+remain\s+unchanged)\b",
    re.IGNORECASE,
)
_MANUAL = re.compile(r"\b(?:manually|by\s+hand|visually)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    code: str
    issue: int
    message: str
    suggestion: str
    line: int | None = None
    path: str | None = None
    related_issue: int | None = None

    def sort_key(self) -> tuple[str, int, str, int, str]:
        return (self.code, self.line or 0, self.path or "",
                self.related_issue or 0, self.message)


@dataclass(frozen=True)
class Section:
    name: str
    line: int
    text: str


@dataclass(frozen=True)
class Issue:
    number: int
    body: str
    state: str
    state_reason: str | None
    labels: frozenset[str]


class IssueLookupError(Exception):
    """A failed or malformed GitHub issue read, safe to show in findings."""


def read_issue(repository: str, number: int, *, runner=None) -> Issue:
    """Read one issue with a subprocess-compatible boundary and a 30s bound.

    Repository may include a GitHub hostname. Never include raw command output
    in errors; a failed lookup must not hide independent lint findings.
    """
    runner = runner or subprocess.run
    argv = ["gh", "issue", "view", str(number), "--repo", repository,
            "--json", "number,body,state,stateReason,labels"]
    try:
        response = runner(argv, capture_output=True, text=True, check=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise IssueLookupError(f"Issue #{number} lookup timed out after 30 seconds.") from None
    except (OSError, subprocess.SubprocessError):
        raise IssueLookupError(f"Issue #{number} lookup failed.") from None
    if response.returncode:
        raise IssueLookupError(f"Issue #{number} lookup failed.")
    try:
        data = json.loads(response.stdout)
    except (ValueError, TypeError):
        raise IssueLookupError(f"Issue #{number} lookup returned malformed data.") from None
    return _parse_issue(data, number)


def _parse_issue(data: object, number: int) -> Issue:
    try:
        if not isinstance(data, dict):
            raise ValueError
        body, state, reason = data.get("body"), data.get("state"), data.get("stateReason")
        labels = data.get("labels")
        if (type(data.get("number")) is not int or data["number"] != number
                or not isinstance(body, str)
                or (state is not None and not isinstance(state, str))
                or (reason is not None and not isinstance(reason, str))
                or not isinstance(labels, list)
                or any(not isinstance(label, dict) or not isinstance(label.get("name"), str)
                       for label in labels)):
            raise ValueError
        return Issue(number, body, (state or "").upper(),
                     reason.upper() if reason is not None else None,
                     frozenset(label["name"] for label in labels))
    except (ValueError, TypeError):
        raise IssueLookupError(f"Issue #{number} lookup returned malformed data.") from None


class IssueCache:
    """Invocation-local snapshot, including failures, keyed by repository and number."""

    def __init__(self, *, runner=None):
        self.runner = runner
        self._issues: dict[tuple[str, int], Issue | IssueLookupError] = {}
        self._armed: dict[str, tuple[list[Issue], list[str]]] = {}
        self._prs: dict[str, ConcurrentContext] = {}
        self._reachability: dict[tuple[str, int, int], tuple[bool, tuple[tuple[int, str], ...]]] = {}

    def reaches(self, repository: str, source: int, target: int
                ) -> tuple[bool, tuple[tuple[int, str], ...]]:
        """Follow directed blockers lazily; cache proofs and failed lookup notes.

        Each search owns its visited set: a cycle is not a cached negative proof
        for another search. Iteration also avoids recursion limits on long chains.
        """
        key = (repository, source, target)
        if key in self._reachability:
            return self._reachability[key]
        pending, visited = [source], set()
        failures: dict[int, str] = {}
        found = False
        while pending:
            number = pending.pop()
            if number in visited:
                continue
            visited.add(number)
            try:
                dependencies = blockers(self.get(repository, number).body)
            except IssueLookupError as error:
                failures[number] = str(error)
                continue
            if target in dependencies:
                found = True
                break
            pending.extend(reversed(dependencies))
        result = (found, tuple(sorted(failures.items())))
        self._reachability[key] = result
        return result

    def open_prs(self, repository: str) -> ConcurrentContext:
        if repository not in self._prs:
            self._prs[repository] = read_pr_context(repository, runner=self.runner)
        return self._prs[repository]

    def armed(self, repository: str) -> tuple[list[Issue], list[str]]:
        """Cache all open intake issues, keeping earlier pages on a failed read."""
        if repository in self._armed:
            return self._armed[repository]
        issues: dict[int, Issue] = {}
        notes: list[str] = []
        parts = repository.split("/")
        host, repo = (parts[0], "/".join(parts[1:])) if len(parts) == 3 else ("github.com", repository)
        page = 1
        while True:
            argv = ["gh", "api", "--hostname", host,
                    f"repos/{repo}/issues?state=open&labels=spec%3Adraft&per_page=100&page={page}"]
            try:
                response = (self.runner or subprocess.run)(
                    argv, capture_output=True, text=True, check=True, timeout=30)
                if response.returncode:
                    raise ValueError
                rows = json.loads(response.stdout)
                if not isinstance(rows, list):
                    raise ValueError
            except (OSError, subprocess.SubprocessError, ValueError, TypeError):
                notes.append(f"Armed issue page {page} could not be read.")
                break
            for row in rows:
                if isinstance(row, dict) and "pull_request" in row:
                    continue
                try:
                    if not isinstance(row, dict) or type(row.get("number")) is not int or row["number"] <= 0:
                        raise ValueError
                    # REST represents an issue without a description as null.
                    data = {**row, "stateReason": row.get("state_reason")}
                    if "body" in data and data["body"] is None:
                        data["body"] = ""
                    issue = _parse_issue(data, row["number"])
                    if issue.state != "OPEN" or "spec:draft" not in issue.labels:
                        continue
                    key = (repository, issue.number)
                    self._issues.setdefault(key, issue)
                    issues[issue.number] = self.get(*key)
                except (ValueError, IssueLookupError):
                    notes.append(f"Armed issue page {page} contains an unreadable issue.")
            if len(rows) < 100:
                break
            page += 1
        result = (list(issues.values()), notes)
        self._armed[repository] = result
        return result

    def get(self, repository: str, number: int) -> Issue:
        key = (repository, number)
        if key not in self._issues:
            try:
                self._issues[key] = read_issue(repository, number, runner=self.runner)
            except IssueLookupError as error:
                self._issues[key] = error
        result = self._issues[key]
        if isinstance(result, IssueLookupError):
            raise result
        return result


@dataclass(frozen=True)
class PathEvidence:
    path: str
    issue: int | None
    source: str


@dataclass
class ConcurrentContext:
    evidence: list[PathEvidence]
    notes: list[str]


def scope_paths(body: str, root: Path) -> set[str]:
    return propose.paths("\n".join(section.text for section in parse_sections(body)
                                   if section.name == "Scope"), root=root)


def front_door_issue(repository: str, change: str) -> int | None:
    """Use the producer's stable change identity, independent of current titles."""
    basename = re.escape(repository.rsplit("/", 1)[-1])
    match = re.fullmatch(rf"{basename}-issue-([1-9][0-9]*)-[a-z0-9-]{{1,40}}", change)
    return int(match[1]) if match else None


def read_pr_context(repository: str, *, runner=None) -> ConcurrentContext:
    """Join the critic's filenames/URLs to front-door source issue identities.

    The two reads are a best-effort snapshot: an absent metadata row is unknown,
    not evidence that a PR is unrelated. Keep earlier pages on lookup failure.
    """
    files, helper_notes = critic.open_pr_files(repository, "", runner=runner)
    notes = list(helper_notes)
    identities: dict[int, int | None] = {}
    parts = repository.split("/")
    host, repo = (parts[0], "/".join(parts[1:])) if len(parts) == 3 else ("github.com", repository)
    page = 1
    while True:
        argv = ["gh", "api", "--hostname", host,
                f"repos/{repo}/pulls?state=open&per_page=100&page={page}"]
        try:
            response = (runner or subprocess.run)(
                argv, capture_output=True, text=True, check=True, timeout=30)
            if response.returncode:
                raise ValueError
            rows = json.loads(response.stdout)
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            notes.append(f"Open PR metadata page {page} could not be read.")
            break
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("number")) is not int
                    or row["number"] <= 0):
                notes.append(f"Open PR metadata page {page} contains an unreadable PR.")
                continue
            number = row["number"]
            head = row.get("head")
            if (not isinstance(head, dict) or not isinstance(head.get("ref"), str)
                    or not isinstance(head.get("repo"), dict)
                    or not isinstance(head["repo"].get("full_name"), str)
                    or not head["repo"]["full_name"]):
                notes.append(f"Open PR #{number} metadata has an unknown head repository or branch.")
                continue
            source_issue = None
            if head["repo"]["full_name"].casefold() == repo.casefold() and head["ref"].startswith("spec/"):
                source_issue = front_door_issue(repository, head["ref"][5:])
            identities[number] = source_issue
        if len(rows) < 100:
            break
        page += 1
    evidence = set()
    for path, labels in files.items():
        for label in labels:
            match = re.match(r"#([1-9][0-9]*) ", label)
            if not match or int(match[1]) not in identities:
                notes.append(f"Open PR metadata unavailable for {label}.")
                continue
            source_issue = identities[int(match[1])]
            if source_issue is not None:
                evidence.add(PathEvidence(path, source_issue, f"PR {label}"))
    return ConcurrentContext(sorted(evidence, key=lambda e: (e.path, e.issue or 0, e.source)),
                             list(dict.fromkeys(notes)))


def collect_concurrent(repository: str, root: Path, targets: list[Issue],
                       cache: IssueCache) -> ConcurrentContext:
    """Collect one shared snapshot; candidates participate even before labelling."""
    armed, notes = cache.armed(repository)
    issues = {issue.number: issue for issue in [*armed, *targets]}
    evidence = [PathEvidence(path, issue.number, f"issue #{issue.number}")
                for issue in issues.values() for path in sorted(scope_paths(issue.body, root))]
    for path, changes in propose.in_flight(root, "").items():
        for change in changes:
            evidence.append(PathEvidence(path, front_door_issue(repository, change), f"run {change}"))
    prs = cache.open_prs(repository)
    return ConcurrentContext([*evidence, *prs.evidence], list(dict.fromkeys([*notes, *prs.notes])))


def context_findings(issue: int, context: ConcurrentContext) -> list[Finding]:
    return sorted((Finding("incomplete-context", issue, note,
                           "Restore concurrent context access, then rerun.")
                   for note in dict.fromkeys(context.notes)), key=Finding.sort_key)


def concurrent_overlaps(issue: Issue, root: Path, context: ConcurrentContext) -> list[PathEvidence]:
    """Exact token intersections, excluding all evidence attributed to self."""
    paths = scope_paths(issue.body, root)
    return sorted((entry for entry in context.evidence
                   if entry.issue != issue.number and entry.path in paths),
                  key=lambda entry: (entry.path, entry.issue or 0, entry.source))


def unattributed_run_findings(issue: Issue, root: Path, context: ConcurrentContext) -> list[Finding]:
    """An unknown run cannot receive a dependency exemption or a guessed blocker."""
    return sorted((Finding(
        "overlap", issue.number, f"{entry.path} overlaps {entry.source} (source issue unknown).",
        "Identify this run's source issue before adding a dependency.", path=entry.path,
    ) for entry in concurrent_overlaps(issue, root, context) if entry.issue is None),
        key=Finding.sort_key)


def overlap_findings(issue: Issue, root: Path, context: ConcurrentContext,
                     repository: str, cache: IssueCache) -> list[Finding]:
    """Combine evidence by path/issue, exempting only proven directed chains."""
    findings = unattributed_run_findings(issue, root, context)
    grouped: dict[tuple[str, int], set[str]] = {}
    for entry in concurrent_overlaps(issue, root, context):
        if entry.issue is not None:
            grouped.setdefault((entry.path, entry.issue), set()).add(entry.source)
    failures: dict[int, str] = {}
    for (path, other), sources in sorted(grouped.items()):
        chained, notes = cache.reaches(repository, issue.number, other)
        failures.update(notes)
        if not chained:
            chained, notes = cache.reaches(repository, other, issue.number)
            failures.update(notes)
        if not chained:
            findings.append(Finding(
                "overlap", issue.number,
                f"{path} overlaps issue #{other}: {'; '.join(sorted(sources))}.",
                f"Add blocked-by: #{other} to sequence this work.",
                path=path, related_issue=other,
            ))
    findings.extend(Finding(
        "incomplete-context", issue.number, message,
        f"Restore access to issue #{number}, then rerun.", related_issue=number,
    ) for number, message in failures.items())
    return sorted(findings, key=Finding.sort_key)


def check_candidates(repository: str, numbers: list[int], root: Path,
                     cache: IssueCache) -> dict[int, list[Finding]]:
    """Evaluate the entire batch before callers decide whether to write labels.

    Preserve argument order, read each target once, and include unlabelled targets
    in the shared snapshot. This phase never mutates GitHub or local state.
    """
    results: dict[int, list[Finding]] = {}
    targets = []
    for number in dict.fromkeys(numbers):
        results[number] = lint_issue(repository, number, cache)
        try:
            targets.append(cache.get(repository, number))
        except IssueLookupError:
            pass  # lint_issue already retained this failure.
    context = collect_concurrent(repository, root, targets, cache)
    for number in results:
        results[number].extend(context_findings(number, context))
    for issue in targets:
        results[issue.number].extend(overlap_findings(issue, root, context, repository, cache))
    for number, findings in results.items():
        # The same failed read may arise in direct lint and graph traversal.
        unique = {finding.sort_key(): finding for finding in findings}
        results[number] = sorted(unique.values(), key=Finding.sort_key)
    return results


def lint_dependencies(issue: int, body: str, repository: str, cache: IssueCache) -> list[Finding]:
    """Check only direct blockers; open dependencies are eligible for arming."""
    findings = []
    for number in blockers(body):
        try:
            blocker = cache.get(repository, number)
        except IssueLookupError as error:
            message = str(error)
        else:
            if blocker.state == "OPEN":
                continue
            if blocker.state == "CLOSED" and blocker.state_reason == "COMPLETED":
                continue
            if blocker.state == "CLOSED" and blocker.state_reason == "NOT_PLANNED":
                findings.append(Finding(
                    "dependency-completion", issue, f"Blocker #{number} is closed as NOT_PLANNED.",
                    f"Resolve the unfinished work or correct blocked-by: #{number}.",
                    related_issue=number,
                ))
                continue
            message = f"Blocker #{number} has unknown state or completion reason."
        findings.append(Finding(
            "incomplete-context", issue, message,
            f"Verify blocker #{number}'s state and completion reason, then rerun.",
            related_issue=number,
        ))
    return sorted(findings, key=Finding.sort_key)


def lint_issue(repository: str, number: int, cache: IssueCache) -> list[Finding]:
    """Accumulate body and direct-dependency findings from the shared snapshot."""
    try:
        issue = cache.get(repository, number)
    except IssueLookupError as error:
        return [Finding("incomplete-context", number, str(error),
                        f"Restore access to issue #{number}, then rerun.")]
    return sorted(lint_body(number, issue.body)
                  + lint_dependencies(number, issue.body, repository, cache),
                  key=Finding.sort_key)


def parse_sections(body: str) -> list[Section]:
    """Keep inline content and source lines; fenced markers are ordinary text.

    Unknown bold markers and Markdown headings end a section. Repeated required
    sections remain separate so their content and locations are not discarded.
    """
    sections: list[Section] = []
    name: str | None = None
    start = 0
    content: list[str] = []
    fence = ""
    paragraph_start: int | None = None
    block_continuation = False
    for number, line in enumerate(body.splitlines(), 1):
        delimiter = _FENCE.match(line)
        marker = None
        heading = False
        if fence:
            if (delimiter and delimiter[1][0] == fence[0]
                    and len(delimiter[1]) >= len(fence) and not delimiter[2].strip()):
                fence = ""
        elif delimiter and (delimiter[1][0] == "~" or "`" not in delimiter[2]):
            fence = delimiter[1]
        else:
            marker = _MARKER.match(line)
            setext = bool(_SETEXT.fullmatch(line) and paragraph_start is not None)
            heading = bool(_HEADING.match(line)) or setext
            if setext and content and paragraph_start is not None:
                # A Setext title spans the entire preceding eligible paragraph.
                del content[paragraph_start - start:]
        if marker or heading:
            if name is not None:
                sections.append(Section(name, start, "\n".join(content)))
            name = _SECTION_NAMES.get(marker[1].casefold()) if marker else None
            start = number
            content = [marker[2]] if marker and name else []
        elif name is not None:
            content.append(line)
        # Wrapped list/quote paragraphs (including lazy, unindented lines) do
        # not become setext titles. Blank lines or boundaries end that context.
        block_continuation = bool(line.strip() and not (
            fence or delimiter or marker or heading or _RULE.fullmatch(line)
            or _SETEXT.fullmatch(line)
        ) and (block_continuation or _BLOCK_START.match(line)))
        if line.strip() and not (
            fence or delimiter or marker or heading or block_continuation
            or _RULE.fullmatch(line) or _SETEXT.fullmatch(line)
        ):
            if paragraph_start is None:
                paragraph_start = number
        else:
            paragraph_start = None
    if name is not None:
        sections.append(Section(name, start, "\n".join(content)))
    return sections


def lint_body(issue: int, body: str) -> list[Finding]:
    """Return every wording finding without IO or repository test lookup.

    Discouraged wording is deliberately lexical, including quoted examples.
    """
    sections = parse_sections(body)
    present = {section.name for section in sections}
    findings = [
        Finding("missing-section", issue, f"Missing **{name}:** section.",
                f"Add **{name}:**.")
        for name in REQUIRED_SECTIONS if name not in present
    ]
    done = [section for section in sections if section.name == "Definition of done"]
    if done and not any(_NAMED_TEST.search(section.text) for section in done):
        findings.append(Finding(
            "named-test", issue, "Definition of done needs a named test.",
            "Name a test, for example `test_expected_behavior`.", line=done[0].line,
        ))
    for match in _DISCOURAGED.finditer(body):
        findings.append(Finding(
            "discouraged-phrase", issue, f"Discouraged wording: {match[0]}",
            "existing assertions are preserved", line=body.count("\n", 0, match.start()) + 1,
        ))
    for section in done:
        for match in _MANUAL.finditer(section.text):
            findings.append(Finding(
                "manual-check", issue, f"Human check in Definition of done: {match[0]}",
                "Move this check under **What CI cannot prove:**.",
                line=section.line + section.text.count("\n", 0, match.start()),
            ))
    return sorted(findings, key=Finding.sort_key)
