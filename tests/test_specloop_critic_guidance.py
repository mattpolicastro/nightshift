"""Reader-facing contracts for the guide's spec critic section."""

import re
from pathlib import Path

import pytest


@pytest.fixture
def critic_section():
    guide = (Path(__file__).resolve().parents[1] / "docs/specloop/README.md").read_text(
        encoding="utf-8"
    )
    # Scope assertions to the guidance a reader sees, not incidental mentions
    # elsewhere in the guide. Ignore Markdown line wrapping in prose checks.
    section = guide.split("**Spec critic.**", 1)[1]
    return re.split(r"\n(?:\*\*|#{1,6} )", section, maxsplit=1)[0]


@pytest.mark.scenario(
    "specloop-documentation/spec-critic-guidance/critic-checklist-is-complete"
)
def test_critic_guide_enumerates_all_eight_checks(critic_section):
    items = re.findall(r"^\d+\. (.+?)\.?$", critic_section, flags=re.MULTILINE)
    assert items == [
        "Over-engineering",
        "File collisions and hidden instructions",
        "Invalidated protected assertions",
        "Conflicts with unowned behavior",
        "Tests broken by pending changes",
        "Unplanned migrations",
        "Retry and idempotency gaps",
        "Stale operator docs and manual steps",
    ]
    assert "[PR #62](https://github.com/mattpolicastro/nightshift/pull/62)" in critic_section


@pytest.mark.scenario(
    "specloop-documentation/spec-critic-guidance/critic-outcome-is-actionable"
)
def test_critic_guide_explains_context_correction_and_failure(critic_section):
    prose = " ".join(critic_section.split())
    # Keep qualifications with each claim: mentioning "overlap", "safety" or
    # "pass" alone would also accept the opposite, unsafe operator advice.
    guidance = (
        "Proposals and spec revisions receive a separate read-only review after lint and feasibility pass",
        "receives the full in-flight file map, including production and test paths, "
        "and changed files from open PRs",
        "These are snapshots, not proof of another change's future behavior",
        "Unavailable or partial PR context stays visible as a risk; missing context does not establish safety",
        "Blocking findings get at most one correction within the remaining drafting budget",
        "followed by lint, feasibility and critic review again",
        "Remaining blockers are contradictions naming the task, problem and evidence",
        "exhausted budgets and unchanged corrections leave them unresolved",
        "Non-blocking notes appear as `Critic: ` risks",
        "Overlap alone is not a blocker: the critic must explain why the plan is unsafe or unworkable",
        "A failed critic review stops the draft with an error; it is not a pass",
    )
    for claim in guidance:
        assert claim in prose, f"Missing critic guidance: {claim}"
    assert "[issue #51](https://github.com/mattpolicastro/nightshift/issues/51)" in critic_section
    assert "[PR #57](https://github.com/mattpolicastro/nightshift/pull/57)" in critic_section
