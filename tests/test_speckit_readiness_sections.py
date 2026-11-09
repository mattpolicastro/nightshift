"""Native phase integrity and the existing readiness evidence policy."""

import json
from pathlib import Path
import subprocess

import pytest

from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo
from speckit_front_door_fixtures import CHANGE, HEADING, native_run


@pytest.mark.scenario("merge-readiness/adapter-parsed-readiness-sections/native-phases-participate-in-evidence")
@pytest.mark.parametrize("field,value,reason", [
    ("passed", True, None), ("passed", False, "passed must be true"),
    ("timeouts", 2, "2 timeouts"), ("unmeasured", True, "unmeasured or invalid measurement flag"),
    ("score", None, "score missing, invalid or unmeasured without no-mutable-lines summary"),
    ("completion", None, "saved evidence completion missing"),
    ("missing", None, "missing event; no bounded test-only diff proof"),
])
def test_native_evidence_uses_latest_event(native_run, field, value, reason):
    env = native_run
    if field == "completion":
        env.run.state.evidenced.remove(2)
    elif field == "missing":
        env.events.pop()
        env.run.state.section_base.clear()
    else:
        env.events.append(dict(env.events[1], **{field: value}))
    env.write_events()
    env.run.save()
    assert intake._readiness_evidence(env.row) == ([] if reason is None else [f"evidence section 2: {reason}"])


@pytest.mark.scenario("merge-readiness/adapter-parsed-readiness-sections/native-phases-participate-in-evidence")
@pytest.mark.parametrize("other", [False, True])
def test_native_tasks_path_is_excluded_from_test_only_diff(native_run, other):
    env = native_run
    env.events = []
    env.write_events()
    path = f"openspec/changes/{CHANGE}/tasks.md" if other else env.tasks_path
    env.commit({path: env.document + "\n"})
    assert intake._readiness_evidence(env.row) == ([
        "evidence section 2: missing event; no bounded test-only diff proof"] if other else [])


@pytest.mark.scenario("merge-readiness/adapter-parsed-readiness-sections/empty-or-ambiguous-phases-block")
@pytest.mark.parametrize("document", ["", "## Phase 1: Empty\n", "## 1. Foreign\n- [x] 1.1 Work\n",
    "## Phase 1: Empty\n## Phase 2: Work\n- [X] T002 Work\n",
    "## Phase 1: Work\n- [X] T001 Work\n## Phase 1: Again\n- [X] T002 Again\n",
    "## Phase 1: Work\n- [X] T001 Work\n- [X] T0001 Alias\n",
    "## Phase 1: Work\n- [X] T001 Same\n- [X] T002 Same\n",
    "## Phase 1: Work\n## Other\n- [X] T001 Outside\n",
    "## Phase 1: Work\n# Other\n- [X] T001 Outside\n",
    "## Phase 1: Work\n- [X] malformed task\n",
    "```md\n## Phase 1: Empty example\n```\n",
    "<!--\n## Phase 1: Empty example\n-->\n", None, "decode", "unreadable"])
def test_native_empty_or_ambiguous_phases_block(native_run, document):
    env = native_run
    env.commit({env.tasks_path: document})
    if document == "decode":
        (env.work / env.tasks_path).write_bytes(b"\xff")
        env.commit({})
    def runner(args, **kwargs):
        if document == "unreadable":
            raise OSError("unreadable tasks")
        return subprocess.run(args, **kwargs)
    reasons = intake._readiness_evidence(env.row, git_runner=runner)
    assert len(reasons) == 1 and reasons[0].startswith("evidence: check committed tasks.md/repo.git")


@pytest.mark.scenario("merge-readiness/adapter-parsed-readiness-sections/empty-or-ambiguous-phases-block")
@pytest.mark.parametrize("wrapper", [("```md\n", "```\n"), ("<!--\n", "-->\n")])
def test_native_physical_phases_in_examples_are_assessed(native_run, wrapper):
    env = native_run
    head = env.commit({env.tasks_path: wrapper[0] + env.document + wrapper[1]
        + "## 3. Foreign\n- [x] 3.1 Ignored\n"})
    env.commit({env.tasks_path: "## Phase 3: New empty phase\n"})
    assert intake._readiness_evidence({**env.row, "head": head}) == []


@pytest.mark.scenario("merge-readiness/adapter-parsed-readiness-sections/disabled-evidence-keeps-early-exemption")
@pytest.mark.parametrize("format", ["openspec", "speckit"])
@pytest.mark.parametrize("identity", ["valid", "manifest", "state", "head"])
def test_disabled_evidence_never_reads_tasks_events_or_diffs(native_run, monkeypatch, format, identity):
    env = native_run
    path = env.run.manifest_path
    path.write_text(path.read_text().replace('"speckit"', f'"{format}"').replace("evidence = true", "evidence = false"))
    if identity == "manifest":
        path.write_text(path.read_text().replace(f'change = "{CHANGE}"', 'change = "other"'))
    elif identity == "state":
        env.run.state.pr = "other"
        env.run.save()
    elif identity == "head":
        env.row["head"] = "invalid"
    (env.run.dir / "events.jsonl").write_text("malformed")
    if identity != "head":
        env.commit({env.tasks_path: None})
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path.name in ("state.json", "manifest.toml"), f"unexpected read: {path}"
        return original(path, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("disabled evidence read tasks or diffs")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded)
        reasons = intake._readiness_evidence(env.row, git_runner=forbidden)
    assert bool(reasons) == (identity != "valid")


@pytest.mark.scenario("merge-readiness/adapter-parsed-readiness-sections/disabled-evidence-keeps-early-exemption")
@pytest.mark.parametrize("condition", ["head", "CI", "revision", "human checks"])
def test_disabled_evidence_keeps_other_conditions(native_run, tmp_path, condition):
    env = native_run
    path = env.run.manifest_path
    path.write_text(path.read_text().replace("evidence = true", "evidence = false"))
    pr = dict(headRefOid="a" * 40 if condition == "head" else env.row["head"],
              labels=[{"name": queue.Labels().revise}] if condition == "revision" else [],
              body=HEADING + ("- [x] no citation" if condition == "human checks" else "(none)"))
    result = intake._readiness_assessment(Repo(name="owner/repo"), queue.Labels(), tmp_path, 1, env.row, pr,
        runner=lambda args: json.dumps([dict(name="CI", state="COMPLETED", bucket="fail" if condition == "CI" else "pass")]))
    assert result["label"] == "merge:blocked"
    assert len(result["reasons"]) == 1 and result["reasons"][0].startswith(condition + ":")
