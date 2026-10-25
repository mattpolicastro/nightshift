import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from specloop import manifest, revise, seal, tasks
from specloop.revision_store import RevisionStore
from specloop.state import Busy, Run
from test_specloop_command_json import revision_run, template


BLOCKS = ['Keep the "café" label.\n- [ ] Regression test.', 'Handle empty input.']
FEEDBACK = json.dumps(BLOCKS)


@pytest.fixture
def ctx(revision_run):
    run, _ = revision_run
    m = manifest.load(run.manifest_path)
    return SimpleNamespace(run=run, m=m, repo=run.dir / "repo.git", ref=f"refs/heads/{m.branch}",
                           path=f"openspec/changes/{m.change}/tasks.md",
                           store=RevisionStore(run.dir, m.change, m.branch, "round:1", BLOCKS))


def call(ctx, **kwargs):
    return revise.revise_result(Run(ctx.run.dir, require_existing=True), ctx.m,
                                kwargs.pop("feedback", FEEDBACK), feedback_format="json",
                                **({"round_id": "round:1"} | kwargs))


def snapshot(ctx):
    return (seal.git(ctx.repo, "show-ref"),
            {p.name: p.read_bytes() for p in ctx.run.dir.iterdir() if p.is_file() and p.name != "lock"},
            {p.name: p.read_bytes() for p in (ctx.run.dir / "revision-rounds").glob("*.json")})


class Interrupted(Exception):
    pass


def interrupt(ctx, monkeypatch, boundary):
    owner, name = {"prepared": (RevisionStore, "prepare"), "ref": (seal, "git"),
                   "state": (Run, "save"), "event": (Run, "event")}[boundary]
    original = getattr(owner, name)

    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        if boundary != "ref" or args[1] == "update-ref":
            raise Interrupted()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(owner, name, interrupted)
        with pytest.raises(Interrupted):
            call(ctx)
    return ctx.store.load()


@pytest.mark.scenario("specloop-interface/revision-round/feedback-becomes-tasks")
@pytest.mark.parametrize("key", [None, "round:1"])
def test_append_and_string_api(ctx, key):
    result = call(ctx, round_id=key)
    assert (result.round_id, result.replayed, result.status, result.pause) == (key, False, "running", None)
    text = seal.git(ctx.repo, "show", f"{result.commit}:{ctx.path}").decode()
    added = tasks.parse(text)[-2:]
    assert [json.loads(t.text.split(": ", 1)[1]) for t in added] == BLOCKS
    assert all(not t.done and t.section_title == "Review round 1" for t in added)
    commit = revise.revise(Run(ctx.run.dir), ctx.m, "\n---\n".join(BLOCKS), round_id=key)
    assert isinstance(commit, str) and (commit == result.commit) == (key is not None)
    assert len(tasks.parse(seal.git(ctx.repo, "show", f"{commit}:{ctx.path}").decode())) == (3 if key else 5)


@pytest.mark.scenario("specloop-interface/revision-round/empty-feedback-refused")
@pytest.mark.parametrize("key", [None, "round:1"])
@pytest.mark.parametrize("feedback", ["[]", '["", "  "]'])
def test_empty_feedback(ctx, key, feedback):
    before = snapshot(ctx)
    with pytest.raises(ValueError, match="empty"):
        call(ctx, round_id=key, feedback=feedback)
    assert snapshot(ctx) == before


@pytest.mark.scenario("specloop-interface/durable-revision-identity/preparation-precedes-mutation")
@pytest.mark.parametrize("failure", [False, True])
def test_preparation_before_ref(ctx, monkeypatch, failure):
    before = snapshot(ctx)
    original = os.replace

    def replace(source, target):
        if Path(target) == ctx.store.path:
            assert snapshot(ctx) == before
            record = json.loads(Path(source).read_text())
            assert record["status"] == "prepared" and record["tasks"] == 2
            assert seal.git(ctx.repo, "cat-file", "-t", record["commit"]) == b"commit\n"
            if failure:
                raise OSError("journal write failed")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        if failure:
            with pytest.raises(OSError, match="journal write failed"):
                call(ctx)
            assert snapshot(ctx) == before and ctx.store.load() is None
        else:
            # Stop before completion, so the replacement assertion concerns preparation only.
            interrupt(ctx, patch, "ref")
    result = call(ctx)
    assert ctx.store.load()["commit"] == result.commit


