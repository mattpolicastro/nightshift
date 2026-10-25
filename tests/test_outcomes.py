import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from nightshift import outcomes, queue
from nightshift.config import Labels


def github(issue_state="OPEN", labels=(), pr_state="OPEN"):
    def run(args):
        if args[0] == "pr":
            return json.dumps({"state": pr_state})
        return json.dumps({"state": issue_state, "labels": [{"name": x} for x in labels]})
    return run


def test_merge_close_and_reopen(monkeypatch):
    monkeypatch.setattr(outcomes, "HISTORY_INTERVAL", 0)
    outcomes.record("a/b", 1, state="awaiting_merge", pr_url="https://github.com/a/b/pull/2")
    assert outcomes.refresh(Labels(), runner=github(pr_state="CLOSED"))[0]["state"] == "needs_decision"
    assert outcomes.refresh(Labels(), runner=github(pr_state="MERGED"))[0]["state"] == "resolved"
    assert outcomes.refresh(Labels(), runner=github())[0]["state"] == "awaiting_merge"
    result = outcomes.refresh(Labels(), runner=github(issue_state="CLOSED", pr_state="CLOSED"))
    assert not outcomes.attention(result)


def test_outage_preserves_observation_even_for_resolved_tasks(monkeypatch):
    monkeypatch.setattr(outcomes, "HISTORY_INTERVAL", 0)
    outcomes.record("a/b", 1, state="needs_decision", escalated=True)
    before = outcomes.refresh(Labels(), runner=github(issue_state="CLOSED"))[0]
    def fail(args):
        raise RuntimeError("HTTP 404")
    after = outcomes.refresh(Labels(), runner=fail)[0]
    assert after["state"] == "resolved"
    assert after["checked_at"] == before["checked_at"]
    assert "STALE a/b#1" in outcomes.render([after])
    assert "HTTP 404" in after["error"]


def test_escalation_resolution_and_crash_are_distinct(monkeypatch):
    monkeypatch.setattr(outcomes, "HISTORY_INTERVAL", 0)
    outcomes.record("a/b", 1, state="needs_decision", escalated=True)
    outcomes.record("a/b", 2, state="needs_decision", escalated=False, reason="crashed")
    items = outcomes.refresh(Labels(), runner=github(labels=[Labels().ready]))
    assert [i["issue"] for i in outcomes.attention(items)] == [2]
    items = outcomes.refresh(Labels(), runner=github(labels=[Labels().needs_human]))
    assert len(outcomes.attention(items)) == 2


def test_slow_refresh_cannot_hide_new_attempt():
    outcomes.record("a/b", 1, state="needs_decision", escalated=True)
    def racing(args):
        outcomes.record("a/b", 1, state="running", run_id="new")
        return github(issue_state="CLOSED")(args)
    item = outcomes.refresh(Labels(), runner=racing)[0]
    assert item["state"] == "running"
    assert item["checked_at"] is None


def test_history_survives_concurrent_writes_and_repo_collisions():
    def write(n):
        outcomes.record("a/b", 1, state="running", attempt=n)
        outcomes.record("c/d", 1, state="needs_decision", attempt=n)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    assert len(outcomes.tasks()) == 2
    with outcomes.database() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 24


def test_escalation_and_pr_are_recorded_before_github_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path)
    def fail(args):
        raise RuntimeError("offline")
    import pytest
    with pytest.raises(RuntimeError):
        queue.escalate("a/b", 1, "workflow requires a human", runner=fail, unclaimed=True)
    with pytest.raises(RuntimeError):
        queue.complete("a/b", 2, "https://github.com/a/b/pull/3", runner=fail)
    items = outcomes.tasks()
    assert items[0]["state"] == "needs_decision"
    assert not items[0]["escalated"]
    assert items[1]["pr_url"].endswith("/3")


def test_running_tasks_do_not_query_github():
    outcomes.record("a/b", 1, state="running")
    def fail(args):
        raise AssertionError("must not refresh active tasks")
    assert not outcomes.attention(outcomes.refresh(Labels(), runner=fail))


