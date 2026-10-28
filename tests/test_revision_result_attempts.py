"""Same-round selections have durable, independent result delivery identities."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from nightshift import daemon, specloop_intake as intake, specloop_job as jobs
from nightshift.config import Config
from test_revision_feedback_consumption import read, receipt_path
from test_revision_feedback_recovery import restart, tick
from test_specloop_spec_revision import CHANGE, HEAD, setup as spec_setup

pytestmark = pytest.mark.real_trust


def attempt(saved):
    value = saved["delivery_attempt"]
    assert UUID(value).version == 4 and UUID(value).hex == value
    return value


@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/rerun-round-posts-new-result")
@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/attempt-identity-is-durable-before-execution")
@pytest.mark.parametrize("outcome,field", [("rejected", "contradictions"), ("needs_input", "questions")])
@pytest.mark.parametrize("identical", [False, True])
@pytest.mark.parametrize("background", [False, True])
def test_rerun_round_posts_new_result(spec_setup, tmp_path, monkeypatch, outcome, field, identical, background):
    _, gh, commands = spec_setup
    original = read(tmp_path)
    jobs.save(receipt_path(tmp_path), {**original, "future": {"keep": [1, 2]}})
    launched, identities = [], []
    monkeypatch.setattr(jobs, "_children", {})
    start = intake._start

    def checked_start(path, receipt, args, **kwargs):
        assert read(tmp_path) == receipt  # Snapshot precedes even background start's own save.
        assert "result" not in receipt and "delivered" not in receipt
        identities.append(receipt.get("delivery_attempt"))
        return start(path, receipt, args, **kwargs)

    def launch(argv, **kwargs):
        launched.append(Path(argv[-2]))
        assert read(tmp_path).get("delivery_attempt") == identities[-1]
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(intake, "_start", checked_start)
    for index in range(4):
        if index:
            gh.label_event("needs-human", "unlabeled")
        if index == 3:
            gh.label_event("agent:revise", "unlabeled")
            gh.label_event("agent:revise", "labeled")
        text = "Which constraint applies?" if identical else f"Unresolved constraint {index}"
        commands.result = {"version": 1, "outcome": outcome, field: [text]}
        earlier = [c.copy() for c in gh.comments]
        tick(spec_setup, tmp_path, background=background, launcher=launch)
        if background:
            pending = read(tmp_path)
            assert "result" not in pending and gh.comments == earlier
            jobs.finalize(launched[-1], {"result": commands.result})
            jobs._children.clear()  # Restart with only durable job/receipt state.
            tick(spec_setup, tmp_path, module=restart())
            assert read(tmp_path).get("delivery_attempt") == pending.get("delivery_attempt")
        saved = read(tmp_path)
        assert saved["round_id"] == str(saved["event"])
        assert (saved["round_id"] == "2") == (index < 3)
        suffix = f":attempt-{attempt(saved)}" if index in (1, 2) else ""
        if not suffix:
            assert "delivery_attempt" not in saved
        marker = f"<!-- nightshift:revise:{CHANGE}:{saved['round_id']}{suffix} -->"
        assert len(gh.comments) == index + 1 and gh.comments[:-1] == earlier
        assert len({c["id"] for c in gh.comments}) == index + 1
        assert text in gh.comments[-1]["body"]
        assert gh.comments[-1]["body"].splitlines()[-1] == marker
        assert gh.comments[-1]["body"].count("<!-- nightshift:") == 1
        assert gh.labels == {"spec:review", "agent:revise", "needs-human"} and gh.head == HEAD
        assert saved["delivered"] and saved["result"] == commands.result
        assert saved["consumed_feedback_ids"] == original["consumed_feedback_ids"]
        assert saved["feedback_ids"] == ["conversation:102"] and saved["future"] == {"keep": [1, 2]}
    assert identities[0] is identities[3] is None and identities[1] != identities[2]
    assert len(launched) == (4 if background else 0)
    assert len(commands.proposals) == (0 if background else 4)


@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/attempt-identity-is-durable-before-execution")
@pytest.mark.parametrize("failed_save", [False, True])
@pytest.mark.parametrize("mode", ["sync", "background", "empty"])
def test_replacement_save_precedes_effects(spec_setup, tmp_path, monkeypatch, mode, failed_save):
    _, gh, commands = spec_setup
    commands.result = {"version": 1, "outcome": "rejected", "contradictions": ["Resolve ambiguity"]}
    tick(spec_setup, tmp_path)
    previous = read(tmp_path)
    gh.label_event("needs-human", "unlabeled")
    if mode == "empty":
        gh.feedback.pop(1)
    before = receipt_path(tmp_path).read_bytes()
    comments = [c.copy() for c in gh.comments]
    effects = []
    replace = Path.replace

    def fail_replace(source, target):
        if target == receipt_path(tmp_path):
            raise OSError("snapshot replace failed")
        return replace(source, target)

    def github(args):
        if args[:2] in (["pr", "comment"], ["pr", "edit"]) or "PATCH" in args:
            effects.append(args)
            assert attempt(read(tmp_path))
            assert read(tmp_path)["round_id"] == previous["round_id"]
        return gh(args)

    def launch(*args, **kwargs):
        effects.append(args)
        assert attempt(read(tmp_path))
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(jobs, "_children", {})
    if failed_save:
        monkeypatch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="snapshot replace failed"):
            tick(spec_setup, tmp_path, background=mode == "background", runner=github, launcher=launch)
        assert receipt_path(tmp_path).read_bytes() == before
        assert not effects and gh.comments == comments and len(commands.proposals) == 1
        assert gh.labels == {"spec:review", "agent:revise"}
    else:
        tick(spec_setup, tmp_path, background=mode == "background", runner=github, launcher=launch)
        assert effects and attempt(read(tmp_path))
        if mode == "empty":
            assert len(commands.proposals) == 1 and len(gh.comments) == 2
            assert gh.comments[0] == comments[0] and gh.labels == {"spec:review"}
            assert read(tmp_path)["result"] == {"outcome": "empty"}
            assert read(tmp_path)["consumed_feedback_ids"] == previous["consumed_feedback_ids"]


@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/invalid-attempt-metadata-blocks-processing")
@pytest.mark.parametrize("metadata", [
    {"delivery_attempt": value} for value in (None, True, 12, {}, "", "A" * 32, "a" * 31,
                                             "a" * 33, "g" * 32, "a" * 32 + "\n")
] + [{"delivery_attempt": "a" * 32, "round_id": value} for value in (None, "", 2, True)])
@pytest.mark.parametrize("state", ["selection", "completion", "collection"])
def test_invalid_attempt_blocks_processing(spec_setup, tmp_path, metadata, state):
    _, gh, commands = spec_setup
    saved = {**read(tmp_path), "event": 2, "round_id": "2", "delivered": False, **metadata}
    if state != "completion":
        saved.pop("result")
    if state == "collection":
        saved["job"] = "never-collect"
    jobs.save(receipt_path(tmp_path), saved)
    before, errors = receipt_path(tmp_path).read_bytes(), []
    assert tick(spec_setup, tmp_path, on_error=lambda n, e: errors.append((n, e))) == []
    assert len(errors) == 1 and errors[0][0] == 8
    assert isinstance(errors[0][1], ValueError) and "delivery_attempt" in str(errors[0][1])
    assert receipt_path(tmp_path).read_bytes() == before
    assert not commands.calls and not gh.comments
    assert gh.labels == {"spec:review", "agent:revise"}
    assert not any(c[:2] == ["pr", "edit"] or "PATCH" in c for c in gh.calls)


@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/invalid-attempt-metadata-blocks-processing")
@pytest.mark.parametrize("pause", [False, True])
def test_invalid_attempt_keeps_item_failure_reporting(spec_setup, tmp_path, monkeypatch, pause):
    repo, gh, commands = spec_setup
    jobs.save(receipt_path(tmp_path), {**read(tmp_path), "delivery_attempt": None})
    before = receipt_path(tmp_path).read_bytes()
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    health = daemon.RepoHealth()
    for _ in range(health.threshold - 1):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not gh.comments and "needs-human" not in gh.labels
    report = intake.report_failure

    def pause_before_reporting(*args, **kwargs):
        if pause:
            daemon.PAUSE_FILE.touch()
        return report(*args, **kwargs)

    monkeypatch.setattr(intake, "report_failure", pause_before_reporting)
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.item_failures[(repo.name, "revise", 8)] == health.threshold
    assert receipt_path(tmp_path).read_bytes() == before and not commands.proposals
    assert "agent:revise" in gh.labels and ("needs-human" in gh.labels) is not pause
    if pause:
        assert not gh.comments
    else:
        comment, = gh.comments
        assert comment["body"].endswith("<!-- nightshift:revise-error:8 -->")
        assert "<!-- nightshift:revise:" not in comment["body"]
