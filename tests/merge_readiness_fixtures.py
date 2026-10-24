"""Minimal saved runs with real Git boundaries, reusable by readiness tests."""

import json
import subprocess

import pytest

from specloop import state


class LocalRun:
    def __init__(self, root):
        self.run = state.Run.for_change("c")
        self.run.dir.mkdir(parents=True)
        self.work = root / "checkout"
        self.work.mkdir()
        self.git("init", "-q", "--separate-git-dir", str(self.run.dir / "repo.git"))
        self.row = {"change": "c", "pr": "https://github.com/owner/repo/pull/1"}
        self.tasks_path = "openspec/changes/c/tasks.md"
        self.document = "## 1. Build\n- [x] 1.1 Implement\n\n## 2. Revision\n- [x] 2.1 Revise\n"
        self.base = self.commit({self.tasks_path: self.document, "source.py": "original\n"})
        self.middle = self.commit({"tests/one.py": "first\n"})
        self.commit({"tests/two.py": "second\n"})
        self.run.state.pr = self.row["pr"]
        self.run.state.section_base = {"1": self.base, "2": self.middle}
        self.run.state.evidenced = [1, 2]
        self.run.save()
        self.events = [dict(event="evidence", section=n, passed=True, score=0.9,
                            timeouts=0, uncovered=[]) for n in (1, 2)]
        self.write_events()
        self.run.manifest_path.write_text('''run = {repo = ".", change = "c", branch = "spec/c"}
roles = {implement = "p", review = "p"}
profiles = {p = {adapter = "codex", model = "m"}}
paths = {allowed = ["tests/**"]}
verify = [{id = "ok", run = ["true"]}]''')

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.work), "-c", "user.name=Test", "-c",
                               "user.email=test@example.invalid", *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files):
        for name, content in files.items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink() if content is None else path.write_text(content)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.row["head"] = self.git("rev-parse", "HEAD")
        return self.row["head"]

    def write_events(self):
        (self.run.dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events))


@pytest.fixture
def local_run(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    return LocalRun(tmp_path)
