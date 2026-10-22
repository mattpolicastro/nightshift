"""Revision-only ledger primitives; polling integration is covered separately."""

import json
import os
import subprocess
from pathlib import Path
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
     ("feedback", [1]), ("legacy_snapshot", "true")] + [
    ("feedback_migration", value) for value in (None, {},
        {"source": "file_mtime", "cutoff": "2026-10-20T10:00:00Z"},
        {"source": "branch_tip"}, {"source": "receipt_saved_at", "cutoff": "invalid"})
])
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


SAVED = "pr-revision/feedback-state-compatibility/legacy-saved-time-initializes-consumption"
TIP = "pr-revision/feedback-state-compatibility/legacy-missing-time-uses-tip-once"
FAILURE = "pr-revision/feedback-state-compatibility/legacy-initialization-failure-preserves-receipt"
CUTOFF = "2026-10-20T10:00:00Z"
ENDPOINTS = ("issues/8/comments", "pulls/8/reviews", "pulls/8/comments")


class LegacyGh:
    def __init__(self):
        self.calls = []
        self.tip = CUTOFF
        self.pages = {}
        for endpoint in ENDPOINTS:
            key = "submitted_at" if endpoint.endswith("reviews") else "created_at"
            records = [{"id": i, "body": body, "user": {"login": "reviewer"},
                        "author_association": "COLLABORATOR",
                        key: stamp, "updated_at": "2026-10-25T00:00:00Z"}
                       for i, (body, stamp) in enumerate([
                           ("", "2026-10-20T09:59:59Z"),
                           (queue.SELF_COMMENT_PREFIX, "2026-10-20T05:00:00-05:00"),
                           ("Later", "2026-10-20T10:00:00.000001Z"),
                       ], 1)]
            # Malformed untrusted records must not affect the baseline.
            records += [{"author_association": "NONE"}, {}]
            self.pages[endpoint] = [[records[0]], [], records[1:]]

    def __call__(self, args):
        self.calls.append(args)
        endpoint = args[1].removeprefix("repos/owner/sample/")
        if endpoint.startswith("commits/"):
            assert endpoint == "commits/current-head"
            payload = {"commit": {"committer": {"date": self.tip}}}
        else:
            assert args[2:] == ["--paginate", "--slurp"]
            payload = self.pages[endpoint]
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)


def legacy_receipt(path, **fields):
    receipt = {"change": "example", "head": "old-head", "event": 7, "round_id": "7",
               "feedback": ["Original saved payload"], "job": "saved-job",
               "result": {"outcome": "drafted"}, "future": {"nested": [1]}, **fields}
    intake._save(path, receipt)
    return receipt


def initialize(path, receipt, gh, head="current-head"):
    return intake._initialize_revision_feedback(path, receipt, "owner/sample", 8, head, runner=gh)


@pytest.mark.real_trust
@pytest.mark.scenario(SAVED)
@pytest.mark.scenario(TIP)
@pytest.mark.parametrize("time_fields,source", [
    ({"saved_at": "2026-10-20T12:00:00+02:00", "updated_at": "invalid"}, "receipt_saved_at"),
    ({"updated_at": CUTOFF}, "receipt_saved_at"), ({}, "branch_tip"),
])
def test_legacy_baseline_is_inclusive_durable_and_uses_creation_time(tmp_path, time_fields, source):
    path, gh = tmp_path / "8.json", LegacyGh()
    receipt = legacy_receipt(path, **time_fields)
    if source == "receipt_saved_at":
        gh.tip = "2026-10-30T00:00:00Z"
    migrated = initialize(path, receipt, gh)
    assert read(path) == migrated  # Baseline is durable before callers can proceed.
    assert all(migrated[key] == value for key, value in receipt.items())
    assert migrated["feedback_state_version"] == 1
    assert migrated["legacy_snapshot"] is True and "feedback_ids" not in migrated
    assert migrated["feedback_migration"] == {"source": source, "cutoff": CUTOFF}
    assert migrated["consumed_feedback_ids"] == sorted(
        f"{kind}:{i}" for kind in ("conversation", "review", "inline") for i in (1, 2))
    assert sum("/commits/" in call[1] for call in gh.calls) == (source == "branch_tip")
    assert "feedback_state_version" not in receipt

    gh.tip = "2026-11-01T00:00:00Z"
    for pages in gh.pages.values():
        pages[0][0]["body"] = "Edited into an instruction"
        pages[2][0]["body"] = "Edited self-prefixed comment"
    # Restart plus tip advance cannot reread or recompute an initialized ledger.
    before, calls = path.read_bytes(), list(gh.calls)
    restarted = initialize(path, read(path), gh, head="advanced-head")
    assert path.read_bytes() == before and gh.calls == calls
    next_round = snapshot(path, restarted, queue.pr_feedback_items("owner/sample", 8, runner=gh))
    assert next_round["feedback_ids"] == ["conversation:3", "review:3", "inline:3"]