@pytest.mark.scenario("specloop-interface/idempotent-revision-recovery/retry-after-commit-does-not-duplicate-round")
@pytest.mark.parametrize("boundary", ["ref", "state", "event"])
def test_fresh_process_replays_original(ctx, monkeypatch, tmp_path, boundary):
    record = interrupt(ctx, monkeypatch, boundary)
    home = tmp_path / "child-home"
    home.mkdir()
    root = Path(__file__).resolve().parents[1]
    script = """import json, sys
from pathlib import Path
from dataclasses import asdict
from specloop import manifest, revise, state
assert Path(revise.__file__).resolve() == Path(sys.argv[1]) / 'specloop/revise.py'
assert state.ROOT is None
assert state.state_root() == Path.home() / '.nightshift'
assert state.legacy_root() == Path.home() / '.specloop'
run = state.Run(Path(sys.argv[2]), require_existing=True)
result = revise.revise_result(run, manifest.load(run.manifest_path), sys.argv[3],
                              feedback_format='json', round_id='round:1')
print(json.dumps(asdict(result)))
"""
    child = subprocess.run([sys.executable, "-c", script, str(root), str(ctx.run.dir), FEEDBACK],
                           cwd=root, env={**os.environ, "HOME": str(home), "PYTHONPATH": str(root),
                                          "NIGHTSHIFT_STATE_ROOT": str(home / ".nightshift"),
                                          "NIGHTSHIFT_LEGACY_STATE_ROOT": str(home / ".specloop")},
                           capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == dict(commit=record["commit"], round_id="round:1",
                                           replayed=True, status="running", pause=None)
    before = snapshot(ctx)
    assert call(ctx).commit == record["commit"] and snapshot(ctx) == before
    text = seal.git(ctx.repo, "show", f"{ctx.ref}:{ctx.path}").decode()
    assert text.count("Review round ") == 1 and len(tasks.parse(text)) == 3
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e.get("round_id") == "round:1" and e["event"] == "revised"]) == 1


@pytest.mark.scenario("specloop-interface/idempotent-revision-recovery/prepared-candidate-resumes-once")
@pytest.mark.parametrize("stage", ["new", "prepared", "completed"])
def test_lock_serializes_all_paths(ctx, monkeypatch, stage):
    if stage == "prepared":
        interrupt(ctx, monkeypatch, "prepared")
    elif stage == "completed":
        call(ctx)
    before = snapshot(ctx)
    with ctx.run.lock() as mine:
        assert mine
        with pytest.raises(Busy):
            call(ctx)
    assert snapshot(ctx) == before
    assert call(ctx).commit == ctx.store.load()["commit"]


@pytest.mark.scenario("specloop-interface/idempotent-revision-recovery/prepared-candidate-resumes-once")
@pytest.mark.parametrize("blocked", ["pause", "cas", None])
def test_prepared_candidate(ctx, monkeypatch, blocked):
    record = interrupt(ctx, monkeypatch, "prepared")
    if blocked == "pause":
        ctx.run.pause("stuck")
        before = snapshot(ctx)
        with pytest.raises(ValueError, match="stuck"):
            call(ctx)
        assert snapshot(ctx) == before
        ctx.run.pause("human_checks")
    elif blocked == "cas":
        original = seal.git
        other = seal.commit_files(ctx.repo, record["parent"], {"other": "concurrent"}, "other")
        def race(repo, *args, **kwargs):
            if args[0] == "update-ref":
                original(repo, "update-ref", ctx.ref, other, record["parent"])
            return original(repo, *args, **kwargs)
        before = snapshot(ctx)
        with monkeypatch.context() as patch:
            patch.setattr(seal, "git", race)
            with pytest.raises(RuntimeError, match="Git operational failure"):
                call(ctx)
        assert snapshot(ctx)[1:] == before[1:]
        seal.git(ctx.repo, "update-ref", ctx.ref, record["parent"], other)
    monkeypatch.setattr(seal, "commit_files", lambda *args, **kwargs:
                        pytest.fail("Recovery must reuse the prepared object"))
    result = call(ctx)
    assert result.commit == record["commit"] and result.replayed
    assert seal.git(ctx.repo, "rev-parse", ctx.ref).decode().strip() == record["commit"]
    assert call(ctx) == result


@pytest.mark.scenario("specloop-interface/idempotent-revision-recovery/later-control-state-takes-precedence")
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("later", ["paused", "aborted", "done", "event_only", "state_only", "request_only"])
def test_later_control_wins(ctx, monkeypatch, completed, archive, later):
    commit = call(ctx).commit if completed else interrupt(ctx, monkeypatch, "ref")["commit"]
    ctx.run.reload()
    if later == "paused":
        ctx.run.pause("stuck", detail="Later human decision")
    elif later in ("aborted", "done"):
        ctx.run.state.status = later
        ctx.run.state.pause = None
        ctx.run.save()
    elif later == "state_only":
        ctx.run.state.pr = "later PR"
        ctx.run.save()
    elif later == "event_only":
        ctx.run.event("control", detail="Later decision with unchanged state")
    ctx.run.request_pause()
    if archive:
        tree = seal.git(ctx.repo, "mktree", input=b"").decode().strip()
        archived = seal.git(ctx.repo, "commit-tree", tree, "-p", commit, "-m", "archived",
                            env=seal.IDENTITY).decode().strip()
        seal.git(ctx.repo, "update-ref", ctx.ref, archived, commit)
    saved = (ctx.run.dir / "state.json").read_bytes()
    result = call(ctx)
    assert result.commit == commit and result.replayed and ctx.run.pause_requested()
    if later != "request_only" or completed:
        assert (ctx.run.dir / "state.json").read_bytes() == saved
        assert result.status == ctx.run.state.status
        assert result.pause == (asdict(ctx.run.state.pause) if ctx.run.state.pause else None)
    else:
        assert result.status == "running" and result.pause is None


