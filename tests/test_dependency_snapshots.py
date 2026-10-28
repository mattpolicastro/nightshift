import hashlib
import os
from dataclasses import replace

import pytest

from specloop import manifest, verify
from test_specloop_units import MANIFEST

DECLARATION = {"id": "deps", "lockfiles": ["z.lock", "pkg/a.lock"],
               "warm": ["tool", "{snapshot:deps}", "{src}", "{build}", "{sha}", "{tool}"]}


@pytest.mark.scenario("verify-preparation/snapshot-manifest-declarations/declarations-round-trip")
@pytest.mark.parametrize("overrides", [{}, {"change": "new", "branch": "spec/new"}])
def test_snapshot_manifest_round_trip(tmp_path, overrides):
    data = {**MANIFEST, "vars": {"tool": "a tool"},
            "snapshot": [DECLARATION, {"id": "Other_2", "lockfiles": ["other.lock"], "warm": ["true"]}]}
    for kind in ("prepare", "verify", "acceptance", "mutation_step"):
        data[kind] = [{"id": kind, "run": ["tool", "--cache={snapshot:deps}", "{snapshot:Other_2}"],
                       "expect": 7, "timeout": 19}]
    parsed = manifest.parse(data)
    path = tmp_path / "manifest.toml"
    path.write_text("\n".join(f"{manifest._toml_value(k)} = {manifest._toml_value(v)}" for k, v in data.items()))
    effective, stored = manifest.template(path, **overrides)
    path.write_text(stored)
    assert manifest.load(path) == effective == replace(parsed, **overrides)
    for kind in ("prepare", "verify", "acceptance", "mutation_step"):
        assert getattr(effective, kind) == (manifest.Step(kind, tuple(data[kind][0]["run"]), 7, 19),)
    assert effective.snapshots == (
        manifest.Snapshot("deps", tuple(DECLARATION["lockfiles"]), tuple(DECLARATION["warm"])),
        manifest.Snapshot("Other_2", ("other.lock",), ("true",)))
    assert manifest.parse(MANIFEST).snapshots == ()
    assert manifest.Manifest(parsed.repo, parsed.change, parsed.branch, parsed.pr_base, parsed.github,
                             parsed.roles, parsed.allowed, parsed.frozen, parsed.verify).snapshots == ()
    values = {**parsed.vars, "src": "/src", "build": "/build", "sha": "base", "snapshot:deps": "/cache"}
    assert [verify.expand(arg, values) for arg in parsed.snapshots[0].warm] == [
        "tool", "/cache", "/src", "/build", "base", "a tool"]


@pytest.mark.scenario("verify-preparation/snapshot-manifest-declarations/invalid-declarations-fail-before-execution")
@pytest.mark.parametrize("raw, field", [
    (None, "snapshot"), ({}, "snapshot"), ([None], "snapshot"), ([{}], "id"),
    ([DECLARATION, DECLARATION], "deps.*id"),
    *[([{**DECLARATION, "id": value}], "id") for value in (None, 1, "", "../x", "/x", "a.b", "a b", "é", "-x")],
    *[([{**DECLARATION, field: value}], "deps.*" + field)
      for field in ("lockfiles", "warm") for value in (None, "x", [], [""], [1], ["x\0y"])],
    *[([{k: v for k, v in DECLARATION.items() if k != field}], "deps.*" + field)
      for field in ("lockfiles", "warm")],
    *[([{**DECLARATION, "lockfiles": [path]}], "deps.*lockfiles") for path in (
        "/lock", "C:/lock", "C:lock", "a\\b", "a//b", "a/./b", "a/../b", "./lock", "../lock",
        "a/", "*.lock", "?.lock", "[a].lock", "a].lock")],
    ([{**DECLARATION, "lockfiles": ["a", "a"]}], "deps.*lockfiles"),
])
def test_snapshot_invalid_declarations(raw, field):
    with pytest.raises(ValueError, match=field):
        manifest.parse({**MANIFEST, "snapshot": raw})


