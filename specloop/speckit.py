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


def scenarios(repo: Path, change: str) -> dict[str, str]:
    path = feature_dir(repo, change) / "spec.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SpecError(f"{path}: cannot read native scenarios: {exc}") from exc
    return scenario_ids(change, parse_spec(text))


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
    entries: list[tasks.Task] = field(default_factory=list)


def parse_tasks(text: str) -> list[tasks.Task]:
    """Read labelled checkboxes in literal phases, retaining flags in task text."""
    return [task for phase in _checked_phases(text) for task in phase.entries]


def _checked_phases(text: str) -> list[_Phase]:
    phases = _parse_phases(text)
    numbers: set[int] = set()
    labels: set[tuple[int, ...]] = set()
    keys: set[str] = set()
    for phase in phases:
        if phase.number in numbers:
            raise SpecError(f"duplicate native phase number: {phase.number}")
        numbers.add(phase.number)
        for task in phase.entries:
            identity = tuple(int(part) for part in task.label[1:].split("."))
            if identity in labels:
                raise SpecError(f"duplicate native task label: {task.label}")
            labels.add(identity)
            if task.key in keys:
                raise SpecError(f"ambiguous native task key: {task.key} ({task.title()!r})")
            keys.add(task.key)
    return phases


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
            current.entries.append(tasks.Task(current.number, current.title, label[1], rest,
                                              checkbox[2] != " ", number))
    return found


def _locate(text: str, task: tasks.Task) -> tasks.Task:
    for current in parse_tasks(text):
        if current.key == task.key:
            return current
    raise SpecError(f"task {task.key} ({task.title()!r}) is no longer in tasks.md")


def _checked_result(text: str) -> str:
    parse_tasks(text)
    return text


def tick(text: str, task: tasks.Task) -> str:
    current = _locate(text, task)
    lines = text.splitlines(keepends=True)
    if not current.done:
        lines[current.line] = re.sub(r"\[ \]", "[X]", lines[current.line], count=1)
    return _checked_result("".join(lines))


def _inherit_flags(parent: tasks.Task, item: str) -> str:
    """Only the label grammar's positional flags convey ownership."""
    original = TASK_LABEL.fullmatch(parent.title())
    supplied = TASK_LABEL.fullmatch(f"T000 {item.strip()}")
    if original is None or supplied is None:
        raise SpecError("split requires a nonempty native task body")
    owner, new_owner = original[2], supplied[2]
    if owner is not None and new_owner is not None and int(owner) != int(new_owner):
        raise SpecError(f"conflicting story ownership: US{owner} and US{new_owner}")
    parallel = any(re.match(r"^\[P\]\s+", body) for body in (parent.text, item.strip()))
    story = owner if owner is not None else new_owner
    return ("[P] " if parallel else "") + (f"[US{story}] " if story is not None else "") + supplied[3]


def split(text: str, task: tasks.Task, subtasks: list[str]) -> str:
    current = _locate(text, task)
    lines = text.splitlines(keepends=True)
    match = tasks.TASK.match(lines[current.line])
    assert match is not None
    new = [f"{match[1]}- [ ] {current.label}.{n} {_inherit_flags(current, item)}\n"
           for n, item in enumerate(subtasks, 1)]
    return _checked_result("".join(lines[:current.line] + new + lines[current.line + 1:]))


def _new_tasks(phases: list[_Phase], items: list[str]) -> str:
    root = max((int(task.label[1:].split(".")[0])
                for phase in phases for task in phase.entries), default=0)
    return "".join(f"- [ ] T{root + n:03d} {item.strip()}\n" for n, item in enumerate(items, 1))


def extend_section(text: str, section: int, items: list[str]) -> str:
    phases = _checked_phases(text)
    phase = next((phase for phase in phases if phase.number == section), None)
    if phase is None:
        raise SpecError(f"no native phase {section}")
    lines = text.splitlines(keepends=True)
    # Empty phases are valid targets; insert just after their heading.
    at = phase.entries[-1].line + 1 if phase.entries else phase.line
    new = _new_tasks(phases, items)
    if new and not lines[at - 1].endswith(("\n", "\r")):
        new = "\n" + new
    return _checked_result("".join(lines[:at]) + new + "".join(lines[at:]))


def append_section(text: str, title: str, items: list[str]) -> str:
    phases = _checked_phases(text)
    number = max((phase.number for phase in phases), default=0) + 1
    body = _new_tasks(phases, items)
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return _checked_result(f"{text}{sep}## Phase {number}: {title}\n\n{body}")


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
                    for task in phase.entries
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
                for task in phase.entries:
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
