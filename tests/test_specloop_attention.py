import json
import subprocess

import pytest

from nightshift import cli, outcomes, specloop_attention
from nightshift.config import Config
from test_specloop_client import ALIAS_NOTICE


PR = "https://github.com/example/repo/pull/7"
MISSING_ALLOWANCE = object()
HUMAN_REASONS = [
    "stuck", "blocked", "needs_human", "gate_evidence", "phase_gate",
    "ready_to_publish", "human_checks", "coverage", "acceptance",
    "violation_repeat", "review_invalid", "secret",
]


def run_record(change="add-gate", status="paused", reason="stuck", pr=PR,
               auto_resumes_remaining=0):
    pause = {"reason": reason, "task": "1.1", "detail": "Needs a decision"} if reason else None
    if pause is not None and auto_resumes_remaining is not MISSING_ALLOWANCE:
        pause["auto_resumes_remaining"] = auto_resumes_remaining
    return {
        "change": change, "status": status, "pr": pr,
        "pause": pause,
        "tasks": {"done": 0, "total": 1}, "head": "abc123",
    }


def status_result(runs):
    return subprocess.CompletedProcess(
        [], 0, json.dumps({"version": 1, "supervisor": {"state": "running"}, "runs": runs}), ALIAS_NOTICE
    )


@pytest.fixture
def status(monkeypatch):
    monkeypatch.setattr(cli.config, "load_env_file", lambda: None)
    monkeypatch.setattr(cli.config, "load", lambda path: Config(repos=[]))
    response = {"result": status_result([run_record()]), "calls": 0}

    def runner(args, **kwargs):
        assert args == ["specloop", "status", "--json"]
        assert kwargs["timeout"] == outcomes.REQUEST_SECONDS
        response["calls"] += 1
        if isinstance(response["result"], Exception):
            raise response["result"]
        return response["result"]

    monkeypatch.setattr(specloop_attention.subprocess, "run", runner)
    return response


