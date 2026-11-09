"""Native saved runs and the two supported human-check sources."""

import pytest

from merge_readiness_fixtures import LocalRun
from specloop import state

CHANGE = "042-add-a-gate"
SID = f"{CHANGE}/us1/as1"
MARKER = "<!-- nightshift:post-deploy -->"
HEADING = "## Checks a human must make\n"
ITEM = f"- [ ] Observe deployment; `{SID}`"
SPEC_PATH = f"specs/{CHANGE}/spec.md"
SPEC = "### User Story 1 - Gate (Priority: P1)\n**Acceptance Scenarios**:\n1. **AS1** Observe deployment. "


class NativeRun(LocalRun):
    def __init__(self, root):
        self.run = state.Run.for_change(CHANGE)
        self.run.dir.mkdir(parents=True)
        self.work = root / "checkout"
        self.work.mkdir()
        self.git("init", "-q", "--separate-git-dir", str(self.run.dir / "repo.git"))
        self.row = dict(change=CHANGE, pr="https://github.com/owner/repo/pull/1")
        self.tasks_path = f"specs/{CHANGE}/tasks.md"
        self.document = "## Phase 1: Build\n- [X] T001 Implement\n## Phase 2: Revision\n- [X] T002 Revise\n"
        self.base = self.commit({self.tasks_path: self.document, SPEC_PATH: SPEC + MARKER + "\n"})
        self.middle = self.commit({"tests/one.py": "first\n"})
        self.commit({"tests/two.py": "second\n"})
        self.run.state.pr = self.row["pr"]
        self.run.state.section_base = {"1": self.base, "2": self.middle}
        self.run.state.evidenced = [1, 2]
        self.run.save()
        self.events = [dict(event="evidence", section=n, passed=True, score=0.9,
                            timeouts=0, uncovered=[]) for n in (1, 2)]
        self.write_events()
        self.run.manifest_path.write_text(f'''run = {{repo = ".", change = "{CHANGE}", branch = "spec/{CHANGE}", format = "speckit"}}
roles = {{implement = "p", review = "p"}}
gates = {{evidence = true}}
profiles = {{p = {{adapter = "codex", model = "m"}}}}
paths = {{allowed = ["tests/**"]}}
verify = [{{id = "ok", run = ["true"]}}]''')


@pytest.fixture
def native_run(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    return NativeRun(tmp_path)


@pytest.fixture(params=["body", "receipt"])
def native_checklist(request):
    def checklist(items=ITEM):
        if request.param == "body":
            return dict(body=HEADING + items + "\n# Next\nignored", publication_checks="malformed fallback")
        return dict(body="Spec: Add a gate", publication_checks=(
            "all tasks landed at abc123; human checks after publishing:\n" + items
            + "\nNext: `specloop publish 042-add-a-gate`"))
    return checklist
