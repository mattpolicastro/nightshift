import json
import subprocess

import pytest

from nightshift import cli, outcomes, specloop_attention
from nightshift.config import Config


PR = "https://github.com/example/repo/pull/7"


def run_record(change="add-gate", status="paused", reason="stuck", pr=PR):
    return {
        "change": change, "status": status, "pr": pr,
        "pause": {"reason": reason, "task": "1.1", "detail": "Needs a decision"} if reason else None,
        "tasks": {"done": 0, "total": 1}, "head": "abc123",
    }


def status_result(runs):
    return subprocess.CompletedProcess(
        [], 0, json.dumps({"version": 1, "supervisor": {"state": "running"}, "runs": runs}), ""
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
        run_record("infra", reason="infra"),
        run_record("usage-limit", reason="usage_limit"),
    ])
    assert cli.main(["attention", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["specloop"] == []
    assert cli.main(["attention"]) == 0
    text = capsys.readouterr().out
    assert "0 specloop run(s) need you" in text
    for change in ("running", "finished", "old-pause", "infra", "usage-limit"):
        assert change not in text


@pytest.mark.parametrize("reason", [
    "stuck", "blocked", "needs_human", "gate_evidence", "phase_gate",
    "ready_to_publish", "human_checks", "coverage", "acceptance",
    "violation_repeat", "review_invalid", "secret",
])
def test_all_human_pause_reasons(status, capsys, reason):
    status["result"] = status_result([run_record(reason=reason, pr=None)])
    assert cli.main(["attention", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["specloop"][0]
    assert entry["reason"] == reason
    assert entry["pr"] is None


@pytest.mark.scenario("attention/stale-tolerance/unreadable-status-marked-stale")
@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess([], 1, "", "status unavailable"),
    subprocess.CompletedProcess([], 0, "not json", ""),
    subprocess.CompletedProcess([], 0, '{"version":2,"runs":[]}', ""),
    subprocess.CompletedProcess([], 0, '{"version":1}', ""),
    status_result([run_record(), {"change": "broken"}]),
    FileNotFoundError("specloop missing"),
    subprocess.TimeoutExpired("specloop", 8),
])
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