@pytest.mark.scenario("attention/specloop-pauses-listed/stuck-run-listed")
@pytest.mark.scenario("nightshift-cli/alias-routed-daemon-callers/daemon-calls-parse-json-despite-notice")
def test_stuck_run_in_text_and_json_beside_worker_tasks(status, monkeypatch, capsys):
    outcomes.record("example/repo", 8, state="needs_decision", reason="Review this")
    monkeypatch.setattr(outcomes, "_read_github", lambda *args: '{"state":"OPEN","labels":[]}')
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert "add-gate — stuck" in text
    assert PR in text
    assert "example/repo#8" in text
    assert "STALE" not in text

    assert cli.main(["attention", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert payload["tasks"][0]["issue"] == 8
    assert len(payload["specloop"]) == 1
    entry = payload["specloop"][0]
    assert (entry["change"], entry["reason"], entry["pr"]) == ("add-gate", "stuck", PR)
    assert entry["stale"] is False
    assert entry["checked_at"]
    assert payload["specloop_error"] is None


@pytest.mark.scenario("attention/specloop-pauses-listed/running-run-not-listed")
def test_running_finished_and_automatic_pauses_not_listed(status, capsys):
    status["result"] = status_result([
        run_record("running", "running", None),
        run_record("finished", "done", None),
        run_record("old-pause", "running", "stuck"),
        run_record("infra", reason="infra", auto_resumes_remaining=2),
        run_record("usage-limit", reason="usage_limit", auto_resumes_remaining=None),
    ])
    assert cli.main(["attention", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["specloop"] == []
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert "0 specloop run(s) need you" in text
    for change in ("running", "finished", "old-pause", "infra", "usage-limit"):
        assert change not in text


@pytest.mark.scenario("attention/pause-driven-attention/other-human-pauses-listed")
@pytest.mark.parametrize("reason", HUMAN_REASONS + ["new_human_reason", "usage_limit"])
def test_all_human_pause_reasons(status, capsys, reason):
    status["result"] = status_result([run_record(reason=reason, pr=None)])
    assert cli.main(["attention", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["specloop"][0]
    assert entry["reason"] == reason
    assert entry["pr"] is None
    assert cli.main(["attention"]) == 0
    assert f"add-gate — {reason}" in capsys.readouterr().out


def assert_pause_listed(status, capsys, change, reason):
    status["result"] = status_result([run_record(change, reason=reason)])
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert f"{change} — {reason}" in text
    assert PR in text
    assert cli.main(["attention", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["specloop"]) == 1
    entry = payload["specloop"][0]
    assert (entry["change"], entry["reason"], entry["pr"]) == (change, reason, PR)
    assert set(entry) == {"change", "reason", "pr", "checked_at", "error", "stale"}
    assert entry["stale"] is False
    assert payload["specloop_error"] is None


@pytest.mark.scenario("attention/pause-driven-attention/exhausted-infra-pause-listed")
def test_exhausted_infra_pause_is_listed(status, capsys):
    assert_pause_listed(status, capsys, "exhausted-infra", "infra")


@pytest.mark.scenario("attention/pause-driven-attention/requested-pause-listed")
def test_requested_pause_is_listed(status, capsys):
    assert_pause_listed(status, capsys, "requested-pause", "requested")


@pytest.mark.scenario("attention/pause-driven-attention/automatic-recovery-omitted")
@pytest.mark.parametrize("reason", ["infra", "usage_limit", "new_automatic_reason", "stuck"])
@pytest.mark.parametrize("allowance", [1, 2, None])
def test_automatic_recovery_is_omitted(status, capsys, reason, allowance):
    run = run_record("automatic-wait", reason=reason, auto_resumes_remaining=allowance)
    run["pause"]["resume_at"] = 1  # Overdue deadlines do not exhaust the allowance.
    status["result"] = status_result([run])
    assert cli.main(["attention", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["specloop"] == []
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert "0 specloop run(s) need you" in text
    assert "automatic-wait" not in text


@pytest.mark.scenario("attention/pause-driven-attention/nonpaused-runs-omitted")
@pytest.mark.parametrize(("run_status", "reason"), [
    ("running", "infra"), ("done", "infra"), ("aborted", "infra"),
    ("running", None), ("done", None), ("aborted", None), ("paused", None),
])
def test_status_and_pause_gate_attention(status, capsys, run_status, reason):
    status["result"] = status_result([run_record("not-waiting", run_status, reason)])
    assert cli.main(["attention", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["specloop"] == []
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert "0 specloop run(s) need you" in text
    assert "not-waiting" not in text


@pytest.mark.scenario("attention/allowance-compatibility-and-snapshots/legacy-pause-without-allowance")
@pytest.mark.parametrize("reason", HUMAN_REASONS + ["infra", "requested", "unknown", "usage_limit"])
def test_legacy_pause_without_allowance(status, capsys, reason):
    status["result"] = status_result([
        run_record("legacy-pause", reason=reason, auto_resumes_remaining=MISSING_ALLOWANCE),
        run_record("unlimited-pause", reason=reason, auto_resumes_remaining=None),
    ])
    assert cli.main(["attention", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["specloop"]
    expected = [] if reason == "usage_limit" else [("legacy-pause", reason, PR)]
    assert [(entry["change"], entry["reason"], entry["pr"]) for entry in entries] == expected
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert "unlimited-pause" not in text
    if expected:
        assert f"legacy-pause — {reason}" in text
        assert PR in text
    else:
        assert "legacy-pause" not in text


@pytest.mark.parametrize("allowance", [True, False, -1, 0.0, 1.5, "0", [], {}])
@pytest.mark.parametrize("run_status", ["paused", "running"])
def test_invalid_allowance_is_rejected(allowance, run_status):
    with pytest.raises(ValueError, match="auto_resumes_remaining"):
        specloop_attention._entries({"runs": [
            run_record(status=run_status, auto_resumes_remaining=allowance),
        ]})


@pytest.mark.scenario("attention/allowance-compatibility-and-snapshots/invalid-allowance-preserves-snapshot")
@pytest.mark.parametrize("allowance", [True, False, -1, 0.0, 1.5, "0", [], {}])
@pytest.mark.parametrize("reason", ["infra", "requested"])
def test_invalid_allowance_preserves_snapshot(status, monkeypatch, capsys, allowance, reason):
    checked_at = "2026-10-21T12:00:00+00:00"
    monkeypatch.setattr(outcomes, "now", lambda: checked_at)
    status["result"] = status_result([run_record("cached-pause", reason=reason)])
    assert cli.main(["attention", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["specloop"] == [{
        "change": "cached-pause", "reason": reason, "pr": PR,
        "checked_at": checked_at, "error": None, "stale": False,
    }]

    # Even a valid entry before the malformed one must not replace the snapshot.
    monkeypatch.setattr(outcomes, "now", lambda: "2026-10-21T13:00:00+00:00")
    status["result"] = status_result([
        run_record("replacement-pause", reason="requested"),
        run_record("malformed-pause", reason=reason, auto_resumes_remaining=allowance),
    ])
    assert cli.main(["attention", "--json"]) == 1
    failed = json.loads(capsys.readouterr().out)
    error = failed["specloop_error"]
    assert error and "auto_resumes_remaining" in error
    assert failed == {
        **before, "specloop_error": error,
        "specloop": [{**before["specloop"][0], "error": error, "stale": True}],
    }
    assert cli.main(["attention"]) == 1
    text = capsys.readouterr().out
    assert f"STALE cached-pause — {reason}" in text
    assert PR in text
    assert f"last checked {checked_at}: {error}" in text
    assert "replacement-pause" not in text
    assert "malformed-pause" not in text

    calls = status["calls"]
    assert cli.main(["attention", "--offline", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {**failed, "offline": True}
    assert status["calls"] == calls


@pytest.mark.scenario("attention/allowance-compatibility-and-snapshots/cached-entries-and-recovery")
@pytest.mark.parametrize("recovered_runs", [
    [],
    [run_record("exhausted-infra", reason="infra", auto_resumes_remaining=1),
     run_record("requested-pause", status="running", reason=None)],
], ids=["empty-status", "no-human-waits"])
def test_cached_entries_and_recovery(status, monkeypatch, capsys, recovered_runs):
    checked_at = "2026-10-21T12:00:00+00:00"
    monkeypatch.setattr(outcomes, "now", lambda: checked_at)
    outcomes.record("example/repo", 8, state="needs_decision", reason="Review this")
    monkeypatch.setattr(outcomes, "_read_github", lambda *args: '{"state":"OPEN","labels":[]}')
    status["result"] = status_result([
        run_record("exhausted-infra", reason="infra"),
        run_record("requested-pause", reason="requested", pr=None),
    ])
    assert cli.main(["attention", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["version"] == 1
    assert before["offline"] is False
    assert before["stale"] == []
    assert before["specloop_error"] is None
    assert before["specloop"] == [
        {"change": change, "reason": reason, "pr": pr,
         "checked_at": checked_at, "error": None, "stale": False}
        for change, reason, pr in [
            ("exhausted-infra", "infra", PR), ("requested-pause", "requested", None),
        ]
    ]
    worker, = before["tasks"]
    assert (worker["repo"], worker["issue"], worker["state"], worker["reason"]) == (
        "example/repo", 8, "needs_decision", "Review this",
    )
    worker_text = outcomes.render(outcomes.tasks())
    assert "example/repo#8 — needs decision" in worker_text
    assert "Review this" in worker_text
    assert worker["issue_url"] in worker_text

    # Offline must use the cache even when the next status read would fail.
    status["result"] = FileNotFoundError("specloop unavailable")
    error = "could not run specloop: specloop unavailable"
    for stale in (False, True):
        if stale:
            assert cli.main(["attention", "--json"]) == 1
            failed = json.loads(capsys.readouterr().out)
            assert failed == {
                **before, "specloop_error": error,
                "specloop": [{**entry, "stale": True, "error": error}
                             for entry in before["specloop"]],
            }
        expected = failed if stale else before
        calls = status["calls"]
        assert cli.main(["attention", "--offline", "--json"]) == int(stale)
        assert json.loads(capsys.readouterr().out) == {**expected, "offline": True}
        assert cli.main(["attention", "--offline"]) == int(stale)
        text = capsys.readouterr().out
        assert "Cached outcomes (GitHub and specloop not checked)" in text
        assert worker_text in text
        assert "2 specloop run(s) need you" in text
        for entry in before["specloop"]:
            prefix = "STALE " if stale else ""
            assert f"{prefix}{entry['change']} — {entry['reason']}" in text
        assert PR in text
        assert ("STALE" in text) is stale
        if stale:
            assert f"last checked {checked_at}: {error}" in text
        assert status["calls"] == calls

    checked_at = "2026-10-21T14:00:00+00:00"
    status["result"] = status_result(recovered_runs)
    assert cli.main(["attention", "--json"]) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered == {
        **before, "specloop": [], "specloop_error": None,
        "tasks": [{**worker, "checked_at": checked_at}],
    }
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert worker_text in text
    assert "0 specloop run(s) need you" in text
    assert "exhausted-infra" not in text
    assert "requested-pause" not in text
    assert "STALE" not in text
    assert "specloop unavailable" not in text

    calls = status["calls"]
    assert cli.main(["attention", "--offline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {**recovered, "offline": True}
    assert specloop_attention.snapshot(offline=True) == {
        "entries": [], "checked_at": checked_at, "error": None,
    }
    assert status["calls"] == calls


@pytest.mark.scenario("attention/stale-tolerance/unreadable-status-marked-stale")
@pytest.mark.scenario("nightshift-cli/alias-routed-daemon-callers/daemon-calls-parse-json-despite-notice")
@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess([], 1, "", "status unavailable"),
    subprocess.CompletedProcess([], 0, "not json", ""),
    subprocess.CompletedProcess([], 0, '{"version":2,"runs":[]}', ""),
    subprocess.CompletedProcess([], 0, '{"version":1}', ""),
    status_result([run_record(), {"change": "broken"}]),
    FileNotFoundError("specloop missing"),
    subprocess.TimeoutExpired("specloop", 8),
])
@pytest.mark.scenario(
    "specloop-documentation/front-door-guidance/input-and-status-remain-usable"
)
def test_unreadable_status_preserves_snapshot_and_recovers(status, capsys, failure):
    assert cli.main(["attention", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)["specloop"][0]
    status["result"] = failure
    assert cli.main(["attention", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    after = payload["specloop"][0]
    assert len(payload["specloop"]) == 1
    for field in ("change", "reason", "pr", "checked_at"):
        assert after[field] == before[field]
    assert after["stale"] is True
    assert after["error"] == payload["specloop_error"]
    assert after["error"]
    assert cli.main(["attention"]) == 1
    text = capsys.readouterr().out
    assert "STALE add-gate — stuck" in text
    assert PR in text
    assert before["checked_at"] in text

    # Offline reads reload the persisted snapshot, including the failed check.
    calls = status["calls"]
    assert cli.main(["attention", "--offline", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["specloop"] == [after]
    assert status["calls"] == calls

    # A successful empty read must clear old pauses and their error.
    status["result"] = status_result([])
    assert cli.main(["attention", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["specloop"] == []
    assert payload["specloop_error"] is None


def test_first_read_failure_is_visible_without_cached_entries(status, capsys):
    status["result"] = FileNotFoundError("specloop missing")
    assert cli.main(["attention", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["specloop"] == []
    assert "specloop missing" in payload["specloop_error"]
    assert cli.main(["attention"]) == 1
    assert "STALE specloop — last checked never" in capsys.readouterr().out


def test_offline_never_launches_status(status, capsys):
    assert cli.main(["attention", "--offline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["specloop"] == []
    assert status["calls"] == 0
    assert cli.main(["attention", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["specloop"]
    assert cli.main(["attention", "--offline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["specloop"] == entries
    assert status["calls"] == 1
