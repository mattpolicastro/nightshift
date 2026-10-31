"""Literal result identities survive legacy recovery and interrupted delivery."""

import json
import subprocess
from functools import partial

import pytest

from nightshift import daemon, queue, specloop_intake as intake, specloop_job as jobs
from nightshift.config import Config, Repo
from test_revision_comment_retry import COMMENTS, REPO, remote
from test_revision_feedback_consumption import read, receipt_path
from test_revision_feedback_recovery import restart, tick
from test_specloop_build_revision import setup as build_setup
from test_specloop_spec_revision import setup as spec_setup

pytestmark = pytest.mark.real_trust
ATTEMPT = "a" * 32
ROUND = "1234567890ab4def81234567890abcde"


def marker(round_id=None, attempt=None, *, change="gate"):
    suffix = "" if round_id is None else f":{round_id}"
    if attempt is not None:
        suffix += f":attempt-{attempt}"
    return f"<!-- nightshift:revise:{change}{suffix} -->"


def saved_delivery(tmp_path, round_id, attempt=None):
    receipt = {"change": "gate", "head": "old-head", "event": 42,
               "command": "revise", "feedback_state_version": 1,
               "feedback": ["Saved λ feedback"], "feedback_ids": ["conversation:7"],
               "consumed_feedback_ids": ["conversation:7", "inline:3"],
               "future": {"keep": [1, 2]},
               "result": {"version": 1, "outcome": "revised", "commit": "saved-commit"}}
    if round_id is not None:
        receipt["round_id"] = round_id
    if attempt is not None:
        receipt["delivery_attempt"] = attempt
    jobs.save(receipt_path(tmp_path), receipt)
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(f'[run]\nrepo = {json.dumps(str(tmp_path))}\n')

    def forbidden(*args, **kwargs):
        pytest.fail("saved delivery must not dispatch another command")

    stage = (Repo(REPO, engine="specloop", manifest=manifest),
             partial(queue._gh, sleep=lambda _: None), forbidden)
    return receipt, stage


@pytest.mark.scenario("pr-revision/legacy-result-marker-compatibility/old-round-markers-still-recognised")
@pytest.mark.scenario("forge-interface/core-policy-preservation/revision-and-proposal-delivery-unchanged")
@pytest.mark.parametrize("round_id", [None, "42", ROUND], ids=["unqualified", "event", "uuid"])
def test_old_round_markers_still_recognised(remote, tmp_path, round_id):
    saved, stage = saved_delivery(tmp_path, round_id)
    token = marker(round_id)
    # Legacy identities remain valid as exact final-line markers.
    remote.comments[:] = [
        {"id": 81, "body": marker(round_id, ATTEMPT) if round_id else marker("42")},
        {"id": 82, "body": f"Earlier response\n{token}"},
    ]
    earlier = remote.comments[0].copy()
    assert tick(stage, tmp_path, module=restart()) == [saved["result"]]
    assert read(tmp_path) == {**saved, "delivered": True}
    assert "delivery_attempt" not in read(tmp_path)
    assert not remote.creates and len(remote.patches) == 1
    assert remote.patches[0][1] == f"repos/{REPO}/issues/comments/82"
    assert remote.comments[0] == earlier
    assert remote.comments[1]["body"].splitlines()[-1] == token


@pytest.mark.scenario("pr-revision/revision-comment-retry-reconciliation/lost-revision-comment-response-reconciled")
@pytest.mark.parametrize("round_id,attempt", [(None, None), ("42", None), (ROUND, None), ("42", ATTEMPT)])
def test_lost_post_reconciles_only_complete_marker_on_later_page(remote, round_id, attempt):
    token = marker(round_id, attempt)
    remote.comments[:] = [
        {"id": 81, "body": marker("41")},
        {"id": 82, "body": marker("42", "b" * 32)},
        {"id": 83, "body": token.removesuffix(" -->")},
    ]
    if attempt:
        remote.comments.insert(0, {"id": 80, "body": marker("42")})
    earlier = [c.copy() for c in remote.comments]
    intake._comment(REPO, 8, "gate", "Current result", action="revise", kind="pr",
                    round_id=round_id, delivery_attempt=attempt,
                    runner=partial(queue._gh, sleep=lambda _: None))
    assert len(remote.creates) == len(remote.patches) == 1
    assert len(remote.comments) == len(earlier) + 1 and remote.comments[:-1] == earlier
    current = remote.comments[-1]
    assert current["body"].splitlines()[-1] == token
    assert current["body"].count("<!-- nightshift:") == 1
    assert remote.patches[0] == ["api", f"repos/{REPO}/issues/comments/{current['id']}",
                                 "--method", "PATCH", "-f", f"body={current['body']}"]
    assert remote.calls == [["api", COMMENTS, "--paginate", "--slurp"], remote.creates[0],
                            ["api", COMMENTS, "--paginate", "--slurp"], remote.patches[0]]


