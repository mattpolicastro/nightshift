"""Physical document structure and parser contracts; prose stays reviewable.

Reuse the publication, limitations and recovery regressions without replacing
their assertions. These checks neither dispatch operator commands nor prove
live permissions, protection behavior or a real merge.
"""

import re
from urllib.parse import urlsplit

import pytest

from doc_commands import argv_cases, physical_commands
from nightshift import config, land
from test_factory_land_docs import HEADING, links_resolve, section
from test_forge_publication_docs import commands_parse
from test_specloop_documentation_integrity import LINK, REPOSITORY, ROOT
from test_unified_command_docs import check_commands


GUIDE = "docs/landing.md"
BOUNDARY = "- **GitHub-only command boundaries:**"


def command_boundaries(document):
    # Physical LF-separated lines only: no Markdown, HTML, fence or indentation
    # semantics. The terminator belongs to the following entry, never this one.
    lines = document.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith(BOUNDARY)]
    assert len(starts) == 1, "missing/duplicate command-boundaries bullet"
    start, = starts
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("- ") or HEADING.fullmatch(lines[i])), len(lines))
    return "\n".join(lines[start:end])


@pytest.mark.scenario("gitea-landing/gitea-operator-guidance/operator-can-prepare-and-recover")
@pytest.mark.scenario("reviewed-pr-landing/landing-interpreter-prerequisites/operator-prepares-the-environment")
@pytest.mark.parametrize("title", [
    "Prerequisites and flags", "Gitea setup and credentials",
    "Separate finish provisioning", "Support boundaries",
    "Check sources and attestation", "Finish, CI, and merge",
    "Gitea update, exact-head CI, and merge requests",
    "Recovering from a refusal", "Remaining human validation",
])
def test_required_guide_sections_and_links(title):
    selected = section((ROOT / GUIDE).read_text(), title)
    assert selected.strip()
    links_resolve(GUIDE, selected)


@pytest.mark.scenario("gitea-landing/gitea-operator-guidance/operator-can-prepare-and-recover")
@pytest.mark.scenario("reviewed-pr-landing/landing-interpreter-prerequisites/operator-prepares-the-environment")
def test_illustrative_commands_and_config_parse(tmp_path, monkeypatch, pytestconfig):
    def forbidden(*args, **kwargs):
        pytest.fail("documentation validation dispatched an operation")

    monkeypatch.setattr(land, "main", forbidden)
    check_commands(GUIDE, pytestconfig)
    setup = section((ROOT / GUIDE).read_text(), "Gitea setup and credentials")
    fragment, = re.findall(r"^```toml\n(.*?)^```$", setup, re.M | re.S)
    path = tmp_path / "config.toml"
    path.write_text(fragment)
    cfg = config.load(path)
    definition, = cfg.forges
    repo, = cfg.repos
    assert definition.kind == "gitea" and repo.engine == "specloop"
    assert cfg.forge_for(repo) == definition
    assert definition.land_token_env != definition.token_env
    assert definition.bot_login in definition.trusted_logins
    assert set(definition.trusted_logins) - {definition.bot_login}

    # Parse only the documented dummy assignment, never the operator's file.
    assignments = re.findall(r"^export [^\n]+$", setup, re.M)
    assert len(assignments) == 1
    env_path = tmp_path / "land.env"
    env_path.write_text("\n".join(assignments))
    values = config.parse_env_file(env_path)
    assert set(values) == {definition.land_token_env}
    assert values[definition.land_token_env]

    operations = land.Operations(forbidden, forbidden, lambda: 0)
    examples = [argv for _, command in physical_commands(setup)
                for argv in argv_cases(command)]
    assert len(examples) >= 2
    for argv in examples:
        assert argv[:3] == ["python", "-m", "nightshift.land"]
        args = land.parser().parse_args(argv[3:])
        identity, selected = land.resolve_target(args.pr, args.repo, cfg, operations)
        assert selected == definition and identity.repo == repo.name
        assert args.checks_done and args.timeout > 0
        assert urlsplit(identity.url).hostname == urlsplit(definition.url).hostname


@pytest.mark.scenario("forge-interface/module-layout-guidance/layout-describes-staged-boundary")
@pytest.mark.scenario("specloop-documentation/explicit-forge-command-guidance/explicit-commands-do-not-enable-the-daemon")
def test_current_guide_links_historical_guides_and_support_boundary():
    guide = (ROOT / GUIDE).read_text()
    targets = {target for _, target in LINK.findall(section(guide, "Landing a reviewed PR"))}
    assert {"../README.md#layout", "specloop/README.md#explicit-forge-publication"} <= targets
    boundary = section(guide, "Support boundaries")
    assert "../README.md#layout" in {target for _, target in LINK.findall(boundary)}
    links_resolve(GUIDE, guide)


