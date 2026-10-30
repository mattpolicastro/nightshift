"""Conservative exclusion evidence through offline, exact transport scripts."""

import base64
from itertools import permutations
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlencode

import pytest

from nightshift import config, land
from test_land import BASE, HEAD, REPO, FakeClock, no_live_processes, scripted_runner
from test_land_gitea import ROOT, opener


TRIGGER = """on:
  push:
    branches: [main]
    paths-ignore: ['**.md']
  pull_request:
    paths-ignore: ['**.md']
"""
DOCS = ("README.md", "docs/nested/guide.md")


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("evidence must use injected transports")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture(params=["github", "gitea"])
def evidence(request, scripted_runner, opener):
    clock = FakeClock()
    identity = land.Identity(REPO, 78)
    forge = land.GitHubLandingForge()
    directory, limit_key = ".github/workflows", "per_page"
    if request.param == "gitea":
        definition = config.ForgeConfig("local", "gitea", ROOT)
        identity = land.Identity(REPO, 78, ROOT, "local")
        forge = land.GiteaLandingForge(definition, "injected-human-token", opener=opener)
        directory, limit_key = ".gitea/workflows", "limit"
    ops = land.Operations(scripted_runner, clock.sleep, clock.monotonic, forge=forge)

    def expect(path, data, query=None, timeout=30, status=200):
        endpoint = f"repos/{REPO}/{path}"
        if query:
            endpoint += "?" + urlencode(query)
        if request.param == "github":
            if status != 200 or isinstance(data, bytes):
                data = subprocess.CompletedProcess([], 1 if status != 200 else 0,
                                                   data.decode() if isinstance(data, bytes) else "", "unavailable")
            scripted_runner.expect(["gh", "api", endpoint], data, timeout=timeout)
        else:
            opener.expect("GET", f"{ROOT}/api/v1/{endpoint}", data, timeout=timeout, status=status)

    yield identity, ops, expect, directory, limit_key, clock
    if request.param == "gitea":
        assert not scripted_runner.calls
        assert all(req.get_header("Authorization") == "token injected-human-token"
                   and req.full_url.startswith(ROOT + "/api/v1/repos/")
                   for req, _ in opener.calls)


def workflow(directory, text=TRIGGER, name="ci.yml"):
    row = {"name": name, "path": f"{directory}/{name}", "type": "file", "sha": "c" * 40}
    content = {**row, "encoding": "base64", "size": len(text.encode()),
               "content": base64.b64encode(text.encode()).decode(),
               "download_url": "https://untrusted.invalid/head-workflow"}
    return row, content


def file_row(path="README.md", status="modified", **extra):
    return {"filename": path, "status": status, **extra}


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/base-content-controls-exclusion")
@pytest.mark.parametrize("base_text, eligible", [(TRIGGER, True), (TRIGGER.replace("**.md", "*.py"), False),
    (TRIGGER + "  schedule:\n    - cron: '0 0 * * *'\n  workflow_dispatch:\n", True)])
@pytest.mark.parametrize("name", ["ci.yml", "ci.yaml"])
def test_base_content_controls_exclusion(evidence, base_text, eligible, name):
    identity, ops, expect, directory, limit_key, _ = evidence
    expect("pulls/78", {"changed_files": 2})
    expect("pulls/78/files", [file_row(path) for path in DOCS], {limit_key: 100, "page": 1})
    expect("pulls/78/files", [], {limit_key: 100, "page": 2})
    row, content = workflow(directory, base_text, name)
    # An advertised head download cannot authorize exclusion. Only BASE
    # Contents requests are scripted; local/head reads fail this transcript.
    expect(f"contents/{directory}", [row], {"ref": BASE})
    expect(f"contents/{row['path']}", content, {"ref": BASE})
    paths = ops.forge.changed_paths(identity, ops)
    result = ops.forge.ci_workflow(identity, BASE, ops)
    assert result == (row["path"], base_text)
    assert land.all_paths_ignored(paths, land.ignored_paths(result[1])) is eligible


