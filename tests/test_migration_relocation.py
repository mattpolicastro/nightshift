import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from specloop import migrate, state_locks
from test_migration_preflight import held_lock, roots, snapshot
from test_specloop_command_json import git


@pytest.mark.scenario("state-migration/destination-preserving-relocation/entries-move-without-replacement")
def test_entries_retain_evidence_and_lock_inodes(roots):
    source, destination = roots
    run = source / "runs/a"
    repo = run / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "history").write_text(f"immutable path: {source}/proposals/owner__repo\n")
    (repo / "history").chmod(0o640)
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "evidence")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/specloop/proposals/a", head)
    git(repo, "worktree", "add", "--detach", str(run / "work"), head)
    run.chmod(0o750)
    # Immediate regular-file entries are renamed too.
    (source / "homes/settings").write_text("retained settings")
    before = snapshot(roots)
    parents = {name: (source / name).stat().st_ino for name in migrate.MAPPING}
    with migrate.preflight() as intent:
        migrate.relocate(intent)
        assert all(entry["phase"] == "linked" for entry in intent["entries"])
        assert json.loads(state_locks.journal_path().read_text()) == intent
        # The original flock remains held even through the new path.
        probe = subprocess.run([sys.executable, "-c", """
import fcntl, sys
with open(sys.argv[1], 'rb') as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(42)
""", str(destination / "runs/a/lock")])
        assert probe.returncode == 42
    after = snapshot((destination,))
    for (root, relative), evidence in before.items():
        namespace, tail = relative.split("/", 1)
        mapped = f"{migrate.MAPPING[namespace]}/{tail}" if root == str(source) else relative
        assert after[str(destination), mapped] == evidence
    for entry in intent["entries"]:
        assert (source / entry["source"]).readlink() == destination / entry["destination"]
    assert {name: (source / name).stat().st_ino for name in parents} == parents
    assert git(destination / "runs/a/work", "rev-parse", "HEAD") == head
    assert git(destination / "runs/a/repo", "rev-parse", "refs/specloop/proposals/a") == head
    with held_lock(destination / "runs/a/lock"):
        pass  # Ownership released, same inode retained.


@pytest.mark.scenario("state-migration/destination-preserving-relocation/relative-links-retain-destinations")
def test_symlink_plan_preserves_internal_external_and_dangling_targets(roots, tmp_path, monkeypatch):
    source, destination = roots
    home = source / "homes/codex"
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "auth.json").write_text("synthetic credential sentinel")
    targets = {"auth.json": credentials / "auth.json", "external-directory": credentials,
               "dangling": credentials / "missing"}
    for name, target in targets.items():
        (home / name).symlink_to(os.path.relpath(target, home))
    (home / "internal").symlink_to("config.toml")
    (home / "internal-alias").symlink_to("auth.json")
    outside = source / "alias"
    outside.symlink_to(home / "config.toml")
    (home / "external-alias").symlink_to("../../alias")
    targets["external-alias"] = home / "config.toml"
    (source / "bridge").symlink_to(credentials)
    (home / "reentry").symlink_to("../../bridge/../homes/codex/missing")
    targets["reentry"] = tmp_path / "homes/codex/missing"
    (home / "internal-dangling").symlink_to("missing")
    (home / "nested").mkdir()
    (home / "nested/internal").symlink_to("../config.toml")
    (home / "absolute").symlink_to(credentials / "auth.json")
    original = {name: str((home / name).readlink()) for name in targets}
    open_file = io.open

    def no_credentials(path, *args, **kwargs):
        if not isinstance(path, int) and Path(path).resolve().is_relative_to(credentials):
            pytest.fail("migration opened linked credential contents")
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", no_credentials)
    replace = Path.replace
    rewrites = []

    def journal_before_rewrite(path, target):
        if path.name == "link":
            journal = json.loads(state_locks.journal_path().read_text())
            entry = next(e for e in journal["entries"] if e["source"] == "homes/codex")
            assert entry["phase"] == "planned"
            assert {link["path"] for link in entry["symlinks"]} == set(targets)
            rewrites.append(target)
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", journal_before_rewrite)
    with migrate.preflight() as intent:
        migrate.relocate(intent)
    assert len(rewrites) == len(targets)
    moved = destination / "homes/codex"
    assert (moved / "internal").readlink() == Path("config.toml")
    assert (moved / "internal-alias").readlink() == Path("auth.json")
    assert (moved / "internal-dangling").readlink() == Path("missing")
    assert (moved / "nested/internal").readlink() == Path("../config.toml")
    assert (moved / "absolute").readlink() == credentials / "auth.json"
    for name, target in targets.items():
        assert (moved / name).is_symlink()
        assert (moved / name).readlink() == target
        assert (home / name).resolve() == target.resolve()
    assert not (moved / "dangling").exists()
    entry = next(e for e in intent["entries"] if e["source"] == "homes/codex")
    assert {link["path"]: (link["original"], link["destination"]) for link in entry["symlinks"]} == {
        name: (original[name], str(target)) for name, target in targets.items()}
    link_inodes = {name: (moved / name).lstat().st_ino for name in targets}
    with migrate.preflight() as resumed:
        migrate.relocate(resumed)
    assert len(rewrites) == len(targets)
    assert {name: (moved / name).lstat().st_ino for name in targets} == link_inodes


