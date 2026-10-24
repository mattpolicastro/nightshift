"""The drafter specifies line rules, not semantic parsing, for rich-text structure."""

import pytest

from specloop import propose


@pytest.mark.scenario("spec-drafting/rich-text-line-rules/structure-is-specified-by-line-rules")
def test_drafter_requires_line_rules_for_rich_text():
    flat = " ".join(propose.PROMPT.split())
    assert "specify a deterministic line rule" in flat
    assert "Never require semantic parsing of fences, HTML blocks or indentation" in flat
