from contextlib import contextmanager
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from specloop import agents, cli, migrate, state, state_locks

@pytest.fixture
def roots():
    source, destination = state.legacy_root(), state.state_root()
    for root, names in ((source, ("runs/a/state.json", "runs/a/lock", "runs/z/lock", "proposals/owner__repo/spec.md",
                                   "homes/codex/config.toml")),
                        (destination, ("runs/other/state.json", "drafts/other/spec.md", "homes/other/config.toml",
                                       "proposals/owner__repo/7.json", "proposals/owner__repo/7.jobs/request.json",
                                       "proposals/owner__repo/7.jobs/result.json"))):
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
    return source, destination


def snapshot(roots):
    # Global lock files alone may be added on refusal. Include directory
    # metadata, bytes and lock inodes throughout every source/destination tree.
    return {(str(root), str(path.relative_to(root))):
            (path.lstat().st_ino, path.lstat().st_mode, path.lstat().st_mtime_ns,
             str(path.readlink()) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
            for root in roots for namespace in ("runs", "proposals", "drafts", "homes")
            for path in (root / namespace).rglob("*")}


@contextmanager
def held_lock(path):
    child = subprocess.Popen([sys.executable, "-c", """
import fcntl, sys
with open(sys.argv[1], 'rb') as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    print('locked', flush=True)
    sys.stdin.read()
""", str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        yield
    finally:
        child.communicate(timeout=10)
        assert child.returncode == 0


@pytest.mark.scenario("state-migration/migration-preflight/locked-run-is-not-moved")
def test_locked_run_is_not_moved(roots):
    source, destination = roots
    before = snapshot(roots)
    with held_lock(source / "runs/z/lock"):
        with pytest.raises(migrate.Refusal, match="Run z is locked.*pause/stop.*retry"):
            with migrate.preflight():
                pytest.fail("a locked run must refuse the entire plan")
    assert snapshot(roots) == before
    assert not (source / migrate.MARKER).exists()
    assert not state_locks.journal_path().exists()
    with migrate.preflight() as intent:
        assert {e["source"]: e["destination"] for e in intent["entries"]} == {
            "runs/a": "runs/a", "runs/z": "runs/z", "proposals/owner__repo": "drafts/owner__repo",
            "homes/codex": "homes/codex"}
    assert snapshot(roots) == before


@pytest.mark.scenario("state-migration/migration-preflight/conflicts-preserve-existing-evidence")
@pytest.mark.parametrize("conflict", ["runs/a", "drafts/owner__repo", "homes/codex", "overlap", "device"])
def test_conflicts_preserve_existing_evidence(roots, monkeypatch, conflict):
    source, destination = roots
    if conflict == "overlap":
        monkeypatch.setattr(state, "ROOT", source / "nested")
    elif conflict == "device":
        monkeypatch.setattr(migrate, "_device", lambda path: -1)
    else:
        path = destination / conflict
        path.mkdir(parents=True)
        (path / "evidence").write_text("must survive")
    before = snapshot(roots)
    with pytest.raises(migrate.Refusal, match="Overlapping|Cross-device|destination already exists"):
        with migrate.preflight():
            pytest.fail("conflicting evidence must refuse")
    assert snapshot(roots) == before
    assert not (source / migrate.MARKER).exists()
    assert not state_locks.journal_path().exists()


@pytest.mark.scenario("state-migration/migration-preflight/locked-run-is-not-moved")
def test_journaled_destination_run_lock_is_checked(roots):
    source, destination = roots
    with migrate.preflight() as intent:
        state_locks.journal_path().write_text(json.dumps(intent))
    (source / "runs/z").rename(destination / "runs/z")  # interrupted rename/link gap
    before = snapshot(roots)
    with held_lock(destination / "runs/z/lock"):
        with pytest.raises(migrate.Refusal, match="Run z is locked"):
            with migrate.preflight():
                pytest.fail("recovery must own the moved inode too")
    with migrate.preflight() as resumed:
        assert resumed == intent
    assert snapshot(roots) == before


@pytest.mark.scenario("state-migration/migration-preflight/conflicts-preserve-existing-evidence")
@pytest.mark.parametrize("unsafe", ["daemon-destination", "traversal", "mapped-parent", "entry-link", "root"])
def test_unsafe_paths_refuse_without_changing_evidence(roots, unsafe):
    source, destination = roots
    if unsafe in ("daemon-destination", "traversal"):
        with migrate.preflight() as intent:
            draft = next(e for e in intent["entries"] if e["source"].startswith("proposals/"))
            if unsafe == "daemon-destination":
                draft["destination"] = draft["source"]
            else:
                draft["source"] = "proposals/../homes"
            state_locks.journal_path().write_text(json.dumps(intent))
    elif unsafe == "mapped-parent":
        (destination / "homes").rename(destination / "saved-homes")
        (destination / "homes").symlink_to(destination / "saved-homes")
    elif unsafe == "root":
        source.rename(source.with_name("saved-legacy"))
        source.symlink_to(source.with_name("saved-legacy"))
    else:
        (source / "proposals/unsupported").symlink_to(destination / "proposals/owner__repo")
    before = snapshot(roots)
    with pytest.raises(migrate.Refusal, match="Invalid migration journal|real directory|Unsupported legacy symlink"):
        with migrate.preflight():
            pytest.fail("unsafe path accepted")
    assert snapshot(roots) == before
    assert not (source / migrate.MARKER).exists()


WRITERS = {"admit": "admit", "run": "run_cmd", "propose": "propose", "revise": "revise",
           "act": "act", "resume": "resume", "accept": "accept", "waive": "waive", "abort": "abort",
           "publish": "publish", "finish": "finish", "drill": "drill_cmd", "pause": "pause_cmd"}


@pytest.mark.scenario("state-migration/migration-preflight/writers-coordinate-with-migration")
@pytest.mark.parametrize("command", WRITERS)
def test_production_writers_own_coordination_before_handlers(roots, monkeypatch, capsys, command):
    entered = []

    def handler(args):
        # This is the earliest point a handler could resolve a state path.
        with pytest.raises(state_locks.MigrationBusy, match="writers.*active"):
            with migrate.preflight():
                pytest.fail("migration acquired exclusive ownership during a writer")
        entered.append(True)
        return 0

    monkeypatch.setattr(cli, WRITERS[command], handler)
    args = SimpleNamespace(cmd=command, json=True, verb="resume")
    assert cli.dispatch(args) == 0
    capsys.readouterr()
    assert entered == [True]
    state_locks.journal_path().write_text('{"incomplete": true}')
    assert cli.dispatch(args) == 1
    assert entered == [True]  # Refused before any handler/path selection.
    document = json.loads(capsys.readouterr().out)
    assert "nightshift migrate" in document.get("error", document.get("detail", ""))
    if command == "act":
        assert set(document) == {"version", "outcome", "verb", "detail"}
        assert document["outcome"] == "refused" and document["verb"] == "resume"


@pytest.mark.scenario("state-migration/migration-preflight/writers-coordinate-with-migration")
def test_home_user_and_exclusive_migration_exclude_each_other(roots, monkeypatch):
    from specloop.agents import codex

    def adapter(call):
        with pytest.raises(state_locks.MigrationBusy):
            with migrate.preflight():
                pytest.fail("agent home is in use")
        return agents.Result("infra")

    monkeypatch.setattr(codex, "run", adapter)
    call = SimpleNamespace(profile=SimpleNamespace(adapter="codex", base_url=""))
    assert agents.run(call).disposition == "infra"
    with state_locks.serve_lock() as owned:
        with migrate.preflight(owned):
            with pytest.raises(state_locks.MigrationBusy):
                agents.run(call)
    state_locks.journal_path().write_text("pending")
    with pytest.raises(state_locks.MigrationBusy, match="nightshift migrate"):
        agents.run(call)
