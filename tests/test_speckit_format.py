from pathlib import Path
import re

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "speckit"
UPSTREAM = FIXTURE.parent / "speckit-upstream" / "v1.3.2"


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-spec-and-plan-inputs-locally"
)
@pytest.mark.parametrize("name", ["spec-template.md", "plan-template.md"])
def test_spec_and_plan_templates_match_local_source(name):
    assert (FIXTURE / ".specify/templates" / name).read_bytes() == (
        UPSTREAM / "templates" / name
    ).read_bytes()


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-spec-and-plan-inputs-locally"
)
def test_fixture_license_matches_local_source():
    assert (FIXTURE / "LICENSE").read_bytes() == (UPSTREAM / "LICENSE").read_bytes()


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-spec-and-plan-inputs-locally"
)
def test_fixture_attribution_links_resolve_locally():
    readme = (FIXTURE / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
    assert links
    for target in links:
        assert (FIXTURE / target).exists(), target


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-task-input-locally"
)
def test_tasks_template_matches_local_source_with_synthetic_constitution():
    assert (FIXTURE / ".specify/templates/tasks-template.md").read_bytes() == (
        UPSTREAM / "templates/tasks-template.md"
    ).read_bytes()
    constitution = FIXTURE / ".specify/memory/constitution.md"
    assert constitution.is_file()
    assert constitution.read_text(encoding="utf-8").strip()