@pytest.mark.scenario("state-migration/destination-preserving-relocation/entries-move-without-replacement")
def test_exdev_never_falls_back_to_copy_delete(roots, monkeypatch):
    before = snapshot(roots)

    def cross_device(src, dst):
        raise OSError(errno.EXDEV, "cross-device rename", str(src), None, str(dst))

    monkeypatch.setattr(Path, "rename", cross_device)
    with migrate.preflight() as intent:
        with pytest.raises(migrate.Refusal, match="cross-device rename"):
            migrate.relocate(intent)
    assert snapshot(roots) == before
    assert state_locks.journal_path().exists()
    assert not (roots[0] / migrate.MARKER).exists()


@pytest.mark.scenario("state-migration/destination-preserving-relocation/relative-links-retain-destinations")
@pytest.mark.parametrize("obstruction", ["missing", "regular-file"])
def test_dangling_parent_traversal_remains_dangling(roots, obstruction):
    source, destination = roots
    home = source / "homes/codex"
    (source / "auth.json").write_text("must never become the link destination")
    if obstruction == "regular-file":
        (source / obstruction).write_text("not a directory")
    original = f"../../{obstruction}/../auth.json"
    link = home / "auth.json"
    link.symlink_to(original)
    assert not link.exists()
    expected = source / obstruction / ".." / "auth.json"
    with migrate.preflight() as intent:
        migrate.relocate(intent)
    moved = destination / "homes/codex/auth.json"
    assert moved.readlink() == expected
    assert not moved.exists()
    assert not link.exists()
    journal = json.loads(state_locks.journal_path().read_text())
    entry = next(e for e in journal["entries"] if e["source"] == "homes/codex")
    assert entry["symlinks"] == [{"path": "auth.json", "original": original,
                                  "destination": str(expected)}]
    with migrate.preflight() as resumed:
        migrate.relocate(resumed)
    assert moved.readlink() == expected
    assert not moved.exists()


