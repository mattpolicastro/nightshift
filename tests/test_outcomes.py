import json
from concurrent.futures import ThreadPoolExecutor

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


def test_cli_json_and_offline_do_not_write_github(monkeypatch, capsys):
    from nightshift import cli
    from nightshift.config import Config
    monkeypatch.setattr(cli.config, "load_env_file", lambda: None)
    monkeypatch.setattr(cli.config, "load", lambda path: Config(repos=[]))
    outcomes.record("a/b", 1, state="needs_decision", reason="needs review")
    def forbidden(args):
        raise AssertionError("offline must not call GitHub")
    monkeypatch.setattr(queue, "_gh", forbidden)
    assert cli.main(["attention", "--offline", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert payload["offline"] is True
    assert payload["tasks"][0]["issue_url"] == "https://github.com/a/b/issues/1"


def test_daemon_crash_before_worker_and_retries_keep_history(tmp_path, monkeypatch):
    from nightshift import daemon
    from nightshift.config import Config, Repo
    from types import SimpleNamespace
    repo = Repo(name="a/b", verify="true")
    cfg = Config(repos=[repo])
    claimed = daemon.Claimed(repo, tmp_path, SimpleNamespace(number=1, title="task"),
                            SimpleNamespace(worktree=str(tmp_path / "wt"), branch="claude/1"))
    dirs = []
    def crash(*args, **kw):
        dirs.append(kw["transcript_dir"])
        raise RuntimeError("fetch failed before worker")
    monkeypatch.setattr(daemon.task, "run", crash)
    monkeypatch.setattr(daemon.queue, "release", lambda *a, **kw: None)
    monkeypatch.setattr(daemon.vcs, "remove_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(daemon.notify, "send", lambda *a, **kw: None)
    for _ in range(2):
        daemon.run_claimed(cfg, claimed, daemon.Tally())
    assert dirs[0] != dirs[1]
    item = outcomes.tasks()[0]
    assert item["state"] == "needs_decision"
    assert "fetch failed" in item["reason"]
    with outcomes.database() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 4


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
    seed_history(active=40)
    assert cli.main(["attention", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["tasks"]) == 40
    assert len(payload["stale"]) == 8
    assert all("deferred" in i["error"] for i in payload["stale"])
