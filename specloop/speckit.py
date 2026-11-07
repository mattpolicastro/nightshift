"""Standalone spec-kit records, recognized literally from physical lines."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import tasks
from .openspec import SpecError, slug

TITLE = re.compile(r"^#\s+(.+?)\s*$")
STATUS = re.compile(r"^\*\*Status\*\*:\s*(.*?)\s*$")
SECTION = re.compile(r"^## (User Scenarios & Testing|Requirements|Success Criteria)(?: \*\(mandatory\)\*)?$")
HEADING = re.compile(r"^(#+)[ \t]+")
STORY = re.compile(r"^### User Story (\d+) - (.+)$")
PRIORITY = re.compile(r"^(.+?) \(Priority: P(\d+)\)$")
SCENARIO = re.compile(r"^\d+\.\s+\*\*AS(\d+)\*\*\s+(.*)$")
CLARIFICATION = re.compile(r"\[NEEDS CLARIFICATION(?:\s*:[^\]]*)?\]")
PHASE = re.compile(r"^##\s+Phase\s+(\d+):\s+(.+?)\s*$")
TASK_LABEL = re.compile(r"^(T\d{3,}(?:\.\d+)*)\s+(?:\[P\]\s+)?(?:\[US(\d+)\]\s+)?(.*)$")


@dataclass(frozen=True)
class SourceLine:
    text: str
    line: int  # All spec locations are one-based.


@dataclass(frozen=True)
class Scenario:
    number: int
    text: str
    line: int


@dataclass
class Story:
    number: int
    title: str
    priority: int | None
    line: int
    scenarios: list[Scenario] = field(default_factory=list)


@dataclass
class Spec:
    title: str = ""
    status: str | None = None
    title_line: int | None = None
    status_line: int | None = None
    sections: list[SourceLine] = field(default_factory=list)
    stories: list[Story] = field(default_factory=list)
    functional_requirements: list[SourceLine] = field(default_factory=list)
    success_criteria: list[SourceLine] = field(default_factory=list)
    clarifications: list[SourceLine] = field(default_factory=list)


def feature_dir(repo: Path, change: str) -> Path:
    return repo / "specs" / change


def parse_spec(text: str) -> Spec:
    spec = Spec()
    story: Story | None = None
    acceptance = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip(" \t")
        if (match := TITLE.match(line)) and spec.title_line is None:
            spec.title, spec.title_line = match[1], number
        if match := STATUS.match(line):
            spec.status, spec.status_line = match[1], number
        if match := SECTION.match(line):
            spec.sections.append(SourceLine(match[1], number))
        for pattern, records in ((r"\bFR-\d+\b", spec.functional_requirements),
                                 (r"\bSC-\d+\b", spec.success_criteria)):
            if re.search(pattern, line):
                records.append(SourceLine(raw, number))
        spec.clarifications.extend(SourceLine(m[0], number) for m in CLARIFICATION.finditer(line))

        if match := HEADING.match(line):
            acceptance = False
            if len(match[1]) <= 3:
                story = None
        if match := STORY.match(line):
            title, priority = match[2], None
            if suffix := PRIORITY.fullmatch(title):
                title, priority = suffix[1], int(suffix[2])
            story = Story(int(match[1]), title, priority, number)
            spec.stories.append(story)
        if story is not None:
            if line == "**Acceptance Scenarios**:":
                acceptance = True
            elif acceptance and (match := SCENARIO.match(line)) and match[2].strip():
                story.scenarios.append(Scenario(int(match[1]), match[2], number))
    spec.stories.sort(key=lambda s: (s.priority is None, s.priority or 0))
    return spec


def scenario_ids(change: str, spec: Spec) -> dict[str, str]:
    if not change or slug(change) != change:
        raise SpecError(f"noncanonical change name: {change!r}")
    found: dict[str, str] = {}
    for story in spec.stories:
        for scenario in story.scenarios:
            sid = f"{change}/us{story.number}/as{scenario.number}"
            if tasks.SCENARIO_ID.fullmatch(sid) is None:
                raise SpecError(f"invalid scenario id: {sid}")
            if sid in found:
                raise SpecError(f"duplicate scenario id: {sid}")
            found[sid] = f"US{story.number} — AS{scenario.number}\n{scenario.text}"
    return found


def parse_tasks(text: str) -> list[tasks.Task]:
    """Read labelled checkboxes in literal phases, retaining flags in task text."""
    found: list[tasks.Task] = []
    section: int | None = None
    title = ""
    for number, line in enumerate(text.splitlines()):
        if phase := PHASE.match(line):
            section, title = int(phase[1]), phase[2]
            continue
        if (heading := HEADING.match(line)) and len(heading[1]) <= 2:
            section = None
        if section is None or (checkbox := tasks.TASK.match(line)) is None:
            continue
        body = checkbox[3]
        if label := TASK_LABEL.match(body):
            # The final capture omits flags; retain them for identity and ownership.
            rest = body[label.end(1):].lstrip()
            found.append(tasks.Task(section, title, label[1], rest, checkbox[2] != " ", number))
    return found