def test_repository_trigger_and_unrelated_yaml_are_supported():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
    assert TRIGGER in text
    patterns = land.ignored_paths(text)
    assert patterns == (("**.md",), ("**.md",))
    assert land.all_paths_ignored(DOCS, patterns)
    unrelated = 'jobs:\t&anchor\n  run: |\n    paths-ignore: [!no]\n    on: weird\n'
    assert land.ignored_paths(TRIGGER + unrelated) == patterns


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/schedule-and-dispatch-events-do-not-block-exclusion")
@pytest.mark.parametrize("order", [order for extra in [("schedule",), ("workflow_dispatch",),
    ("schedule", "workflow_dispatch")] for order in permutations(("push", "pull_request", *extra))])
@pytest.mark.parametrize("cron", ["    - cron: '0 0 * * *'\n",
    '    - cron: "0 0 * * *"  \n    - cron: \'another scalar\'\n'])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_schedule_and_dispatch_events_do_not_block_exclusion(order, cron, newline):
    blocks = {"push": "  push:\n    paths-ignore: ['**.md']\n",
              "pull_request": "  pull_request:\n    paths-ignore: ['**.md', 'docs/**']\n",
              "schedule": "  schedule:  \n" + cron,
              "workflow_dispatch": "  workflow_dispatch:  \n\n# comment\n  # comment\n"}
    text = "on:\n" + "".join(blocks[event] for event in order)
    expected = tuple(("**.md",) if event == "push" else ("**.md", "docs/**")
                     for event in order if event in ("push", "pull_request"))
    assert land.ignored_paths(text.replace("\n", newline)) == expected
    assert land.all_paths_ignored(DOCS, land.ignored_paths(text.replace("\n", newline)))
    mismatch = text.replace(blocks["push"], blocks["push"].replace("**.md", "*.md"))
    assert not land.all_paths_ignored(DOCS, land.ignored_paths(mismatch))
    missing = text.replace(blocks["pull_request"], "")
    assert land.ignored_paths(missing) is None
    assert not land.all_paths_ignored(DOCS, land.ignored_paths(missing))
    assert land.ignored_paths(text.replace(blocks["push"], "")) == (("**.md", "docs/**"),)
    missing_ignore = text.replace("    paths-ignore: ['**.md']\n", "")
    assert land.ignored_paths(missing_ignore) is None
    assert not land.all_paths_ignored(DOCS, land.ignored_paths(missing_ignore))


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/schedule-accepts-only-cron-items")
@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/incomplete-or-ambiguous-evidence-needs-ci")
@pytest.mark.parametrize("ending", ["  workflow_dispatch:\n", "jobs:\n  run: anything\n", ""])
@pytest.mark.parametrize("schedule, valid", [
    ("  schedule:  \n\n    # comment\n    - cron: '0 0 * * *'  \n  \n# end\n", True),
    ('  schedule:\n    - cron: ""\n    - cron: \'not a cron expression\'\n', True),
    ("  schedule:\n", False), ("  schedule:\n    # comment\n   \n", False),
    ("  schedule: []\n", False), ("  schedule: [{cron: '0 0 * * *'}]\n", False),
    ("  schedule: # inline\n    - cron: '0 0 * * *'\n", False),
    *[("  schedule:\n" + item + "\n", False) for item in [
        "    - cron: 0 0 * * *", "    - cron: '0 0 * * *' # inline",
        "   - cron: '0 0 * * *'", "     - cron: '0 0 * * *'", "\t- cron: 'x'",
        "    - cron:  'x'", "    cron: 'x'", "    - timezone: 'UTC'",
        "    paths-ignore: ['**.md']", "    - 'x'", "    - cron: 'a' 'b'",
        '    - cron: "a\\nb"', "    - cron: |\n        value",
        "    - cron: 'x'\n      other: 'value'",
        "    - cron: 'x'\n  schedule:\n    - cron: 'y'",
        "    - cron: 'x'\n  repository_dispatch:"]],
])
def test_schedule_accepts_only_cron_items(schedule, valid, ending):
    patterns = land.ignored_paths(TRIGGER + schedule + ending)
    assert patterns == ((("**.md",), ("**.md",)) if valid else None)
    assert land.all_paths_ignored(DOCS, patterns) is valid


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/dispatch-with-nested-lines-needs-ci")
@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/incomplete-or-ambiguous-evidence-needs-ci")
@pytest.mark.parametrize("text", [
    TRIGGER.replace("  push:", "  workflow_dispatch:"),
    TRIGGER + "  workflow_dispatch: {}\n", TRIGGER + "  workflow_dispatch: []\n",
    TRIGGER + "  workflow_dispatch: # inline\n",
    TRIGGER + "  workflow_dispatch:\n  workflow_dispatch:\n",
    *[TRIGGER + "  workflow_dispatch:\n" + nested + "\n" for nested in [
        "    inputs:", "    branches: [main]", "    paths-ignore: ['**.md']",
        "    - item", "   # comment", "    # comment", "   ", "    ", "  \t# comment"]],
])
def test_dispatch_with_nested_lines_needs_ci(text):
    assert land.ignored_paths(text) is None
    assert not land.all_paths_ignored(DOCS, land.ignored_paths(text))


