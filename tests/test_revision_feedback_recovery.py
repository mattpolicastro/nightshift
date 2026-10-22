"""Revision recovery retains the selected payload and acknowledges only success."""

import importlib.util
import json
import os
import subprocess
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import queue, specloop_intake as intake, specloop_job
from test_revision_feedback_consumption import read, receipt_path, stage_setup
from test_specloop_spec_revision import setup as spec_setup
from test_specloop_intake import idea_file

pytestmark = pytest.mark.real_trust


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
