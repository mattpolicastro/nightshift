"""Revision recovery retains the selected payload and acknowledges only success."""

import importlib.util
import json
import os
import subprocess
from contextlib import closing
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nightshift import queue, specloop_intake as intake, specloop_job
from test_revision_feedback_consumption import read, receipt_path, stage_setup
from test_specloop_build_revision import setup as build_setup
from test_specloop_jobs import identity
from test_specloop_spec_revision import setup as spec_setup
from test_specloop_intake import idea_file
from test_revision_feedback_state import CUTOFF, ENDPOINTS, LegacyGh

pytestmark = pytest.mark.real_trust


def legacy_saved(stage, state, time_field="saved_at", *, job=False):
    _, gh, commands = stage
    history = LegacyGh()
    for name, endpoint in zip(("feedback", "reviews", "inline"), ENDPOINTS):
        setattr(gh, name, [record for page in history.pages[endpoint] for record in page])
    saved = {key: read(state)[key] for key in ("change", "head")}
    saved.update(event=gh.events[-1]["id"], feedback=["Saved λ payload with no recoverable ID"],
                 command="revise" if commands.result["outcome"] == "revised" else "propose",
                 future={"nested": [1, 2]})
    if time_field:
        saved[time_field] = CUTOFF
    directory = receipt_path(state).parent / "8.jobs" / "legacy-job"
    if job:
        saved["job"] = directory.name
        directory.mkdir(parents=True)
        (directory / "payload.txt").write_text(saved["feedback"][0])
    else:
        saved["result"] = commands.result
    specloop_job.save(receipt_path(state), saved)
    return saved, directory


