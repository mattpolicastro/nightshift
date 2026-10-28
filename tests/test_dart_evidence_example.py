"""Structural guide checks only; prose and real Dart usefulness need human review."""

import re
import tomllib
from pathlib import Path

import pytest

from specloop import evidence, manifest, verify


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/specloop/README.md"
EXAMPLE = ROOT / "examples/specloop/dart.toml"
HEADING = "### Section evidence"
DART_LINK = "[Dart manifest](../../examples/specloop/dart.toml)"


def evidence_passage(text):
    # Physical lines only: Markdown fences, HTML and indentation have no state.
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line == HEADING]
    assert len(starts) == 1, "missing or duplicate evidence heading"
    start, = starts
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^#{1,3} ", lines[i])), len(lines))
    return "\n".join(lines[start + 1:end])


def dart_target(text):
    passage = evidence_passage(text)
    assert passage.splitlines().count(DART_LINK) == 1
    assert passage.count(DART_LINK) == 1
    target = re.fullmatch(r"\[Dart manifest\]\(([^)]+)\)", DART_LINK).group(1)
    return (GUIDE.parent / target).resolve()


@pytest.mark.scenario("specloop-documentation/native-evidence-example/guidance-states-operational-limits")
def test_dart_link_resolves_in_physical_evidence_passage():
    assert dart_target(GUIDE.read_text()) == EXAMPLE
    assert EXAMPLE.is_file()


@pytest.mark.scenario("specloop-documentation/native-evidence-example/guidance-states-operational-limits")
@pytest.mark.parametrize("wrapper", ["{}", "```md\n{}\n```", "<!--\n{}\n-->", "    text\n{}"])
def test_missing_and_duplicate_physical_starts_rejected(wrapper):
    for invalid in ("", " " + HEADING, HEADING + " ", "#### Section evidence",
                    HEADING + "\n" + wrapper.format(HEADING)):
        with pytest.raises(AssertionError):
            evidence_passage(invalid)


@pytest.mark.scenario("specloop-documentation/native-evidence-example/guidance-states-operational-limits")
@pytest.mark.parametrize("prefix", ["", "```md\n", "<!--\n", "    text\n"])
@pytest.mark.parametrize("heading", ["# Next", "## Next", "### Next"])
def test_next_physical_heading_ends_passage_even_inside_markup(prefix, heading):
    text = HEADING + "\n" + prefix + heading + "\n" + DART_LINK
    assert evidence_passage(text) == prefix.rstrip("\n")
    with pytest.raises(AssertionError):
        dart_target(text)


@pytest.mark.scenario("specloop-documentation/native-evidence-example/guidance-states-operational-limits")
@pytest.mark.parametrize("nonboundary", ["    ### Next", "\t# Next", "#### Next", "###\tNext"])
def test_nonheadings_remain_in_passage_through_eof(nonboundary):
    content = nonboundary + "\n" + DART_LINK
    assert evidence_passage(HEADING + "\n" + content) == content
    assert dart_target(HEADING + "\n" + content) == EXAMPLE


@pytest.mark.scenario("specloop-documentation/native-evidence-example/guidance-states-operational-limits")
@pytest.mark.parametrize("links", ["", " " + DART_LINK, DART_LINK + " text",
                                  DART_LINK + "\n" + DART_LINK,
                                  DART_LINK + "\n<!--\n" + DART_LINK + "\n-->"])
def test_standalone_link_is_required_exactly_once(links):
    with pytest.raises(AssertionError):
        dart_target(HEADING + "\n" + links)


@pytest.mark.scenario("specloop-documentation/native-evidence-example/dart-configuration-is-structurally-valid")
def test_real_parser_resolves_dart_steps_and_expands_argv_without_execution():
    m = manifest.load(EXAMPLE)
    assert m.evidence and m.mutation_paths == ("lib/**",)
    assert m.mutation_steps == ("dart-analyze", "dart-tests")
    assert m.mutation_invalid_steps == ("dart-analyze",)
    route, steps = evidence.resolve_mutation_steps("lib/engine.dart", m)
    assert route is None and steps == m.verify
    assert tuple(s.id for s in steps) == m.mutation_steps
    assert not m.coverage_step and not m.coverage_exempt
    assert str(m.repo) == "/path/to/dart-project"
    assert m.vars == {"dart_sdk": "/path/to/dart-sdk"}
    values = {**m.vars, "src": "/source with spaces", "build": "/build with spaces"}
    expected = [
        ("dart-dependencies", 600,
         'cp -R "$1/." "$2" && chmod -R u+w "$2" && cd "$2" && "$3" pub get --enforce-lockfile',
         ["dart-prepare", values["src"], values["build"] + "/project", "/path/to/dart-sdk/bin/dart"]),
        ("dart-analyze", 300, 'cd "$1" && exec "$2" analyze',
         ["dart-analyze", values["build"] + "/project", "/path/to/dart-sdk/bin/dart"]),
        ("dart-tests", 900, 'cd "$1" && exec "$2" test',
         ["dart-tests", values["build"] + "/project", "/path/to/dart-sdk/bin/dart"]),
    ]
    assert len(m.prepare) == 1
    for step, (step_id, timeout, script, args) in zip(m.prepare + steps, expected, strict=True):
        assert (step.id, step.expect, step.timeout) == (step_id, 0, timeout)
        assert [verify.expand(arg, values) for arg in step.run] == ["/bin/sh", "-c", script, *args]
        assert not re.search(r"\{[^}]+\}", step.run[2])  # paths stay shell argv
    placeholders = {name for step in m.prepare + steps for arg in step.run
                    for name in re.findall(r"\{([^}]+)\}", arg)}
    assert placeholders == {"src", "build", "dart_sdk"}


@pytest.mark.scenario("specloop-documentation/mutation-evidence-guidance/parallel-and-timed-out-mutants-are-interpreted")
@pytest.mark.scenario("specloop-documentation/mutation-evidence-guidance/exclusions-do-not-fabricate-evidence")
def test_guide_gate_example_parses_with_real_defaults():
    blocks = re.findall(r"```toml\n(.*?)```", evidence_passage(GUIDE.read_text()), re.DOTALL)
    gates, = [tomllib.loads(block)["gates"] for block in blocks
              if "mutation_skip" in tomllib.loads(block).get("gates", {})]
    data = tomllib.loads(EXAMPLE.read_text())
    data["gates"] = gates
    m = manifest.parse(data)
    assert m.mutation_parallel == manifest.load(EXAMPLE).mutation_parallel == 2
    assert m.mutation_invalid_steps == evidence.BUILD_STEPS == ("configure", "build")
    assert m.mutation_skip == ("fontWeight", "# cosmetic")


@pytest.mark.scenario("specloop-documentation/mutation-evidence-guidance/guard-clauses-produce-candidates")
def test_guide_python_guard_example_generates_both_operators():
    source, = re.findall(r"```python\n(.*?)```", evidence_passage(GUIDE.read_text()), re.DOTALL)
    lines = [("example.py", n, line) for n, line in enumerate(source.splitlines(), 1)]
    mutants = evidence.generate(lines, 100, 0, sources={"example.py": source})
    assert {m.op for m in mutants} == {"negate-condition", "drop-return"}
    assert len(mutants) == 3