@pytest.mark.scenario("pr-revision/revision-comment-retry-reconciliation/revision-reconciliation-read-fails")
@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/retained-attempt-survives-delivery-interruption")
@pytest.mark.parametrize("error,reads", [("HTTP 503: history unavailable", 3),
                                       ("HTTP 403: history forbidden", 1)])
def test_suffixed_result_survives_failed_reconciliation(remote, tmp_path, monkeypatch, error, reads):
    saved, stage = saved_delivery(tmp_path, "42", ATTEMPT)
    before = receipt_path(tmp_path).read_bytes()
    earlier = {"id": 80, "body": marker("42")}
    remote.comments.append(earlier.copy())
    transport = remote.run

    def lose_then_fail_history(args, **kwargs):
        result = transport(args, **kwargs)
        if args[1:3] == ["pr", "comment"]:
            remote.read_error = error  # Initial lookup succeeds; reconciliation fails.
        return result

    monkeypatch.setattr(queue.subprocess, "run", lose_then_fail_history)
    with pytest.raises(queue.GhError, match=error) as caught:
        tick(stage, tmp_path)
    assert caught.value.transient is (reads == 3)
    assert len(remote.creates) == 1 and not remote.patches
    assert sum(c[:2] == ["api", COMMENTS] for c in remote.calls) == 1 + reads
    assert receipt_path(tmp_path).read_bytes() == before
    assert not any(c[:2] == ["pr", "edit"] for c in remote.calls)
    remote.read_error = None
    assert tick(stage, tmp_path, module=restart()) == [saved["result"]]
    assert read(tmp_path) == {**saved, "delivered": True}
    assert len(remote.creates) == len(remote.patches) == 1
    assert remote.comments[0] == earlier
    assert remote.comments[-1]["body"].splitlines()[-1] == marker("42", ATTEMPT)
    assert remote.patches[0][1] == f"repos/{REPO}/issues/comments/{remote.comments[-1]['id']}"
    assert remote.patches[0][-1] == f"body={remote.creates[0][-1]}"


@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/retained-attempt-survives-delivery-interruption")
@pytest.mark.parametrize("pause_at", ["post", "retry"])
def test_pause_and_restart_keep_suffixed_attempt(spec_setup, tmp_path, monkeypatch, pause_at):
    repo, gh, commands = spec_setup
    saved = {**read(tmp_path), "event": 2, "round_id": "2", "delivery_attempt": ATTEMPT,
             "command": "revise", "delivered": False,
             "result": {"version": 1, "outcome": "revised", "commit": "saved-commit"}}
    jobs.save(receipt_path(tmp_path), saved)
    before = receipt_path(tmp_path).read_bytes()
    earlier = {"id": 80, "body": marker("2", change=saved["change"])}
    gh.comments.append(earlier.copy())
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    paused_calls = []

    def pause():
        daemon.PAUSE_FILE.touch()
        paused_calls.extend(gh.calls)

    def transport(args, **kwargs):
        assert args[0] == "gh"
        result = gh(args[1:])
        if args[1:3] == ["pr", "comment"]:
            if pause_at == "retry":
                return subprocess.CompletedProcess(args, 1, "", "HTTP 502: comment response lost")
            pause()
        return subprocess.CompletedProcess(args, 0, result, "")

    monkeypatch.setattr(queue.subprocess, "run", transport)
    runner = partial(queue._gh, sleep=lambda _: pause())

    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=runner, specloop_runner=commands)
    assert paused_calls and gh.calls == paused_calls
    assert receipt_path(tmp_path).read_bytes() == before and not health.failures
    assert "agent:revise" in gh.labels and not commands.calls
    daemon.poll_specloop(Config(repos=[repo]), daemon.RepoHealth(), runner=runner, specloop_runner=commands)
    assert gh.calls == paused_calls and receipt_path(tmp_path).read_bytes() == before
    daemon.PAUSE_FILE.unlink()
    tick(spec_setup, tmp_path, module=restart(), runner=runner)
    assert read(tmp_path) == {**saved, "delivered": True} and not commands.calls
    assert len(gh.comments) == 2 and gh.comments[0] == earlier
    assert gh.comments[-1]["body"].splitlines()[-1] == marker("2", ATTEMPT, change=saved["change"])
    assert sum(c[:2] == ["pr", "comment"] for c in gh.calls) == 1
    patch, = [c for c in gh.calls if "PATCH" in c]
    assert patch[1] == f"repos/{REPO}/issues/comments/{gh.comments[-1]['id']}"