@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/historical-attention-remains-readable")
@pytest.mark.parametrize("offline", [False, True])
def test_cli_json_and_offline_do_not_write_github(tmp_path, monkeypatch, capsys, offline):
    from nightshift import cli, specloop_attention
    from nightshift.config import Config
    monkeypatch.setattr(cli.config, "load_env_file", lambda: None)
    monkeypatch.setattr(cli.config, "load", lambda path: Config(repos=[]))
    outcomes.record("a/b", 1, state="needs_decision", reason="needs review")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    claim = queue.claim_path("a/b", 1)
    claim.parent.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    claim.write_text(json.dumps({"repo": "a/b", "number": 1, "branch": "claude/1",
                                "worktree": str(worktree), "started_at": outcomes.now(),
                                "phase": "review"}))
    work = worktree / "work.txt"
    work.write_text("unfinished worker change")
    evidence = {path: path.read_bytes() for path in (claim, work)}
    def status(args, **kwargs):
        assert args == ["status"]
        return {"version": 1, "runs": [{"change": "pending", "status": "paused",
                "pr": None, "pause": {"reason": "stuck"}}]}
    monkeypatch.setattr(specloop_attention.specloop_client, "call", status)
    specloop_attention.snapshot()
    def forbidden(args):
        raise AssertionError("offline must not call GitHub")
    monkeypatch.setattr(queue, "_gh", forbidden)
    def read(args, deadline):
        assert not offline
        assert args[:3] == ["issue", "view", "1"]
        return github()(args)
    monkeypatch.setattr(outcomes, "_read_github", read)
    if offline:
        monkeypatch.setattr(specloop_attention.specloop_client, "call", forbidden)
    assert cli.main(["attention", "--json"] + (["--offline"] if offline else [])) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert payload["offline"] is offline
    assert payload["tasks"][0]["issue_url"] == "https://github.com/a/b/issues/1"
    assert payload["tasks"][0]["reason"] == "needs review"
    assert payload["specloop"][0]["change"] == "pending"
    assert payload["specloop"][0]["reason"] == "stuck"
    assert payload["stale"] == []
    assert {path: path.read_bytes() for path in evidence} == evidence
    with outcomes.database() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def seed_history(active=0, resolved=0):
    # Bulk insert so the scale test measures refresh, not fixture setup.
    with outcomes.database() as db:
        for n in range(1, active + resolved + 1):
            state = "needs_decision" if n <= active else "resolved"
            db.execute("INSERT INTO events(repo,issue,at,data) VALUES(?,?,?,?)",
                       ("a/b", n, outcomes.now(), json.dumps({"state": state})))


def test_large_history_bounds_work_and_reserves_both_groups():
    seed_history(active=100, resolved=2000)
    seen = set()

    def run(args):
        seen.add(int(args[2]))
        return github()(args)

    items = outcomes.refresh(Labels(), runner=run)
    assert len(seen) == 32
    assert len([n for n in seen if n <= 100]) == 24
    assert len([n for n in seen if n > 100]) == 8
    deferred = [i for i in items if i["issue"] not in seen]
    assert len(deferred) == 2068
    assert all(i["error"] and i["checked_at"] is None for i in deferred)
    assert all(i["state"] == "resolved" for i in deferred if i["issue"] > 100)


def test_failed_attempts_rotate_across_invocations():
    seed_history(active=70)
    batches = []
    for _ in range(3):
        seen = set()

        def fail(args):
            seen.add(int(args[2]))
            raise RuntimeError("offline")

        outcomes.refresh(Labels(), runner=fail)
        batches.append(seen)
    assert not batches[0] & batches[1]
    assert set.union(*batches) == set(range(1, 71))


def test_history_cooldown_still_detects_reopening(monkeypatch):
    monkeypatch.setattr(outcomes, "now", lambda: "2026-09-05T00:00:00+00:00")
    outcomes.record("a/b", 1, state="needs_decision", escalated=True)
    outcomes.refresh(Labels(), runner=github(issue_state="CLOSED"))

    def forbidden(args):
        raise AssertionError("history must cool down")

    assert outcomes.refresh(Labels(), runner=forbidden)[0]["state"] == "resolved"
    monkeypatch.setattr(outcomes, "now", lambda: "2026-09-05T01:00:00+00:00")
    item = outcomes.refresh(Labels(), runner=github(labels=[Labels().needs_human]))[0]
    assert item["state"] == "needs_decision"
    assert item["error"] is None


def test_failed_history_retries_after_a_minute(monkeypatch):
    monkeypatch.setattr(outcomes, "now", lambda: "2026-09-05T00:00:00+00:00")
    seed_history(resolved=1)

    def fail(args):
        raise RuntimeError("offline")

    outcomes.refresh(Labels(), runner=fail)
    monkeypatch.setattr(outcomes, "now", lambda: "2026-09-05T00:01:00+00:00")
    item = outcomes.refresh(Labels(), runner=github(issue_state="CLOSED"))[0]
    assert item["state"] == "resolved"
    assert item["error"] is None


def test_small_active_queue_borrows_history_capacity():
    seed_history(active=2, resolved=100)
    seen = set()

    def run(args):
        seen.add(int(args[2]))
        return github(issue_state="CLOSED")(args)

    outcomes.refresh(Labels(), runner=run)
    assert len(seen) == 32
    assert {1, 2} <= seen


def test_deadline_stops_new_work_and_history_gets_a_slot(monkeypatch):
    import threading
    seed_history(active=100, resolved=100)
    tick = 0
    monkeypatch.setattr(outcomes.time, "monotonic", lambda: tick)
    barrier = threading.Barrier(4)
    seen = set()

    def run(args):
        nonlocal tick
        seen.add(int(args[2]))
        barrier.wait(timeout=5)
        tick = 41
        return github()(args)

    items = outcomes.refresh(Labels(), runner=run)
    assert len(seen) == 4
    assert 101 in seen
    assert all(i["error"] for i in items if i["issue"] not in seen)


