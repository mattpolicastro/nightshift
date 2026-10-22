"""Revision snapshots consume durable IDs across stages and publication."""

import importlib.util
import json
from pathlib import Path

import pytest

from nightshift import queue, specloop_intake as intake, specloop_job
from test_specloop_approval import Commands as AdmissionCommands
from test_specloop_build_revision import BuildCommands, setup, poll
from test_specloop_intake import idea_file
from test_specloop_spec_revision import HEAD, REVISED, TIP_DATE, setup as spec_setup


pytestmark = pytest.mark.real_trust


def receipt_path(tmp_path):
    return tmp_path / "revisions/owner__sample/8.json"


def read(tmp_path):
    return json.loads(receipt_path(tmp_path).read_text())


@pytest.fixture(params=["spec", "build"])
def stage_setup(request, spec_setup):
    repo, gh, commands = spec_setup
    return repo, gh, BuildCommands(gh) if request.param == "build" else commands


def dispatched_feedback(commands):
    if isinstance(commands, BuildCommands):
        return "\n".join(json.loads(commands.feedback[-1]))
    return idea_file(commands.proposals[-1]).read_text(encoding="utf-8")


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


@pytest.mark.scenario("pr-revision/consumption-based-eligibility/first-selection-and-state-isolation")
def test_first_selection_includes_historical_ids_without_commit_read(stage_setup, tmp_path):
    _, gh, commands = stage_setup
    gh.feedback = [{**gh.feedback[1], "id": i, "createdAt": created}
                   for i, created in enumerate(("2026-10-19T12:00:00Z", TIP_DATE,
                                                 "2026-10-20T13:00:00Z"), 101)]
    ids = ["conversation:101", "conversation:102", "conversation:103"]
    other = {**read(tmp_path), "consumed_feedback_ids": ids}
    paths = [tmp_path / "revisions/owner__sample/9.json",
             tmp_path / "revisions/another__repo/8.json"]
    for path in paths:
        specloop_job.save(path, other)
    before = [path.read_bytes() for path in paths]
    receipt_path(tmp_path).unlink()
    poll(stage_setup, tmp_path)
    saved = read(tmp_path)
    assert saved["feedback_ids"] == saved["consumed_feedback_ids"] == ids
    # Equal bodies on distinct IDs remain distinct instructions.
    assert dispatched_feedback(commands).count("Handle empty input") == 3
    assert [path.read_bytes() for path in paths] == before
    assert not any("/commits/" in c[1] for c in gh.calls)