@pytest.mark.scenario("pr-revision/feedback-state-compatibility/legacy-recovery-preserves-saved-work")
@pytest.mark.scenario("pr-revision/feedback-state-compatibility/legacy-saved-time-initializes-consumption")
@pytest.mark.scenario("pr-revision/feedback-state-compatibility/legacy-missing-time-uses-tip-once")
@pytest.mark.parametrize("time_field", ["saved_at", "updated_at", None])
@pytest.mark.parametrize("recovery", ["running", "delivery", "human_input", "stopped", "paused"])
def test_legacy_saved_work_finishes_before_exact_snapshot(
    stage_setup, tmp_path, monkeypatch, time_field, recovery,
):
    import fcntl
    from nightshift import daemon
    from nightshift.config import Config

    repo, gh, commands = stage_setup
    saved, directory = legacy_saved(stage_setup, tmp_path, time_field,
                                    job=recovery in ("running", "stopped", "paused"))
    if recovery == "human_input":
        saved["result"] = {"outcome": "refused" if saved["command"] == "revise" else "needs_input",
                           "reasons": ["Saved question"]}
        saved["round_id"] = "original-round"
        specloop_job.save(receipt_path(tmp_path), saved)
    suffix = ":original-round" if "round_id" in saved else ""
    marker = f"<!-- nightshift:revise:{saved['change']}{suffix} -->"
    gh.comments.append({"id": 90, "body": marker})  # Reconcile an earlier ambiguous POST.
    gh.label_event("agent:revise", "unlabeled")
    gh.label_event("agent:revise", "labeled")
    gh.head = commands.result["commit"]  # Current tip differs from the saved receipt's head.
    current_head = gh.head
    monkeypatch.setattr("test_specloop_spec_revision.TIP_DATE",
                        "2026-10-30T00:00:00Z" if time_field else CUTOFF)
    baseline = sorted(f"{kind}:{i}" for kind in ("conversation", "review", "inline") for i in (1, 2))

    def github(args):
        if args[:2] in (["pr", "comment"], ["pr", "edit"]) or "PATCH" in args:
            durable = read(tmp_path)
            assert durable["feedback_state_version"] == 1
            assert durable["feedback_migration"] == {
                "source": "receipt_saved_at" if time_field else "branch_tip", "cutoff": CUTOFF}
            assert durable["consumed_feedback_ids"] == baseline
            assert durable["legacy_snapshot"] and "feedback_ids" not in durable
            assert durable["feedback"] == saved["feedback"]
        return gh(args)

    if recovery == "stopped":
        specloop_job.save(directory / "stop.json", {"state": "stopped"})
    if "job" in saved:
        specloop_job.finalize(directory, {"result": commands.result})
    if recovery == "running":
        with (directory / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            tick(stage_setup, tmp_path)
            tick(stage_setup, tmp_path, module=restart())
            assert "result" not in read(tmp_path)
            assert not dispatches(commands) and gh.comments[0]["body"] == marker
    if recovery == "paused":
        collect = specloop_job._collect_locked
        pause = tmp_path / "pause"

        def collect_then_pause(*args):
            result = collect(*args)
            pause.touch()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(specloop_job, "_collect_locked", collect_then_pause)
            patch.setattr(daemon, "STATE_DIR", tmp_path)
            patch.setattr(daemon, "PAUSE_FILE", pause)
            patch.setattr(intake, "run_git", commands)
            health = daemon.RepoHealth()
            daemon.poll_specloop(Config(repos=[repo]), health, runner=github, specloop_runner=commands)
            assert not health.failures and read(tmp_path)["result"] == commands.result
            assert not read(tmp_path).get("delivered") and gh.comments[0]["body"] == marker
            daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
        pause.unlink()
    errors = []
    tick(stage_setup, tmp_path, module=restart(), runner=github,
         on_error=lambda n, e: errors.append(e))
    migrated = read(tmp_path)
    assert not errors  # No manual initialization diagnostic.
    assert all(migrated[key] == value for key, value in saved.items())
    assert migrated["legacy_snapshot"] and "feedback_ids" not in migrated
    assert migrated["consumed_feedback_ids"] == baseline
    assert migrated["feedback_migration"] == {
        "source": "receipt_saved_at" if time_field else "branch_tip", "cutoff": CUTOFF}
    assert not dispatches(commands) and len(gh.comments) == 1
    assert gh.comments[0]["body"].endswith(marker) and "agent:revise" in gh.labels
    if "job" in saved:
        assert (directory / "payload.txt").read_text() == saved["feedback"][0]
    commit_reads = [c for c in gh.calls if c[0] == "api" and "/commits/" in c[1]]
    assert len(commit_reads) == (not time_field)
    if commit_reads:
        assert commit_reads[0][1].endswith(current_head)
    # Corrupt historical timestamps and advance the tip: migration must never rerun.
    monkeypatch.setattr("test_specloop_spec_revision.TIP_DATE", "2026-11-01T00:00:00Z")
    for records in (gh.feedback, gh.reviews, gh.inline):
        for record in records[:2]:
            record.update(body="Old ID edited into an instruction", created_at="bad", submitted_at="bad")
    if recovery == "stopped":
        tick(stage_setup, tmp_path, module=restart())
        assert read(tmp_path) == migrated and not dispatches(commands)
        assert gh.comments[0]["body"] == marker
        assert json.loads((directory / "stop.json").read_text()) == {"state": "stopped"}
        return
    assert migrated["delivered"]
    gh.label_event("needs-human", "unlabeled")
    commands.fetch_head = gh.head
    tick(stage_setup, tmp_path, module=restart())
    next_round = read(tmp_path)
    assert next_round["feedback_ids"] == ["conversation:3", "review:3", "inline:3"]
    assert "legacy_snapshot" not in next_round and next_round["future"] == saved["future"]
    assert next_round["feedback_migration"] == migrated["feedback_migration"]
    assert next_round["consumed_feedback_ids"] == sorted([*baseline, *next_round["feedback_ids"]])
    assert len(dispatches(commands)) == 1
    gh.label_event("agent:revise", "labeled")
    assert tick(stage_setup, tmp_path, module=restart()) == [{"outcome": "empty"}]
    assert read(tmp_path)["consumed_feedback_ids"] == next_round["consumed_feedback_ids"]
    assert read(tmp_path)["feedback_migration"] == migrated["feedback_migration"]
    assert len(dispatches(commands)) == 1
    assert [c for c in gh.calls if c[0] == "api" and "/commits/" in c[1]] == commit_reads


@pytest.mark.scenario("pr-revision/feedback-state-compatibility/legacy-initialization-failure-preserves-receipt")
@pytest.mark.parametrize("failure", ["saved_at", "updated_at", "missing_time", "invalid_time",
                                    "missing_id", "invalid_id", "page", "commit", "save"])
@pytest.mark.parametrize("pending_job", [False, True])
def test_legacy_initialization_failure_prevents_collection_and_delivery(
    stage_setup, tmp_path, monkeypatch, failure, pending_job,
):
    _, gh, commands = stage_setup
    saved, directory = legacy_saved(stage_setup, tmp_path, None, job=pending_job)
    if pending_job:
        specloop_job.finalize(directory, {"result": commands.result})
    record = gh.reviews[2]  # A later record must also validate before saving any baseline.
    if failure in ("saved_at", "updated_at"):
        saved[failure] = "malformed"
    elif failure == "missing_time":
        record.pop("submitted_at")
    elif failure == "invalid_time":
        record["submitted_at"] = "invalid"
    elif failure == "missing_id":
        record.pop("id")
    elif failure == "invalid_id":
        record["id"] = True
    specloop_job.save(receipt_path(tmp_path), saved)
    before = receipt_path(tmp_path).read_bytes()

    def github(args):
        if args[0] == "api" and (
                failure == "page" and args[1].endswith("/pulls/8/comments")
                or failure == "commit" and "/commits/" in args[1]):
            raise RuntimeError("read failed")
        return gh(args)

    def fail_replace(*args):
        raise OSError("atomic replacement failed")

    errors = []
    with monkeypatch.context() as patch:
        if failure == "save":
            patch.setattr(Path, "replace", fail_replace)
        tick(stage_setup, tmp_path, runner=github, on_error=lambda n, e: errors.append((n, e)))
    assert len(errors) == 1 and errors[0][0] == 8
    assert receipt_path(tmp_path).read_bytes() == before
    assert not (directory / "collected.json").exists()
    assert not commands.calls and not gh.comments and "agent:revise" in gh.labels
    if failure in ("saved_at", "updated_at"):
        assert not any("/commits/" in c[1] for c in gh.calls)
    # Repair the failed input and retry the same saved result after restart.
    for field in ("saved_at", "updated_at"):
        saved.pop(field, None)
    record.update(id=3, submitted_at="2026-10-20T10:00:00.000001Z")
    specloop_job.save(receipt_path(tmp_path), saved)
    tick(stage_setup, tmp_path, module=restart())
    assert read(tmp_path)["delivered"] and not dispatches(commands)


@pytest.mark.scenario("pr-revision/feedback-state-compatibility/validated-state-survives-replacement")
@pytest.mark.parametrize("replacement", ["empty", "refused", "bad_version", "bad_ids", "missing_ids"])
def test_validated_receipt_replacement_through_intake(stage_setup, tmp_path, replacement):
    _, gh, commands = stage_setup
    saved = read(tmp_path)
    saved["future"] = {"nested": [1, 2]}
    if replacement == "empty":
        saved["consumed_feedback_ids"].append("conversation:102")
    elif replacement == "refused":
        commands.result = {"version": 1, "outcome": "refused" if commands.result["outcome"] == "revised"
                           else "rejected", "reasons": ["Human input required"]}
    elif replacement == "bad_version":
        saved["feedback_state_version"] = 2
    elif replacement == "bad_ids":
        saved["consumed_feedback_ids"] = [True]
    else:
        saved.pop("feedback_ids")
    specloop_job.save(receipt_path(tmp_path), saved)
    before, errors = receipt_path(tmp_path).read_bytes(), []
    tick(stage_setup, tmp_path, on_error=lambda n, e: errors.append(e))
    if replacement not in ("empty", "refused"):
        assert len(errors) == 1 and isinstance(errors[0], ValueError)
        assert receipt_path(tmp_path).read_bytes() == before
        assert not commands.calls and not gh.comments
    else:
        assert not errors and read(tmp_path)["delivered"]
        assert read(tmp_path)["future"] == saved["future"]
        assert read(tmp_path)["consumed_feedback_ids"] == sorted(saved["consumed_feedback_ids"])
        assert read(tmp_path)["feedback_ids"] == ([] if replacement == "empty" else ["conversation:102"])
        assert len(dispatches(commands)) == (replacement == "refused")


def restart():
    spec = importlib.util.spec_from_file_location("nightshift.recovery_intake", intake.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tick(stage, state, *, module=intake, background=False, **kwargs):
    repo, gh, commands = stage
    return module.revise_pending(repo, queue.Labels(), state,
                                 runner=kwargs.pop("runner", gh), git_runner=commands,
                                 status_runner=commands,
                                 specloop_runner=kwargs.pop(
                                     "specloop_runner", None if background else commands), **kwargs)


def dispatches(commands):
    return [c for c in commands.calls if c[:2] in (["specloop", "propose"], ["specloop", "revise"])]


def fixed(saved):
    return {key: saved[key] for key in ("feedback", "feedback_ids", "event", "round_id")}


def later_feedback(gh):
    gh.feedback[1]["body"] = "Edited after dispatch"
    gh.feedback.append({**gh.feedback[1], "id": 104, "body": "Later feedback"})


@pytest.fixture
def feedback_streams(monkeypatch):
    streams = []
    original = Path.open

    def opened(path, mode="r", *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        if path.name.startswith("feedback-") and mode == "x":
            assert kwargs["encoding"] == "utf-8"
            streams.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", opened)
    return streams


@pytest.mark.scenario("pr-revision/immutable-build-revision-input/replacement-preserves-stopped-input")
@pytest.mark.scenario("pr-revision/immutable-build-revision-input/delayed-reader-retains-complete-blocks")
@pytest.mark.scenario("pr-revision/durable-round-snapshot/pause-and-stop-preserve-snapshots")
@pytest.mark.parametrize("legacy", [False, True])
def test_released_revision_keeps_stopped_attempt_input(
    build_setup, tmp_path, monkeypatch, feedback_streams, legacy,
):
    _, gh, commands = build_setup
    state = tmp_path / "state with spaces"
    specloop_job.save(receipt_path(state), read(tmp_path))
    monkeypatch.chdir(tmp_path)
    state = state.relative_to(tmp_path)  # Intake must turn even relative state into absolute input.
    gh.inline[0]["body"] = "Preserve café λ\n\n---\n\n# Section\n...\n最後"
    jobs, locks = [], []
    monkeypatch.setattr(specloop_job, "_children", {})

    def launch(args, **kwargs):
        assert feedback_streams and all(stream.closed for stream in feedback_streams)
        jobs.append(Path(args[-2]))
        locks.append(os.dup(kwargs["pass_fds"][0]))
        return SimpleNamespace(poll=lambda: None)

    def request(directory):
        return json.loads((directory / "request.json").read_text())["args"]

    try:
        tick(build_setup, state, background=True, launcher=launch)
        saved = read(state)
        original, = jobs
        args = request(original)
        source = Path(args[args.index("--feedback") + 1])
        assert source.is_absolute()
        if legacy:
            # Model an attempt launched by the old event-named writer.
            old = receipt_path(state).with_suffix(f".{saved['event']}.feedback.json").resolve()
            source.rename(old)
            args[args.index("--feedback") + 1] = str(old)
            specloop_job.save(original / "request.json", {"args": args})
            source = old
        before = source.read_bytes()  # First read happens after the launching poll returns.
        assert json.loads(before.decode("utf-8")) == saved["feedback"]
        assert gh.inline[0]["body"] in saved["feedback"][0]
        specloop_job.save(original / "process.json", identity(original))
        specloop_job.request_stop(original, verify_identity=lambda: identity(original))
        specloop_job.finalize(original, {"result": {**commands.result, "commit": "e" * 40}})
        os.close(locks.pop())
        specloop_job.finish_stop(original)
        gh.inline[0]["body"] = "Edited λ feedback\n\n---\n\nKeep this block whole"
        gh.inline.append({**gh.inline[0], "id": 4, "body": "Additional feedback"})
        specloop_job._children.clear()
        tick(build_setup, state, module=restart(), background=True, launcher=launch)
        assert read(state) == saved and len(jobs) == 1 and not gh.comments
        assert specloop_job.release_job(state, original.name) == "released"
        tick(build_setup, state, module=restart(), background=True, launcher=launch)
        replacement = read(state)
        assert len(jobs) == 2 and replacement["job"] != saved["job"]
        assert replacement["event"] == saved["event"]
        assert replacement["consumed_feedback_ids"] == saved["consumed_feedback_ids"]
        assert replacement["feedback_ids"] == ["inline:1", "inline:2", "inline:4"]
        assert gh.inline[0]["body"] in replacement["feedback"][0]
        assert "Additional feedback" in replacement["feedback"][-1]
        replacement_bytes = Path(request(jobs[1])[3]).read_bytes()
        specloop_job._children.clear()
        tick(build_setup, state, module=restart(), background=True, launcher=launch)
        assert len(jobs) == 2 and read(state) == replacement and not gh.comments
        retained = [Path(request(job)[3]) for job in jobs]
        assert retained[0] == source and retained[1].is_absolute() and retained[1] != source
        assert retained[0].read_bytes() == before
        assert retained[1].read_bytes() == replacement_bytes
        assert json.loads(retained[1].read_text(encoding="utf-8")) == replacement["feedback"]
        assert specloop_job.release_job(state, original.name) == "already released"
        assert specloop_job.collect(receipt_path(state), saved) is None
        assert not (original / "collected.json").exists()
    finally:
        for fd in locks:
            os.close(fd)


@pytest.mark.scenario("pr-revision/immutable-build-revision-input/delayed-reader-retains-complete-blocks")
def test_synchronous_revision_invocations_keep_distinct_inputs(build_setup, tmp_path, feedback_streams):
    _, gh, commands = build_setup
    commands.result = {"version": 1, "outcome": "refused", "reasons": ["Try again"]}

    def run(args, **kwargs):
        if args[:2] == ["specloop", "revise"]:
            assert feedback_streams and all(stream.closed for stream in feedback_streams)
        return commands(args, **kwargs)

    tick(build_setup, tmp_path, specloop_runner=run)
    original = read(tmp_path)
    first = Path(dispatches(commands)[0][4])
    before = first.read_bytes()
    gh.labels.remove("needs-human")
    gh.inline[0]["body"] = "Changed under the same trigger"
    tick(build_setup, tmp_path, module=restart(), specloop_runner=run)
    second = Path(dispatches(commands)[1][4])
    assert first.is_absolute() and second.is_absolute() and first != second
    assert read(tmp_path)["event"] == original["event"]
    assert first.read_bytes() == before
    assert json.loads(second.read_text(encoding="utf-8")) == read(tmp_path)["feedback"]


@pytest.mark.scenario("pr-revision/immutable-build-revision-input/feedback-file-preparation-fails")
@pytest.mark.parametrize("failure", ["collision", "write"])
def test_feedback_file_failure_prevents_dispatch(build_setup, tmp_path, monkeypatch, failure):
    path = receipt_path(tmp_path)
    before = path.read_bytes()
    earlier = path.parent / "8.jobs" / ("feedback-" + "a" * 32 + ".json")
    earlier.parent.mkdir()
    earlier.write_text('["Retained original"]\n', encoding="utf-8")
    retained = earlier.read_bytes()
    token = "a" if failure == "collision" else "b"
    errors = []
    with monkeypatch.context() as patch:
        patch.setattr(intake.uuid, "uuid4", lambda: SimpleNamespace(hex=token * 32))
        opened = Path.open

        def fail_write(file, mode="r", *args, **kwargs):
            stream = opened(file, mode, *args, **kwargs)
            if file.name.startswith("feedback-") and mode == "x":
                def write(value):
                    stream.write(value[:5])
                    raise OSError("feedback write failed")

                proxy = Mock(wraps=stream)
                proxy.write.side_effect = write
                return closing(proxy)
            return stream

        if failure == "write":
            patch.setattr(Path, "open", fail_write)
        tick(build_setup, tmp_path, background=True,
             launcher=lambda *a, **k: pytest.fail("must not launch"),
             on_error=lambda number, error: errors.append((number, error)))
    assert len(errors) == 1 and errors[0][0] == 8 and isinstance(errors[0][1], OSError)
    assert path.read_bytes() == before and earlier.read_bytes() == retained
    assert not list(earlier.parent.glob("*/request.json"))
    assert not dispatches(build_setup[2]) and not build_setup[1].comments
    tick(build_setup, tmp_path, module=restart())
    assert read(tmp_path)["feedback_ids"] == ["inline:1", "inline:2"]
    assert read(tmp_path)["delivered"] and earlier.read_bytes() == retained


@pytest.mark.scenario("pr-revision/durable-round-snapshot/pause-and-stop-preserve-snapshots")
@pytest.mark.scenario("pr-revision/revision-actor-gate/unauthorized-revision-ignored-at-either-stage")
@pytest.mark.parametrize("control", ["pause", "stopping", "stopped"])
def test_selected_job_survives_pause_stop_and_reauthorization(stage_setup, tmp_path, monkeypatch, control):
    from nightshift import daemon
    from nightshift.config import Config

    repo, gh, commands = stage_setup
    jobs = []
    monkeypatch.setattr(specloop_job, "_children", {})

    def launch(args, **kwargs):
        jobs.append(Path(args[-2]))
        return SimpleNamespace(poll=lambda: None)

    tick(stage_setup, tmp_path, background=True, launcher=launch)
    saved = read(tmp_path)
    directory, = jobs
    assert saved["feedback_ids"] == ["conversation:102"]
    later_feedback(gh)
    if control != "pause":
        specloop_job.save(directory / "stop.json", {"state": control})
    specloop_job.finalize(directory, {"result": commands.result})
    if control == "pause":
        collect = specloop_job._collect_locked
        pause = tmp_path / "pause"

        def collect_then_pause(*args):
            result = collect(*args)
            pause.touch()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(specloop_job, "_collect_locked", collect_then_pause)
            patch.setattr(daemon, "STATE_DIR", tmp_path)
            patch.setattr(daemon, "PAUSE_FILE", pause)
            patch.setattr(intake, "run_git", commands)
            health = daemon.RepoHealth()
            daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
            paused = read(tmp_path)
            assert paused["result"] == commands.result and not paused.get("delivered")
            assert fixed(paused) == fixed(saved)
            # Build success is acknowledged before notification; specs wait for push.
            expected = sorted([*saved["consumed_feedback_ids"], "conversation:102"])
            assert paused["consumed_feedback_ids"] == (
                expected if commands.result["outcome"] == "revised" else saved["consumed_feedback_ids"])
            assert not health.failures and not health.item_failures and not gh.comments
            daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
            assert read(tmp_path) == paused
        pause.unlink()

    # Resume requires the current label actor's authorization even for a saved result.
    gh.label_event("agent:revise", "unlabeled")
    gh.label_event("agent:revise", "labeled", actor="outsider")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    commands.calls.clear()
    for _ in range(2):
        assert tick(stage_setup, tmp_path, module=restart(), background=True, launcher=launch) == []
        assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not commands.calls and len(jobs) == len(gh.comments) == 1
    assert "label-authorization-8:" in gh.comments[0]["body"]
    gh.label_event("agent:revise", "unlabeled")
    gh.label_event("agent:revise", "labeled")
    tick(stage_setup, tmp_path, module=restart(), background=True, launcher=launch)
    recovered = read(tmp_path)
    assert fixed(recovered) == fixed(saved) and len(jobs) == 1 and not dispatches(commands)
    assert "agent:revise" in gh.labels  # The newer application belongs to a later round.
    assert json.loads((directory / "result.json").read_text()) == {"result": commands.result}
    if control == "pause":
        assert recovered["delivered"] and recovered["consumed_feedback_ids"] == expected
        assert len(gh.comments) == 2
        assert gh.comments[-1]["body"].endswith(
            f"<!-- nightshift:revise:{saved['change']}:{saved['round_id']} -->")
    else:
        tick(stage_setup, tmp_path, module=restart(), background=True, launcher=launch)
        assert read(tmp_path) == saved and not commands.calls and len(gh.comments) == 1
        assert not (directory / "collected.json").exists()
        assert json.loads((directory / "stop.json").read_text()) == {"state": control}


@pytest.mark.scenario("pr-revision/durable-round-snapshot/snapshot-is-fixed-before-dispatch")
@pytest.mark.scenario("pr-revision/durable-round-snapshot/new-label-preserves-completed-round")
@pytest.mark.parametrize("replace_event", [False, True])
@pytest.mark.parametrize("save_failure", [None, "result", "acknowledgement"])
def test_background_reader_retains_snapshot_across_restart(
    stage_setup, tmp_path, monkeypatch, save_failure, replace_event,
):
    _, gh, commands = stage_setup
    consumed = read(tmp_path)["consumed_feedback_ids"]
    jobs = []
    monkeypatch.setattr(specloop_job, "_children", {})

    def launch(args, **kwargs):
        saved = read(tmp_path)
        assert saved["feedback_ids"] == ["conversation:102"]
        assert saved["consumed_feedback_ids"] == consumed
        directory = Path(args[-2])
        request = json.loads((directory / "request.json").read_text())["args"]
        if request[0] == "revise":
            assert Path(request[3]).is_absolute()
            assert json.loads(Path(request[3]).read_text(encoding="utf-8")) == saved["feedback"]
        # Retain the real job lifetime lock while delaying the CLI's file read.
        jobs.append((directory, request, os.dup(kwargs["pass_fds"][0])))
        return SimpleNamespace(poll=lambda: None)

    tick(stage_setup, tmp_path, background=True, launcher=launch)
    saved = read(tmp_path)
    later_feedback(gh)
    if replace_event:
        gh.label_event("agent:revise", "unlabeled")
        gh.label_event("agent:revise", "labeled")
    module = restart()
    try:
        for _ in range(2):
            tick(stage_setup, tmp_path, module=module, background=True, launcher=launch)
            assert read(tmp_path) == saved
        assert len(jobs) == 1
        directory, args, _ = jobs[0]
        payload = (json.loads(Path(args[3]).read_text()) if args[0] == "revise"
                   else idea_file(args).read_text())
        assert (payload == saved["feedback"] if args[0] == "revise"
                else saved["feedback"][0] in payload)
        assert "Edited after dispatch" not in str(payload) and "Later feedback" not in str(payload)
        specloop_job.finalize(directory, {"result": commands.result})
    finally:
        for _, _, fd in jobs:
            os.close(fd)
    if save_failure:
        def save(path, receipt):
            if (save_failure == "result" and "result" in receipt
                    or receipt["consumed_feedback_ids"] != consumed):
                raise OSError("collection save failed")
            specloop_job.save(path, receipt)

        monkeypatch.setattr(module, "_save", save)
        with pytest.raises(OSError, match="collection save failed"):
            tick(stage_setup, tmp_path, module=module, background=True, launcher=launch)
        assert fixed(read(tmp_path)) == fixed(saved)
        assert read(tmp_path)["consumed_feedback_ids"] == consumed
        assert not gh.comments and "agent:revise" in gh.labels
        assert json.loads((directory / "result.json").read_text()) == {"result": commands.result}
        module = restart()
    tick(stage_setup, tmp_path, module=module, background=True, launcher=launch)
    assert len(jobs) == 1 and not dispatches(commands)
    assert fixed(read(tmp_path)) == fixed(saved)
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, "conversation:102"])
    assert ("agent:revise" in gh.labels) == replace_event
    assert len(gh.comments) == 1
    assert gh.comments[0]["body"].endswith(
        f"<!-- nightshift:revise:{saved['change']}:{saved['round_id']} -->")
    if not replace_event:
        gh.label_event("agent:revise", "labeled")
    commands.fetch_head = gh.head
    tick(stage_setup, tmp_path, module=module)
    assert read(tmp_path)["feedback_ids"] == ["conversation:104"]
    assert read(tmp_path)["feedback"] == ["**reviewer:**\n\nLater feedback"]
    assert len(dispatches(commands)) == 1


@pytest.mark.scenario("pr-revision/durable-round-snapshot/new-label-preserves-completed-round")
@pytest.mark.scenario("pr-revision/consumption-based-eligibility/relabel-does-not-requeue-consumed-feedback")
@pytest.mark.parametrize("replacement", ["execution", "notification", "recovery"])
def test_new_application_finishes_original_round_before_remaining_ids(stage_setup, tmp_path, replacement):
    _, gh, commands = stage_setup
    initial_head = gh.head
    saved = None

    def reapply():
        nonlocal saved
        saved = read(tmp_path)
        later_feedback(gh)
        gh.label_event("agent:revise", "unlabeled")
        gh.label_event("agent:revise", "labeled")

    def run(args, **kwargs):
        if replacement == "execution" and args[:2] in (
                ["specloop", "propose"], ["specloop", "revise"]):
            reapply()
        return commands(args, **kwargs)

    def github(args):
        response = gh(args)
        if args[:2] == ["pr", "comment"]:
            if replacement == "notification":
                reapply()  # After intake's event read, immediately before removal.
            elif replacement == "recovery":
                raise RuntimeError("comment response lost")
        return response

    if replacement == "recovery":
        with pytest.raises(RuntimeError, match="comment response lost"):
            tick(stage_setup, tmp_path, runner=github, specloop_runner=run)
        reapply()
        tick(stage_setup, tmp_path, module=restart())
    else:
        tick(stage_setup, tmp_path, runner=github, specloop_runner=run)
    completed = read(tmp_path)
    assert completed["delivered"] and fixed(completed) == fixed(saved)
    assert completed["consumed_feedback_ids"] == sorted(
        {*saved["consumed_feedback_ids"], "conversation:102"})
    assert len(dispatches(commands)) == len(gh.comments) == 1
    original_comment = gh.comments[0].copy()
    assert original_comment["body"].endswith(
        f"<!-- nightshift:revise:{saved['change']}:{saved['round_id']} -->")
    assert "agent:revise" in gh.labels
    newer_event = gh.events[-1]["id"]
    assert newer_event != saved["event"]
    if commands.result["outcome"] == "revised":
        assert gh.head == initial_head  # Build publication has not happened.

    commands.fetch_head = gh.head
    tick(stage_setup, tmp_path, module=restart())  # Use the retained application.
    remaining = read(tmp_path)
    assert remaining["event"] == newer_event and remaining["round_id"] == str(newer_event)
    assert remaining["feedback_ids"] == ["conversation:104"]
    assert remaining["feedback"] == ["**reviewer:**\n\nLater feedback"]
    assert remaining["consumed_feedback_ids"] == sorted(
        [*completed["consumed_feedback_ids"], "conversation:104"])
    assert remaining["delivered"] and "agent:revise" not in gh.labels
    assert len(dispatches(commands)) == len(gh.comments) == 2
    assert gh.comments[0] == original_comment
    for _ in range(2):
        gh.label_event("agent:revise", "labeled")
        assert tick(stage_setup, tmp_path, module=restart()) == [{"outcome": "empty"}]
        assert read(tmp_path)["consumed_feedback_ids"] == remaining["consumed_feedback_ids"]
        assert "agent:revise" not in gh.labels
    assert len(dispatches(commands)) == 2
    if commands.result["outcome"] == "revised":
        inputs = [Path(args[4]) for args in dispatches(commands)]
        assert len(set(inputs)) == 2
        assert [json.loads(p.read_text(encoding="utf-8")) for p in inputs] == [
            completed["feedback"], remaining["feedback"]]


@pytest.mark.scenario("pr-revision/durable-round-snapshot/completion-delivery-retries-preserve-consumption")
def test_acknowledgement_save_failure_keeps_durable_result(stage_setup, tmp_path, monkeypatch):
    _, gh, commands = stage_setup
    consumed = read(tmp_path)["consumed_feedback_ids"]

    def save(path, receipt):
        if receipt["consumed_feedback_ids"] != consumed:
            assert read(tmp_path)["result"] == commands.result
            raise OSError("acknowledgement save failed")
        specloop_job.save(path, receipt)

    monkeypatch.setattr(intake, "_save", save)
    with pytest.raises(OSError, match="acknowledgement save failed"):
        tick(stage_setup, tmp_path)
    saved = read(tmp_path)
    assert saved["consumed_feedback_ids"] == consumed
    assert saved["result"] == commands.result and not saved.get("delivered")
    assert not gh.comments and "agent:revise" in gh.labels
    later_feedback(gh)
    tick(stage_setup, tmp_path, module=restart())
    assert len(dispatches(commands)) == 1
    assert fixed(read(tmp_path)) == fixed(saved)
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, "conversation:102"])
    assert read(tmp_path)["delivered"]


