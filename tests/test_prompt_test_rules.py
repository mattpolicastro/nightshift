"""Implementer and reviewer rules about which tests are protected and how docs are tested."""

import pytest

from specloop import prompts


@pytest.mark.scenario("agent-test-rules/existing-tests/tests-from-earlier-tasks-may-evolve")
def test_existing_means_predating_the_change():
    for prompt in (prompts.IMPLEMENT, prompts.REVIEW):
        flat = " ".join(prompt.split())
        assert "predates this change" in flat
    assert "earlier tasks of this same change may be updated" in " ".join(prompts.IMPLEMENT.split())


@pytest.mark.scenario("agent-test-rules/documentation-tests/docs-are-tested-by-structure")
def test_docs_are_tested_by_structure_not_wording():
    flat = " ".join(prompts.IMPLEMENT.split())
    assert "Test documentation by structure, never by wording" in flat
    assert "Never assert that a document contains particular phrases" in flat
