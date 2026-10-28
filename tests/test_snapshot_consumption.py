import ctypes
import errno
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import manifest, verify
from test_snapshot_warming import SUCCESS, warming


def command(id, script, *args, expect=0):
    return manifest.Step(id, ("/bin/sh", "-ec", script, "sh", *args), expect, 10)


@pytest.fixture
def stored(warming, monkeypatch):
    repo, commit, m, context, events = warming
    sha = commit(SUCCESS + '; mkdir "$3/nested"; echo nested > "$3/nested/file"')
    verify.warm_snapshots(repo, sha, m, context)
    entry, _ = verify._snapshot_entries(context.root)[0]
    events.clear()
    monkeypatch.setattr(verify, "warm_snapshots", lambda *a: pytest.fail("consumer must never warm"))
    return repo, sha, m, context, events, entry


@pytest.mark.scenario("verify-preparation/private-snapshot-consumption/clones-are-writable-and-isolated")
@pytest.mark.scenario("verify-preparation/private-snapshot-consumption/preparation-and-checks-share-the-private-path")
@pytest.mark.parametrize("real", [False, True], ids=["portable-clone-double", "real-APFS-clonefile"])
@pytest.mark.parametrize("check_kind", ["verify", "acceptance"])
def test_snapshot_clone_is_writable_and_isolated(stored, tmp_path, monkeypatch, real, check_kind):
    repo, sha, m, context, events, entry = stored
    if real:
        if sys.platform != "darwin":
            pytest.skip("real APFS clonefile requires macOS; portable double is not COW evidence")
        try:
            verify._clone_payload(entry / "payload", tmp_path / "probe")
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.ENOSYS, errno.EXDEV):
                pytest.skip(f"real clonefile unsupported on this filesystem: {exc}")
            raise
    else:
        monkeypatch.setattr(verify, "_clone_api", lambda: lambda src, dst, flags: (shutil.copyfile(src, dst), 0)[1])
    m = replace(m, prepare=(command("prepare", 'test "$(cat "$1/dependency")" = base; '
        'printf "%s" "$1" > "$1/prepared"', "{snapshot:deps}"),))
    checks = (command("check", 'test "$(cat "$1/prepared")" = "$1"; test -x "$1/dependency"', "{snapshot:deps}"),)
    m = replace(m, **{check_kind: checks})
    outputs = [tmp_path / "first", tmp_path / "second"]
    for out in outputs:
        rs = verify.run_steps(repo, sha, getattr(m, check_kind), m, out, snapshots=context)
        assert verify.ok(rs, m.prepare + checks), verify.summary(rs)
        assert [r.id for r in rs] == ["prepare", "check"]
        assert rs[0].argv[-1] == rs[1].argv[-1] == str(out / "build/snapshots/deps")
        assert json.loads((out / "receipts.json").read_text())[0]["id"] == "prepare"
        for mode in ("prepare", "verify"):
            profile = (out / f"{mode}.sb").read_text()
            assert ("(deny network*)" in profile) == (mode == "verify")
            assert profile.splitlines()[-1] == f'(deny file-write* (subpath "{context.root / "entries"}"))'
    first, second = [out / "build/snapshots/deps" for out in outputs]
    (first / "dependency").write_text("changed")
    (first / "origin").unlink()
    (first / "new").write_text("new")
    (first / "nested/file").write_text("changed nested")
    for root in (entry / "payload", second):
        assert (root / "dependency").read_text() == "base"
        assert (root / "nested/file").read_text() == "nested\n"
        assert (root / "origin").exists() and not (root / "new").exists()
    assert all(not p.stat().st_mode & 0o222 for p in verify._payload_paths(entry))
    assert all(p.stat().st_mode & 0o200 for p in verify._payload_paths(second))
    assert all(p.stat().st_mode & 0o111 == 0o111 for p in (first / "dependency", second / "dependency"))
    verify.cleanup(outputs[0])
    assert not first.exists() and second.exists() and entry.exists()
    assert not events and (outputs[0] / "receipts.json").exists()
    assert (outputs[0] / "snapshot-events.jsonl").read_text() == ""


@pytest.mark.scenario("verify-preparation/snapshot-fallback-events/missing-or-stale-snapshot-uses-existing-preparation")
@pytest.mark.scenario("verify-preparation/snapshot-fallback-events/clone-failure-discards-partial-output")
@pytest.mark.parametrize("case", ["missing", "stale", "key_unavailable", "context_unavailable", "invalid-metadata",
    "invalid-payload", "unsupported", "unavailable", "cross-device", "partial", "cleanup-error", "prepare-failure"])