@pytest.mark.parametrize("header", ["on", "'on'", '"on"'])
@pytest.mark.parametrize("ignore", ["['**.md', 'docs/**']", '["**.md", "docs/**"]',
                                  '\n      - "**.md"\n      - \'docs/**\''])
@pytest.mark.parametrize("branch", ["[main, release/*]", "['main', \"release/*\"]",
                                  "\n      - main\n      - 'release/*'"])
def test_supported_list_spellings(header, ignore, branch):
    text = (f"{header}:\n  # comment\n  pull_request:\n    branches-ignore: {branch}\n"
            f"    paths-ignore: {ignore}\n\n")
    assert land.ignored_paths(text) == (("**.md", "docs/**"),)
    assert land.ignored_paths(text.replace("\n", "\r\n")) == (("**.md", "docs/**"),)


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/mixed-and-renamed-paths-need-ci")
@pytest.mark.parametrize("paths, pattern, expected", [
    (DOCS, "**.md", True), (DOCS, "*.md", False), (DOCS, "**/*.md", True),
    (("docs/a.md",), "docs/*.md", True), (("docs/a/b.md",), "docs/*.md", False),
    (("docs/a/b.md", "docs/a.md"), "docs/**/*.md", True),
    (("README.MD",), "**.md", False), (("docs/.md",), "**.md", True),
    (("README.md", "code.py"), "**.md", False), (("xREADME.md",), "README.md", False),
    (("a,b.md",), "a,b.md", True), (("docs/a.md/x",), "**.md", False),
    (("docs/guide.md",), "docs**/guide.md", True),
    (("docsguide.md",), "docs**/guide.md", False),
])
def test_whole_path_glob_subset(paths, pattern, expected):
    assert land.all_paths_ignored(paths, land.ignored_paths(
        f"on:\n  pull_request:\n    paths-ignore: ['{pattern}']\n")) is expected


def test_every_declared_event_must_ignore_every_path():
    assert not land.all_paths_ignored(DOCS, land.ignored_paths(TRIGGER.replace("**.md", "*.md", 1)))


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/mixed-and-renamed-paths-need-ci")
@pytest.mark.parametrize("row, paths", [
    (file_row("README.md", "renamed", previous_filename="code.py"), ("README.md", "code.py")),
    (file_row("code.py", "removed"), ("code.py",)),
    (file_row("code.py", "deleted"), ("code.py",)),
])
def test_rename_and_deletion_provenance(evidence, row, paths):
    identity, ops, expect, _, key, _ = evidence
    expect("pulls/78", {"changed_files": 1})
    expect("pulls/78/files", [row], {key: 100, "page": 1})
    expect("pulls/78/files", [], {key: 100, "page": 2})
    result = ops.forge.changed_paths(identity, ops)
    assert result == paths
    assert not land.all_paths_ignored(result, land.ignored_paths(TRIGGER))
    extra = "  schedule:\n    - cron: '0 0 * * *'\n  workflow_dispatch:\n"
    assert not land.all_paths_ignored(result, land.ignored_paths(TRIGGER + extra))


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/incomplete-or-ambiguous-evidence-needs-ci")
@pytest.mark.parametrize("count", [None, True, "1", -1, 0, 3000, 3001])
def test_missing_or_capped_count(evidence, count):
    identity, ops, expect, _, _, _ = evidence
    expect("pulls/78", {"changed_files": count})
    assert ops.forge.changed_paths(identity, ops) is None


@pytest.mark.parametrize("rows", [[None], [{}], [file_row(None)], [file_row("")],
    [file_row("/absolute.md")], [file_row("a/../b.md")], [file_row("a//b.md")],
    [file_row("a\\b.md")], [file_row("a\nb.md")], [file_row(status="unknown")],
    [file_row(status="renamed")], [file_row(status="renamed", previous_filename="README.md")],
    [file_row(previous_filename="code.py")], [file_row(), file_row()], {"files": []}])
