"""Structural checks for attention and retirement guidance; prose stays free."""

import re
import shlex
from pathlib import Path

import pytest

from nightshift import cli, config, outcomes
from test_factory_land_docs import links_resolve
from test_specloop_documentation_integrity import LINK, REPOSITORY, ROOT

HEADING = re.compile(r"^(#{1,6}) +\S.*$")


def section(path, heading):
    """A unique exact column-zero heading through the next same-or-higher heading or EOF."""
    lines = (ROOT / path).read_text().split("\n")
    starts = [i for i, line in enumerate(lines) if line == heading]
    assert len(starts) == 1, (path, heading, "missing/duplicate heading")
    start, = starts
    level = len(HEADING.match(heading)[1])
    end = next((i for i in range(start + 1, len(lines))
                if (match := HEADING.match(lines[i])) and len(match[1]) <= level), len(lines))
    passage = "\n".join(lines[start + 1:end])
    assert passage.strip(), (path, heading, "empty section")
    return passage


def table(passage, header):
    rows = [line for line in passage.splitlines() if line.startswith("|")]
    first = rows.index(f"| {header[0]} | {header[1]} |")
    body = []
    for row in rows[first + 2:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) != 2:
            break
        body.append(cells)
    return dict(body)


def test_section_blocks_missing_and_duplicate_boundaries(tmp_path, monkeypatch):
    import test_attention_docs as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "doc.md").write_text("## A\nbody\n### B\nnested\n## C\nrest\n## A\n")
    with pytest.raises(AssertionError, match="missing/duplicate"):
        section("doc.md", "## A")
    with pytest.raises(AssertionError, match="missing/duplicate"):
        section("doc.md", "## Z")
    with pytest.raises(AssertionError, match="missing/duplicate"):
        section("doc.md", " ## C")
    assert section("doc.md", "### B") == "nested"
    assert section("doc.md", "## C") == "rest"


@pytest.mark.scenario(
    "specloop-documentation/front-door-attention-guidance/operator-understands-the-attention-list"
)
def test_attention_guidance_structure_matches_code():
    readme = section("README.md", "### What needs you")
    page = section("README.md", "### Paseo attention page")
    refresh = section("plugins/nightshift/README.md", "## Attention refresh behavior")
    for path, passage in (("README.md", readme), ("README.md", page),
                          ("plugins/nightshift/README.md", refresh)):
        links_resolve(path, passage)
    assert any(target.endswith("#attention-refresh-behavior") for _, target in LINK.findall(page))
    limits = table(refresh, ("Attention limit", "Value"))
    assert limits == {
        "Shared GitHub budget seconds": str(outcomes.REFRESH_SECONDS),
        "Request seconds": str(outcomes.REQUEST_SECONDS),
        "Concurrent reads": str(outcomes.REFRESH_WORKERS),
        "Summary characters": str(outcomes.SUMMARY_LIMIT),
    }
    # Every documented attention invocation parses with the public CLI.
    usage = (ROOT / "README.md").read_text()
    commands = re.findall(r"^uv run nightshift (attention[^#\n]*?)\s*(?:#.*)?$", usage, re.M)
    assert commands
    for command in commands:
        args = cli.build_parser().parse_args(shlex.split(command))
        assert args.cmd == "attention"
    # The example's label section loads with the configured needs-human label.
    cfg = config.load(ROOT / "config.example.toml")
    assert cfg.labels.needs_human == config.Labels().needs_human
    guide = (ROOT / "docs/specloop/README.md").read_text()
    assert any(target == "../../README.md#what-needs-you" for _, target in LINK.findall(guide))
    links_resolve("docs/specloop/README.md", guide)


@pytest.mark.scenario(
    "specloop-documentation/worker-retirement-guidance/new-enrollment-follows-one-engine"
)
@pytest.mark.scenario(
    "specloop-documentation/worker-retirement-guidance/existing-installation-is-checked-before-merge"
)
def test_retirement_rollout_section_structure():
    rollout = section("docs/specloop/README.md", "### Retirement rollout")
    links_resolve("docs/specloop/README.md", rollout)
    targets = {target for _, target in LINK.findall(rollout)}
    assert {f"{REPOSITORY}/issues/16", f"{REPOSITORY}/issues/40"} <= targets
    assert f"{REPOSITORY}/pull/16" not in targets
    assert any(target.endswith("README.md#host-path-setup") for target in targets)
    checklist = [line for line in rollout.splitlines() if line.startswith("- [ ] ")]
    assert len(checklist) >= 7  # Pending human checks, never ticked by CI.
    assert not [line for line in rollout.splitlines() if line.startswith("- [x]")]
    cfg = config.load(ROOT / "config.example.toml")
    repo, = cfg.repos
    assert repo.engine == "specloop" and str(repo.manifest).strip()
    assert cfg.forge_for(repo).kind == "github"
