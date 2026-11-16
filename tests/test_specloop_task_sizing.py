"""Offline sizing contracts; diff review and live reader checks remain human."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from specloop import propose, tasks
from test_specloop_documentation_integrity import LINK, heading_ids
from test_specloop_propose import GOOD, M, drafter, source


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def task_rules():
    prompt = propose.PROMPT.format(
        change="size-a-plan", idea="Update related guidance", sources="- README.md",
        in_flight="", feedback="", drafter_root="openspec/changes/size-a-plan/",
    )
    section = prompt.split("- `tasks.md`", 1)[1].split("\n- Prefer behaviour", 1)[0]
    return [" ".join(rule.split()) for rule in section.split("\n  - ")[1:]]


def assert_clause(clauses, expected):
    assert any(expected in clause for clause in clauses), f"Missing clause: {expected}"


@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/related-work-shares-a-task")
def test_drafter_rules_size_tasks_by_risk(task_rules):
    assert_clause(task_rules,
        "Keep coupled production changes and their tests together, even across files.")
    assert_clause(task_rules,
        "Combine work touching the same file for one concern into one task. Split at independent behavior")
    assert_clause(task_rules,
        "Most changes should use six tasks or fewer and as few sections as dependencies allow. "
        "Six is a recommendation, not a target or a hard rejection limit: "
        "use fewer tasks when they suffice and never pad to six.")
    assert_clause(task_rules,
        "Every task incurs a full implement, verify and review cycle; every section incurs an "
        "evidence gate. Size boundaries by risk and dependencies to avoid paying those costs "
        "for unnecessary splits.")


@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/necessary-boundaries-remain")
def test_drafter_keeps_task_boundaries_and_coverage(task_rules):
    for clause in (
        "Each task is one coherent, independently verifiable concern that fits a single session.",
        "Split at independent behavior, migration or recovery boundaries, distinct verification "
        "strategies or substantial uncertainty, in dependency order",
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
        "use one or two tasks in one section, each coherent single-session work under the same soft guideline.")
    assert_clause(task_rules, "in dependency order, not by document heading.")


@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/cohesive-task-exceeds-old-ceiling")
@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/related-work-shares-a-task")
def test_cohesive_task_above_300_lines_is_not_rejected_for_size(source, tmp_path):
    body = ("## 1. Sign\n\n- [ ] 1.1 Add sign in `calc.py` with its tests and README section, one concern "
            "estimated at 450 changed lines (production, tests and documentation together), "
            "covering `calc/sign/zero` and `calc/sign/negative`\n")
    agent = drafter(body)
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    writes = [c for c in agent.calls if c.access == "write"]
    reviews = [c for c in agent.calls if c.access == "read"]
    assert len(writes) == 1 and len(reviews) == 2
    assert d.commit and not d.problems and not d.contradictions and not d.risks
    prompt = " ".join(writes[0].prompt.split())
    assert "About 600 changed lines per task, counting production code, tests and documentation together, " \
        "is a soft planning guideline, never a target or an automatic rejection threshold." in prompt
    assert not re.search(r"\d+ changed lines at most", prompt)


@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/necessary-boundaries-remain")
@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/above-guideline-retains-rationale")
@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/related-work-shares-a-task")
@pytest.mark.parametrize("fmt", ["openspec", "speckit"])
def test_sizing_guidance_preserves_risk_boundaries_and_session_limits(source, tmp_path, fmt):
    template, design = (propose.SPECKIT_PROMPT, "plan.md") if fmt == "speckit" else (propose.PROMPT, "design.md")
    prompt = " ".join(template.format(change="c", drafter_root="root/", idea="i", sources="",
                                      in_flight="", feedback="").split())
    for clause in (
        "Split at independent behavior, migration or recovery boundaries, distinct verification "
        "strategies or substantial uncertainty",
        "Above roughly 600 changed lines prefer a safe split; if splitting would break cohesion, keep the task "
        f"and record its boundary and verification rationale in {design}.",
        "Estimated lines or task count alone never require another issue or pull request.",
        "use fewer tasks when they suffice", "never pad", "single session", "order",
        "review cycle", "evidence gate",
    ):
        assert clause in prompt, clause
    assert not re.search(r"\d+ changed lines at most|within (?:that|the) ceiling", prompt)
    # Execution budgets are untouched: manifest limits for drafting, 20 minutes for each review.
    agent = drafter(GOOD)
    assert propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent).commit
    for call in agent.calls:
        wall = M.implement_minutes * 60 if call.access == "write" else 20 * 60
        assert (call.wall_seconds, call.idle_seconds) == (wall, M.idle_minutes * 60)


@pytest.fixture
def guide():
    return (ROOT / "docs/specloop/README.md").read_text(encoding="utf-8")


@pytest.fixture
def drafting(guide):
    section = guide.split("**`propose`**", 1)[1]
    section = re.split(r"\n(?:\*\*|#{1,6} )", section, maxsplit=1)[0]
    return " ".join(section.split())


SIZE = re.compile(r"(\d[\d,]*) changed lines")
ISSUE = "https://github.com/mattpolicastro/nightshift/issues/{}"


def sizes(text):
    return set(SIZE.findall(" ".join(text.split())))


@pytest.mark.scenario("specloop-documentation/task-sizing-guidance/reader-sizes-a-plan")
def test_guide_explains_sizing_and_preserves_proposal_stages(drafting):
    # The documented line guideline is the one the drafter actually receives.
    assert sizes(drafting) == sizes(propose.PROMPT) == {"600"}
    links = {target for _, target in LINK.findall(drafting)}
    assert {ISSUE.format(200), ISSUE.format(80), ISSUE.format(75), "#task-sizing-observation"} <= links
    for clause in (
        "Size tasks by cohesion, verification and risk: each task is one coherent, "
        "independently verifiable concern for a single session.",
        "keep coupled production changes with their tests even across files.",
        "Split at independent behavior, migration or recovery boundaries, distinct verification "
        "strategies or substantial uncertainty, keeping dependency order.",
        "record the boundary and verification rationale in `design.md`.",
        "Neither estimated lines nor task count alone requires another issue or PR",
        "Documentation-only changes update documentation without changing executable behavior; "
        "use one or two tasks in one section, rather than splitting by document heading.",
        "Most changes should use six tasks or fewer and as few sections as dependencies allow. "
        "Six is a recommendation, not a target or a hard rejection limit: fewer is fine, "
        "and plans are never padded to six.",
        "Every task incurs a full implement, verify and review cycle; every section incurs an "
        "evidence gate, so unnecessary splits add overhead",
    ):
        assert clause in drafting, clause
    assert not re.search(r"\d+ changed lines at most|within (?:that|the) (?:single-session )?ceiling", drafting)
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


@pytest.mark.scenario("specloop-documentation/task-sizing-guidance/reader-distinguishes-guidance-from-limits")
def test_operator_docs_match_advisory_size_contract(guide):
    import test_speckit_build_docs as doc
    from nightshift.specloop_job import COMMAND_TIMEOUT_SECONDS
    from specloop import critic

    expected = sizes(propose.PROMPT) | sizes(propose.SPECKIT_PROMPT) | sizes(critic.PROMPT) \
        | sizes(critic.SPECKIT_PROMPT)
    assert expected == {"600"}
    for path, heading in ((doc.GUIDE, "### OpenSpec drafting"), (doc.GUIDE, "#### Draft and admit"),
                          (doc.HOUSE, "## What the drafter produces")):
        assert sizes(doc.passage(doc.read(path), heading)) == expected, (path, heading)
    # No scoped operator document keeps a stale per-task line figure.
    for path in ("README.md", "KNOWN-LIMITATIONS.md", doc.GUIDE, doc.HOUSE):
        assert sizes(doc.read(path)) <= expected, path
    # Documented reporting deadlines still match the daemon's budgets.
    table = dict(re.findall(r"^\| `(\w+)` \| (\d+) \|$", guide, re.M))
    assert {name: int(table[name]) for name in COMMAND_TIMEOUT_SECONDS} == COMMAND_TIMEOUT_SECONDS