@pytest.mark.scenario("pr-revision/durable-round-snapshot/unsuccessful-work-keeps-feedback-eligible")
@pytest.mark.parametrize("failure", ["launch", "invalid", "refused", "rejected", "needs_input"])
def test_unsuccessful_work_preserves_eligibility(stage_setup, tmp_path, failure):
    _, gh, commands = stage_setup
    consumed = read(tmp_path)["consumed_feedback_ids"]
    success = commands.result
    commands.result = ({**success, "commit": ""} if failure == "invalid" else
                       {"version": 1, "outcome": failure, "reasons": ["Resolve this"]})

    def launch(*args, **kwargs):
        raise OSError("launch failed")

    errors = []
    tick(stage_setup, tmp_path, background=failure == "launch", launcher=launch,
         on_error=lambda number, error: errors.append(error))
    saved = read(tmp_path)
    assert saved["feedback_ids"] == ["conversation:102"]
    assert saved["consumed_feedback_ids"] == consumed
    if failure in ("launch", "invalid") or failure not in (
            ("refused",) if success["outcome"] == "revised" else ("rejected", "needs_input")):
        assert len(errors) == 1 and "result" not in saved
        assert not gh.comments
    else:
        assert not errors and saved["delivered"] and "needs-human" in gh.labels
    gh.label_event("needs-human", "unlabeled")
    commands.result = success
    tick(stage_setup, tmp_path, module=restart())
    assert read(tmp_path)["feedback"] == saved["feedback"]
    assert read(tmp_path)["feedback_ids"] == saved["feedback_ids"]
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, "conversation:102"])
    assert read(tmp_path)["delivered"]
    if success["outcome"] == "revised":
        inputs = list(receipt_path(tmp_path).parent.glob("8.jobs/feedback-*.json"))
        assert len(inputs) == 2
        assert all(json.loads(p.read_text(encoding="utf-8")) == saved["feedback"] for p in inputs)


