"""A syntactic tripwire for the staged GitHub command boundary."""

import ast
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = "nightshift/forge/github.py"
DEFERRED = {
    "nightshift/queue.py", "nightshift/land.py", "nightshift/factory_land.py",
    "nightshift/arm.py", "nightshift/outcomes.py", "nightshift/preflight.py",
    "specloop/cli.py", "specloop/critic.py",
}


def argv_violations(root):
    failures = []
    for directory in ("nightshift", "specloop"):
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeError, SyntaxError) as exc:
                failures.append(f"{relative}:{getattr(exc, 'lineno', None) or '?'}: {exc}")
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.List) and node.elts
                        and isinstance(node.elts[0], ast.Constant)
                        and isinstance(node.elts[0].value, str)
                        and node.elts[0].value in {"gh", "issue", "pr", "api"}
                        and relative not in DEFERRED | {BACKEND}):
                    failures.append(f"{relative}:{node.lineno}: GitHub argv outside backend")
    return failures


@pytest.mark.scenario("forge-interface/ast-source-boundary/scan-rejects-new-argv-sites")
@pytest.mark.scenario("forge-interface/ast-source-boundary/deferred-modules-remain-allowed")
def test_no_gh_argv_outside_backends():
    assert not (failures := argv_violations(ROOT)), "\n".join(failures)


def source(root, relative, contents):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


@pytest.mark.scenario("forge-interface/ast-source-boundary/scan-rejects-new-argv-sites")
@pytest.mark.parametrize("prefix", ["gh", "issue", "pr", "api"])
@pytest.mark.parametrize("template,line", [
    ("run([{p}, 'view'])", 1), ("args = [{p}, *tail]", 1),
    ("def build():\n    return [\n        {p},\n        'view',\n    ]", 2),
])
def test_scanner_rejects_literal_lists(tmp_path, prefix, template, line):
    source(tmp_path, "nightshift/new.py", template.format(p=repr(prefix)))
    assert argv_violations(tmp_path) == [f"nightshift/new.py:{line}: GitHub argv outside backend"]


@pytest.mark.scenario("forge-interface/ast-source-boundary/deferred-modules-remain-allowed")
@pytest.mark.parametrize("relative", sorted(DEFERRED | {BACKEND}))
def test_scanner_allows_only_exact_paths(tmp_path, relative):
    source(tmp_path, relative, "args = ['gh', *tail]")
    assert argv_violations(tmp_path) == []
    near = str(Path(relative).with_name("near_" + Path(relative).name))
    nested = str(Path(relative).parent / "nested" / Path(relative).name)
    for path in (near, nested, "nightshift/daemon.py", "nightshift/specloop_intake.py"):
        source(tmp_path, path, "['api', endpoint]")
    assert {failure.split(":")[0] for failure in argv_violations(tmp_path)} == {
        near, nested, "nightshift/daemon.py", "nightshift/specloop_intake.py"}


@pytest.mark.scenario("forge-interface/ast-source-boundary/scan-rejects-new-argv-sites")
def test_scanner_ignores_text_and_fails_closed(tmp_path, monkeypatch):
    path = source(tmp_path, "specloop/new.py", '# ["gh"]\ns = "[\'api\']"\nx = []\ny = [1]\n')
    assert argv_violations(tmp_path) == []
    path.write_text("ok = 1\n['pr'\n")
    assert argv_violations(tmp_path)[0].startswith("specloop/new.py:2:")
    # Even allowlisted files must parse and be readable.
    path.unlink()
    source(tmp_path, BACKEND, "[")
    assert argv_violations(tmp_path)[0].startswith(f"{BACKEND}:1:")
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable")))
    assert argv_violations(tmp_path) == [f"{BACKEND}:?: unreadable"]


@pytest.mark.scenario("forge-interface/module-layout-guidance/layout-describes-staged-boundary")
def test_layout_module_paths_resolve():
    layout = (ROOT / "README.md").read_text().split("## Layout\n", 1)[1].split("\n## ", 1)[0]
    modules = {name for row in layout.splitlines() if row.startswith("| `")
               for name in re.findall(r"`([^`]+)`", row.split("|")[1])}
    assert {"forge/__init__.py", "forge/types.py", "forge/github.py", "queue.py"} <= modules
    for name in modules:
        assert (ROOT / name if name.endswith("/") else ROOT / "nightshift" / name).exists()
