import errno
import os
from dataclasses import replace
from pathlib import Path

import pytest

from specloop import verify
from test_snapshot_warming import warming  # noqa: F401


@pytest.mark.scenario("verify-preparation/run-snapshot-retention/fourth-publication-evicts-oldest-entry")
def test_retention_across_ids_and_runs(warming, tmp_path):
    repo, commit, m, context, events = warming
    base = commit()
    other = verify.SnapshotContext(tmp_path / "other-run/snapshots")
    verify.warm_snapshots(repo, base, m, other)
    other_files = {p: p.read_bytes() for p in other.root.rglob("*") if p.is_file()}
    diagnostics = {}
    for number in range(4):
        definition = replace(m.snapshots[0], id=f"deps{number}",
                             warm=tuple(s.replace("snapshot:deps", f"snapshot:deps{number}")
                                        for s in m.snapshots[0].warm))
        current = replace(m, snapshots=(definition,))
        if number == 0:
            first = current
        if number == 3:
            verify.warm_snapshots(repo, base, first, context)
            assert events[-1][0] == "snapshot_reused"
        verify.warm_snapshots(repo, base, current, context)
        for entry, data in verify._snapshot_entries(context.root):
            os.utime(entry, (1, 1))  # Age and access time do not choose victims.
        diagnostics.update({p: p.read_bytes() for p in (context.root / "warms").rglob("*") if p.is_file()})
    assert sorted(d["sequence"] for _, d in verify._snapshot_entries(context.root)) == [2, 3, 4]
    assert {d["id"] for _, d in verify._snapshot_entries(context.root)} == {"deps1", "deps2", "deps3"}
    assert all(p.read_bytes() == content for p, content in diagnostics.items())
    assert {p: p.read_bytes() for p in other.root.rglob("*") if p.is_file()} == other_files
    assert [(d["sequence"], (p / "payload/dependency").read_text())
            for p, d in verify._snapshot_entries(other.root)] == [(1, "base")]


@pytest.mark.scenario("verify-preparation/run-snapshot-retention/interrupted-retention-recovers")
@pytest.mark.parametrize("operation", ["lstat", "read_text"])
def test_retention_reports_inspection_io_errors(warming, monkeypatch, operation):
    repo, commit, m, context, events = warming
    with monkeypatch.context() as patch:
        patch.setattr(verify, "_retain_snapshots", lambda context: None)
        for number in range(4):
            sha = commit(lock=str(number))
            verify.warm_snapshots(repo, sha, m, context)
    before = {p for p, _ in verify._snapshot_entries(context.root)}
    oldest = min(before, key=lambda p: int(p.name.split("-")[0]))
    original = getattr(Path, operation)
    def unreadable(path, *args, **kwargs):
        if path == oldest / "metadata.json":
            raise OSError(errno.EIO, "entry inspection failed")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, operation, unreadable)
        verify._retain_snapshots(context)
        assert events[-1] == ("snapshot_retention_failed", dict(
            error=str(OSError(errno.EIO, "entry inspection failed")), root=str(context.root)))
        assert {p for p, _ in verify._snapshot_entries(context.root)} == before - {oldest}
    assert {p for p, _ in verify._snapshot_entries(context.root)} == before
    verify.warm_snapshots(repo, sha, m, verify.SnapshotContext(context.root, context.event))
    assert events[-1][0] == "snapshot_reused"
    assert sorted(d["sequence"] for _, d in verify._snapshot_entries(context.root)) == [2, 3, 4]


@pytest.mark.scenario("verify-preparation/run-snapshot-retention/interrupted-retention-recovers")
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("partial", [False, True])
def test_interrupted_retention_recovers(warming, monkeypatch, failure, partial):
    repo, commit, m, context, events = warming
    for number in range(3):
        verify.warm_snapshots(repo, commit(lock=str(number)), m, context)
    before = {p for p, _ in verify._snapshot_entries(context.root)}
    verify.warm_snapshots(repo, commit("exit 1", "bad"), m, context)
    assert {p for p, _ in verify._snapshot_entries(context.root)} == before
    oldest = min(before, key=lambda p: int(p.name.split("-")[0]))
    # Foreign names, malformed metadata and links are never pruning targets.
    foreign = context.root / "entries/foreign"
    foreign.symlink_to(oldest, target_is_directory=True)
    malformed = context.root / ("entries/1-invalid-" + "0" * 64)
    malformed.mkdir()
    (malformed / "metadata.json").write_text("{}")
    target, method = (verify.shutil, "rmtree") if partial else (verify, "_remove_snapshot_tree")
    original = getattr(target, method)
    def interrupted(path, *args, **kwargs):
        if Path(path) == oldest:
            if partial:
                (oldest / "payload/dependency").unlink()
                (oldest / "metadata.json").unlink()
            raise failure("interrupted prune")
        return original(path, *args, **kwargs)
    sha = commit(lock="fourth")
    with monkeypatch.context() as patch:
        patch.setattr(target, method, interrupted)
        if failure is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                verify.warm_snapshots(repo, sha, m, context)
        else:
            verify.warm_snapshots(repo, sha, m, context)
            assert events[-1][0] == "snapshot_retention_failed"
    assert oldest.exists() and list((context.root / "entries").glob(".prune-*"))
    verify.warm_snapshots(repo, sha, m, verify.SnapshotContext(context.root, context.event))
    assert events[-1][0] == "snapshot_reused"
    assert not oldest.exists() and not list((context.root / "entries").glob(".prune-*"))
    assert foreign.is_symlink() and (malformed / "metadata.json").read_text() == "{}"
    assert sorted(d["sequence"] for _, d in verify._snapshot_entries(context.root)) == [2, 3, 4]
