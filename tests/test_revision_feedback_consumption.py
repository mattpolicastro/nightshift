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


@pytest.mark.scenario("pr-revision/revision-actor-gate/unauthorized-revision-ignored-at-either-stage")
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("state", ["absent", "current", "legacy"])
def test_denial_precedes_feedback_and_receipt_writes(stage_setup, tmp_path, monkeypatch, empty, state):
    repo, gh, commands = stage_setup
    saved = read(tmp_path)
    if empty:
        saved["consumed_feedback_ids"].append("conversation:102")
        gh.feedback = []
    if state == "legacy":
        for key in ("feedback_state_version", "consumed_feedback_ids", "feedback_ids"):
            saved.pop(key)
    specloop_job.save(receipt_path(tmp_path), saved)
    if state == "absent":
        receipt_path(tmp_path).unlink()
    gh.label_event("agent:revise", "unlabeled")
    gh.label_event("agent:revise", "labeled", actor="outsider")
    labels = gh.labels.copy()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("denial must precede feedback reads, collection, saves, and commands")

    monkeypatch.setattr(queue, "_pr_feedback_records", forbidden)
    monkeypatch.setattr(intake, "_save", forbidden)
    monkeypatch.setattr(specloop_job, "collect", forbidden)
    for _ in range(2):
        assert intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                     specloop_runner=forbidden, git_runner=forbidden,
                                     status_runner=forbidden, launcher=forbidden) == []
        assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert gh.labels == labels and not commands.calls
    assert len(gh.comments) == 1
    assert f"label-authorization-8:{gh.events[-1]['id']}" in gh.comments[0]["body"]
    assert not any(c[:2] == ["pr", "edit"] or "/commits/" in c[1] for c in gh.calls)


@pytest.mark.scenario("pr-revision/revision-actor-gate/trusted-label-does-not-trust-feedback")
@pytest.mark.scenario("pr-revision/no-instruction-no-revision/empty-revise-refused")
@pytest.mark.parametrize("empty", [False, True])
def test_authorized_selection_uses_independent_author_trust(stage_setup, tmp_path, empty):
    _, gh, commands = stage_setup
    saved = read(tmp_path)
    saved["consumed_feedback_ids"] = [f"{kind}:1" for kind in ("conversation", "inline", "review")]
    specloop_job.save(receipt_path(tmp_path), saved)
    for records in (gh.feedback, gh.reviews, gh.inline):
        records.clear()
        for i, (association, body) in enumerate([
            ("OWNER", "Consumed instruction"), ("NONE", "Outsider instruction"),
            (None, "Missing association"), ("MEMBER", queue.SELF_COMMENT_PREFIX + " Self comment"),
            ("COLLABORATOR", "   "), ("MEMBER", "Historical actionable instruction"),
        ], 1):
            record = {"id": i, "user": {"login": "reviewer"}, "body": body,
                      "created_at": "2026-10-19T12:00:00Z", "submitted_at": TIP_DATE,
                      "path": "gate.py", "line": 12}
            if association is not None:
                record["author_association"] = association
            if i != 6 or not empty:
                records.append(record)
    result, = poll(stage_setup, tmp_path)
    receipt = read(tmp_path)
    assert receipt["delivered"] and "agent:revise" not in gh.labels
    selected = [] if empty else ["conversation:6", "review:6", "inline:6"]
    assert receipt["feedback_ids"] == selected
    assert receipt["consumed_feedback_ids"] == sorted(saved["consumed_feedback_ids"] + selected)
    if empty:
        assert result == {"outcome": "empty"} and not commands.calls
        assert "nothing to revise" in gh.comments[0]["body"]
        assert "no unconsumed review feedback" in gh.comments[0]["body"]
        assert "branch tip" not in gh.comments[0]["body"]
    else:
        expected = ["**reviewer:**\n\nHistorical actionable instruction"] * 2 + [
            "**reviewer on `gate.py:12`:**\n\nHistorical actionable instruction"]
        assert receipt["feedback"] == expected
        assert all(block in dispatched_feedback(commands) for block in expected)
    assert not any("/commits/" in c[1] for c in gh.calls)