@pytest.mark.scenario("state-migration/destination-preserving-relocation/relative-links-retain-destinations")
@pytest.mark.parametrize("suffix", ["/", "/."])
@pytest.mark.parametrize("phase", ["planned", "rewritten", "moved", "linked"])
def test_directory_suffix_stays_dangling_through_relocation_and_resume(roots, monkeypatch, suffix, phase):
    source, destination = roots
    (source / "auth.json").touch()  # Synthetic regular file; inspect metadata only.
    legacy = source / "homes/codex/auth.json"
    canonical = destination / "homes/codex/auth.json"
    original = "../../auth.json" + suffix
    expected = str(source / "auth.json") + suffix
    legacy.symlink_to(original)

    def assert_dangling(path, target):
        assert os.readlink(path) == target
        with pytest.raises(OSError) as error:
            os.stat(path)
        assert error.value.errno == errno.ENOTDIR

    assert_dangling(legacy, original)
    save = migrate._save

    class Interrupted(BaseException):
        pass

    def interrupt(intent):
        save(intent)
        entry = next(e for e in intent["entries"] if e["source"] == "homes/codex")
        if entry["phase"] == phase:
            raise Interrupted

    with monkeypatch.context() as patch:
        patch.setattr(migrate, "_save", interrupt)
        with pytest.raises(Interrupted), migrate.preflight() as intent:
            migrate.relocate(intent)
    journal = json.loads(state_locks.journal_path().read_text())
    entry = next(e for e in journal["entries"] if e["source"] == "homes/codex")
    plan = [{"path": "auth.json", "original": original, "destination": expected}]
    assert entry["symlinks"] == plan
    assert_dangling(canonical if phase in ("moved", "linked") else legacy,
                    original if phase == "planned" else expected)
    with migrate.preflight() as resumed:
        migrate.relocate(resumed)
    for path in (canonical, legacy):
        assert_dangling(path, expected)
    inode = canonical.lstat().st_ino
    # Replay must compare raw targets, without normalizing away the suffix.
    with migrate.preflight() as resumed:
        migrate.relocate(resumed)
    assert canonical.lstat().st_ino == inode
    assert_dangling(canonical, expected)
    assert_dangling(legacy, expected)
    journal = json.loads(state_locks.journal_path().read_text())
    assert next(e for e in journal["entries"] if e["source"] == "homes/codex")["symlinks"] == plan


@pytest.mark.scenario("state-migration/destination-preserving-relocation/relative-links-retain-destinations")
@pytest.mark.parametrize("dangling", [False, True])
def test_intermediate_symlinks_determine_escape(roots, monkeypatch, dangling):
    source, destination = roots
    home = source / "homes/codex"
    target = source / "auth.json"
    if not dangling:
        target.write_text("synthetic credential sentinel")
    (destination / "auth.json").write_text("different canonical destination")
    (home / "alias").symlink_to(".")
    original = "alias/alias/../../auth.json"
    (home / "auth.json").symlink_to(original)
    assert (home / "auth.json").resolve() == target
    # Conversely, an alias can add real depth: this link stays within the entry
    # even though counting its lexical '..' components suggests an escape.
    (home / "deep/a/b").mkdir(parents=True)
    (home / "deep-alias").symlink_to("deep/a/b")
    internal = "deep-alias/../../../config.toml"
    (home / "internal").symlink_to(internal)
    assert (home / "internal").resolve() == home / "config.toml"
    open_file = io.open

    def no_credentials(path, *args, **kwargs):
        if not isinstance(path, int) and Path(path).resolve() == target:
            pytest.fail("migration opened linked credential contents")
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", no_credentials)
    with migrate.preflight() as intent:
        migrate.relocate(intent)
    moved = destination / "homes/codex"
    assert (moved / "auth.json").readlink() == target
    assert (moved / "auth.json").resolve() == target
    assert (moved / "auth.json").exists() is not dangling
    assert (home / "auth.json").resolve() == target
    assert (moved / "alias").readlink() == Path(".")
    assert (moved / "internal").readlink() == Path(internal)
    assert (moved / "internal").resolve() == moved / "config.toml"
    journal = json.loads(state_locks.journal_path().read_text())
    entry = next(e for e in journal["entries"] if e["source"] == "homes/codex")
    assert entry["symlinks"] == [{"path": "auth.json", "original": original,
                                  "destination": str(target)}]
