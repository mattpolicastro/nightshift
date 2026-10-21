"""tasks.md, parsed by the harness itself.

A task's identity is a hash of its section title and its text (without the
numeric label), so renumbering is harmless and editing a task's wording makes
it a new task rather than silently inheriting the old one's attempts.
"""

import hashlib
import re
from dataclasses import dataclass

SECTION = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
TASK = re.compile(r"^(\s*)-\s+\[( |x|X)\]\s+(.+?)\s*$")
LABEL = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")
# capability/requirement/scenario, as slugged by specloop.openspec.slug
SCENARIO_ID = re.compile(r"\b([a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*)\b")
SCENARIO_REF = re.compile(r"`([a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*)`")


@dataclass(frozen=True)
class Task:
    section: int
    section_title: str
    label: str
    text: str
    done: bool
    line: int  # 0-based index into the file's lines

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.section_title}\n{self.text}".encode()).hexdigest()[:12]

    @property
    def scenarios(self) -> list[str]:
        return SCENARIO_REF.findall(self.text)

    def title(self) -> str:
        return f"{self.label} {self.text}".strip()


def parse(text: str) -> list[Task]:
    tasks = []
    section, title = 0, ""
    for i, line in enumerate(text.splitlines()):
        if m := SECTION.match(line):
            section, title = int(m.group(1)), m.group(2)
            continue
        if (m := TASK.match(line)) and section:
            body = m.group(3)
            label, rest = (LABEL.match(body).groups() if LABEL.match(body) else ("", body))
            tasks.append(Task(section, title, label, rest, m.group(2) != " ", i))
    return tasks


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _locate(lines: list[str], task: Task) -> int:
    """The task's current line; it may have moved if the file was edited."""
    for i, t in enumerate(parse("".join(lines))):
        if t.key == task.key:
            return t.line
    raise KeyError(f"task {task.key} ({task.title()!r}) is no longer in tasks.md")


def tick(text: str, task: Task) -> str:
    lines = _lines(text)
    i = _locate(lines, task)
    lines[i] = re.sub(r"\[ \]", "[x]", lines[i], count=1)
    return "".join(lines)


def split(text: str, task: Task, subtasks: list[str]) -> str:
    """Replace one task with its subtasks, labelled under the original label."""
    lines = _lines(text)
    i = _locate(lines, task)
    indent = TASK.match(lines[i].rstrip("\n")).group(1)
    base = task.label or f"{task.section}"
    new = [f"{indent}- [ ] {base}.{n} {s.strip()}\n" for n, s in enumerate(subtasks, 1)]
    return "".join(lines[:i] + new + lines[i + 1 :])


def extend_section(text: str, section: int, items: list[str]) -> str:
    """Add tasks at the end of an existing section, numbered after its last."""
    lines = _lines(text)
    in_section = [t for t in parse(text) if t.section == section]
    if not in_section:
        raise KeyError(f"no section {section}")
    last = in_section[-1]
    nums = [int(t.label.split(".")[1]) for t in in_section if t.label.count(".") >= 1 and t.label.split(".")[1].isdigit()]
    start = max(nums, default=0) + 1
    new = [f"- [ ] {section}.{start + k} {s.strip()}\n" for k, s in enumerate(items)]
    return "".join(lines[:last.line + 1] + new + lines[last.line + 1:])


def append_section(text: str, title: str, items: list[str]) -> str:
    sections = [int(m.group(1)) for line in text.splitlines() if (m := SECTION.match(line))]
    n = max(sections, default=0) + 1
    body = "".join(f"- [ ] {n}.{k} {s.strip()}\n" for k, s in enumerate(items, 1))
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return f"{text}{sep}## {n}. {title}\n\n{body}"
