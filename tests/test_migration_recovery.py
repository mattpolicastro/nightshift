import json
from pathlib import Path
import subprocess
import sys

import pytest

from specloop import migrate, state, state_locks
from test_migration_preflight import roots, snapshot


# Inject real process exits immediately around the filesystem operations,
# including the window before the following journal phase is published.
CHILD = """
import os, sys
from pathlib import Path
from specloop import migrate, state
boundary, relative = sys.argv[1:]
source = state.legacy_root() / relative
operation, _, timing = boundary.partition(':')
def intercept(method, matches):
    original = getattr(Path, method)
    def wrapped(path, *args, **kwargs):
        hit = matches(path, *args, **kwargs)
        if hit and timing == 'before':
            os._exit(73)
        result = original(path, *args, **kwargs)
        if hit and timing == 'after':
            os._exit(73)
        return result
    setattr(Path, method, wrapped)
if operation == 'rewrite':
    intercept('replace', lambda path, target: Path(target) == source / 'external')
elif operation == 'rename':
    intercept('rename', lambda path, target: path == source)
elif operation == 'link':
    intercept('symlink_to', lambda path, *args, **kwargs: path == source)
elif operation == 'marker':
    intercept('replace', lambda path, target: Path(target).name == migrate.MARKER)
result = migrate.migrate()
assert 'specloop.serve' not in sys.modules  # No scheduler activation.
sys.exit(result)
"""


def child(boundary="", relative="runs/a"):
    return subprocess.run([sys.executable, "-c", CHILD, boundary, relative],
                          capture_output=True, text=True, timeout=20)


@pytest.mark.scenario("state-migration/journaled-completion/state-migrates-once-and-leaves-a-marker")
@pytest.mark.parametrize("empty", [False, True])
def test_state_migrates_once_and_leaves_a_marker(tmp_path, roots, monkeypatch, empty):
    source, destination = roots
    if empty:
        source, destination = tmp_path / "legacy", tmp_path / "canonical"
        monkeypatch.setenv("NIGHTSHIFT_LEGACY_STATE_ROOT", str(source))
        monkeypatch.setenv("NIGHTSHIFT_STATE_ROOT", str(destination))
    assert child().returncode == 0
    marker = source / migrate.MARKER
    document = json.loads(marker.read_text())
    assert document["version"] == 1 and document["completed"] is True
    assert document["source_root"] == str(source)
    assert document["destination_root"] == str(destination)
    assert all(e["phase"] == "linked" for e in document["entries"])
    assert not state_locks.journal_path().exists()
    # A late unsupported entry would refuse if completion scanned again.
    late = source / "runs/late"
    late.parent.mkdir(exist_ok=True)
    late.symlink_to(tmp_path / "missing")
    before, marker_stat = snapshot((source, destination)), marker.stat()

    def no_scan(*args, **kwargs):
        pytest.fail("completed migration rescanned entries")

    monkeypatch.setattr(Path, "iterdir", no_scan)
    monkeypatch.setattr(migrate, "relocate", no_scan)
    assert migrate.migrate() == 0
    assert child().returncode == 0
    assert snapshot((source, destination)) == before
    assert marker.stat() == marker_stat
    assert not (destination / "runs/late").exists()
    with state_locks.writer():
        pass  # Completion also releases the pending-journal write barrier.


@pytest.mark.scenario("state-migration/journaled-completion/state-migrates-once-and-leaves-a-marker")
def test_migration_creates_missing_destination_ancestors(tmp_path, monkeypatch):
    source = state.legacy_root()
    destination = tmp_path / "new" / "nested" / "canonical"
    monkeypatch.setenv("NIGHTSHIFT_STATE_ROOT", str(destination))
    old = source / "proposals/example"
    old.mkdir(parents=True)
    (old / "spec.md").write_text("draft evidence")
    inode = old.stat().st_ino
    assert not destination.parent.exists()

    result = child()
    assert result.returncode == 0, result.stderr
    new = destination / "drafts/example"
    assert old.readlink() == new
    assert new.stat().st_ino == inode
    assert (new / "spec.md").read_text() == "draft evidence"
    assert json.loads((source / migrate.MARKER).read_text())["completed"] is True
    assert not state_locks.journal_path().exists()
    before = snapshot((source, destination))
    assert child().returncode == 0
    assert snapshot((source, destination)) == before


