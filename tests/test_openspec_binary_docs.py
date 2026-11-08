"""Physical documentation contracts; external parser and rollout evidence stay human."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tomllib
from urllib.parse import unquote, urlsplit

import pytest

from nightshift import preflight
from specloop import manifest, openspec, spec_format

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\[\]\n]+\]\(([^\s)]+)\)")


def section(text, heading):
    """Exact level-two boundaries, even inside literal fences and HTML."""
    assert heading.startswith("## ") and "\n" not in heading
    lines = text.splitlines()
    assert lines.count(heading) == 1, "missing/duplicate boundary"
    assert not any(line != heading and line.lstrip() == heading for line in lines), "indented boundary"
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    assert not any(line != line.lstrip() and line.lstrip().startswith("## ")
                   for line in lines[start:end]), "indented boundary"
    return "\n".join(lines[start:end])


def read_section(path, heading):
    return section((ROOT / path).read_text(), heading)


def rows(text):
    return [tuple(cell.strip().strip("`") for cell in line.strip("|").split("|"))
            for line in text.splitlines() if line.startswith("|")]


@pytest.mark.scenario("specloop-documentation/format-selection-guidance/compatibility-claims-retain-their-limits")
@pytest.mark.parametrize("wrapper", ["", "```markdown\n", "<!--\n"])
@pytest.mark.parametrize("ending", ["", "\n## Next\nexcluded"])
def test_selection_uses_only_exact_physical_boundaries(wrapper, ending):
    body = "body\n# Not a boundary\n### Child\n##\tNot a boundary\n| a | b |\n  | ignored | row |"
    text = wrapper + "## Selected\n" + body + ending
    assert section(text, "## Selected") == body
    assert rows(section(text, "## Selected")) == [("a", "b")]
    for bad in (text.replace("## Selected", "## Missing"),
                text + "\n## Selected", text + "\n  ## Selected",
                text.replace("body", "  ## Next"), text.replace("## Selected", "\t## Selected")):
        with pytest.raises(AssertionError):
            section(bad, "## Selected")
    # Literal fence/HTML content cannot hide duplicate starts or physical ends.
    for literal in ("```\n", "<!--\n"):
        with pytest.raises(AssertionError):
            section(text + "\n" + literal + "## Selected", "## Selected")
        assert section("## Selected\n" + literal + "## End\nexcluded", "## Selected") == literal.rstrip()


@pytest.mark.scenario("specloop-documentation/host-portability-guidance/platform-claims-retain-their-evidence-limits")
@pytest.mark.parametrize("path,heading", [
    ("README.md", "## Prerequisites"),
    ("KNOWN-LIMITATIONS.md", "## Platform assumptions"),
    ("plugins/nightshift/README.md", "## Installation"),
    ("docs/specloop/README.md", "## How the engine works"),
    ("docs/specloop/command-json.md", "## Manifest format"),
    ("docs/deploy.md", "## Install OpenSpec"),
    ("docs/deploy.md", "## OpenSpec rollout checks"),
])
def test_scoped_links_resolve(path, heading):
    targets = LINK.findall(read_section(path, heading))
    assert targets
    for target in targets:
        url = urlsplit(target)
        if url.scheme or url.netloc:
            assert url.scheme == "https"
            continue
        destination = ((ROOT / path).parent / unquote(url.path)).resolve() if url.path else ROOT / path
        assert destination.is_relative_to(ROOT) and destination.is_file()
        if url.fragment:
            headings = [line[3:] for line in destination.read_text().splitlines() if line.startswith("## ")]
            anchors = [re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-") for title in headings]
            # These scoped links intentionally target level-two boundaries.
            if url.fragment not in anchors:
                # Existing links also target level-three headings.
                headings = re.findall(r"^### (.+)$", destination.read_text(), re.M)
                anchors += [re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-") for title in headings]
            assert unquote(url.fragment) in anchors, target
    if path == "KNOWN-LIMITATIONS.md":
        assert "https://github.com/mattpolicastro/nightshift/issues/15" in targets
        assert preflight._OPENSPEC_HOSTS == {("Darwin", "arm64"): "darwin-arm64"}


@pytest.mark.scenario("specloop-documentation/host-portability-guidance/operator-configures-an-alternate-layout")
def test_plugin_exports_select_the_documented_binary():
    text = read_section("plugins/nightshift/README.md", "## Installation")
    exports = [shlex.split(line)[1] for line in text.splitlines() if line.startswith("export NIGHTSHIFT_")]
    env = dict(assignment.split("=", 1) for assignment in exports)
    assert openspec.resolve_binary(env) == env["NIGHTSHIFT_OPENSPEC_BIN"]
    assert openspec.resolve_binary({"NIGHTSHIFT_BIN_DIR": env["NIGHTSHIFT_BIN_DIR"]}) == env["NIGHTSHIFT_OPENSPEC_BIN"]
    default = openspec.resolve_binary({})
    literals = re.findall(r"`([^`\n]+)`", read_section("KNOWN-LIMITATIONS.md", "## Platform assumptions"))
    assert default in literals
    assert openspec.resolve_binary({"NIGHTSHIFT_OPENSPEC_BIN": env["NIGHTSHIFT_OPENSPEC_BIN"],
                                   "NIGHTSHIFT_BIN_DIR": "invalid unused setting"}) == env["NIGHTSHIFT_OPENSPEC_BIN"]


@pytest.mark.scenario("specloop-documentation/host-portability-guidance/frozen-service-wrappers-remain-explicit")
def test_wrapper_table_matches_unchanged_bootstrap_commands():
    table = rows(read_section("KNOWN-LIMITATIONS.md", "## Platform assumptions"))
    wrappers = [row for row in table if row[0].startswith("bin/")]
    assert {row[0] for row in wrappers} == {"bin/nightshift-daemon", "bin/specloop-serve"}
    for path, checkout, invocation, path_cell in wrappers:
        lines = (ROOT / path).read_text().splitlines()
        assert f'cd "{checkout}"' in lines
        assert f"exec {invocation}" in lines
        # The daemon row adds a prose note after the literal PATH cell.
        documented_path = path_cell.split("`", 1)[0]
        assert f'export PATH="{documented_path}"' in lines


@pytest.mark.scenario("specloop-documentation/format-selection-guidance/operator-selects-the-supported-format")
@pytest.mark.parametrize("path,heading", [
    ("docs/specloop/README.md", "## How the engine works"),
    ("docs/specloop/command-json.md", "## Manifest format"),
])
def test_documented_format_assignments_match_manifest_adapter(path, heading):
    text = read_section(path, heading)
    assignments = re.findall(r'`(format = "[^"]+")`', text)
    assert len(assignments) == 2
    data = tomllib.loads((ROOT / "examples/specloop/self-hosting.toml").read_text())
    explicit = manifest.parse(data)
    del data["run"]["format"]
    assert manifest.parse(data) == explicit
    for assignment in assignments:
        value = tomllib.loads(assignment)["format"]
        data["run"]["format"] = value
        selected = manifest.parse(data)
        if value == explicit.format:
            assert spec_format.get(selected) is spec_format.OPENSPEC
        else:
            with pytest.raises(openspec.SpecError) as error:
                spec_format.get(selected)
            assert str(error.value) in re.findall(r"`([^`\n]+)`", text)


@pytest.mark.scenario("specloop-documentation/standalone-openspec-operator-guidance/operator-understands-stricter-drafts-and-rollout")
def test_rollout_has_three_pending_checks_and_tracking_links():
    text = read_section("docs/deploy.md", "## OpenSpec rollout checks")
    assert re.findall(r"^- \[([ xX])\] ", text, re.M) == [" "] * 3
    targets = LINK.findall(text)
    assert {f"https://github.com/mattpolicastro/nightshift/issues/{n}" for n in (191, 184)} <= set(targets)
    commands = re.findall(r"`(openspec validate [^`]+)`", text)
    assert len(commands) == 2
    assert all(shlex.split(command) == ["openspec", "validate", "--all", "--strict"] for command in commands)
    # The guide's physical lint expression must match the backend's actual rule.
    import inspect
    expressions = re.findall(r"`(\^#[^`]+)`", read_section("docs/specloop/README.md", "## How the engine works"))
    assert len(expressions) == 1
    assert expressions[0] in inspect.getsource(openspec.validate)


@pytest.mark.scenario("specloop-documentation/standalone-openspec-operator-guidance/operator-installs-and-verifies-the-release")
@pytest.mark.parametrize("failure", ["", "archive.sha256", "binary.sha256"])
def test_install_shell_example_offline(tmp_path, failure):
    text = read_section("docs/deploy.md", "## Install OpenSpec")
    script, = re.findall(r"^```sh\n(.*?)^```$", text, re.M | re.S)
    subprocess.run(["/bin/sh", "-n"], input=script, text=True, check=True)
    sums = preflight._openspec_checksums(preflight._OPENSPEC_SUMS.read_bytes().decode("ascii"))
    # Check the actual published command arguments, never prose wording.
    literals = [shlex.split(line) for line in script.splitlines() if line.startswith("printf ")]
    assert {line[2] for line in literals} == {
        sums["archive/openspec-darwin-arm64.tar.gz"] + "  openspec-darwin-arm64.tar.gz",
        sums["binary/darwin-arm64/openspec"] + "  openspec",
    }
    stub = tmp_path / "stub"
    stub.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TRACE"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
if name == "uv":
    if args[2] == "python":
        sys.exit(subprocess.call([sys.executable, *args[3:]]))
    assert args == ["run", "--no-sync", "nightshift", "preflight"]
elif name == "curl":
    # Downloads and extraction must stay in the caller's writable staging root,
    # including on macOS, where bare mktemp can select the system temp root.
    assert pathlib.Path.cwd().is_relative_to(pathlib.Path(os.environ["TMPDIR"]))
    release = "https://github.com/Fission-AI/OpenSpec/releases/download/v" + os.environ["PIN"]
    assert args[6].startswith(release + "/")
    destination = pathlib.Path(args[-1])
    destination.write_text(os.environ["ARCHIVE_SUM"] + "\n" if destination.name == "SHA256SUMS" else "fixture")
elif name == "shasum":
    assert args[:3] == ["-a", "256", "-c"]
    if args[-1] == os.environ["FAIL_SUM"]:
        sys.exit(1)
elif name == "tar":
    assert args == ["-xzf", "openspec-darwin-arm64.tar.gz"]
    pathlib.Path("openspec").write_text('#!/bin/sh\nprintf "%s\\n" "' + os.environ["PIN"] + '"\n')
''')
    stub.chmod(0o755)
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    for name in ("uv", "curl", "shasum", "tar"):
        (tool_dir / name).symlink_to(stub)
    destination = tmp_path / "installed binary"
    trace = tmp_path / "trace.jsonl"
    env = {**os.environ, "PATH": f"{tool_dir}:/usr/bin:/bin", "PYTHONPATH": str(ROOT),
           "TMPDIR": str(tmp_path), "TRACE": str(trace), "PIN": openspec.VERSION,
           "ARCHIVE_SUM": sums["archive/openspec-darwin-arm64.tar.gz"] + "  openspec-darwin-arm64.tar.gz",
           "FAIL_SUM": failure, "NIGHTSHIFT_OPENSPEC_BIN": str(destination)}
    result = subprocess.run(["/bin/sh"], input=script, cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=30)
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    names = [call[0] for call in calls]
    if failure:
        assert result.returncode != 0 and not destination.exists(), result.stderr
        assert names[-1] == "shasum"
        if failure == "archive.sha256":
            assert "tar" not in names
    else:
        assert result.returncode == 0, result.stderr
        assert names == ["uv", "curl", "curl", "shasum", "tar", "shasum", "uv"]
        assert result.stdout.strip() == openspec.VERSION
        assert destination.stat().st_mode & 0o777 == 0o755
