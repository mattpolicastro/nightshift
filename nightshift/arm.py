"""Issue-body lint and bounded GitHub reads for issue arming."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from nightshift.queue import blockers

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
