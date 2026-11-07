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
STORY_PHASE = re.compile(r"^User Story (\d+)\b")
CHECKBOX = re.compile(r"^[-*+]\s+\[([^\]]*)\]")


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
            sid = _scenario_id(change, story, scenario)
            if sid in found:
                raise SpecError(f"duplicate scenario id: {sid}")
            found[sid] = f"US{story.number} — AS{scenario.number}\n{scenario.text}"
    return found


def _scenario_id(change: str, story: Story, scenario: Scenario) -> str:
    sid = f"{change}/us{story.number}/as{scenario.number}"
    if tasks.SCENARIO_ID.fullmatch(sid) is None:
        raise SpecError(f"invalid scenario id: {sid}")
    return sid


@dataclass
class _Phase:
    number: int
    title: str
    line: int
    tasks: list[tasks.Task] = field(default_factory=list)


def parse_tasks(text: str) -> list[tasks.Task]:
    """Read labelled checkboxes in literal phases, retaining flags in task text."""
    return [task for phase in _parse_phases(text) for task in phase.tasks]


def _parse_phases(text: str) -> list[_Phase]:
    found: list[_Phase] = []
    current: _Phase | None = None
    for number, line in enumerate(text.splitlines()):
        if phase := PHASE.match(line):
            current = _Phase(int(phase[1]), phase[2], number + 1)
            found.append(current)
            continue
        if (heading := HEADING.match(line)) and len(heading[1]) <= 2:
            current = None
        if current is None or (checkbox := tasks.TASK.match(line)) is None:
            continue
        body = checkbox[3]
        if label := TASK_LABEL.match(body):
            # The final capture omits flags; retain them for identity and ownership.
            rest = body[label.end(1):].lstrip()
            current.tasks.append(tasks.Task(current.number, current.title, label[1], rest,
                                            checkbox[2] != " ", number))
    return found


def validate(repo: Path, change: str) -> list[str]:
    """Report independent readiness failures at deterministic physical locations."""
    root = feature_dir(repo, change)
    errors: list[tuple[str, int, str]] = []

    def report(name: str, line: int, message: str) -> None:
        errors.append(((root / name).relative_to(repo).as_posix(), line, message))

    inputs: dict[str, str] = {}
    for path in [root / name for name in ("spec.md", "tasks.md", "plan.md")] + sorted(
        (root / "checklists").glob("*.md")
    ):
        name = path.relative_to(root).as_posix()
        try:
            inputs[name] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            report(name, 1, "missing required file")
        except UnicodeError:
            report(name, 1, "cannot decode UTF-8 file")
        except OSError:
            report(name, 1, "cannot read file")

    known: dict[str, int] = {}
    if "spec.md" in inputs:
        spec = parse_spec(inputs["spec.md"])
        sections = {section.text for section in spec.sections}
        for required in ("User Scenarios & Testing", "Requirements", "Success Criteria"):
            if required not in sections:
                report("spec.md", 1, f"missing section: {required}")
        has_scenarios = any(story.scenarios for story in spec.stories)
        if not has_scenarios:
            report("spec.md", 1, "no user story with a labelled acceptance scenario")
        canonical = bool(change) and slug(change) == change
        if not canonical:
            report("spec.md", 1, f"noncanonical change name: {change!r}")
        # Priority order must not change which physical occurrence is the duplicate.
        for story in sorted(spec.stories, key=lambda s: s.line):
            if story.priority is None:
                report("spec.md", story.line, f"US{story.number}: missing priority")
            if has_scenarios and not story.scenarios:
                report("spec.md", story.line, f"US{story.number}: no labelled acceptance scenario")
            for scenario in story.scenarios:
                try:
                    sid = _scenario_id(change, story, scenario)
                except SpecError as error:
                    report("spec.md", scenario.line, str(error))
                    continue
                if sid in known:
                    report("spec.md", scenario.line, f"duplicate scenario id: {sid}")
                else:
                    known[sid] = scenario.line
        for marker in spec.clarifications:
            report("spec.md", marker.line, f"unresolved clarification: {marker.text}")

    if "tasks.md" in inputs:
        phases = _parse_phases(inputs["tasks.md"])
        missing: list[tuple[int, int]] = []
        has_owner = False
        for phase in phases:
            if owner := STORY_PHASE.match(phase.title):
                number = int(owner[1])
                matching = any(
                    (label := TASK_LABEL.match(task.title())) is not None
                    and label[2] is not None and int(label[2]) == number
                    for task in phase.tasks
                )
                has_owner |= matching
                if not matching:
                    missing.append((phase.line, number))
        if not has_owner:
            report("tasks.md", 1, "no user-story task")
        else:
            for line, number in missing:
                report("tasks.md", line, f"user-story phase has no matching [US{number}] task")
        if "spec.md" in inputs:
            covered: set[str] = set()
            for phase in phases:
                for task in phase.tasks:
                    for sid in dict.fromkeys(task.scenarios):
                        covered.add(sid)
                        if sid not in known:
                            report("tasks.md", task.line + 1, f"unknown scenario: {sid}")
            for sid, line in known.items():
                if sid not in covered:
                    report("spec.md", line, f"uncovered scenario: {sid}")

    for name, text in inputs.items():
        for line, raw in enumerate(text.splitlines(), 1):
            if name != "spec.md":  # Spec markers already retain their parsed locations.
                for match in CLARIFICATION.finditer(raw):
                    report(name, line, f"unresolved clarification: {match[0]}")
            if name.startswith("checklists/"):
                checkbox = CHECKBOX.match(raw.strip(" \t"))
                if checkbox and checkbox[1] not in ("x", "X"):
                    report(name, line, "unchecked checklist item")
    return [f"{path}:{line}: {message}" for path, line, message in sorted(errors)]


def questions(spec: Spec) -> list[str]:
    """Return complete clarification markers in source order, including repeats."""
    return [marker.text for marker in spec.clarifications]


def human_checks(plan_text: str) -> list[str]:
    """Collect literal pending lines under exact Human checks sections."""
    selected = False
    checks = []
    for raw in plan_text.splitlines():
        line = raw.strip(" \t")
        heading = re.match(r"^(#{1,6})[ \t]+", line)
        if heading and len(heading[1]) <= 2:
            selected = False
        if line == "## Human checks":
            selected = True
        elif selected and re.match(r"- \[ \][ \t]", line) and line[6:].strip():
            checks.append(line)
    return checks


def mark_complete(spec_text: str) -> str:
    """Complete all physical statuses, or insert after the required first title."""
    lines = spec_text.splitlines(keepends=True)
    title_index: int | None = None
    title_ending = ""
    has_status = False
    for index, raw in enumerate(lines):
        content = raw.splitlines()[0]
        ending = raw[len(content):]
        line = content.strip(" \t")
        if title_index is None and TITLE.match(line):
            title_index, title_ending = index, ending
        if STATUS.match(line):
            lines[index] = "**Status**: Complete" + ending
            has_status = True
    if title_index is None:
        raise SpecError("cannot mark complete: missing title")
    if not has_status:
        ending = title_ending or "\n"
        if not title_ending:
            lines[title_index] += ending
        lines.insert(title_index + 1, "**Status**: Complete" + ending)
    return "".join(lines)
