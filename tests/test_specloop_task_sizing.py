"""Offline sizing contracts; diff review and live reader checks remain human."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from specloop import propose, tasks
from test_specloop_documentation_integrity import LINK, heading_ids


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def task_rules():
    prompt = propose.PROMPT.format(
        change="size-a-plan", idea="Update related guidance", sources="- README.md",
        in_flight="", feedback="",
    )
    section = prompt.split("- `tasks.md`", 1)[1].split("\n- Prefer behaviour", 1)[0]
    return [" ".join(rule.split()) for rule in section.split("\n  - ")[1:]]


def assert_clause(clauses, expected):
    assert any(expected in clause for clause in clauses), f"Missing clause: {expected}"


@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/related-work-shares-a-task")
def test_drafter_rules_size_tasks_by_risk(task_rules):
    assert_clause(task_rules,
        "Combine work touching the same file for one concern into one task within that ceiling. "
        "Split when distinct concerns, dependencies or the ceiling require it, not by document heading.")
    assert_clause(task_rules,
        "Most changes should use six tasks or fewer and as few sections as dependencies allow. "
        "Six is a normal target, not a hard rejection limit.")
    assert_clause(task_rules,
        "Every task incurs a full implement, verify and review cycle; every section incurs an "
        "evidence gate. Size boundaries by risk and dependencies to avoid paying those costs "
        "for unnecessary splits.")


@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/necessary-boundaries-remain")
def test_drafter_keeps_task_boundaries_and_coverage(task_rules):
    for clause in (
        "Each task is a single session's work: about 300 changed lines at most, one concern.",
        "Split when distinct concerns, dependencies or the ceiling require it",
        "Each task names the scenario IDs it covers, in backticks, as "
        "`capability/requirement-slug/scenario-slug`. A slug is the name lowercased, "
        "with every run of other characters replaced by `-`.",
        "Every scenario must be covered by at least one task.",
        "Order tasks so each builds on landed work. Put tests with the code they test.",
        "Every task goes to an implementing agent working in this repository. A check "
        "only a person can make (real hardware, live services, how something looks) "
        "is never a task: describe it in the design, where it becomes the pull request's human checklist.",
    ):
        assert_clause(task_rules, clause)


@pytest.mark.scenario("spec-drafting/compact-documentation-plans/documentation-only-sizing")
def test_drafter_keeps_documentation_plans_compact(task_rules):
    assert_clause(task_rules,
        "For documentation-only changes (documentation updates without changing executable behavior), "
        "use one or two tasks in one section, subject to the same single-session task ceiling.")
    assert_clause(task_rules,
        "Split when distinct concerns, dependencies or the ceiling require it, not by document heading.")


@pytest.fixture
def guide():
    return (ROOT / "docs/specloop/README.md").read_text(encoding="utf-8")


@pytest.fixture
def drafting(guide):
    section = guide.split("**`propose`**", 1)[1]
    section = re.split(r"\n(?:\*\*|#{1,6} )", section, maxsplit=1)[0]
    return " ".join(section.split())


@pytest.mark.scenario("specloop-documentation/task-sizing-guidance/reader-sizes-a-plan")
def test_guide_explains_sizing_and_preserves_proposal_stages(drafting):
    for clause in (
        "Size tasks by risk and dependencies: combine work touching the same file for one concern "
        "within the single-session ceiling of about 300 changed lines.",
        "Split when distinct concerns, dependencies or that ceiling require it.",
        "Documentation-only changes update documentation without changing executable behavior; "
        "use one or two tasks in one section within that ceiling, rather than splitting by document heading.",
        "Most changes should use six tasks or fewer and as few sections as dependencies allow. "
        "Six is a normal target, not a hard rejection limit.",
        "Every task incurs a full implement, verify and review cycle; every section incurs an "
        "evidence gate, so unnecessary splits add overhead",
    ):
        assert clause in drafting
    stages = (
        "1. validates and lints it;",
        "2. runs the feasibility review;",
        "3. runs an independent read-only spec critic after lint and feasibility pass;",
        "4. feeds problems back within a default budget of four drafting rounds "
        "([PR #37](https://github.com/mattpolicastro/nightshift/pull/37));",
        "5. leaves the draft on a local `spec/<change>` branch for a human to approve.",
        "For daemon-managed issues, Nightshift pushes it and opens the spec review PR.",
    )
    positions = [drafting.index(stage) for stage in stages]
    assert positions == sorted(positions)


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/scope-and-regression-checks-remain-intact"
)
def test_sizing_guide_keeps_local_links_and_acceptance_instructions(guide):
    document = ROOT / "docs/specloop/README.md"
    local_links = []
    for _, target in LINK.findall(guide):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = document.parent / unquote(url.path) if url.path else document
        assert destination.exists(), f"Broken guide link: {target}"
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text(encoding="utf-8"))
        local_links.append(target)
    assert "../../examples/specloop/scenario_plugin.py" in local_links
    assert "Run `uv run pytest` for implementation acceptance." in guide
    assert "Failed tests contribute\nno passed IDs." in guide


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
def test_sizing_cites_tracking_issue_and_leaves_reader_checks_in_design(drafting):
    assert "unnecessary splits add overhead " \
        "([issue #75](https://github.com/mattpolicastro/nightshift/issues/75))." in drafting
    assert "https://github.com/mattpolicastro/nightshift/pull/75" not in drafting
    change = "nightshift-issue-75-drafting-size-tasks-by-risk-not-by-secti"
    # Keep working when the approved change is archived.
    designs = list((ROOT / "openspec/changes").glob(f"**/*{change}/design.md"))
    assert len(designs) == 1
    design = " ".join(designs[0].read_text(encoding="utf-8").split())
    checklist = design.split("Human checklist for the resulting PR and follow-up:", 1)[1]
    assert "existing live-reference/ledger check and Matt's single readability pass" in checklist
    assert "do not describe unlanded behavior as already shipped" in checklist
    implementation = tasks.parse(designs[0].with_name("tasks.md").read_text(encoding="utf-8"))
    assert implementation
    for task in implementation:
        assert not re.search(r"\bMatt\b|live-reference/ledger check|single readability pass", task.text)