@pytest.mark.scenario("verify-preparation/snapshot-manifest-declarations/invalid-declarations-fail-before-execution")
@pytest.mark.parametrize("kind", ["prepare", "verify", "acceptance", "mutation_step", "warm", "vars"])
@pytest.mark.parametrize("reference", ["snapshot:missing", "snapshot:", "snapshot:other", "snapshot:deps", "mutant_path"])
@pytest.mark.parametrize("alias", ["literal", "cache", "chain", "mutant"])
def test_snapshot_invalid_placeholders(kind, reference, alias):
    data = {**MANIFEST, "snapshot": [DECLARATION, {"id": "other", "lockfiles": ["x"], "warm": ["true"]}]}
    arg = "prefix={" + reference + "}/suffix"
    if alias != "literal":
        data["vars"] = {"cache": "{next}" if alias == "chain" else arg, "next": arg, "mutant_path": ""}
        if alias == "mutant":
            data["vars"] = {"cache": "{mutant_path}", "mutant_path": arg}
        arg = "{cache}"
    if kind == "warm":
        data["snapshot"] = [{**DECLARATION, "warm": ["tool", arg]}, data["snapshot"][1]]
    elif kind == "vars":
        data["vars"] = {reference: "override"}
    else:
        data[kind] = [{"id": kind, "run": ["tool", arg]}]
    if (kind not in ("warm", "vars") and reference in ("snapshot:other", "snapshot:deps", "mutant_path")) or (
            kind == "vars" and reference == "mutant_path") or (
            kind == "warm" and reference == "snapshot:deps" and alias != "mutant"):
        manifest.parse(data)  # Preserve ordinary mutant_path handling and declared cross-references.
    else:
        setting = "snapshot 'deps'\\.warm" if kind == "warm" else "vars" if kind == "vars" else f"{kind} '{kind}'\\.run"
        with pytest.raises(ValueError, match=setting):
            manifest.parse(data)


@pytest.mark.scenario("verify-preparation/lockfile-content-identity/lockfile-changes-invalidate-reuse")
def test_snapshot_key_changes_with_lockfile(tmp_path):
    definition = manifest.Snapshot("deps", ("z.lock", "pkg/a.lock"), ("true",))
    (tmp_path / "pkg").mkdir()
    for path in definition.lockfiles:
        (tmp_path / path).write_bytes(b"\xff\r\n\0")
    key = verify.snapshot_key(tmp_path, definition)
    framed = b"specloop-snapshot-v1\0"
    for path in sorted(definition.lockfiles):
        for value in (path.encode(), b"\xff\r\n\0"):
            framed += len(value).to_bytes(8, "big") + value
    assert key == hashlib.sha256(framed).hexdigest()
    assert verify.snapshot_key(tmp_path, replace(definition, lockfiles=definition.lockfiles[::-1])) == key
    (tmp_path / "source.py").write_text("unrelated edit")
    for path in definition.lockfiles:
        os.utime(tmp_path / path, (1, 1))
    assert verify.snapshot_key(tmp_path, definition) == key
    for path in definition.lockfiles:
        (tmp_path / path).write_bytes(b"\xff\n\0")
        assert verify.snapshot_key(tmp_path, definition) != key
        (tmp_path / path).write_bytes(b"\xff\r\n\0")
    (tmp_path / "z.lock").rename(tmp_path / "renamed.lock")
    assert verify.snapshot_key(tmp_path, replace(definition, lockfiles=("renamed.lock", "pkg/a.lock"))) != key
    (tmp_path / "a").write_bytes(b"bc")
    (tmp_path / "ab").write_bytes(b"c")
    assert verify.snapshot_key(tmp_path, replace(definition, lockfiles=("a",))) != verify.snapshot_key(
        tmp_path, replace(definition, lockfiles=("ab",)))


@pytest.mark.scenario("verify-preparation/lockfile-content-identity/unsafe-key-inputs-are-unavailable")
@pytest.mark.parametrize("kind", ["missing", "unreadable", "directory", "file-link", "parent-link", "fifo"])
def test_snapshot_unsafe_key_inputs(tmp_path, monkeypatch, kind):
    src = tmp_path / "src"
    src.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "lock").write_bytes(b"must not read")
    lock = src / "lock"
    if kind == "unreadable":
        lock.write_bytes(b"private")
        real_open = os.open
        def denied(path, *args, **kwargs):
            if path == "lock":
                raise PermissionError("unreadable lockfile")
            return real_open(path, *args, **kwargs)
        monkeypatch.setattr(os, "open", denied)
    elif kind == "directory":
        lock.mkdir()
    elif kind == "file-link":
        lock.symlink_to(external / "lock")
    elif kind == "parent-link":
        (src / "parent").symlink_to(external, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(lock)
    monkeypatch.setattr(os, "fdopen", lambda *a, **kw: pytest.fail("unsafe input opened for reading"))
    definition = manifest.Snapshot("deps", ("parent/lock" if kind == "parent-link" else "lock",), ("true",))
    assert verify.snapshot_key(src, definition) is None