@pytest.mark.scenario("pr-revision/worker-feedback-compatibility/worker-timestamp-cutoff-remains")
@pytest.mark.parametrize("failure", [None, "view", "api", "list"])
def test_worker_cutoff_and_best_effort_reads_remain_independent(failure):
    from test_revise import PrGh

    gh = PrGh(numbers=(8, 15, 9))
    for hour in (11, 12, 13):
        for association in ("MEMBER", "NONE", None):
            item = {"body": f"Feedback at {hour}", "author": {"login": "reviewer"},
                    "user": {"login": "reviewer"}, "path": "gate.py", "original_line": 12,
                    "createdAt": f"2026-10-20T{hour}:00:00Z",
                    "submittedAt": f"2026-10-20T{hour}:00:00Z",
                    "created_at": f"2026-10-20T{hour}:00:00Z"}
            if association:
                item.update(authorAssociation=association, author_association=association)
            gh.comments.append(item)
            gh.reviews.append({**item, "state": "CHANGES_REQUESTED"})
            gh.inline.append(item)

    def github(args):
        if failure and failure in args[:2]:
            raise RuntimeError("read unavailable")
        return gh(args)

    expected = ["**reviewer:**\n\nFeedback at 13",
                "**reviewer (changes requested):**\n\nFeedback at 13",
                "**reviewer on `gate.py:12`:**\n\nFeedback at 13"]
    expected = {None: expected, "view": expected[2:], "api": expected[:2], "list": []}[failure]
    assert queue.pr_feedback("owner/sample", "spec/gate", TIP_DATE, runner=github) == expected
    if failure not in ("list", "view"):
        assert next(c for c in gh.calls if c[:2] == ["pr", "view"])[2] == "15"


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
        assert read(tmp_path)["feedback_ids"] == read(tmp_path)["feedback"] == []
        assert read(tmp_path)["delivered"]
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


@pytest.mark.scenario("pr-revision/durable-round-snapshot/unsuccessful-work-keeps-feedback-eligible")
@pytest.mark.parametrize("feedback_change", ["unchanged", "body_only", "id_only"])
def test_undelivered_failure_retries_only_when_feedback_changes(stage_setup, tmp_path, feedback_change):
    _, gh, commands = stage_setup
    success = commands.result
    failure = ({"version": 1, "outcome": "refused", "reasons": ["Run is busy."]}
               if isinstance(commands, BuildCommands) else
               {"version": 1, "outcome": "needs_input", "questions": ["Which empty input?"]})
    commands.result = failure
    gh.fail_needs_human = True
    with pytest.raises(RuntimeError, match="label response lost"):
        poll(stage_setup, tmp_path)
    saved = read(tmp_path)
    assert saved["result"] == failure
    assert not saved.get("delivered")
    assert saved["feedback_ids"] == ["conversation:102"]
    assert "conversation:102" not in saved["consumed_feedback_ids"]
    assert "needs-human" in gh.labels

    # Only clear needs-human: a new revise event would replace the receipt
    # before the comparison of live feedback with the undelivered snapshot.
    gh.label_event("needs-human", "unlabeled")
    if feedback_change == "body_only":
        gh.feedback[1]["body"] = "Handle empty input by returning an empty list."
    elif feedback_change == "id_only":
        gh.feedback[1]["id"] = 104  # Identical text still represents new feedback.
    commands.result = success
    expected = failure if feedback_change == "unchanged" else success
    assert poll(stage_setup, tmp_path) == [expected]
    recovered = read(tmp_path)
    assert recovered["event"] == saved["event"]
    assert recovered["delivered"]
    dispatches = commands.feedback if isinstance(commands, BuildCommands) else commands.proposals
    assert len(dispatches) == (1 if feedback_change == "unchanged" else 2)
    if feedback_change == "unchanged":
        assert recovered["feedback"] == saved["feedback"]
        assert recovered["feedback_ids"] == saved["feedback_ids"]
        assert recovered["consumed_feedback_ids"] == saved["consumed_feedback_ids"]
        assert {"agent:revise", "needs-human"} <= gh.labels
    else:
        selected = "conversation:104" if feedback_change == "id_only" else "conversation:102"
        block = "**reviewer:**\n\n" + gh.feedback[1]["body"]
        assert recovered["feedback_ids"] == [selected]
        assert recovered["feedback"] == [block]
        assert (recovered["feedback"] == saved["feedback"]) == (feedback_change == "id_only")
        assert recovered["consumed_feedback_ids"] == sorted([*saved["consumed_feedback_ids"], selected])
        assert block in dispatched_feedback(commands)
        assert "agent:revise" not in gh.labels and "needs-human" not in gh.labels


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
@pytest.mark.parametrize("same_body", [False, True])
def test_comments_during_revision_feed_the_next_round(stage_setup, tmp_path, monkeypatch, source, same_body):
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
    a.update(body=b["body"] if same_body else "Consumed ID with a new instruction",
             createdAt="2026-10-21T12:00:00Z",
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
    assert read(tmp_path)["consumed_feedback_ids"] == sorted([*consumed, a_id, "conversation:104"])
    assert read(tmp_path)["feedback_ids"] == []
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