def test_missing_snapshot_falls_back(stored, tmp_path, monkeypatch, case):
    repo, sha, m, context, events, entry = stored
    key = verify.snapshot_key(repo, m.snapshots[0])
    reason = case
    if case in ("missing", "prepare-failure"):
        context.root = tmp_path / "absent-store"
        reason = "missing"
    elif case == "stale":
        (repo / "lock").write_text("candidate")
    elif case == "key_unavailable":
        (repo / "lock").unlink()
    elif case.startswith("invalid"):
        target = entry / ("metadata.json" if case == "invalid-metadata" else "payload/dependency")
        target.chmod(0o644)
        target.write_text("{}")
        if case == "invalid-metadata":
            target.chmod(0o444)
        reason = "invalid"
    elif case != "context_unavailable":
        calls = []
        def failed_clone(src, dst, flags):
            calls.append(dst)
            Path(dst.decode()).write_text("partial output")
            if case in ("partial", "cleanup-error") and len(calls) == 1:
                return 0
            ctypes.set_errno(errno.EXDEV if case == "cross-device" else errno.ENOTSUP)
            return -1
        def api():
            if case == "unavailable":
                raise AttributeError("clonefile unavailable")
            return failed_clone
        monkeypatch.setattr(verify, "_clone_api", api)
        reason = "clone_failed"
    m = replace(m, prepare=(command("prepare", 'test -z "$(ls -A "$1")"; '
        'cp "$2/warm.sh" "$1/fetched"; ' + ("exit 7" if case == "prepare-failure" else "true"),
        "{snapshot:deps}", "{src}"),), verify=(command("check", 'cmp "$1/fetched" "$2/warm.sh"',
        "{snapshot:deps}", "{src}"),))
    out = tmp_path / "execution"
    if case == "cleanup-error":
        remove = shutil.rmtree
        def denied(path, *args, **kwargs):
            if Path(path).name == "deps":
                raise PermissionError("partial cleanup denied")
            return remove(path, *args, **kwargs)
        monkeypatch.setattr(shutil, "rmtree", denied)
        with pytest.raises(PermissionError, match="partial cleanup denied"):
            verify.run_extracted(repo, sha, m.verify, m, out, snapshots=context)
        assert not list(out.glob("*.log")) and not (out / "receipts.json").exists()
        assert json.loads((out / "snapshot-events.jsonl").read_text())["reason"] == "clone_failed"
        return
    rs = verify.run_extracted(repo, sha, m.verify, m, out,
                              snapshots=None if case == "context_unavailable" else context)
    assert [r.id for r in rs] == (["prepare"] if case == "prepare-failure" else ["prepare", "check"])
    assert verify.ok(rs, m.prepare + m.verify) == (case != "prepare-failure"), verify.summary(rs)
    assert rs[0].exit == (7 if case == "prepare-failure" else 0)
    assert (out / "check.log").exists() == (case != "prepare-failure")
    record = dict(id="deps", key=verify.snapshot_key(repo, m.snapshots[0]), sha=sha, reason=reason, out=str(out))
    assert json.loads((out / "snapshot-events.jsonl").read_text()) == dict(event="snapshot_fallback", **record)
    assert events == ([] if case == "context_unavailable" else [("snapshot_fallback", record)])
    assert list((out / "build/snapshots/deps").iterdir()) == [out / "build/snapshots/deps/fetched"]
    if case == "stale":
        assert record["key"] != key and (entry / "payload/dependency").read_text() == "base"
    verify.cleanup(out)
    assert not (out / "build").exists() and entry.exists() and (out / "snapshot-events.jsonl").exists()


@pytest.mark.scenario("verify-preparation/snapshot-fallback-events/legacy-manifests-keep-their-behavior")
@pytest.mark.parametrize("with_context", [False, True])
def test_legacy_snapshot_behavior(warming, tmp_path, with_context):
    repo, commit, m, context, events = warming
    sha = commit()
    m = replace(m, snapshots=(), vars={"value": "legacy"}, prepare=(command("prepare",
        'cp "$1/lock" "$2/dependency"; test "$3" = legacy; exit 3', "{src}", "{build}", "{value}", expect=3),),
        verify=(command("check", 'test "$(cat dependency)" = base; test "$1" = "$2"', "{sha}", sha),))
    out = tmp_path / "legacy"
    rs = verify.run_steps(repo, sha, m.verify, m, out, snapshots=context if with_context else None)
    assert verify.ok(rs, m.prepare + m.verify), verify.summary(rs)
    assert [(r.id, r.exit) for r in rs] == [("prepare", 3), ("check", 0)]
    assert [r["argv"] for r in json.loads((out / "receipts.json").read_text())] == [r.argv for r in rs]
    assert not events and not context.root.exists() and not (out / "snapshot-events.jsonl").exists()
    assert not (out / "build/snapshots").exists()
    verify.cleanup(out)
    assert not (out / "build").exists() and not (out / "src").exists() and (out / "receipts.json").exists()


@pytest.mark.scenario("verify-preparation/snapshot-fallback-events/missing-or-stale-snapshot-uses-existing-preparation")
@pytest.mark.parametrize("reason", ["missing", "stale"])
def test_overlapping_snapshot_ids_fall_back(warming, tmp_path, monkeypatch, reason):
    repo, commit, m, context, events = warming
    sha = commit()
    if reason == "stale":
        verify.warm_snapshots(repo, sha, m, context)
        sha = commit(lock="candidate")
    other = replace(m.snapshots[0], id="other-deps",
                    warm=tuple(arg.replace("snapshot:deps", "snapshot:other-deps") for arg in m.snapshots[0].warm))
    verify.warm_snapshots(repo, sha, replace(m, snapshots=(other,)), context)
    events.clear()
    monkeypatch.setattr(verify, "warm_snapshots", lambda *a: pytest.fail("consumer must never warm"))
    monkeypatch.setattr(verify, "_clone_payload", lambda *a: pytest.fail("another ID or stale key must not be cloned"))
    m = replace(m, prepare=(command("prepare", 'test -z "$(ls -A "$1")"; cp "$2/lock" "$1/fetched"',
        "{snapshot:deps}", "{src}"),), verify=(command("check", 'cmp "$1/fetched" "$2/lock"', "{snapshot:deps}", "{src}"),))
    out = tmp_path / "overlapping"
    receipts = verify.run_steps(repo, sha, m.verify, m, out, snapshots=context)
    assert verify.ok(receipts, m.prepare + m.verify), verify.summary(receipts)
    record = dict(id="deps", key=verify.snapshot_key(repo, m.snapshots[0]), sha=sha, reason=reason, out=str(out))
    assert json.loads((out / "snapshot-events.jsonl").read_text()) == dict(event="snapshot_fallback", **record)
    assert events == [("snapshot_fallback", record)]