def test_malformed_file_evidence(evidence, rows):
    identity, ops, expect, _, key, _ = evidence
    expect("pulls/78", {"changed_files": 2})
    expect("pulls/78/files", rows, {key: 100, "page": 1})
    assert ops.forge.changed_paths(identity, ops) is None


@pytest.mark.parametrize("case", ["complete", "short", "overflow", "duplicate", "non-array", "page-cap"])
def test_pagination_requires_empty_termination_and_exact_count(evidence, case):
    identity, ops, expect, _, key, _ = evidence
    count = 101 if case == "page-cap" else 1 if case == "overflow" else 2
    expect("pulls/78", {"changed_files": count})
    expect("pulls/78/files", [file_row()], {key: 100, "page": 1})
    second = ([file_row()] if case == "duplicate" else [] if case == "short"
              else {} if case == "non-array" else [file_row("nested/doc.md")])
    expect("pulls/78/files", second, {key: 100, "page": 2})
    if case == "complete":
        expect("pulls/78/files", [], {key: 100, "page": 3})
    if case == "page-cap":
        for page in range(3, 101):
            expect("pulls/78/files", [file_row(f"{page}.md")], {key: 100, "page": page})
    result = ops.forge.changed_paths(identity, ops)
    assert result == (("README.md", "nested/doc.md") if case == "complete" else None)


@pytest.mark.parametrize("listing", [None, [], {}, "multiple", "symlink", "wrong-name", "duplicate", "truncated", "cap"])
def test_missing_or_ambiguous_workflow_selection(evidence, listing):
    identity, ops, expect, directory, _, _ = evidence
    row, _ = workflow(directory)
    listings = {"multiple": [row, workflow(directory, name="test.yaml")[0]],
                "symlink": [{**row, "type": "symlink"}], "wrong-name": [workflow(directory, name="test.yml")[0]],
                "duplicate": [row, row], "truncated": [{**row, "truncated": True}], "cap": [row] * 1000}
    expect(f"contents/{directory}", listings[listing] if isinstance(listing, str) else listing,
           {"ref": BASE})
    assert ops.forge.ci_workflow(identity, BASE, ops) is None


@pytest.mark.parametrize("change", [{"encoding": "none"}, {"content": "%%%"}, {"content": None},
    {"content": "/w==", "size": 1}, {"size": 1}, {"size": True}, {"sha": HEAD},
    {"type": "symlink"}, {"truncated": True}, {"path": "other.yml"}, {"target": "elsewhere"}])
def test_unavailable_or_ambiguous_content(evidence, change):
    identity, ops, expect, directory, _, _ = evidence
    row, content = workflow(directory)
    expect(f"contents/{directory}", [row], {"ref": BASE})
    expect(f"contents/{row['path']}", {**content, **change}, {"ref": BASE})
    assert ops.forge.ci_workflow(identity, BASE, ops) is None


@pytest.mark.parametrize("stage", ["metadata", "files", "directory", "content"])
@pytest.mark.parametrize("status, data", [(404, {}), (403, {}), (302, {}), (200, b"bad JSON")])
def test_transport_failures_are_unavailable_evidence(evidence, stage, status, data):
    identity, ops, expect, directory, key, _ = evidence
    if stage in ("metadata", "files"):
        if stage == "files":
            expect("pulls/78", {"changed_files": 1})
        expect("pulls/78" if stage == "metadata" else "pulls/78/files", data,
               None if stage == "metadata" else {key: 100, "page": 1}, status=status)
        assert ops.forge.changed_paths(identity, ops) is None
    else:
        row, _ = workflow(directory)
        if stage == "content":
            expect(f"contents/{directory}", [row], {"ref": BASE})
        expect(f"contents/{directory}" if stage == "directory" else f"contents/{row['path']}",
               data, {"ref": BASE}, status=status)
        assert ops.forge.ci_workflow(identity, BASE, ops) is None


@pytest.mark.parametrize("base", ["main", "", "abc", BASE + "?ref=" + HEAD])
def test_workflow_reads_require_a_full_base_sha(evidence, base):
    identity, ops, _, _, _, _ = evidence
    assert ops.forge.ci_workflow(identity, base, ops) is None