@pytest.mark.parametrize("field", ["device", "inode"])
@pytest.mark.parametrize("value", [0, 1, -1, False])
def test_journal_identity_accepts_nonnegative_integers(roots, field, value):
    source, destination = roots
    with migrate.preflight() as intent:
        # Exercise the serialized identity boundary independently of whichever
        # device/inode numbers the host filesystem assigns to this fixture.
        intent["entries"][0][field] = value
        journal = state_locks.journal_path()
        journal.write_text(json.dumps(intent))
        if type(value) is int and value >= 0:
            assert migrate._intent(source, destination) == intent
        else:
            with pytest.raises(migrate.Refusal, match="Invalid migration journal"):
                migrate._intent(source, destination)


@pytest.mark.scenario("state-migration/journaled-completion/ambiguous-recovery-refuses")
@pytest.mark.parametrize("damage", ["completed-false", "completed-missing", "unlinked-entry"])
def test_invalid_marker_refuses_without_leftover_journal(roots, damage):
    source, destination = roots
    assert child().returncode == 0
    journal, marker = state_locks.journal_path(), source / migrate.MARKER
    assert not journal.exists()
    document = json.loads(marker.read_text())
    if damage == "completed-false":
        document["completed"] = False
    elif damage == "completed-missing":
        del document["completed"]
    else:
        document["entries"][0]["phase"] = "moved"
    marker.write_text(json.dumps(document))
    marker_bytes, before = marker.read_bytes(), snapshot((source, destination))

    result = child()
    assert result.returncode == 1
    assert "Invalid migration marker" in result.stderr
    assert marker.read_bytes() == marker_bytes
    assert snapshot((source, destination)) == before
    assert not journal.exists()


@pytest.mark.scenario("state-migration/journaled-completion/interrupted-transitions-resume")
@pytest.mark.parametrize("relative,mapped", [("runs/a", "runs/a"),
                                           ("proposals/owner__repo", "drafts/owner__repo")])
@pytest.mark.parametrize("boundary", [f"{op}:{when}" for op in ("rewrite", "rename", "link", "marker")
                                     for when in ("before", "after")])
def test_fresh_process_recovery(roots, relative, mapped, boundary):
    source, destination = roots
    old, new = source / relative, destination / mapped
    (source / "evidence").write_text("external evidence")
    (old / "external").symlink_to("../../evidence")
    (old / "external-two").symlink_to("../../evidence")
    (old / "internal").symlink_to("state.json" if relative.startswith("runs/") else "spec.md")
    (source / "runs/a/state.json").write_text(json.dumps({"change": "a", "status": "paused",
                                                       "pause": {"reason": "infra", "resume_at": 1}}))
    identity = old.stat().st_ino
    evidence = {p.name: p.read_bytes() for p in old.iterdir() if p.is_file() and not p.is_symlink()}
    daemon_before = {k: v for k, v in snapshot((destination,)).items() if k[1].startswith("proposals/")}
    stopped = child(boundary, relative)
    assert stopped.returncode == 73, stopped.stderr
    journal = state_locks.journal_path()
    assert journal.exists()
    intent = json.loads(journal.read_text())
    entry = next(e for e in intent["entries"] if e["source"] == relative)
    assert entry["destination"] == mapped and entry["inode"] == identity
    actual = old if old.exists() else new
    assert (actual / "external").read_text() == "external evidence"
    assert (actual / "external-two").read_text() == "external evidence"
    if boundary == "rewrite:after":
        assert (actual / "external-two").readlink() == Path("../../evidence")
    if boundary in ("rename:after", "link:before"):
        assert not old.exists() and new.exists()
        before, journal_bytes, journal_stat = snapshot(roots), journal.read_bytes(), journal.stat()
        assert migrate.resolve_recorded(relative) == new
        if relative.startswith("runs/"):
            assert state.run_dir("a") == new
            assert [p.name for p in state.iter_run_dirs()].count("a") == 1
            assert state.Run.for_change("a", require_existing=True).state.status == "paused"
        assert snapshot(roots) == before
        assert journal.read_bytes() == journal_bytes and journal.stat() == journal_stat
        assert not old.is_symlink()  # A reader must not repair the gap.
    # Recovery is restricted to the persisted plan, not newly discovered state.
    late = source / "homes/late"
    late.write_text("not part of the interrupted attempt")
    resumed = child()
    assert resumed.returncode == 0, resumed.stderr
    assert new.stat().st_ino == identity and old.readlink() == new
    assert {name: (new / name).read_bytes() for name in evidence} == evidence
    assert (new / "external").readlink() == source / "evidence"
    assert (new / "external-two").readlink() == source / "evidence"
    assert not (new / "internal").readlink().is_absolute()
    assert late.is_file() and not late.is_symlink()
    assert not (destination / "homes/late").exists()
    assert not journal.exists() and (source / migrate.MARKER).exists()
    assert {k: v for k, v in snapshot((destination,)).items() if k[1].startswith("proposals/")} == daemon_before
    before = snapshot(roots)
    assert child().returncode == 0
    assert snapshot(roots) == before


