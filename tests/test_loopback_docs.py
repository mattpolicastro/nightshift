import tomllib
from pathlib import Path

import pytest

from specloop import manifest
from test_specloop_units import MANIFEST

START, END = "<!-- loopback-example:start -->", "<!-- loopback-example:end -->"
GUIDE = Path(__file__).resolve().parents[1] / "docs/specloop/README.md"
pytestmark = pytest.mark.scenario(
    "specloop-documentation/verification-loopback-guidance/operator-configures-a-flutter-test-step"
)


def loopback_example(text):
    lines = text.splitlines()
    for marker in (START, END):
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
def test_loopback_physical_boundaries(prefix, suffix):
    example = START + '\n```toml\nx = 1\n```\n' + END
    assert loopback_example(prefix + example + suffix) == {"x": 1}
    bad = [END + '\n```toml\nx = 1\n```\n' + START, START + "\n" + END]
    for marker in (START, END):
        bad.extend(example.replace(marker, value) for value in
                   ("", marker + "\n" + marker, " " + marker, "\t" + marker, marker + " "))
        for before, after in (("", ""), ("```html\n", "\n```"), ("~~~\n", "\n~~~"), ("<!--\n", "\n-->")):
            for padding in ("", "    "):
                bad.append(example + "\n" + before + padding + marker + after)
    for fence in ("```toml", "```"):
        for value in ("", " " + fence, fence + " ", "\n" + fence + "\n", "~~~", "````"):
            bad.append(example.replace("\n" + fence + "\n", "\n" + value + "\n"))
    bad.append(example.replace("```toml", "```"))
    for content in ("invalid TOML", 'x = "```"', 'x = "~~~"', "# ```", "    # ~~~", "```\nx = 1"):
        bad.append(example.replace("x = 1", content))
    for text in bad:
        with pytest.raises((AssertionError, tomllib.TOMLDecodeError)):
            loopback_example(prefix + text + suffix)


def test_flutter_fragment_fields_and_substitution(tmp_path):
    fragment = loopback_example(GUIDE.read_text())
    parsed = manifest.parse({**MANIFEST, **fragment})
    assert set(fragment) == {"vars", "verify"}
    assert "flutter_root" in parsed.vars
    assert len(parsed.verify) == 2 and len({step.id for step in parsed.verify}) == 2
    assert "loopback" not in fragment["verify"][0]
    for step, command, flag in zip(parsed.verify, ("analyze", "test"), (False, True), strict=True):
        assert step.loopback is flag and step.expect == 0
        assert step.env == {"FLUTTER_ALREADY_LOCKED": "true"}
        script = f'cd "$1" && exec "$2" "$3" --suppress-analytics {command} --no-pub'
        assert step.run[:3] == ("/bin/sh", "-c", script) and step.run[3]
        assert step.run[4:] == ("{build}/project", "{flutter_root}/bin/cache/dart-sdk/bin/dart",
                               "{flutter_root}/bin/cache/flutter_tools.snapshot")
        values = {**parsed.vars, "build": str(tmp_path / "build with spaces"),
                  "flutter_root": str(tmp_path / "SDK with spaces")}
        argv = [manifest.expand(arg, values) for arg in step.run]
        assert argv[:4] == list(step.run[:4])
        assert argv[4:] == [values["build"] + "/project", values["flutter_root"] + "/bin/cache/dart-sdk/bin/dart",
                            values["flutter_root"] + "/bin/cache/flutter_tools.snapshot"]
        assert {k: manifest.expand(v, values) for k, v in step.env.items()} == step.env