@pytest.mark.parametrize("operation", ["paths", "workflow"])
@pytest.mark.parametrize("failure", ["unavailable", "expired-before", "expired-during", "timeout-during"])
def test_evidence_never_swallows_shared_deadline(evidence, operation, failure):
    identity, ops, expect, directory, _, clock = evidence
    ops.deadline = clock.now + (0 if failure == "expired-before" else 5)
    def response():
        if failure in ("expired-during", "timeout-during"):
            clock.now += 5
            if failure == "timeout-during":
                if isinstance(ops.forge, land.GitHubLandingForge):
                    raise subprocess.TimeoutExpired([], 5)
                raise TimeoutError("lost response")
            return {}
        raise land.Refusal("unavailable")
    if failure != "expired-before":
        expect("pulls/78" if operation == "paths" else f"contents/{directory}", response,
               None if operation == "paths" else {"ref": BASE}, timeout=5)
    def read():
        return (ops.forge.changed_paths(identity, ops) if operation == "paths"
                else ops.forge.ci_workflow(identity, BASE, ops))
    if failure == "unavailable":
        assert read() is None
    else:
        with pytest.raises(land._CIDeadlineExpired):
            read()
    assert not clock.sleeps


@pytest.mark.scenario("reviewed-pr-landing/base-workflow-exclusion-proof/incomplete-or-ambiguous-evidence-needs-ci")
@pytest.mark.parametrize("text", [
    "", "on: [pull_request]", "on:\n  push:\n    paths-ignore: ['**.md']",
    TRIGGER + "on:\n", TRIGGER + "'on': [pull_request]\n",
    TRIGGER.replace("on:", " on:", 1), TRIGGER.replace("on:", "on: &trigger", 1),
    TRIGGER.replace("  push:", "  push: {}"), TRIGGER.replace("  push:", "  workflow_dispatch:"),
    TRIGGER.replace("  push:", "  pull_request:"), TRIGGER.replace("    branches", "   branches"),
    TRIGGER.replace("    branches: [main]", "    branches: [main]\n    branches: [dev]"),
    TRIGGER.replace("    branches: [main]", "    paths: ['**']"),
    TRIGGER.replace("    branches: [main]", "    paths-ignore: ['**']"),
    TRIGGER.replace("['**.md']", "[**.md]"), TRIGGER.replace("['**.md']", "['**.md',]"),
    TRIGGER.replace("['**.md']", "[]"), TRIGGER.replace("['**.md']", "*alias"),
    TRIGGER.replace("['**.md']", "['**.md', '**.md']"),
    TRIGGER.replace("['**.md']", "\n      - '**.md'\n      - '**.md'"),
    TRIGGER.replace("['**.md']", "!!str '**.md'"), TRIGGER.replace("['**.md']", "|\n      '**.md'"),
    TRIGGER.replace("['**.md']", "['**.md'] # inline"), TRIGGER.replace("  push:", "\tpush:"),
    TRIGGER.replace("  push:", "  <<: *events"), TRIGGER.replace("  push:", "\f  push:"),
    TRIGGER.replace("['**.md']", "\n     - '**.md'"), TRIGGER.replace("['**.md']", "\n      - **.md"),
    "on:\n  pull_request:\n    branches: [main]\njobs:\n  paths-ignore: ['**']",
    TRIGGER.replace("    branches: [main]", "    branches:\n    paths-ignore: ['**']"),
])
def test_ambiguous_trigger_block_needs_ci(text):
    assert land.ignored_paths(text) is None
    assert not land.all_paths_ignored(DOCS, land.ignored_paths(text))


@pytest.mark.parametrize("pattern", ["", "!*.md", "?.md", "[ab].md", "{a,b}.md", "a\\*.md",
                                     "/**", "../*.md", "a/../*.md", "a//*.md", "a+", "***.md"])
def test_unsupported_globs_never_prove_exclusion(pattern):
    assert not land.all_paths_ignored(DOCS, ((pattern,),))
    assert land.ignored_paths(f"on:\n  pull_request:\n    paths-ignore: ['{pattern}']") is None


@pytest.mark.parametrize("paths", [None, (), ("README.md", "README.md"), ("../README.md",)])
def test_empty_or_invalid_paths_never_prove_exclusion(paths):
    assert not land.all_paths_ignored(paths, (("**",),))