@pytest.mark.scenario("pr-revision/durable-round-snapshot/unsuccessful-work-keeps-feedback-eligible")
@pytest.mark.parametrize("remote_effect", [False, True])
def test_failed_push_reuses_saved_draft(spec_setup, tmp_path, remote_effect):
    repo, gh, commands = spec_setup
    consumed = read(tmp_path)["consumed_feedback_ids"]

    def git(args, **kwargs):
        if args[3] == "push":
            if remote_effect:
                commands(args, **kwargs)
            return subprocess.CompletedProcess(args, 1, "", "push response lost")
        return commands(args, **kwargs)

    with pytest.raises(intake.DeliveryError, match="push response lost"):
        intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                              specloop_runner=commands, git_runner=git)
    saved = read(tmp_path)
    assert saved["result"] == commands.result and saved["consumed_feedback_ids"] == consumed
    assert not gh.comments and "agent:revise" in gh.labels
    later_feedback(gh)
    tick(spec_setup, tmp_path, module=restart())
    assert len(dispatches(commands)) == 1 and fixed(read(tmp_path)) == fixed(saved)
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, "conversation:102"])


@pytest.mark.scenario("pr-revision/durable-round-snapshot/completion-delivery-retries-preserve-consumption")
@pytest.mark.scenario("pr-revision/revision-comment-retry-reconciliation/revision-reconciliation-read-fails")
@pytest.mark.parametrize("failure", ["lost_comment", "read", "remove_before", "remove_after"])
def test_production_delivery_recovery(stage_setup, tmp_path, monkeypatch, failure):
    _, gh, commands = stage_setup
    consumed = sorted([*read(tmp_path)["consumed_feedback_ids"], "conversation:102"])
    calls = []
    posted_receipts = []
    armed = True
    read_error = failure == "read"

    def run(args, **kwargs):
        nonlocal armed
        assert args[0] == "gh"
        args = args[1:]
        calls.append(args)
        error = ""
        if args[:2] in (["pr", "comment"], ["pr", "edit"]):
            assert read(tmp_path)["consumed_feedback_ids"] == consumed
            assert read(tmp_path)["result"] == commands.result
        if args[:2] == ["api", "repos/owner/sample/issues/8/comments"]:
            if gh.comments and read_error:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 403: history unavailable")
            pages = json.loads(gh(args))
            out = json.dumps([pages[0][:1], pages[0][1:]])  # Marker lives on a later page.
        elif armed and args[:2] == ["pr", "comment"] and failure in ("lost_comment", "read"):
            posted_receipts.append(receipt_path(tmp_path).read_bytes())
            out = gh(args)
            armed = False
            error = "HTTP 502: comment response lost"
        elif armed and args[:2] == ["pr", "edit"] and failure.startswith("remove_"):
            out = gh(args) if failure == "remove_after" else ""
            armed = False
            error = "HTTP 403: label response lost"
        else:
            out = gh(args)
        return subprocess.CompletedProcess(args, int(bool(error)), out, error)

    monkeypatch.setattr(queue.subprocess, "run", run)
    github = partial(queue._gh, sleep=lambda _: None)
    if failure == "lost_comment":
        tick(stage_setup, tmp_path, runner=github)
    else:
        with pytest.raises(queue.GhError, match="history unavailable|label response lost"):
            tick(stage_setup, tmp_path, runner=github)
    saved = read(tmp_path)
    assert saved["consumed_feedback_ids"] == consumed and saved["result"] == commands.result
    assert len(gh.comments) == 1
    body = gh.comments[0]["body"]
    assert body.endswith(f"<!-- nightshift:revise:{saved['change']}:{saved['round_id']} -->")
    if failure == "read":
        assert not any("PATCH" in c or c[:2] == ["pr", "edit"] for c in calls)
        assert receipt_path(tmp_path).read_bytes() == posted_receipts[0]
    later_feedback(gh)
    module = restart()
    read_error = False
    if failure == "remove_after":
        before = receipt_path(tmp_path).read_bytes()
        assert tick(stage_setup, tmp_path, module=module, runner=github) == []
        assert receipt_path(tmp_path).read_bytes() == before
        gh.label_event("agent:revise", "labeled")
    tick(stage_setup, tmp_path, module=module, runner=github)
    assert read(tmp_path)["delivered"] and fixed(read(tmp_path)) == fixed(saved)
    assert read(tmp_path)["consumed_feedback_ids"] == consumed
    assert read(tmp_path)["result"] == saved["result"]
    if failure == "remove_after":
        assert "agent:revise" in gh.labels  # Completing the old event retains the new request.
    assert len(dispatches(commands)) == 1 and gh.comments[0]["body"] == body
    assert sum(c[:2] == ["pr", "comment"] for c in calls) == 1
    assert any("PATCH" in c for c in calls)
    gh.label_event("agent:revise", "labeled")
    commands.fetch_head = gh.head
    # New-round delivery has a larger ledger; use the ordinary fake after recovery.
    tick(stage_setup, tmp_path, module=module)
    assert read(tmp_path)["feedback_ids"] == ["conversation:104"]
    assert read(tmp_path)["feedback"] == ["**reviewer:**\n\nLater feedback"]
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, "conversation:104"])
    assert len(dispatches(commands)) == 2
    if commands.result["outcome"] == "revised":
        inputs = [Path(args[4]) for args in dispatches(commands)]
        assert len(set(inputs)) == 2
        assert json.loads(inputs[0].read_text(encoding="utf-8")) == saved["feedback"]
        assert json.loads(inputs[1].read_text(encoding="utf-8")) == read(tmp_path)["feedback"]
