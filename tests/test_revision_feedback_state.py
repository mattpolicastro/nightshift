"""Revision-only ledger primitives; polling integration is covered separately."""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from nightshift import queue, specloop_intake as intake, specloop_job


COMPAT = "pr-revision/feedback-state-compatibility/validated-state-survives-replacement"
ISOLATION = "pr-revision/consumption-based-eligibility/first-selection-and-state-isolation"
SNAPSHOT = "pr-revision/durable-round-snapshot/snapshot-is-fixed-before-dispatch"


def read(path):
    return json.loads(path.read_text())


def snapshot(path, previous=None, items=(), **fields):
    return intake._save_revision_snapshot(path, previous, list(items), **{
        "change": "example", "head": "old", "event": 1, "round_id": "1", **fields,
    })


@pytest.mark.scenario(COMPAT)
def test_replacements_preserve_cumulative_ids_and_unknown_fields(tmp_path):
    path = tmp_path / "8.json"
    items = [queue.FeedbackItem("review:9", "Fix λ\n\nKeep 🦉")]
    first = snapshot(path, items=items, future={"nested": [1, 2]})
    intake._acknowledge_revision_feedback(path, first)
    first.update(job="old-job", command="propose", delivered=True, result={"outcome": "drafted"})
    intake._save(path, first)
    for outcome in ("empty", "refused"):
        new = snapshot(path, read(path), items, change="admitted", head="new", event=2,
                       round_id="2", result={"outcome": outcome})
        assert read(path) == new
        assert new["feedback"] == new["feedback_ids"] == []
        assert new["consumed_feedback_ids"] == ["review:9"]
        assert new["future"] == {"nested": [1, 2]}
        assert not {"job", "command", "delivered"}.intersection(new)
    second = snapshot(path, read(path), items + [queue.FeedbackItem("inline:2", "Next")])
    assert second["feedback_ids"] == ["inline:2"]
    intake._acknowledge_revision_feedback(path, second)
    before = path.read_bytes()
    intake._acknowledge_revision_feedback(path, read(path))
    assert path.read_bytes() == before
    assert read(path)["consumed_feedback_ids"] == ["inline:2", "review:9"]


@pytest.mark.scenario(ISOLATION)
def test_first_selection_is_isolated_by_repository_and_pr(tmp_path):
    # The structured boundary has no timestamps: before/at/after tip all qualify.
    items = [queue.FeedbackItem(f"conversation:{i}", when)
             for i, when in enumerate(("before tip", "at tip", "after tip"), 1)]
    paths = [tmp_path / "revisions" / repo / f"{pr}.json"
             for repo, pr in (("one__repo", 8), ("one__repo", 9), ("two__repo", 8))]
    for path in paths:
        receipt = snapshot(path, items=items)
        assert read(path)["feedback"] == [item.block for item in items]
        assert receipt["consumed_feedback_ids"] == []
        intake._acknowledge_revision_feedback(path, receipt)
    for path in paths:
        assert snapshot(path, read(path), items)["feedback_ids"] == []


@pytest.mark.scenario(SNAPSHOT)
@pytest.mark.parametrize("command,outcome", [("propose", "drafted"), ("revise", "revised")])
@pytest.mark.parametrize("background", [False, True])
def test_saved_snapshot_precedes_dispatch_and_survives_restart(tmp_path, monkeypatch, command, outcome, background):
    path = tmp_path / "8.json"
    items = [queue.FeedbackItem("review:1", "Original λ\n\nbody")]
    receipt = snapshot(path, items=items, command=command)
    result = {"version": 1, "outcome": outcome, "commit": "b" * 40, "branch": "spec/example"}
    held = []
    monkeypatch.setattr(specloop_job, "_children", {})

    def dispatch(args, **kwargs):
        assert read(path)["feedback"] == ["Original λ\n\nbody"]
        assert read(path)["feedback_ids"] == ["review:1"]
        items[:] = [queue.FeedbackItem("review:1", "Edited"), queue.FeedbackItem("inline:2", "Later")]
        if background:
            # Retain the real job lock as a detached wrapper would, without a process.
            held.append(os.dup(kwargs["pass_fds"][0]))
            return SimpleNamespace(poll=lambda: None)
        return subprocess.CompletedProcess(args, 0, json.dumps(result), "")

    try:
        assert intake._start(path, receipt, [command, "example"],
                             runner=None if background else dispatch, launcher=dispatch) is not background
        if background:
            receipt = read(path)  # Restart while work is pending.
            for _ in range(2):
                assert not intake._collect(path, receipt, command)
                assert read(path)["feedback"] == ["Original λ\n\nbody"]
            directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
            specloop_job.finalize(directory, {"result": result})
            os.close(held.pop())
            assert intake._collect(path, receipt, command)
        intake._save(path, receipt)
        intake._acknowledge_revision_feedback(path, read(path))
        assert read(path)["consumed_feedback_ids"] == ["review:1"]
        assert snapshot(path, read(path), items)["feedback_ids"] == ["inline:2"]
    finally:
        for fd in held:
            os.close(fd)


@pytest.mark.scenario(COMPAT)
@pytest.mark.parametrize("field,bad", [
    ("feedback_state_version", value) for value in (None, True, 1.0, 2, "1")
] + [
    ("consumed_feedback_ids", value) for value in (None, "review:1", [True], [1], [{}],
                                                 ["review:0"], ["unknown:1"], ["review:1"] * 2)
] + [("feedback_ids", None), ("feedback_ids", ["inline:1"]), ("feedback", "body"),
     ("feedback", [1]), ("legacy_snapshot", "true")])
def test_malformed_state_never_overwrites_or_dispatches(tmp_path, field, bad):
    path = tmp_path / "8.json"
    receipt = snapshot(path)
    receipt[field] = bad
    intake._save(path, receipt)
    before = path.read_bytes()
    for operation in (lambda: snapshot(path, read(path)),
                      lambda: intake._acknowledge_revision_feedback(path, read(path))):
        with pytest.raises(ValueError, match="revision"):
            operation()
        assert path.read_bytes() == before


@pytest.mark.scenario(COMPAT)
def test_missing_fields_legacy_snapshot_and_save_failure(tmp_path, monkeypatch):
    path = tmp_path / "8.json"
    receipt = snapshot(path, items=[queue.FeedbackItem("inline:2", "Saved")])
    for field in ("feedback_state_version", "consumed_feedback_ids", "feedback_ids", "feedback"):
        with pytest.raises(ValueError):
            intake._validate_revision_feedback({k: v for k, v in receipt.items() if k != field})
    legacy = {**receipt, "legacy_snapshot": True, "consumed_feedback_ids": ["review:1"]}
    del legacy["feedback_ids"]
    intake._acknowledge_revision_feedback(path, legacy)
    assert read(path)["consumed_feedback_ids"] == ["review:1"]
    receipt = snapshot(path, read(path), [queue.FeedbackItem("inline:2", "Saved")])
    assert "legacy_snapshot" not in receipt
    before = path.read_bytes()

    def fail_save(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(intake, "_save", fail_save)
    for operation in (lambda: intake._acknowledge_revision_feedback(path, receipt),
                      lambda: snapshot(path, receipt)):
        with pytest.raises(OSError, match="disk unavailable"):
            operation()
        assert path.read_bytes() == before
        assert receipt == read(path)
