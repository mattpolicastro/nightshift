import tomllib
from pathlib import Path

import pytest

from specloop import manifest
from test_specloop_units import MANIFEST

START, END = "<!-- step-env-example:start -->", "<!-- step-env-example:end -->"
GUIDE = Path(__file__).resolve().parents[1] / "docs/specloop/README.md"
KINDS = ("prepare", "verify", "acceptance", "mutation_step")
pytestmark = pytest.mark.scenario(
    "specloop-documentation/step-environment-guidance/operator-configures-step-environments"
)


def step_env_example(text):
    lines = text.splitlines()
    for marker in (START, END):
        # Count globally, including marker lines inside Markdown/HTML contexts.
        assert lines.count(marker) == sum(line.strip() == marker for line in lines) == 1
    start, end = lines.index(START), lines.index(END)
    assert start < end
    block = lines[start + 1:end]
    assert len(block) >= 2 and block[0] == "```toml" and block[-1] == "```"
    assert not any("```" in line or "~~~" in line for line in block[1:-1])
    return tomllib.loads("\n".join(block[1:-1]))


@pytest.mark.parametrize("prefix,suffix", [
    ("", ""), ("```html\n", "\n```"), ("~~~\n", "\n~~~"),
    ("<!--\n", "\n-->"), ("    <div>\n", "\n</div>"),
])
def test_step_env_physical_boundaries(prefix, suffix):
    example = START + '\n```toml\nx = 1\n```\n' + END
    assert step_env_example(prefix + example + suffix) == {"x": 1}
    bad_examples = [END + '\n```toml\nx = 1\n```\n' + START, START + "\n" + END]
    for marker in (START, END):
        bad_examples.extend(example.replace(marker, replacement) for replacement in
                            ("", marker + "\n" + marker, " " + marker, "\t" + marker, marker + " "))
        for before, after in (("", ""), ("```html\n", "\n```"), ("<!--\n", "\n-->")):
            bad_examples.append(example + "\n" + before + marker + after)
        bad_examples.append(example + "\n    " + marker)
    for fence in ("```toml", "```"):
        # Replace entire physical fence lines, not substrings of the opening fence.
        for replacement in ("", " " + fence, fence + " ", "\n" + fence + "\n", "~~~"):
            bad_examples.append(example.replace("\n" + fence + "\n", "\n" + replacement + "\n"))
    bad_examples.append(example.replace("```toml", "```"))
    for content in ("invalid TOML", 'x = "```"', 'x = "~~~"', "# ```", "    # ~~~", "```\nx = 1"):
        bad_examples.append(example.replace("x = 1", content))
    for bad in bad_examples:
        with pytest.raises((AssertionError, tomllib.TOMLDecodeError)):
            step_env_example(prefix + bad + suffix)


def test_documented_step_env_fields_and_substitution(tmp_path):
    fragment = step_env_example(GUIDE.read_text())
    parsed = manifest.parse({**MANIFEST, **fragment})
    assert set(KINDS) <= fragment.keys() and fragment["vars"]
    steps = [step for kind in KINDS for step in getattr(parsed, kind)]
    assert len(steps) == len({step.id for step in steps}) == 4
    for kind in KINDS:
        step, = getattr(parsed, kind)
        assert step.id and step.env == fragment[kind][0]["env"]
        assert step.expect == 0 and step.timeout == 300
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in step.env.items())
        assert "{build}" in " ".join(step.env.values())
        assert any("{" + key + "}" in value for key in parsed.vars for value in step.env.values())
        for build in (tmp_path / "first", tmp_path / "second"):
            values = {**parsed.vars, "src": str(tmp_path / "src"), "build": str(build), "sha": "revision"}
            expanded = {key: manifest.expand(value, values) for key, value in step.env.items()}
            argv = [manifest.expand(arg, values) for arg in step.run]
            assert expanded == {"CACHE_DIR": str(build / "cache"), "MODE": fragment["vars"]["env_mode"]}
            assert argv[2:] == [expanded["CACHE_DIR"], expanded["MODE"]]
