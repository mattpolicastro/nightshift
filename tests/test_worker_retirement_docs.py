"""Validate enrollment examples and citation targets without freezing prose."""

import re
import tomllib

import pytest

from nightshift import config
from test_specloop_documentation_integrity import DOCS, LINK, REPOSITORY, ROOT


@pytest.mark.scenario(
    "specloop-documentation/worker-retirement-guidance/new-enrollment-follows-one-engine"
)
@pytest.mark.parametrize("path", ("README.md", "docs/specloop/README.md"))
def test_enrollment_examples_load_and_reject_worker(path, tmp_path):
    # Literal raw TOML lines, wherever they occur; duplicate keys/tables fail.
    lines = re.findall(r'^\[\[repos\]\]$|^(?:name|engine|manifest|base) = [^\r\n]+$',
                       (ROOT / path).read_text(), re.M)
    example = "\n".join(lines) + "\n"
    row, = tomllib.loads(example)["repos"]
    assert set(row) == {"name", "engine", "manifest", "base"}
    selected = tmp_path / "config.toml"
    for engine in ('engine = "specloop"\n', ""):
        selected.write_text(example.replace('engine = "specloop"\n', engine))
        repo, = config.load(selected).repos
        assert repo.engine == "specloop" and str(repo.manifest) == row["manifest"]
        assert repo.verify == "" and repo.base == row["base"]
    selected.write_text(example.replace('engine = "specloop"', 'engine = "worker"'))
    with pytest.raises(ValueError, match="worker engine is retired"):
        config.load(selected)


@pytest.mark.parametrize("path", ("README.md", "docs/specloop/README.md"))
@pytest.mark.parametrize("references", [
    pytest.param(("issues/24", "pull/29", "issues/5", "pull/42"), marks=pytest.mark.scenario(
        "specloop-documentation/front-door-guidance/dependencies-and-label-actors-gate-work")),
    pytest.param(("issues/6", "pull/61"), marks=pytest.mark.scenario(
        "specloop-documentation/front-door-guidance/revision-feedback-survives-a-long-round")),
    pytest.param(("issues/12", "pull/35", "issues/10", "pull/32"), marks=pytest.mark.scenario(
        "specloop-documentation/front-door-guidance/input-and-status-remain-usable")),
])
def test_preserved_front_door_citations(path, references):
    targets = {target for _, target in LINK.findall((ROOT / path).read_text())}
    assert {f"{REPOSITORY}/{reference}" for reference in references} <= targets


@pytest.mark.scenario(
    "specloop-documentation/worker-retirement-guidance/existing-installation-is-checked-before-merge"
)
@pytest.mark.parametrize("path", DOCS)
def test_retirement_references_and_layout(path):
    targets = {target for _, target in LINK.findall((ROOT / path).read_text())}
    assert f"{REPOSITORY}/issues/16" in targets
    assert f"{REPOSITORY}/pull/16" not in targets
    if path != "README.md":
        assert f"{REPOSITORY}/issues/40" in targets
    for line in (ROOT / "README.md").read_text().splitlines():
        if line.startswith("| `") and " | " in line:
            for name in re.findall(r"`([^`]+\.py)`", line.split(" | ")[0]):
                assert (ROOT / "nightshift" / name).is_file()
