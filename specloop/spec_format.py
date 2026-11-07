"""Change-format paths and operations, with OpenSpec as the default backend."""

import re
import subprocess
from pathlib import Path
from typing import Protocol

from specloop import openspec
from specloop.manifest import Manifest
from specloop.openspec import SpecError as SpecError, ValidatorUnavailable as ValidatorUnavailable
from specloop.seal import git


class SpecFormat(Protocol):
    def change_dir(self, repo: Path, change: str) -> Path: ...
    def tasks_path(self, change: str) -> str: ...
    def tasks_paths_at(self, repo: Path, head: str, change: str) -> list[str]: ...
    def validate(self, repo: Path, change: str) -> list[str]: ...
    def scenarios(self, repo: Path, change: str) -> dict[str, str]: ...
    def human_checks(self, repo: Path, head: str, change: str) -> list[str]: ...
    def context_files(self, repo: Path, change: str) -> list[tuple[Path, str]]: ...
    def drafter_root(self, change: str) -> str: ...
    def finish(self, work: Path, change: str, all_tasks_done: bool) -> dict: ...
    def pr_title(self, change: str) -> str: ...

    @property
    def pr_kind(self) -> str: ...


class _OpenSpec:
    pr_kind = "OpenSpec change"

    def change_dir(self, repo: Path, change: str) -> Path:
        return openspec.change_dir(repo, change)

    def tasks_path(self, change: str) -> str:
        return f"openspec/changes/{change}/tasks.md"

    def tasks_paths_at(self, repo: Path, head: str, change: str) -> list[str]:
        """Locate committed tasks, including an interrupted finish's archive."""
        live = self.tasks_path(change)
        paths = git(repo, "ls-tree", "-r", "--name-only", head,
                    live, "openspec/changes/archive/").decode().splitlines()
        if live in paths:
            return [live]
        archived = sorted((p for p in paths if re.fullmatch(
            rf"openspec/changes/archive/\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(change)}/tasks\.md", p)),
            reverse=True)
        return archived or [live]

    def validate(self, repo: Path, change: str) -> list[str]:
        return openspec.validate(repo, change)

    def scenarios(self, repo: Path, change: str) -> dict[str, str]:
        return openspec.scenarios(repo, change)

    def human_checks(self, repo: Path, head: str, change: str) -> list[str]:
        """Collect literal pending checks from the committed design, including examples."""
        path = f"openspec/changes/{change}/design.md"
        try:
            if not git(repo, "ls-tree", "-z", "--name-only", head, "--", path):
                return []
            text = git(repo, "show", f"{head}:{path}").decode("utf-8")
        except (RuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise RuntimeError(f"could not collect human checks from {head}:{path}: {exc}") from exc
        selected = {"## Human checks": 2, "### Verification and human rollout checklist": 3}
        depth = 0
        checks = []
        for physical_line in text.splitlines():
            line = physical_line.strip(" \t")
            heading = re.match(r"^(#{1,6})[ \t]+", line)
            if heading and len(heading[1]) <= depth:
                depth = 0
            # A nested selected heading must not narrow its enclosing section.
            if not depth and line in selected:
                depth = selected[line]
            elif depth and re.match(r"- \[ \][ \t]", line) and line[6:].strip():
                checks.append(line)
        return checks

    def context_files(self, repo: Path, change: str) -> list[tuple[Path, str]]:
        src = self.change_dir(repo, change)
        return [(src / name, name) for name in ("proposal.md", "design.md", "specs")
                if (src / name).exists()]

    def drafter_root(self, change: str) -> str:
        return f"openspec/changes/{change}/"

    def finish(self, work: Path, change: str, all_tasks_done: bool) -> dict:
        return openspec.archive(work, change, all_tasks_done=all_tasks_done)

    def pr_title(self, change: str) -> str:
        return f"{change}: OpenSpec change via specloop"


OPENSPEC: SpecFormat = _OpenSpec()


def get(m: Manifest) -> SpecFormat:
    if m.format == "speckit":
        raise SpecError("spec-kit format is not available yet")
    return OPENSPEC