@pytest.mark.scenario("pr-revision/attempt-scoped-revision-results/invalid-attempt-metadata-retains-failure-reporting")
@pytest.mark.parametrize("pause", [False, True])
def test_malformed_attempt_reaches_failure_threshold(spec_setup, tmp_path, monkeypatch, pause):
    repo, gh, commands = spec_setup
    saved = {**read(tmp_path), "delivery_attempt": "not-a-uuid", "delivered": False,
             "future": {"keep": [1, 2]}}
    jobs.save(receipt_path(tmp_path), saved)
    before = receipt_path(tmp_path).read_bytes()
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    health = daemon.RepoHealth()
    for _ in range(health.threshold - 1):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert not gh.comments and gh.labels == {"spec:review", "agent:revise"}
    report, reporting_calls = intake.report_failure, []

    def reporting(*args, **kwargs):
        reporting_calls.append(list(gh.calls))
        if pause:
            daemon.PAUSE_FILE.touch()
        return report(*args, **kwargs)

    monkeypatch.setattr(intake, "report_failure", reporting)
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
    assert health.item_failures[(repo.name, "revise", 8)] == health.threshold
    assert len(reporting_calls) == 1 and not commands.calls
    assert receipt_path(tmp_path).read_bytes() == before and read(tmp_path) == saved
    assert gh.labels == {"spec:review", "agent:revise"} | (set() if pause else {"needs-human"})
    writes = [c for c in gh.calls if c[:2] in (["pr", "comment"], ["pr", "edit"]) or "PATCH" in c]
    if pause:
        assert not writes and not gh.comments and gh.calls == reporting_calls[0]
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=commands)
        assert gh.calls == reporting_calls[0] and receipt_path(tmp_path).read_bytes() == before
    else:
        comment, = gh.comments
        assert comment["body"].splitlines()[-1] == "<!-- nightshift:revise-error:8 -->"
        assert "<!-- nightshift:revise:" not in comment["body"]
        assert len(writes) == 2 and writes[0][:2] == ["pr", "comment"]
        assert writes[1] == ["pr", "edit", "8", "--repo", REPO, "--add-label", "needs-human"]


@pytest.mark.scenario("pr-revision/legacy-result-marker-compatibility/initial-markers-and-build-replay-remain-compatible")
def test_initial_build_marker_survives_keyed_replay(build_setup, tmp_path):
    _, gh, commands = build_setup
    rounds = []

    def command(args, **kwargs):
        if args[:2] == ["specloop", "revise"]:
            rounds.append(args[args.index("--round-id") + 1])
            if len(rounds) == 1:
                raise OSError("lost command response")
        result = commands(args, **kwargs)
        if args[:2] == ["specloop", "revise"]:
            result.stdout = json.dumps({**json.loads(result.stdout), "replayed": True, "status": "done"})
        return result

    with pytest.raises(RuntimeError, match="lost command response"):
        tick(build_setup, tmp_path, specloop_runner=command)
    saved = read(tmp_path)
    assert not gh.comments and "delivery_attempt" not in saved
    tick(build_setup, tmp_path, module=restart(), specloop_runner=command)
    completed = read(tmp_path)
    assert rounds == [saved["round_id"], saved["round_id"]]
    assert all(completed[k] == saved[k] for k in ("round_id", "event", "feedback", "feedback_ids"))
    assert "delivery_attempt" not in completed and completed["delivered"]
    assert completed["result"]["round_id"] == saved["round_id"] and completed["result"]["replayed"]
    comment, = gh.comments
    assert comment["body"] == (
        f"{queue.SELF_COMMENT_PREFIX} Acknowledged replay of review tasks for {saved['change']} "
        f"in {completed['result']['commit']}; the build was observed finished when acknowledged."
        f"\n\n{marker(saved['round_id'], change=saved['change'])}")
