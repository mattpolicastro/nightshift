"""Front-door PRs: readiness reads human checks from the publication, not the spec PR body."""

import pytest

from nightshift import specloop_intake as intake

UNKNOWN = ["human checks: unknown checklist or scenario annotation"]
SPEC_BODY = "Closes #7\n\nFeasibility risks:\n- Timing needs a human check."
ROW = {"change": "c", "head": "a" * 40, "pr": "https://github.com/o/r/pull/1"}


@pytest.mark.scenario("merge-readiness/human-check-source/front-door-publication-supplies-the-checklist")
@pytest.mark.parametrize("checks", [
    "all tasks landed at 590836149be4; human checks after publishing:",
    "all tasks landed at 590836149be4; human checks after publishing:\n\nNext: `specloop publish c`",
    "(none)",
    "",
])
def test_front_door_publication_without_checks_passes(checks):
    assert intake._readiness_human_checks(ROW, SPEC_BODY, publication_checks=checks) == []


@pytest.mark.scenario("merge-readiness/human-and-revision-conditions/pre-merge-or-unknown-checks-block")
@pytest.mark.parametrize("checks", [
    "all tasks landed at 59083614; human checks after publishing:\nsomething unexpected",
    "all tasks landed at 59083614; human checks after publishing:\n- [ ] no citation here",
])
def test_front_door_publication_with_unknown_content_blocks(checks):
    assert intake._readiness_human_checks(ROW, SPEC_BODY, publication_checks=checks) == UNKNOWN


def test_without_body_list_or_publication_checks_blocks():
    assert intake._readiness_human_checks(ROW, SPEC_BODY) == UNKNOWN


def test_body_checklist_still_preferred():
    body = "## Checks a human must make\n\n(none)\n\n## Non-blocking review findings\n"
    assert intake._readiness_human_checks(ROW, body, publication_checks="- [ ] `a/b/c`: x") == []
