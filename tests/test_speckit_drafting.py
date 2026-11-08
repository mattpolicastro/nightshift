from dataclasses import replace
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest

from specloop import agents, cli, openspec, propose, spec_format
from specloop.seal import git
from test_spec_format import commit, repo
from test_specloop_propose import M

CHANGE = "042-add-a-gate"
FMT = spec_format.SPECKIT
ROOT = FMT.drafter_root(CHANGE)


@pytest.fixture
def feature(repo, monkeypatch):
    shutil.copytree(Path(__file__).parent / "fixtures/speckit", repo, dirs_exist_ok=True)
    # Harness sources are read-only; copytree retains their permissions.
    for path in [repo, *repo.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    commit(repo, {})
    monkeypatch.setattr(openspec, "validate", Mock(side_effect=AssertionError("external validator")))
    monkeypatch.setattr(propose, "in_flight", lambda *a: {})
    for owner, name, result in ((propose.feasibility, "check", {"contradictions": [], "risks": []}),
                                (propose.critic, "check", {"contradictions": [], "risks": []}),
                                (propose.critic, "open_pr_files", ({}, []))):
        monkeypatch.setattr(owner, name, Mock(return_value=result))
    return repo


def draft(feature, tmp_path, edit, *, questions=(), status="drafted", **kwargs):
    calls = []
    def agent(call):
        calls.append(call)
        edit(call.cwd / ROOT)
        return agents.Result("ok", {"status": status, "questions": list(questions)})
    result = propose.draft(feature, "main", CHANGE, f"Revise {CHANGE}", [],
                           replace(M, format="speckit"), tmp_path / "draft", agent=agent,
                           from_branch="main", branch=f"spec/{CHANGE}", **kwargs)
    return result, calls


@pytest.mark.scenario("spec-drafting/native-feature-drafting/native-prompt-names-house-rules")
@pytest.mark.scenario("spec-drafting/native-feature-drafting/existing-feature-is-revised-in-place")
def test_speckit_prompt_names_house_rules(feature, tmp_path):
    original = (feature / ROOT / "tasks.md").read_bytes()
    d, calls = draft(feature, tmp_path, lambda root: (root / "plan.md").write_text("Revised plan\n"))
    prompt = calls[0].prompt
    for token in (ROOT, *[f".specify/templates/{name}-template.md" for name in ("spec", "plan", "tasks")],
                  ".specify/memory/constitution.md", "ASn", f"`{CHANGE}/us<k>/as<n>`", "[USk]",
                  "Setup/Foundational", "## Phase N: User Story K", "numeric priority", "source order",
                  "300 changed lines", "tests with code", "plan.md under `## Human checks`", "at most three",
                  "revise", "in place", "committed task/scenario identities", "unrelated content",
                  "conflicting target identities", "without writing elsewhere", "Complete records are immutable",
                  "new feature", "## Assumptions", "Do not invoke `specify`", "missing templates"):
        assert token in prompt
    assert "openspec/" not in prompt
    assert d.commit and not d.problems and len(calls) == 1
    assert (d.work / ROOT / "tasks.md").read_bytes() == original
    assert git(d.work, "diff", "--name-only", "HEAD^", "HEAD").decode().splitlines() == [ROOT + "plan.md"]
    assert git(d.work, "show", "-s", "--format=%P", "HEAD") == git(feature, "rev-parse", "HEAD")
    assert (feature / ROOT / "plan.md").read_bytes() != b"Revised plan\n"


@pytest.mark.scenario("spec-drafting/native-draft-lint/fixture-passes-and-stray-files-refuse")
def test_lint_accepts_fixture_and_rejects_stray_file(feature):
    assert propose.lint(feature, CHANGE, FMT) == []
    for stray in ("stray.py", f"specs/{CHANGE}-sibling/spec.md"):
        path = feature / stray
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stray")
        assert propose.lint(feature, CHANGE, FMT) == [f"{stray}: only files under {ROOT} may change"]
        path.unlink()


@pytest.mark.scenario("spec-drafting/native-draft-questions/clarification-marker-becomes-question")
@pytest.mark.scenario("spec-drafting/native-draft-questions/explicit-questions-and-unreadable-input")
@pytest.mark.parametrize("status,explicit", [("drafted", []), ("drafted", ["Agent?"]), ("needs_input", ["Agent?"])])
def test_clarification_marker_becomes_question(feature, tmp_path, monkeypatch, status, explicit):
    markers = ["[NEEDS CLARIFICATION: Which?]", "[NEEDS CLARIFICATION]"] * 3
    monkeypatch.setattr(propose, "lint", Mock(side_effect=AssertionError("lint before questions")))
    d, calls = draft(feature, tmp_path, lambda root: (root / "spec.md").write_text("\n".join(markers)),
                     questions=explicit, status=status)
    assert d.questions == explicit + markers and not d.commit and not d.problems and len(calls) == 1
    assert git(d.work, "rev-parse", "HEAD") == git(feature, "rev-parse", "HEAD")
    for method in (propose.feasibility.check, propose.critic.check, propose.critic.open_pr_files):
        method.assert_not_called()


@pytest.mark.scenario("spec-drafting/native-draft-questions/explicit-questions-and-unreadable-input")
@pytest.mark.parametrize("unreadable", [None, b"\xff"])
@pytest.mark.parametrize("explicit", [[], ["Agent?"]])
def test_unreadable_spec_is_a_problem_without_explicit_questions(feature, tmp_path, unreadable, explicit):
    def edit(root):
        path = root / "spec.md"
        path.unlink() if unreadable is None else path.write_bytes(unreadable)
    d, calls = draft(feature, tmp_path, edit, questions=explicit)
    assert d.questions == explicit and not d.commit and len(calls) == 1
    assert bool(d.problems) is (not explicit)
    if not explicit:
        assert ROOT + "spec.md: cannot read draft" in d.problems[0]


@pytest.mark.scenario("spec-drafting/native-draft-questions/explicit-questions-and-unreadable-input")
@pytest.mark.parametrize("name", ["spec.md", "plan.md", "tasks.md", "checklists/readiness.md"])
def test_direct_lint_and_admission_reject_markers(feature, name):
    path = feature / ROOT / name
    head = commit(feature, {ROOT + name: path.read_text() + "\n[NEEDS CLARIFICATION]\n"})
    for problems in (propose.lint(feature, CHANGE, FMT),
                     cli.problems_with(feature, head, replace(M, change=CHANGE, format="speckit"))):
        assert any(ROOT + name in p and "unresolved clarification" in p for p in problems)


@pytest.mark.scenario("spec-drafting/native-draft-lint/native-tasks-retain-lint-and-overlap-checks")
@pytest.mark.scenario("spec-format/manifest-selected-task-operations/consumers-use-selected-task-operations")
def test_native_lint_and_overlap_feedback(feature, tmp_path, monkeypatch):
    path = feature / ROOT / "tasks.md"
    path.write_text(path.read_text().replace("Refuse failing", "Manually verify failing").replace(
        f"{CHANGE}/us1/as2", f"{CHANGE}/us1/as99"))
    problems = propose.lint(feature, CHANGE, FMT)
    assert any("task T004: unknown scenario" in p for p in problems)
    assert any(f"scenario `{CHANGE}/us1/as2` is not covered" in p for p in problems)
    assert any("task T004: a person" in p and "plan.md instead" in p for p in problems)
    monkeypatch.setattr(propose, "in_flight", lambda *a: {"tests/shared.py": ["other"]})
    def edit(root):
        p = root / "tasks.md"
        p.write_text(p.read_text().replace("Create the gate", "Create `tests/shared.py` and the gate"))
    d, calls = draft(feature, tmp_path, edit)
    assert d.commit and not d.problems and len(calls) == 2
    assert "task T001: `tests/shared.py` is also being changed by other" in calls[1].prompt
    assert len(d.risks) == 1 and "so the two may conflict on merge" in d.risks[0]


@pytest.mark.scenario("spec-drafting/native-draft-lint/native-tasks-retain-lint-and-overlap-checks")
@pytest.mark.parametrize("extra,error", [
    ("## Phase 1: Repeated\n", "duplicate native phase"),
    ("- [ ] T0007 Reused\n", "duplicate native task label"),
    ("- [ ] T008 Document the configuration and retry workflow.\n", "ambiguous native task key"),
])
def test_parse_errors_become_draft_problems(feature, tmp_path, extra, error):
    def edit(root):
        path = root / "tasks.md"
        path.write_text(path.read_text() + extra)
    d, _ = draft(feature, tmp_path, edit, rounds=1)
    assert not d.commit and any(error in p for p in d.problems)


@pytest.mark.scenario("spec-drafting/native-draft-lint/native-tasks-retain-lint-and-overlap-checks")
@pytest.mark.parametrize("body,error", [
    (b"\xff", "decode"),
    (b"## 1. Legacy\n- [ ] 1.1 Ignored\n", "no tasks under '## Phase N: Title'"),
])
def test_unreadable_or_wrong_format_tasks_refuse(feature, tmp_path, body, error):
    d, _ = draft(feature, tmp_path, lambda root: (root / "tasks.md").write_bytes(body), rounds=1)
    assert not d.commit and any(error in p for p in d.problems)


@pytest.mark.scenario("in-flight-file-map/native-spec-reference-exclusion/native-references-are-not-planned-edits")
@pytest.mark.scenario("spec-format/engine-routing-compatibility/openspec-planned-edits-exclude-reserved-specs-root")
@pytest.mark.parametrize("rooted", [False, True])
@pytest.mark.parametrize("extract", [propose.paths, propose.edited_paths])
def test_reserved_spec_paths_are_excluded(tmp_path, rooted, extract):
    root = tmp_path if rooted else None
    text = f"`{ROOT}spec.md` `{ROOT}tasks.md` `src/gate.py:run` `tests/test_gate.py::test_gate`"
    assert extract(text, root) == {"src/gate.py", "tests/test_gate.py"}
    task, = spec_format.OPENSPEC.parse_tasks("## 1. Core\n- [ ] 1.1 Edit `specs/helper.py` and `src/helper.py`\n")
    assert extract(task.text, root) == {"src/helper.py"}
    assert extract("`openspec/specs/gate/spec.md`", root) == set()