@pytest.mark.scenario("specloop-interface/durable-revision-identity/conflicting-or-corrupt-evidence-is-refused")
@pytest.mark.parametrize("corruption", ["feedback", "json", "version", "change", "branch", "round", "parent",
                                        "missing", "append", "extra_file", "events", "unterminated", "unknown"])
def test_bad_evidence_preserved(ctx, monkeypatch, corruption):
    record = interrupt(ctx, monkeypatch, "ref")
    if corruption == "parent":
        record["parent"] = seal.commit_files(ctx.repo, record["parent"], {}, "different parent")
    elif corruption in ("version", "change", "branch", "round", "missing"):
        field, value = {"version": ("version", 2), "change": ("change", "other"), "branch": ("branch", "other"),
                        "round": ("round", True), "missing": ("commit", "f" * 40)}[corruption]
        record[field] = value
    elif corruption in ("append", "extra_file"):
        text = seal.git(ctx.repo, "show", f"{record['commit']}:{ctx.path}").decode()
        files = {ctx.path: text + "wrong"} if corruption == "append" else {ctx.path: text, "other": "bad"}
        record["commit"] = seal.commit_files(ctx.repo, record["parent"], files, "specloop: append review round 1")
        seal.git(ctx.repo, "update-ref", ctx.ref, record["commit"])
    elif corruption in ("events", "unterminated"):
        with (ctx.run.dir / "events.jsonl").open("a") as stream:
            stream.write('{"event":' if corruption == "events" else '{"event":"later"}')
    record["future"] = {"retained": True}
    ctx.store.path.write_text("{" if corruption == "json" else json.dumps(record))
    before = snapshot(ctx)
    if corruption == "unknown":
        assert call(ctx).commit == record["commit"] and ctx.store.load()["future"] == record["future"]
    else:
        with pytest.raises(ValueError, match="revision (record|evidence)"):
            call(ctx, feedback='["different"]' if corruption == "feedback" else FEEDBACK)
        assert snapshot(ctx) == before


@pytest.mark.scenario("specloop-interface/idempotent-revision-recovery/git-mismatch-differs-from-operational-failure")
@pytest.mark.parametrize("failure", [1, 128, "timeout", "launch", "unrelated"])
def test_ancestry_failures(ctx, monkeypatch, failure):
    call(ctx)
    if failure == "unrelated":
        seal.git(ctx.repo, "update-ref", ctx.ref, ctx.store.load()["parent"])
    original = subprocess.run
    def run(args, **kwargs):
        if "merge-base" in args and failure != "unrelated":
            assert kwargs["timeout"] == 60 and kwargs["env"].items() >= seal.SAFE_GIT.items()
            assert args[1:1 + len(seal.SAFE_FLAGS)] == seal.SAFE_FLAGS
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 60)
            if failure == "launch":
                raise OSError("cannot launch Git")
            return subprocess.CompletedProcess(args, failure, b"", b"injected")
        return original(args, **kwargs)
    before = snapshot(ctx)
    monkeypatch.setattr(subprocess, "run", run)
    conflict = failure in (1, "unrelated")
    with pytest.raises(ValueError if conflict else RuntimeError, match="evidence conflict" if conflict else "Git operational failure"):
        call(ctx)
    assert snapshot(ctx) == before


@pytest.mark.scenario("specloop-interface/idempotent-revision-recovery/different-keys-and-runs-remain-independent")
def test_independent_keys_and_runs(ctx, tmp_path):
    second = tmp_path / "independent-run"
    shutil.copytree(ctx.run.dir, second)
    first = call(ctx)
    assert call(ctx, feedback=json.dumps(BLOCKS, indent=4)).commit == first.commit
    next_round = call(ctx, round_id="round:2")
    assert next_round.commit != first.commit and not next_round.replayed
    independent = revise.revise_result(Run(second), ctx.m, FEEDBACK, feedback_format="json", round_id="round:1")
    assert not independent.replayed
    assert len(tasks.parse(seal.git(second / "repo.git", "show", f"{ctx.ref}:{ctx.path}").decode())) == 3