@pytest.mark.scenario("state-migration/journaled-completion/ambiguous-recovery-refuses")
@pytest.mark.parametrize("damage", ["json", "shape", "version", "root", "phase", "plan", "identity",
                                    "phase-location", "link-target", "journal-symlink",
                                    "mapping", "missing", "conflict", "symlink", "marker-json",
                                    "marker-root", "marker-incomplete", "marker-mismatch"])
def test_ambiguous_recovery_preserves_evidence(roots, damage):
    source, destination = roots
    (source / "runs/a/external").symlink_to("../../evidence")
    assert child("rename:after").returncode == 73
    journal, marker = state_locks.journal_path(), source / migrate.MARKER
    intent = json.loads(journal.read_text())
    entry = next(e for e in intent["entries"] if e["source"] == "runs/a")
    if damage in ("json", "shape"):
        journal.write_text("{" if damage == "json" else "null")
    elif damage == "journal-symlink":
        journal.rename(destination / "retained-journal")
        journal.symlink_to(destination / "retained-journal")
    elif damage.startswith("marker-"):
        completed = {**intent, "completed": True}
        for e in completed["entries"]:
            e["phase"] = "linked"
        if damage == "marker-root":
            completed["destination_root"] = str(source)
        elif damage == "marker-incomplete":
            completed["completed"] = False
        marker.write_text("{" if damage == "marker-json" else json.dumps(completed))
    elif damage == "missing":
        (destination / "runs/a").rename(destination / "retained")
    elif damage == "conflict":
        (source / "runs/a").mkdir()
        (source / "runs/a/evidence").write_text("conflicting source")
    elif damage == "symlink":
        (source / "runs/a").symlink_to(destination / "proposals/owner__repo")
    elif damage == "link-target":
        (destination / "runs/a/external").unlink()
        (destination / "runs/a/external").symlink_to("different-evidence")
    else:
        if damage == "version":
            intent["version"] = 2
        elif damage == "root":
            intent["source_root"] = str(destination)
        elif damage == "phase":
            entry["phase"] = "unknown"
        elif damage == "phase-location":
            entry["phase"] = "planned"
        elif damage == "identity":
            entry["inode"] += 1
        elif damage == "mapping":
            entry["destination"] = "proposals/a"
        elif damage == "plan":
            entry["symlinks"] = [{"path": "../outside", "original": "x", "destination": "/outside"}]
        journal.write_text(json.dumps(intent))
    before, journal_bytes = snapshot(roots), journal.read_bytes()
    marker_bytes = marker.read_bytes() if marker.exists() else None
    result = child()
    assert result.returncode == 1 and "Migration refused:" in result.stderr
    assert snapshot(roots) == before
    assert journal.read_bytes() == journal_bytes
    assert (marker.read_bytes() if marker.exists() else None) == marker_bytes
    if not damage.startswith("marker-") and damage != "link-target":
        with pytest.raises((migrate.Refusal, ValueError)):
            state.run_dir("a")
