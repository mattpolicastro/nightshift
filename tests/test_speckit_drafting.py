from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest

from nightshift.cli import main
from specloop import agents, cli, critic, feasibility, manifest, openspec, propose, spec_format
from specloop.seal import git
from specloop.state import Run
from test_spec_format import commit, repo
from test_speckit_build import native_admission, three_phase_feature
from test_specloop_propose import M

CHANGE = "042-add-a-gate"
FMT = spec_format.SPECKIT
ROOT = FMT.drafter_root(CHANGE)


@pytest.fixture
def native_feature(repo):
    shutil.copytree(Path(__file__).parent / "fixtures/speckit", repo, dirs_exist_ok=True)
    # Harness sources are read-only; copytree retains their permissions.
    for path in [repo, *repo.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    commit(repo, {})
    return repo


@pytest.fixture
def feature(native_feature, monkeypatch):
    monkeypatch.setattr(openspec, "validate", Mock(side_effect=AssertionError("external validator")))
    monkeypatch.setattr(propose, "in_flight", lambda *a: {})
    for owner, name, result in ((propose.feasibility, "check", {"contradictions": [], "risks": []}),
                                (propose.critic, "check", {"contradictions": [], "risks": []}),
                                (propose.critic, "open_pr_files", ({}, []))):
        monkeypatch.setattr(owner, name, Mock(return_value=result))
    return native_feature


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
    # The recommendation includes smaller plans, not six tasks AND fewer.
    assert "Target six tasks or fewer per user story" in " ".join(prompt.split())
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


PASS = {"contradictions": [], "risks": []}
UNDISCLOSED = {"task": "T003", "problem": "Changed guarantee is not justified in Assumptions",
               "evidence": "specs/010-old-gate/spec.md:3; specs/042-add-a-gate/plan.md:2"}


def add_review_context(root):
    """Commit explicit references in both documents, including literal examples."""
    return commit(root, {
        ROOT + "plan.md": """# Plan
Change the old gate's refusal behavior.
## Depends on
010-old-gate
### Nested references
specs/011-old-check/spec.md
## Assumptions
Revisit specs/012-old-result/.
# Next
```markdown
## Depends on
013-example
```
\t Depends on: 014-field \t
 **Depends on**: specs/015-bold-field/spec.md
""",
        ROOT + "spec.md": (root / ROOT / "spec.md").read_text() + "\n## Assumptions\n016-spec-reference\n",
        **{f"specs/{name}/spec.md": f"# {name}\n**Status**: Complete\nPreserve refusals — committed.\n"
           for name in ("010-old-gate", "011-old-check", "012-old-result", "013-example",
                        "014-field", "015-bold-field", "016-spec-reference")},
    })


def assert_native_review_prompt(prompt):
    text = " ".join(prompt.split())
    # These are agent instructions: preserve the logical relationships, not
    # just the individual context names or finding categories.
    for instruction in (
        "code and tests, and repository instructions (including AGENTS.md when present)",
        "for every feature named by these sections or fields",
        "Missing or ambiguous referenced identity, unreadable required context, or conflicting Status lines "
        "must produce a concrete inability-to-complete finding in contradictions",
        "removes only that missing-disclosure basis; it does not waive allowed/frozen paths, "
        "trusted verify commands, or other impossibilities",
    ):
        assert instruction in text
    for instruction in (
        *(ROOT + name for name in ("spec.md", "plan.md", "tasks.md")),
        ".specify/memory/constitution.md", "specs/<named-feature>/spec.md",
        "exact candidate commit", "Do not modify anything", "AGENTS.md",
        "exact column-zero `## Depends on`", "`## Assumptions`",
        "next column-zero level-one/two heading", "ASCII-trimmed `Depends on:`",
        "`**Depends on**:` field line", "spec.md's `## Assumptions`",
        "explicit canonical `NNN-slug` names", "`specs/NNN-slug/` paths, optionally ending in `spec.md`",
        "physical `**Status**: Complete`", "Do not treat Draft features as Complete guarantees",
        "Do not infer identities from titles", "recurse through named features' references",
        "scan unrelated historical features", "physical lines inside examples receive no semantic Markdown exemption",
        "Missing or ambiguous referenced identity", "unreadable required context", "conflicting Status lines",
        "inability-to-complete finding in contradictions", "affected task and file/line evidence",
        "Never silently omit required context and return a clean review",
        "Breaking a constitution principle", "changing behavior specified by a named Complete feature",
        "explicitly identifying and justifying that change", "spec.md or plan.md is a contradiction",
        "Name the task and cite concrete file/line evidence", "generic dependency mention is not acknowledgement",
        "removes only that missing-disclosure basis", "does not waive allowed/frozen paths",
        "trusted verify commands", "Human-only checks belong in plan.md, not tasks.md",
    ):
        assert instruction in text
    assert "openspec/" not in prompt and "template-name" not in prompt


def review_candidate(kind, root, head, m, out, agent):
    if kind == "feasibility":
        return feasibility.check(root, head, m, out, agent=agent)
    return critic.check(root, head, m, out, change=CHANGE, planned={}, pr_files={}, context_notes=[], agent=agent)


def assert_review_candidate(kind, native_feature, tmp_path, selection):
    head = add_review_context(native_feature)
    snapshot = {p.relative_to(native_feature): p.read_bytes() for p in native_feature.rglob("*")
                if p.is_file() and ".git" not in p.parts}
    # Neither a later committed version nor dirty source files may leak into review.
    commit(native_feature, {str(p): "later commit\n" for p in snapshot})
    for p in snapshot:
        (native_feature / p).write_bytes(b"dirty\n")
    (native_feature / "untracked.txt").write_text("untracked")
    preferred = manifest.Profile("preferred", "codex", "test")
    chains = {"feasibility": {"feasibility": (preferred,), "review": (M.roles["review"],)},
              "review": {"review": (preferred,)}, "role": {}}[selection]
    m = replace(M, format="speckit", change=CHANGE if kind == "feasibility" else "template-name",
                chains=chains, idle_minutes=7, review_minutes=3)
    calls = []

    def agent(call):
        calls.append(call)
        assert_native_review_prompt(call.prompt)
        assert call.access == "read" and call.schema is feasibility.SCHEMA
        assert call.profile == (M.roles["review"] if selection == "role" else preferred)
        assert call.wall_seconds == 1200 and call.idle_seconds == 420
        assert call.home == agents.homes_dir() / call.profile.name
        assert call.transcript == tmp_path / kind / f"{kind}.jsonl"
        assert not (call.cwd / ".git").exists() and not (call.cwd / "untracked.txt").exists()
        for path, content in snapshot.items():
            assert (call.cwd / path).read_bytes() == content
        return agents.Result("ok", PASS)

    assert review_candidate(kind, native_feature, head, m, tmp_path / kind, agent) == PASS
    assert len(calls) == 1 and not calls[0].cwd.exists()
    assert json.loads((tmp_path / kind / f"{kind}.json").read_text()) == PASS
    assert all((native_feature / path).read_bytes() == b"dirty\n" for path in snapshot)


@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/constitution-and-named-features-are-read")
@pytest.mark.parametrize("selection", ["feasibility", "review", "role"])
def test_feasibility_prompt_includes_constitution_and_named_features(native_feature, tmp_path, selection):
    assert_review_candidate("feasibility", native_feature, tmp_path, selection)


@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/constitution-and-named-features-are-read")
@pytest.mark.parametrize("selection", ["feasibility", "review", "role"])
def test_critic_prompt_includes_constitution_and_named_features(native_feature, tmp_path, selection):
    assert_review_candidate("critic", native_feature, tmp_path, selection)


@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/missing-or-ambiguous-context-is-not-a-pass")
@pytest.mark.parametrize("kind", ["feasibility", "critic"])
@pytest.mark.parametrize("problem", ["missing-constitution", "unreadable-feature", "ambiguous", "conflicting-status", "draft"])
def test_native_context_findings_retain_evidence(native_feature, tmp_path, kind, problem):
    add_review_context(native_feature)
    named = "specs/010-old-gate/spec.md"
    path = ".specify/memory/constitution.md" if problem == "missing-constitution" else named
    if problem == "missing-constitution":
        (native_feature / path).unlink()
    elif problem == "unreadable-feature":
        (native_feature / path).write_bytes(b"\xff")
    elif problem == "ambiguous":
        path = ROOT + "plan.md"
        (native_feature / path).write_text("## Depends on\nThe old gate (which feature?)\n")
    else:
        (native_feature / path).write_text("# Old gate\n**Status**: Draft\n" + (
            "**Status**: Complete\n" if problem == "conflicting-status" else ""))
    head = commit(native_feature, {})
    expected = git(native_feature, "show", f"{head}:{path}") if problem != "missing-constitution" else None
    verdict = PASS if problem == "draft" else {"contradictions": [{
        "task": "T003", "problem": f"Cannot complete review: {problem}", "evidence": path}], "risks": []}

    def agent(call):
        assert_native_review_prompt(call.prompt)
        if expected is None:
            assert not (call.cwd / path).exists()
        else:
            assert (call.cwd / path).read_bytes() == expected
        # Scripted findings test routing, not model reasoning or a Markdown parser.
        return agents.Result("ok", verdict)

    assert review_candidate(kind, native_feature, head, replace(M, format="speckit", change=CHANGE),
                            tmp_path / "review", agent) == verdict


@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/undisclosed-behavior-changes-contradict")
@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/constitution-and-named-features-are-read")
@pytest.mark.parametrize("kind", ["feasibility", "critic"])
@pytest.mark.parametrize("acknowledgement", ["plan.md", "spec.md", "frozen-conflict"])
def test_native_draft_review_correction(native_feature, tmp_path, monkeypatch, kind, acknowledgement):
    add_review_context(native_feature)
    monkeypatch.setattr(propose, "in_flight", lambda *a: {})
    lookup = Mock(return_value=({}, []))
    monkeypatch.setattr(critic, "open_pr_files", lookup)
    events, revisions = [], []
    rounds = 0
    frozen = {"task": "T003", "problem": "Frozen checker rejects the required output",
              "evidence": "checker.py:12"}

    def agent(call):
        nonlocal rounds
        if call.access == "write":
            rounds += 1
            events.append("draft")
            path = call.cwd / ROOT / ("spec.md" if acknowledgement == "spec.md" else "plan.md")
            text = "\nInitial draft update.\n" if rounds == 1 else (
                "\n## Assumptions\nSupersede 010-old-gate: change refusals because the new gate reports failures.\n")
            if rounds == 2:
                assert UNDISCLOSED["problem"] in call.prompt and UNDISCLOSED["evidence"] in call.prompt
            path.write_text(path.read_text() + text)
            revisions.append({name: (call.cwd / ROOT / name).read_bytes() for name in ("spec.md", "plan.md", "tasks.md")})
            return agents.Result("ok", {"status": "drafted", "questions": []})
        reviewer = "critic" if call.prompt.startswith("# Spec critic") else "feasibility"
        events.append(reviewer)
        assert_native_review_prompt(call.prompt)
        for name, content in revisions[-1].items():
            assert (call.cwd / ROOT / name).read_bytes() == content
        findings = [] if reviewer != kind else [UNDISCLOSED] if rounds == 1 else (
            [frozen] if acknowledgement == "frozen-conflict" else [])
        return agents.Result("ok", {"contradictions": findings, "risks": []})

    m = replace(M, format="speckit", change="template-name")
    d = propose.draft(native_feature, "main", CHANGE, f"Revise {CHANGE}", [], m, tmp_path / "draft",
                      rounds=2, from_branch="main", agent=agent)
    assert rounds == 2 and d.commit and not d.problems
    assert d.contradictions == ([frozen] if acknowledgement == "frozen-conflict" else [])
    assert events[:3] == (["draft", "feasibility", "draft"] if kind == "feasibility" else
                          ["draft", "feasibility", "critic"])
    assert m.change == "template-name"
    assert lookup.call_count == (0 if kind == "feasibility" and acknowledgement == "frozen-conflict" else 1)


@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/native-admission-uses-feasibility-normally")
@pytest.mark.parametrize("outcome", ["pass", "risk", "contradiction", "error"])
def test_native_admission_uses_feasibility_normally(native_admission, monkeypatch, capfd, outcome):
    root, _, path, _ = native_admission
    head = add_review_context(root)
    snapshot = {name: git(root, "show", f"{head}:{name}") for name in (
        ROOT + "spec.md", ROOT + "plan.md", ROOT + "tasks.md", ".specify/memory/constitution.md",
        "specs/010-old-gate/spec.md", "specs/016-spec-reference/spec.md")}
    commit(root, {name: "later commit\n" for name in snapshot})
    for name in snapshot:
        (root / name).write_text("dirty\n")
    refs = git(root, "show-ref")
    calls = []
    verdict = {"contradictions": [UNDISCLOSED] if outcome == "contradiction" else [],
               "risks": ["Retry may be needed"] if outcome == "risk" else []}

    def agent(call):
        calls.append(call)
        assert_native_review_prompt(call.prompt)
        assert call.access == "read" and call.schema is feasibility.SCHEMA
        assert call.prompt.startswith("# Feasibility check of a spec-kit feature")
        for name, content in snapshot.items():
            assert (call.cwd / name).read_bytes() == content
        return agents.Result("timeout", detail="review expired") if outcome == "error" else agents.Result("ok", verdict)

    monkeypatch.setattr(agents, "run", agent)
    assert main(["admit", str(path), "--head", head, "--json"]) == int(outcome in {"contradiction", "error"})
    output = json.loads(capfd.readouterr().out)
    assert output["head"] == head and len(calls) == 1 and not calls[0].cwd.exists()
    run = Run.for_change(CHANGE)
    if outcome in {"pass", "risk"}:
        assert output["outcome"] == "admitted" and output["risks"] == verdict["risks"]
        assert (run.dir / "state.json").is_file()
        assert json.loads((run.dir / "admission/feasibility.json").read_text()) == verdict
    else:
        assert output["outcome"] == "refused" and not (run.dir / "state.json").exists()
        assert (UNDISCLOSED["evidence"] if outcome == "contradiction" else "review did not complete") in output["reasons"][0]
    assert git(root, "show-ref") == refs
    assert all((root / name).read_text() == "dirty\n" for name in snapshot)


@pytest.mark.scenario("spec-critic/native-feasibility-and-critic-context/native-admission-uses-feasibility-normally")
def test_original_review_prompts_and_native_checklist_are_preserved():
    assert hashlib.sha256(feasibility.PROMPT.encode()).hexdigest() == "58c2d98ae31ff9af8239ebbaeee9018ce37aa324494d261bd4bb9682dc0a74d3"
    assert hashlib.sha256(critic.PROMPT.encode()).hexdigest() == "b221906f7a9e0d3fc5669ae58d3c22f093ebeb56aeada6abdb6187f074e52781"
    checklist = critic.PROMPT.split("Work through all eight checklist items:\n", 1)[1].split("\nReturn contradictions", 1)[0]
    assert checklist in critic.SPECKIT_PROMPT