@pytest.mark.real_trust
@pytest.mark.scenario(FAILURE)
@pytest.mark.parametrize("field", ["saved_at", "updated_at"])
@pytest.mark.parametrize("bad", [None, "", True, 123, "not a date", "2026-10-20", "2026-10-20T10:00:00"])
def test_present_invalid_saved_time_never_falls_back(tmp_path, field, bad):
    path, gh = tmp_path / "8.json", LegacyGh()
    receipt = legacy_receipt(path, **{field: bad})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="revision timestamp"):
        initialize(path, receipt, gh)
    assert path.read_bytes() == before and receipt == read(path)
    assert gh.calls == []


@pytest.mark.real_trust
@pytest.mark.scenario(FAILURE)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("failure", ["page", "shape", "missing_time", "naive_time", "invalid_time",
                                    "missing_id", "bool_id", "invalid_id", "commit", "commit_time", "save"])
def test_initialization_failure_preserves_receipt_and_retries(tmp_path, monkeypatch, endpoint, failure):
    path, gh = tmp_path / "8.json", LegacyGh()
    receipt = legacy_receipt(path)
    before = path.read_bytes()
    if failure in ("page", "shape"):
        gh.pages[endpoint] = RuntimeError("page failed") if failure == "page" else [[{}], None]
    else:
        record = gh.pages[endpoint][2][1]  # Validate even records after the cutoff.
        key = "submitted_at" if endpoint.endswith("reviews") else "created_at"
        if failure == "missing_time":
            record.pop(key)
        elif failure in ("naive_time", "invalid_time"):
            record[key] = "2026-10-20T10:00:00" if failure == "naive_time" else "bad"
        elif failure == "missing_id":
            record.pop("id")
        elif failure in ("bool_id", "invalid_id"):
            record["id"] = True if failure == "bool_id" else "3"
    if failure == "commit_time":
        gh.tip = None

    def runner(args):
        if failure == "commit" and "/commits/" in args[1]:
            raise RuntimeError("commit read failed")
        return gh(args)

    def fail_replace(*args):
        raise OSError("atomic replacement failed")

    effects, errors = [], []

    def process(number):
        initialize(path, receipt, runner)
        effects.append("dispatch or delivery")

    with monkeypatch.context() as patch:
        if failure == "save":
            patch.setattr(Path, "replace", fail_replace)
        assert intake._each([8], process, on_error=lambda n, e: errors.append((n, e))) == []
    assert len(errors) == 1 and errors[0][0] == 8 and effects == []
    assert isinstance(errors[0][1], (ValueError, RuntimeError, OSError))
    assert path.read_bytes() == before and receipt == read(path)
    assert initialize(path, read(path), LegacyGh())["feedback_state_version"] == 1


@pytest.mark.scenario(FAILURE)
@pytest.mark.parametrize("metadata", [
    {"feedback_state_version": 1}, {"feedback_state_version": None},
    {"consumed_feedback_ids": []}, {"feedback_ids": []},
    {"legacy_snapshot": True}, {"feedback_migration": {}},
])
def test_partial_metadata_is_corrupt_not_legacy(tmp_path, metadata):
    path, gh = tmp_path / "8.json", LegacyGh()
    receipt = legacy_receipt(path, **metadata)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="revision"):
        initialize(path, receipt, gh)
    assert path.read_bytes() == before and gh.calls == []
    assert initialize(path, None, gh) is None and gh.calls == []
