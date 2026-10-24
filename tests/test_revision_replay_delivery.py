"""Keyed acknowledgements validate identity and deliver the observed run state."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import specloop_client, specloop_intake as intake, specloop_job as jobs
from revision_result_fixtures import revision_success
from specloop.state import Pause, Run
from test_revision_feedback_consumption import read, receipt_path
from test_revision_feedback_recovery import restart, tick
from test_specloop_build_revision import setup as build_setup

pytestmark = pytest.mark.real_trust


@pytest.fixture
def pending(build_setup, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_children", {})
    launched = []

    def launch(argv, **kwargs):
        launched.append(Path(argv[-2]))
        return SimpleNamespace(poll=lambda: None)

    assert tick(build_setup, tmp_path, background=True, launcher=launch) == []
    directory, = launched
    request = json.loads((directory / "request.json").read_text())
    expected = revision_success(request["args"], build_setup[2].result)
    assert expected["round_id"] == read(tmp_path)["round_id"]
    return directory, expected


@pytest.mark.scenario("pr-revision/keyed-result-acknowledgement/result-identity-gates-consumption")
@pytest.mark.parametrize("source", ["sync", "background", "saved"])
@pytest.mark.parametrize("field,value", [
    ("round_id", ...), ("round_id", "another-round"), ("round_id", None),
    ("commit", ...), ("commit", ""), ("commit", 123),
    ("replayed", ...), ("replayed", 1), ("replayed", "false"),
    ("status", ...), ("status", "finished"), ("status", []),
    ("pause", ...), ("pause", None), ("pause", []), ("pause", {}),
    ("pause", {"reason": ""}), ("pause", {"reason": 1}),
    ("pause", {"reason": "manual", "detail": []}),
    ("pause", {"reason": "manual", "task": 1}),
    ("pause", {"reason": "manual", "resume_at": True}),
    ("pause", {"reason": "manual", "resume_at": float("inf")}),
])
def test_invalid_success_cannot_consume(build_setup, tmp_path, request, source, field, value):
    _, gh, commands = build_setup
    baseline = read(tmp_path)["consumed_feedback_ids"]
    emitted = []

    def corrupt(payload):
        # Construct the COMPLETE payload first; never let the builder repair it.
        payload.update(replayed=True, status="paused", pause={"reason": "manual"})
        if value is ...:
            del payload[field]
        else:
            payload[field] = value
        emitted.append(deepcopy(payload))
        return payload

    def command(argv, **kwargs):
        proc = commands(argv, **kwargs)
        if argv[1] == "revise":
            proc.stdout = json.dumps(corrupt(json.loads(proc.stdout)))
        return proc

    if source != "sync":
        directory, expected = request.getfixturevalue("pending")
        corrupt(expected)
        if source == "saved":
            jobs.save(receipt_path(tmp_path), {**read(tmp_path), "result": expected})
        else:
            jobs.finalize(directory, {"result": expected})
    with pytest.raises(specloop_client.SpecloopError, match="revise returned"):
        tick(build_setup, tmp_path, module=restart(), specloop_runner=command)
    saved = read(tmp_path)
    assert saved["consumed_feedback_ids"] == baseline
    assert saved["feedback_ids"] == ["inline:1", "inline:2"]
    assert not saved.get("delivered") and not gh.comments
    assert gh.labels == {"agent:revise"}
    assert len(emitted) == 1
    if source == "saved":
        assert saved["result"] == emitted[0]
        before = receipt_path(tmp_path).read_bytes()
        with pytest.raises(specloop_client.SpecloopError):
            tick(build_setup, tmp_path, module=restart())
        assert receipt_path(tmp_path).read_bytes() == before
    else:
        assert "result" not in saved
    assert len(commands.feedback) == (source == "sync")


@pytest.mark.scenario("pr-revision/keyed-result-acknowledgement/result-identity-gates-consumption")
@pytest.mark.scenario("pr-revision/keyed-result-acknowledgement/replay-reports-observed-control-state")
@pytest.mark.parametrize("status", ["running", "paused", "aborted", "done", "legacy"])
@pytest.mark.parametrize("failure", ["ack", "comment", "lost_comment", "label", "lost_label", "pause"])
def test_replay_delivery_recovers_without_redispatch(
    build_setup, tmp_path, monkeypatch, pending, status, failure,
):
    _, gh, commands = build_setup
    directory, expected = pending
    original = read(tmp_path)
    run = Run(tmp_path / "runs" / original["change"])
    run.state.status = "running" if status == "legacy" else status
    run.state.pause = Pause("manual", "1.2", "Human decision", 123) if status in ("paused", "aborted") else None
    run.save()
    run.request_pause()
    run.event("control", status=run.state.status)
    control = {p.name: p.read_bytes() for p in run.dir.iterdir()}
    if status == "legacy":
        # A coherent historical attempt keeps its original event marker/schema.
        request = json.loads((directory / "request.json").read_text())
        index = request["args"].index("--round-id")
        del request["args"][index:index + 2]
        request.pop("round_id")
        jobs.save(directory / "request.json", request)
        for field in ("revision_key_version", "round_job"):
            original.pop(field)
        original["round_id"] = str(original["event"])
        jobs.save(receipt_path(tmp_path), original)
        expected = revision_success(request["args"], commands.result)
    else:
        expected.update(replayed=True, status=status,
                        pause=asdict(run.state.pause) if run.state.pause else None)
    jobs.finalize(directory, {"result": expected})
    gh.inline.append({**gh.inline[0], "id": 4, "body": "Later feedback stays unconsumed"})
    consumed = sorted(set(original["consumed_feedback_ids"] + original["feedback_ids"]))
    saved_calls = deepcopy(commands.calls)

    def github(args):
        commenting = args[:2] == ["pr", "comment"] or "PATCH" in args
        labeling = args[:2] == ["pr", "edit"]
        if commenting or labeling:
            assert read(tmp_path)["result"] == expected
            assert read(tmp_path)["consumed_feedback_ids"] == consumed
        if commenting and failure in ("comment", "pause"):
            raise (intake.Paused if failure == "pause" else RuntimeError)("delivery interrupted")
        if commenting and failure == "lost_comment":
            gh(args)
            raise RuntimeError("delivery interrupted")
        if labeling and failure in ("label", "lost_label"):
            if failure == "lost_label":
                gh(args)
            raise RuntimeError("delivery interrupted")
        return gh(args)

    save = intake._save

    def fail_ack(path, receipt):
        if receipt.get("consumed_feedback_ids") == consumed:
            raise OSError("ack interrupted")
        save(path, receipt)

    with monkeypatch.context() as patch:
        if failure == "ack":
            patch.setattr(intake, "_save", fail_ack)
        with pytest.raises((OSError, RuntimeError), match="interrupted"):
            tick(build_setup, tmp_path, runner=github)
    saved = read(tmp_path)
    assert saved["result"] == expected and not saved.get("delivered")
    assert saved["consumed_feedback_ids"] == (original["consumed_feedback_ids"] if failure == "ack" else consumed)
    assert len(gh.comments) == (failure in ("lost_comment", "label", "lost_label"))
    assert ("agent:revise" in gh.labels) == (failure != "lost_label")
    # A lost successful removal leaves no armed PR to revisit.
    for _ in range(3):
        tick(build_setup, tmp_path, module=restart())
    completed = read(tmp_path)
    assert completed == (saved if failure == "lost_label" else
                         {**saved, "consumed_feedback_ids": consumed, "delivered": True})
    assert commands.calls == saved_calls and not gh.labels
    assert {p.name: p.read_bytes() for p in run.dir.iterdir()} == control
    comment, = gh.comments
    body = comment["body"]
    marker = f"<!-- nightshift:revise:{original['change']}:{original['round_id']} -->"
    assert body.endswith(marker) and expected["commit"] in body
    assert sum(c[:2] == ["pr", "comment"] for c in gh.calls) == 1
    if status == "legacy":
        assert f"Queued review tasks for {original['change']} in {expected['commit']}; the build has resumed." in body
    else:
        assert "replay" in body and "observed" in body and "when acknowledged" in body
        assert ("finished" if status == "done" else status) in body
        if status == "paused":
            assert "manual" in body
        if status != "running":
            assert "resumed" not in body


@pytest.mark.scenario("pr-revision/keyed-result-acknowledgement/refusal-and-replacement-have-distinct-comments")
def test_refusal_and_replacement_reconcile_independently(build_setup, tmp_path):
    _, gh, commands = build_setup
    success = deepcopy(commands.result)
    commands.result = {"version": 1, "outcome": "refused", "reasons": ["Run is busy."]}
    gh.fail_needs_human = True
    with pytest.raises(RuntimeError, match="label response lost"):
        tick(build_setup, tmp_path)
    refused = read(tmp_path)
    original_comment = deepcopy(gh.comments[0])
    assert refused["result"] == commands.result and not refused.get("delivered")
    assert refused["consumed_feedback_ids"] == ["conversation:101", "inline:3"]
    assert gh.labels == {"agent:revise", "needs-human"}
    for _ in range(2):
        tick(build_setup, tmp_path, module=restart())
    assert len(commands.feedback) == len(gh.comments) == 1
    gh.label_event("needs-human", "unlabeled")
    tick(build_setup, tmp_path, module=restart())  # Finish the original refusal.
    gh.label_event("needs-human", "unlabeled")
    commands.result = success
    gh.fail_comment = True
    with pytest.raises(RuntimeError, match="comment response lost"):
        tick(build_setup, tmp_path, module=restart())
    succeeded = read(tmp_path)
    for _ in range(3):
        tick(build_setup, tmp_path, module=restart())
    assert succeeded["result"] == commands.result
    assert succeeded["event"] == refused["event"] and succeeded["round_id"] != refused["round_id"]
    assert succeeded["consumed_feedback_ids"] == ["conversation:101", "inline:1", "inline:2", "inline:3"]
    assert read(tmp_path) == {**succeeded, "delivered": True} and not gh.labels
    assert len(commands.feedback) == len(gh.comments) == 2 and gh.comments[0] == original_comment
    assert success["commit"] not in gh.comments[0]["body"] and success["commit"] in gh.comments[1]["body"]
    for saved in (refused, succeeded):
        marker = f"<!-- nightshift:revise:{saved['change']}:{saved['round_id']} -->"
        assert sum(c["body"].endswith(marker) for c in gh.comments) == 1