def test_github_subprocess_respects_remaining_deadline(monkeypatch):
    import subprocess
    import pytest
    calls = []
    monkeypatch.setattr(outcomes.time, "monotonic", lambda: 10)

    def run(args, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(outcomes.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="timed out"):
        outcomes._read_github(["issue", "view", "1"], 13)
    with pytest.raises(RuntimeError, match="timed out"):
        outcomes._read_github(["issue", "view", "1"], 100)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        outcomes._read_github(["issue", "view", "1"], 9)
    assert calls == [3, 8]


def test_cli_reports_deferred_checks_as_stale_json(monkeypatch, capsys):
    from nightshift import cli
    from nightshift.config import Config
    monkeypatch.setattr(cli.config, "load_env_file", lambda: None)
    monkeypatch.setattr(cli.config, "load", lambda path: Config(repos=[]))
    monkeypatch.setattr(outcomes, "_read_github", lambda args, deadline: github()(args))
    # Attention now reads specloop too; isolate that CLI from the live service.
    monkeypatch.setattr(cli.specloop_attention.specloop_client, "call",
                        lambda *args, **kwargs: {"version": 1, "runs": []})
    seed_history(active=40)
    assert cli.main(["attention", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["tasks"]) == 40
    assert len(payload["stale"]) == 8
    assert all("deferred" in i["error"] for i in payload["stale"])


@pytest.mark.scenario("worker-retirement/historical-evidence-remains-available/historical-attention-remains-readable")
@pytest.mark.scenario("worker-retirement/client-command-boundaries-remain-stable/existing-clients-use-surviving-commands")
@pytest.mark.parametrize("failure", ["github", "specloop", "both"])
def test_attention_refresh_stale_offline_and_resolution(tmp_path, monkeypatch, capsys, failure):
    from nightshift import cli
    from nightshift.config import Config

    monkeypatch.setattr(cli.config, "ENV_FILE", tmp_path / "absent-env")
    monkeypatch.setattr(cli.config, "load", lambda path: Config(repos=[]))
    monkeypatch.setattr(outcomes, "HISTORY_INTERVAL", 0)
    outcomes.record("a/b", 1, state="awaiting_merge", reason="historical worker PR",
                    pr_url="https://github.com/a/b/pull/2")
    phase, calls = "healthy", []

    def read_github(args, deadline):
        calls.append("github")
        assert args[:3] in (["issue", "view", "1"], ["pr", "view", "https://github.com/a/b/pull/2"])
        if phase == "outage" and failure in ("github", "both"):
            raise RuntimeError("GitHub offline")
        return github(pr_state="MERGED" if phase == "resolved" else "OPEN")(args)

    def run(args, **kwargs):
        calls.append("specloop")
        assert args == ["specloop", "status", "--json"]
        assert kwargs == {"capture_output": True, "text": True, "check": False,
                          "timeout": outcomes.REQUEST_SECONDS}
        if phase == "outage" and failure in ("specloop", "both"):
            raise OSError("specloop offline")
        runs = [] if phase == "resolved" else [{"change": "historical-pause", "status": "paused",
                "pr": None, "pause": {"reason": "stuck"}}]
        return subprocess.CompletedProcess(args, 0, json.dumps({"version": 1, "runs": runs}), "")

    monkeypatch.setattr(outcomes, "_read_github", read_github)
    monkeypatch.setattr(subprocess, "run", run)

    def attention(offline=False, code=0):
        assert cli.main(["attention", "--json", *(["--offline"] if offline else [])]) == code
        output = capsys.readouterr()
        assert output.err == ""
        payload = json.loads(output.out)
        assert set(payload) == {"version", "offline", "tasks", "specloop", "specloop_error", "stale"}
        assert payload["version"] == 1 and payload["offline"] is offline
        return payload

    healthy = attention()
    assert healthy["tasks"][0]["state"] == "awaiting_merge"
    assert healthy["tasks"][0]["reason"] == "historical worker PR"
    assert healthy["specloop"][0]["reason"] == "stuck"
    phase = "outage"
    stale = attention(code=1)
    assert bool(stale["stale"]) == (failure in ("github", "both"))
    assert bool(stale["specloop_error"]) == (failure in ("specloop", "both"))
    for key, broken in (("tasks", failure != "specloop"), ("specloop", failure != "github")):
        assert len(stale[key]) == 1
        assert bool(stale[key][0]["error"]) is broken
        if broken:
            assert stale[key][0]["checked_at"] == healthy[key][0]["checked_at"]
    before = list(calls)
    assert attention(offline=True, code=1) == {**stale, "offline": True}
    assert cli.main(["attention", "--offline"]) == 1
    rendered = capsys.readouterr().out
    assert "STALE" in rendered and "historical-pause" in rendered and "a/b#1" in rendered
    assert calls == before
    phase = "resolved"
    resolved = attention()
    assert resolved == {"version": 1, "offline": False, "tasks": [], "specloop": [],
                        "specloop_error": None, "stale": []}
    before = list(calls)
    assert attention(offline=True) == {**resolved, "offline": True}
    assert calls == before and outcomes.tasks()[0]["state"] == "resolved"
    with outcomes.database() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
