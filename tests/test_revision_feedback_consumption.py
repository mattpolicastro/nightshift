"""Build intake consumes durable IDs independently of publication."""

import importlib.util
import json
from pathlib import Path

import pytest

from nightshift import queue, specloop_intake as intake, specloop_job
from test_specloop_build_revision import setup, poll
from test_specloop_spec_revision import HEAD, REVISED


pytestmark = pytest.mark.real_trust


def receipt_path(tmp_path):
    return tmp_path / "revisions/owner__sample/8.json"


def read(tmp_path):
    return json.loads(receipt_path(tmp_path).read_text())


@pytest.mark.scenario("pr-revision/build-stage-revision/review-comments-become-tasks")
@pytest.mark.scenario("pr-revision/consumption-based-eligibility/relabel-does-not-requeue-consumed-feedback")
@pytest.mark.scenario("pr-revision/no-instruction-no-revision/empty-revise-refused")
def test_relabel_does_not_requeue_consumed_feedback(setup, tmp_path):
    repo, gh, commands = setup
    consumed = ["conversation:101", "inline:1", "inline:2", "inline:3"]

    def run(args, **kwargs):
        if args[:2] == ["specloop", "revise"]:
            saved = read(tmp_path)
            assert saved["feedback_ids"] == ["inline:1", "inline:2"]
            assert saved["feedback"] == json.loads(Path(args[4]).read_text())
            assert "src/gate.py:12" in saved["feedback"][0]
            assert "tests/test_gate.py:42" in saved["feedback"][1]
        return commands(args, **kwargs)

    def github(args):
        if args[:2] in (["pr", "comment"], ["pr", "edit"]):
            assert read(tmp_path)["consumed_feedback_ids"] == consumed
            assert read(tmp_path)["result"]["commit"] == REVISED
        return gh(args)

    intake.revise_pending(repo, queue.Labels(), tmp_path, runner=github, specloop_runner=run)
    assert gh.head == HEAD and not gh.labels  # Local result has not been published.
    assert [c[1] for c in commands.calls] == ["status", "revise"]
    # A fresh module and disk receipt exercise restart without mutating the live module.
    spec = importlib.util.spec_from_file_location("nightshift.restarted_consumption", intake.__file__)
    restarted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restarted)
    for module in (intake, restarted, restarted):
        gh.label_event("agent:revise", "labeled")
        assert module.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                     specloop_runner=commands) == [{"outcome": "empty"}]
        assert read(tmp_path)["consumed_feedback_ids"] == consumed
        assert not gh.labels and gh.head == HEAD
        assert "no unconsumed review feedback" in gh.comments[-1]["body"]
        assert "branch tip" not in gh.comments[-1]["body"]
    assert [c[1] for c in commands.calls] == ["status", "revise"]
    assert not any("/commits/" in c[1] for c in gh.calls)


@pytest.mark.scenario("pr-revision/identified-feedback/feedback-read-failure-preserves-request")
@pytest.mark.parametrize("source", ["issues/8/comments", "pulls/8/reviews", "pulls/8/comments"])
@pytest.mark.parametrize("failure", ["outage", "page", "id", "conflict"])
def test_build_feedback_read_failure_preserves_snapshot(setup, tmp_path, source, failure):
    repo, gh, commands = setup
    before = receipt_path(tmp_path).read_bytes()
    record = {"id": 9, "body": "Fix this", "user": {"login": "reviewer"},
              "author_association": "COLLABORATOR"}

    def github(args):
        if args[:2] == ["api", f"repos/{repo.name}/{source}"]:
            if failure == "outage":
                raise RuntimeError("feedback unavailable")
            return json.dumps([None] if failure == "page" else
                              [[{**record, "id": None}]] if failure == "id" else
                              [[record], [{**record, "body": "Conflicting record"}]])
        return gh(args)

    errors = []
    assert intake.revise_pending(repo, queue.Labels(), tmp_path, runner=github,
                                 specloop_runner=commands,
                                 on_error=lambda n, e: errors.append((n, e))) == []
    assert len(errors) == 1 and errors[0][0] == 8
    assert isinstance(errors[0][1], (ValueError, RuntimeError))
    assert receipt_path(tmp_path).read_bytes() == before
    assert not commands.calls and not gh.comments and "agent:revise" in gh.labels


@pytest.mark.parametrize("legacy", [False, True])
def test_refusal_preserves_history_and_unknown_fields(setup, tmp_path, legacy):
    _, gh, commands = setup
    previous = read(tmp_path)
    if legacy:
        for field in ("feedback_state_version", "consumed_feedback_ids", "feedback_ids"):
            previous.pop(field)
    previous["future"] = {"keep": True}
    specloop_job.save(receipt_path(tmp_path), previous)
    commands.result = {"version": 1, "outcome": "refused", "reasons": ["Busy"]}
    poll(setup, tmp_path)
    saved = read(tmp_path)
    assert saved["consumed_feedback_ids"] == ["conversation:101", "inline:3"]
    assert saved["feedback_ids"] == ["inline:1", "inline:2"]
    assert saved["future"] == {"keep": True}
    if legacy:
        assert saved["feedback_migration"]["source"] == "branch_tip"
    gh.label_event("needs-human", "unlabeled")
    poll(setup, tmp_path)
    assert read(tmp_path)["consumed_feedback_ids"] == saved["consumed_feedback_ids"]
    assert sum("/commits/" in c[1] for c in gh.calls) == int(legacy)


def test_first_build_selection_includes_historical_ids_without_commit_read(setup, tmp_path):
    _, gh, commands = setup
    receipt_path(tmp_path).unlink()
    poll(setup, tmp_path)
    assert read(tmp_path)["feedback_ids"] == ["conversation:101", "inline:1", "inline:2", "inline:3"]
    assert "Old feedback" in commands.feedback[0] and "Already addressed" in commands.feedback[0]
    assert not any("/commits/" in c[1] for c in gh.calls)