@pytest.mark.scenario("specloop-documentation/explicit-forge-command-guidance/operator-selects-and-publishes")
def test_finish_provisioning_links_existing_publication_commands():
    provision = section((ROOT / GUIDE).read_text(), "Separate finish provisioning")
    target = "specloop/README.md#explicit-forge-publication"
    assert target in {url for _, url in LINK.findall(provision)}
    document = (ROOT / GUIDE).parent / target.split("#")[0]
    examples = commands_parse(section(document.read_text(), "Explicit forge publication"))
    assert {args.cmd for args in examples} == {"publish", "finish"}
    assert all(args.change == "example-change" for args in examples)


@pytest.mark.scenario("specloop-documentation/current-limitations/fixed-limitations-leave-the-open-list")
@pytest.mark.scenario("specloop-documentation/current-limitations/remaining-ledger-gaps-are-explicit")
@pytest.mark.scenario("specloop-documentation/current-limitations/landing-correction-preserves-unresolved-boundaries")
def test_unique_limitations_boundary_links_landing_and_tracking():
    document = (ROOT / "KNOWN-LIMITATIONS.md").read_text()
    selected = command_boundaries(document)
    links_resolve("KNOWN-LIMITATIONS.md", selected)
    targets = {target for _, target in LINK.findall(selected)}
    assert GUIDE in targets
    # No implementation PR has been verified; #130 is the approved reference.
    assert f"{REPOSITORY}/issues/130" in targets
    assert f"{REPOSITORY}/issues/150" in targets
    assert f"{REPOSITORY}/issues/17" in {target for _, target in LINK.findall(document)}


@pytest.mark.scenario("gitea-landing/gitea-operator-guidance/operator-can-prepare-and-recover")
@pytest.mark.parametrize("wrapper", ["{}", "```md\n{}\n```", "<!--\n{}\n-->"])
@pytest.mark.parametrize("terminator", ["## Next", "# Next", "##   Next", "###### Next"])
def test_heading_selection_uses_physical_levels(wrapper, terminator):
    # A deeper heading is included; the next equal/shallower one terminates.
    level = len(terminator.split()[0])
    heading = "#" * level + "   Selected"
    nested = "#" * (level + 1) + " Child"
    body = "\nbody\n" + nested + "\nchild\n    # Indented\n#No-space\n"
    assert section(wrapper.format(heading + body + terminator + "\noutside"), "Selected") == body


@pytest.mark.scenario("gitea-landing/gitea-operator-guidance/operator-can-prepare-and-recover")
@pytest.mark.parametrize("document", [
    "", "    ## Selected\nbody", "##Selected\nbody", "####### Selected\nbody",
    "## Selected\n## Selected", "## Selected\n```md\n## Selected\n```",
    "## Selected\n<!--\n### Selected\n-->", "prefix\u2028## Selected\nbody",
])
def test_missing_or_duplicate_guide_sections_refuse(document):
    with pytest.raises(AssertionError):
        section(document, "Selected")


@pytest.mark.scenario("specloop-documentation/current-limitations/landing-correction-preserves-unresolved-boundaries")
@pytest.mark.parametrize("wrapper", ["{}", "```md\n{}\n```", "<!--\n{}\n-->"])
@pytest.mark.parametrize("terminator", ["- Next", "# Next", "######   Next"])
def test_bullet_terminates_on_physical_bullet_or_heading(wrapper, terminator):
    selected = BOUNDARY + " support\n  continued\n  - Nested\n  ## Indented\n#No-space\n####### Invalid"
    text = wrapper.format(selected + "\n" + terminator + "\noutside")
    assert command_boundaries(text) == selected


@pytest.mark.scenario("specloop-documentation/current-limitations/landing-correction-preserves-unresolved-boundaries")
def test_bullet_eof_and_non_line_separators():
    selected = BOUNDARY + " support\n  body\u2028- not a physical bullet"
    assert command_boundaries(selected) == selected
    assert command_boundaries("prefix\u2028" + BOUNDARY + "\n" + selected) == selected


@pytest.mark.scenario("specloop-documentation/current-limitations/landing-correction-preserves-unresolved-boundaries")
@pytest.mark.parametrize("document", [
    "", "  " + BOUNDARY, "prefix\u2028" + BOUNDARY,
    BOUNDARY + "\n" + BOUNDARY,
    BOUNDARY + "\n- Next\n" + BOUNDARY,
    BOUNDARY + "\n## Next\n<!--\n" + BOUNDARY + "\n-->",
    BOUNDARY + "\n```md\n" + BOUNDARY + "\n```",
])
def test_missing_or_duplicate_command_boundaries_refuse(document):
    with pytest.raises(AssertionError):
        command_boundaries(document)
