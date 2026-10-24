import hashlib
import json
import os
import stat

import pytest

from specloop import revision_store as store, seal
from specloop.state import Run


@pytest.fixture
def revision(tmp_path):
    run = Run(tmp_path / "change")
    run.pause("ready_to_publish")
    run.request_pause()
    repo = run.dir / "repo.git"
    repo.mkdir()
    seal.git(repo, "init", "--bare")
    tree = seal.git(repo, "mktree", input=b"").decode().strip()
    parent = seal.git(repo, "commit-tree", tree, "-m", "base", env=seal.IDENTITY).decode().strip()
    seal.git(repo, "update-ref", "refs/heads/spec/change", parent)
    candidate = seal.commit_files(repo, parent, {"tasks.md": "## 1. Review round 1\n- [ ] fix it\n"}, "review")
    journal = store.RevisionStore(run.dir, "change", "spec/change", "round:1", ["fix it"])
    return run, journal, dict(parent=parent, commit=candidate, number=1)


def control_snapshot(run):
    return ([(run.dir / name).read_bytes() for name in ("state.json", "events.jsonl", "pause-requested")],
            seal.git(run.dir / "repo.git", "show-ref"))


@pytest.mark.scenario("specloop-interface/durable-revision-identity/preparation-precedes-mutation")
@pytest.mark.parametrize("fail_at", [None, "replace", "fsync"])
def test_preparation_is_durable_before_branch_mutation(revision, monkeypatch, fail_at):
    run, journal, candidate = revision
    before = control_snapshot(run)
    operations = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        operations.append(kind)
        if fail_at == "fsync" and kind == "file":
            raise OSError("injected fsync failure")
        real_fsync(fd)

    def replace(source, target):
        assert operations[-1] == "file"
        with open(source) as stream:
            assert json.load(stream)["commit"] == candidate["commit"]
        assert control_snapshot(run) == before
        if fail_at == "replace":
            raise OSError("injected replace failure")
        operations.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(store.os, "fsync", fsync)
    monkeypatch.setattr(store.os, "replace", replace)
    if fail_at:
        with pytest.raises(OSError, match="injected"):
            journal.prepare(**candidate)
        assert journal.load() is None
        assert list(journal.path.parent.iterdir()) == []
    else:
        record = journal.prepare(**candidate)
        assert operations == ["directory", "file", "replace", "directory"]
        assert journal.load() == record and record["status"] == "prepared"
        assert record["state_digest"] == hashlib.sha256(before[0][0]).hexdigest()
        assert record["events_digest"] == hashlib.sha256(before[0][1]).hexdigest()
    assert control_snapshot(run) == before
    assert seal.git(run.dir / "repo.git", "ls-tree", candidate["parent"]) == b""


@pytest.mark.scenario("specloop-interface/durable-revision-identity/identity-survives-state-rewrites")
def test_state_rewrite_and_completion_preserve_identity(revision, monkeypatch):
    run, journal, candidate = revision
    record = journal.prepare(**candidate)
    record["future"] = {"retained": True}
    journal.path.write_text(json.dumps(record))
    state_path = run.dir / "state.json"
    state_path.write_text(json.dumps({**json.loads(state_path.read_text()), "future": "dropped"}))
    Run(run.dir).save()
    assert "future" not in json.loads(state_path.read_text())
    restarted = store.RevisionStore(run.dir, "change", "spec/change", "round:1", ["fix it"])
    assert restarted.load() == record
    before = journal.path.read_bytes()
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("replacement failed")
        patch.setattr(store.os, "replace", fail)
        with pytest.raises(OSError, match="replacement failed"):
            restarted.complete()
    assert journal.path.read_bytes() == before
    assert list(journal.path.parent.iterdir()) == [journal.path]
    assert restarted.complete() == {**record, "status": "completed"}
    completed = journal.path.read_bytes()
    restarted.complete()
    assert journal.path.read_bytes() == completed and run.pause_requested()
    with pytest.raises(ValueError, match="already exists"):
        restarted.prepare(**candidate)


@pytest.mark.scenario("specloop-interface/durable-revision-identity/conflicting-or-corrupt-evidence-is-refused")
@pytest.mark.parametrize("corruption", [
    "{", "[]", "null", {"version": 2}, {"version": True}, {"version": 1.0},
    {"round": 0}, {"round": True}, {"tasks": 2}, {"tasks": True},
    {"parent": "bad"}, {"commit": None}, {"state_digest": []}, {"events_digest": ""},
    {"payload_digest": "f" * 64}, {"change": "other"}, {"branch": "other"},
    {"round_id": "other"}, {"status": []}, {"status": "unknown"}, "missing",
])
def test_conflicting_or_malformed_evidence_is_preserved(revision, corruption):
    run, journal, candidate = revision
    record = journal.prepare(**candidate)
    if corruption == "missing":
        del record["state_digest"]
    journal.path.write_text(json.dumps(record | corruption) if isinstance(corruption, dict)
                            else json.dumps(record) if corruption == "missing" else corruption)
    before, evidence = control_snapshot(run), journal.path.read_bytes()
    for operation in (journal.load, journal.complete, lambda: journal.prepare(**candidate)):
        with pytest.raises(ValueError, match="revision record"):
            operation()
        assert journal.path.read_bytes() == evidence and control_snapshot(run) == before


@pytest.mark.parametrize("key", ["", "../escape", "/absolute", ".first", "a/b", "é", "a\n", "a" * 129, None])
def test_invalid_keys_refused(tmp_path, key):
    with pytest.raises(ValueError, match="round_id"):
        store.RevisionStore(tmp_path, "change", "branch", key, ["fix"])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change,branch,blocks", [
    ("other", "spec/change", ["fix it"]), ("change", "other", ["fix it"]),
    ("change", "spec/change", ["different feedback"]),
])
def test_retry_identity_conflicts(revision, change, branch, blocks):
    run, journal, candidate = revision
    journal.prepare(**candidate)
    before, evidence = control_snapshot(run), journal.path.read_bytes()
    retry = store.RevisionStore(run.dir, change, branch, "round:1", blocks)
    with pytest.raises(ValueError, match="conflict"):
        retry.prepare(**candidate)
    assert journal.path.read_bytes() == evidence and control_snapshot(run) == before


def test_payload_canonicalization_and_fingerprints(tmp_path):
    key = "A._:-" + "9" * 123
    journal = store.RevisionStore(tmp_path, "change", "branch", key, ["fix"])
    assert journal.path.name == hashlib.sha256(key.encode()).hexdigest() + ".json"
    with pytest.raises(ValueError, match="prepare before completion"):
        journal.complete()
    digest = store.payload_digest(["é", "fix"])
    assert digest == store.payload_digest(json.loads('[ "\\u00e9", "fix" ]'))
    for blocks in (["fix", "é"], ["é", "fix", "fix"], ["é", " fix"]):
        assert store.payload_digest(blocks) != digest
    for blocks in ([], [" "], [1], "fix"):
        with pytest.raises(ValueError, match="feedback"):
            store.payload_digest(blocks)
    path = tmp_path / "events.jsonl"
    assert store.fingerprint(path) is None
    path.touch()
    assert store.fingerprint(path) == hashlib.sha256(b"").hexdigest()