@pytest.mark.scenario("pr-revision/consumption-based-eligibility/comments-during-revision-feed-the-next-round")
@pytest.mark.scenario("pr-revision/consumption-based-eligibility/edited-consumed-comment-stays-consumed")
@pytest.mark.parametrize("source", ["conversation", "review"])
def test_comments_during_revision_feed_the_next_round(stage_setup, tmp_path, monkeypatch, source):
    repo, gh, commands = stage_setup
    a = {**gh.feedback[1], "submittedAt": gh.feedback[1]["createdAt"]}
    gh.feedback = [a] if source == "conversation" else []
    gh.reviews = [a] if source == "review" else []
    a["body"] = "A edited before selection: café."
    a_id = f"{source}:102"
    consumed = read(tmp_path)["consumed_feedback_ids"]
    jobs = []
    ready = False

    def start(path, receipt, args, **kwargs):
        saved = read(tmp_path)
        assert saved["feedback_ids"] == [a_id]
        assert saved["consumed_feedback_ids"] == consumed
        assert saved["feedback"] == ["**reviewer:**\n\nA edited before selection: café."]
        payload = (json.loads(Path(args[3]).read_text()) if args[0] == "revise"
                   else idea_file(args).read_text())
        assert (saved["feedback"] == payload if args[0] == "revise"
                else saved["feedback"][0] in payload)
        jobs.append(args)
        receipt["job"] = "pending"
        specloop_job.save(path, receipt)

    monkeypatch.setattr(specloop_job, "start", start)
    monkeypatch.setattr(specloop_job, "collect", lambda *args: commands.result if ready else None)

    def tick():
        return intake.revise_pending(repo, queue.Labels(), tmp_path, runner=github,
                                     git_runner=commands, status_runner=commands)

    def github(args):
        if args[0] == "api" and "/commits/" in args[1]:
            pytest.fail("ID selection must not read commit dates")
        if args[:2] in (["pr", "comment"], ["pr", "edit"]):
            assert a_id in read(tmp_path)["consumed_feedback_ids"]
            assert "conversation:104" not in read(tmp_path)["consumed_feedback_ids"]
        return gh(args)

    tick()  # A is durably selected and dispatched before B exists.
    snapshot = read(tmp_path)
    b = {**a, "id": 104, "body": "B posted during revision", "createdAt": "2026-10-20T14:00:00Z"}
    gh.feedback.append(b)
    a["body"] = "A edited after selection"
    tick()  # A pending job must retain its exact payload, despite live edits.
    assert len(jobs) == 1 and read(tmp_path) == snapshot
    ready = True
    tick()
    assert read(tmp_path)["feedback"] == snapshot["feedback"]
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, a_id])
    assert "agent:revise" not in gh.labels
    # Build publication advances the tip after the result; spec delivery has
    # already pushed it. B predates the new commit in either stage.
    gh.head = REVISED
    later_tip_date = "2026-10-20T15:00:00Z"
    monkeypatch.setattr("test_specloop_spec_revision.TIP_DATE", later_tip_date)
    assert b["createdAt"] < later_tip_date
    commands.fetch_head = REVISED
    a.update(body=b["body"], createdAt="2026-10-21T12:00:00Z",
             submittedAt="2026-10-21T12:00:00Z")  # Same ID stays consumed after another edit.
    gh.label_event("agent:revise", "labeled")
    poll(stage_setup, tmp_path)
    assert read(tmp_path)["feedback_ids"] == ["conversation:104"]
    assert read(tmp_path)["feedback"] == ["**reviewer:**\n\nB posted during revision"]
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, a_id, "conversation:104"])
    payload = dispatched_feedback(commands)
    assert payload.count("B posted during revision") == 1 and "A edited" not in payload
    assert len(commands.feedback if isinstance(commands, BuildCommands) else commands.proposals) == 1
    gh.label_event("agent:revise", "labeled")
    assert poll(stage_setup, tmp_path) == [{"outcome": "empty"}]
    assert len(commands.feedback if isinstance(commands, BuildCommands) else commands.proposals) == 1
    assert not any("/commits/" in c[1] for c in gh.calls)


@pytest.mark.scenario("pr-revision/consumption-based-eligibility/spec-consumption-survives-admission")
def test_spec_consumption_survives_admission(spec_setup, tmp_path):
    repo, gh, commands = spec_setup
    poll(spec_setup, tmp_path)
    before = receipt_path(tmp_path).read_bytes()
    assert "conversation:102" in read(tmp_path)["consumed_feedback_ids"]
    gh.label_event("spec:approved", "labeled")
    admissions = AdmissionCommands()
    admissions.result["head"] = REVISED
    commands.fetch_head = REVISED
    intake.admit_pending(repo, queue.Labels(), tmp_path, runner=gh,
                         specloop_runner=admissions, git_runner=commands)
    assert len(admissions.admissions) == 1
    assert receipt_path(tmp_path).read_bytes() == before
    gh.feedback.append({**gh.feedback[1], "id": 104, "body": "New build feedback"})
    gh.label_event("agent:revise", "labeled")
    build = BuildCommands(gh)
    build.runs = admissions.runs
    poll((repo, gh, build), tmp_path)
    assert json.loads(build.feedback[0]) == ["**reviewer:**\n\nNew build feedback"]
    assert read(tmp_path)["feedback_ids"] == ["conversation:104"]
    assert read(tmp_path)["consumed_feedback_ids"] == ["conversation:101", "conversation:102",
                                                       "conversation:104", "inline:1000"]
    assert len(commands.proposals) == 1 and not build.proposals
