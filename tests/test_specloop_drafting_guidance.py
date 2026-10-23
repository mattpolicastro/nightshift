"""Reader-facing contracts for the guide's spec drafting instructions."""

import re
from pathlib import Path

import pytest


@pytest.fixture
def drafting_sections():
    guide = (Path(__file__).resolve().parents[1] / "docs/specloop/README.md").read_text(
        encoding="utf-8"
    )
    sections = {}
    for heading in ("`propose`", "Work in flight.", "Feasible tasks."):
        section = guide.split(f"**{heading}**", 1)[1]
        section = re.split(r"\n(?:\*\*|#{1,6} )", section, maxsplit=1)[0]
        # Check claims where readers encounter them, ignoring line wrapping.
        sections[heading] = " ".join(section.split())
    return sections


@pytest.mark.scenario(
    "specloop-documentation/spec-drafting-guidance/concurrent-files-inform-a-draft"
)
def test_guide_explains_concurrent_file_scope_and_drafting_advice(drafting_sections):
    section = drafting_sections["Work in flight."]
    claims = (
        "Before drafting, the harness snapshots paths named in `tasks.md` by other "
        "running or paused runs for the same source repository",
        "It excludes the current change and skips unreadable runs without stopping the draft",
        "This is a snapshot of planned work, not a file reservation or a complete list of future edits",
        "References such as `file.py:function` and `file.py::function` normalize to `file.py`",
        "source-root names such as `README.md` count when they are existing files in the source checkout",
        "The drafter sees the map and should avoid those files where possible, putting new tests in new files",
        "On the first drafting round, tasks that edit shared test files get overlap feedback",
        "Explicit hands-off clauses, such as “do not edit `tests/test_calc.py`”, do not count as edits",
        "Necessary overlap may remain as a merge-conflict risk rather than an unconditional ban",
        "Implementers do not receive the map: any necessary avoidance instruction must name each "
        "file to leave untouched in the draft",
        "for example “do not edit `tests/test_calc.py`”, rather than referring to “files in the in-flight list”",
    )
    for claim in claims:
        assert claim in section, f"Missing concurrent-file guidance: {claim}"
    for number in (36, 37, 43, 60):
        assert f"[PR #{number}](https://github.com/mattpolicastro/nightshift/pull/{number})" in section
    assert "[issue #58](https://github.com/mattpolicastro/nightshift/issues/58)" in section


@pytest.mark.scenario(
    "specloop-documentation/spec-drafting-guidance/drafting-rules-preserve-feasible-tasks"
)
def test_guide_keeps_human_checks_and_changed_guarantees_explicit(drafting_sections):
    section = drafting_sections["Feasible tasks."]
    claims = (
        "Human-only checks belong in `design.md`, where they become the PR's human checklist; "
        "every task in `tasks.md` goes to an implementing agent",
        "Lint catches common human-task forms such as “Manually …” or “The operator …”",
        "Feasibility also rejects named-person checks such as “Matt checks …” and tasks that are "
        "impossible under the allowed paths, frozen files or trusted verify commands",
        "Preserve existing `openspec/specs/` guarantees: feasibility rejects a change that removes, "
        "hides or breaks one without a justified `MODIFIED` or `REMOVED` delta for that requirement",
    )
    for claim in claims:
        assert claim in section, f"Missing feasibility guidance: {claim}"
    for number in (30, 36):
        assert f"[PR #{number}](https://github.com/mattpolicastro/nightshift/pull/{number})" in section

    proposal = drafting_sections["`propose`"]
    assert "feeds problems back within a default budget of four drafting rounds" in proposal
    assert "[PR #37](https://github.com/mattpolicastro/nightshift/pull/37)" in proposal
