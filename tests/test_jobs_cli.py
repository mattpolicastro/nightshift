"""Operator-facing job commands use local durable state, even without config."""

import fcntl
import os
import re
import shlex
import socket
import subprocess
from contextlib import ExitStack
from pathlib import Path

import pytest

from nightshift import cli, config, daemon, specloop_job as jobs


def forbidden(*args, **kwargs):
    pytest.fail("local jobs command attempted configuration, external work, or signalling")


@pytest.fixture
def local_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(daemon, "STATE_DIR", state)
    monkeypatch.setattr(jobs, "_children", {})
    monkeypatch.setattr(config, "load", forbidden)
    monkeypatch.setattr(config, "load_env_file", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(jobs.specloop_client, "call", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)
    return state


def attempt(root, stage="proposals", number=7, digit="a", command=None):
    directory = root / stage / "owner__sample" / f"{number}.jobs" / (digit * 32)
    directory.mkdir(parents=True)
    (directory / "lock").touch()
    (directory / "log").write_text("preserved evidence\n")
    jobs.save(directory / "request.json", {"args": command or ["propose", "--issue", str(number)]})
    return directory


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None
            for p in root.rglob("*")}


def inventory_rows(output):
    return [line for line in output.splitlines() if re.match(r"[0-9a-f]{32}  ", line)]


@pytest.fixture
def mixed_inventory(local_state, monkeypatch):
    monkeypatch.setattr(jobs.time, "time", lambda: 1000)
    monkeypatch.setattr(jobs, "collect", forbidden)
    monkeypatch.setattr(jobs, "compact_delivered", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    states = ["starting", "running", "overdue", "stopping", "stopped",
              "finished", "delivered", "failed"]
    with ExitStack() as locks:
        for number, state in reversed(list(enumerate(states, 1))):
            directory = attempt(local_state, number=number, digit=str(number))
            if state in ("running", "overdue", "stopping"):
                jobs.save(directory / "process.json", {
                    "version": 1, "job_id": directory.name, "pid": 1234, "pgid": 1234,
                    "start_identity": "persisted identity", "started_at": "2026-10-21T12:00:00Z",
                })
                jobs.save(directory / "request.json", {
                    "args": ["propose", "--issue", str(number)], "stage": "proposals",
                    "started_at": 0, "timeout_seconds": 900 if state == "overdue" else 1200,
                    "deadline_at": 900 if state == "overdue" else 1200,
                })
            if state in ("starting", "running", "overdue", "stopping"):
                fcntl.flock(locks.enter_context((directory / "lock").open()), fcntl.LOCK_EX)
            if state in ("stopping", "stopped"):
                jobs.save(directory / "stop.json", {"state": state})
            if state in ("finished", "delivered", "failed"):
                jobs.save(directory / "result.json", {"error": "wrapper failed"} if state == "failed"
                          else {"result": {"outcome": "drafted"}})
            # Item delivery must not relabel any non-finished execution state.
            jobs.save(directory.parent.with_suffix(".json"), {"delivered": state != "finished"})
        before = snapshot(local_state)
        mtimes = {p: p.stat().st_mtime_ns for p in local_state.rglob("*")}
        yield local_state
        assert snapshot(local_state) == before
        assert {p: p.stat().st_mtime_ns for p in local_state.rglob("*")} == mtimes


@pytest.mark.scenario("background-job-control/local-job-inventory/default-hides-delivered-history")
def test_jobs_default_hides_delivered_history(mixed_inventory, capsys):
    inventory, errors = jobs.list_jobs(mixed_inventory)
    assert errors == [] and len(inventory) == 8
    assert inventory[2].state == "running" and inventory[2].deadline_at < jobs.time.time()
    assert cli.main(["jobs"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert inventory_rows(output.out) == [
        f"{str(number) * 32}  proposals  owner/sample#{number}  {state}"
        for number, state in enumerate([
            "starting", "running", "running", "stopping", "stopped", "finished (delivery pending)"
        ], 1)
    ]
    assert "7" * 32 not in output.out and "8" * 32 not in output.out
    assert output.out.count("Hidden historical jobs: 2 (use --all to show).\n") == 1
    assert output.out.endswith("Hidden historical jobs: 2 (use --all to show).\n")
    assert jobs.list_jobs(mixed_inventory) == (inventory, errors)


@pytest.mark.scenario("background-job-control/local-job-inventory/all-lists-history")
def test_jobs_all_lists_history(mixed_inventory, capsys):
    assert cli.main(["jobs", "--all"]) == 0
    output = capsys.readouterr()
    assert output.err == "" and "Hidden historical jobs:" not in output.out
    assert inventory_rows(output.out) == [
        f"{str(number) * 32}  proposals  owner/sample#{number}  {state}"
        for number, state in enumerate([
            "starting", "running", "running", "stopping", "stopped",
            "finished (delivery pending)", "delivered", "failed"
        ], 1)
    ]
    for job in jobs.list_jobs(mixed_inventory)[0]:
        assert f"  command: propose --issue {job.number}\n" in output.out
        assert f"  log: {job.log_path}\n" in output.out
    assert output.out.count("PID=1234 PGID=1234\n") == 3
    assert output.out.count("PID=? PGID=? (process identity unavailable)\n") == 5


@pytest.mark.scenario("background-job-control/receipt-aware-delivery-display/finished-undelivered-job-reads-pending")
@pytest.mark.parametrize("receipt", [None, {}, {"delivered": False}])
@pytest.mark.parametrize("paused", [False, True])
def test_finished_undelivered_job_reads_pending(local_state, capsys, receipt, paused):
    directory = attempt(local_state)
    jobs.save(directory / "result.json", {"result": {"outcome": "drafted"}})
    jobs.save(directory / "collected.json", {"at": "long ago"})
    jobs.save(directory / "cleanup.json", {"complete": True})
    os.utime(directory / "request.json", (1, 1))
    if receipt is not None:
        jobs.save(directory.parent.with_suffix(".json"), receipt)
    if paused:
        daemon.PAUSE_FILE.touch()
    before = snapshot(local_state)
    for options in ([], ["--all"]):
        assert cli.main(["jobs", *options]) == 0
        output = capsys.readouterr()
        assert inventory_rows(output.out) == [
            f"{directory.name}  proposals  owner/sample#7  finished (delivery pending)"]
        assert "Hidden historical jobs:" not in output.out and output.err == ""
    assert daemon.PAUSE_FILE.exists() == paused
    assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/receipt-aware-delivery-display/delivered-item-history-reads-delivered")
@pytest.mark.scenario("background-job-control/local-job-inventory/only-history-remains")
@pytest.mark.parametrize("identity", [{}, {"job": "b" * 32}])
def test_delivered_item_history_reads_delivered(local_state, capsys, identity):
    for digit in ("a", "b", "c"):
        directory = attempt(local_state, digit=digit)
        jobs.save(directory / "result.json", {"error": "failed"} if digit == "c" else {"result": {}})
    jobs.save(directory.parent.with_suffix(".json"), {"delivered": True, **identity})
    before = snapshot(local_state)
    assert cli.main(["jobs"]) == 0
    output = capsys.readouterr()
    assert output.out == "No active background jobs.\nHidden historical jobs: 3 (use --all to show).\n"
    assert output.err == ""
    assert cli.main(["jobs", "--all"]) == 0
    output = capsys.readouterr()
    assert inventory_rows(output.out) == [
        f"{digit * 32}  proposals  owner/sample#7  {state}"
        for digit, state in [("a", "delivered"), ("b", "delivered"), ("c", "failed")]]
    assert output.err == "" and "Hidden historical jobs:" not in output.out
    # Display delivery cannot compact even the matching attempt's evidence.
    assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/receipt-aware-delivery-display/damaged-receipt-stays-visible")
@pytest.mark.parametrize("damage", ["malformed", "unreadable", "symlink", "array", "null", "number", "string"])
def test_damaged_receipt_stays_visible(local_state, monkeypatch, capsys, damage):
    directory = attempt(local_state)
    jobs.save(directory / "result.json", {"result": {}})
    failed = attempt(local_state, digit="b")
    jobs.save(failed / "result.json", {"error": "wrapper failed"})
    healthy = attempt(local_state, number=8, digit="c")
    jobs.save(healthy / "stop.json", {"state": "stopped"})
    receipt = directory.parent.with_suffix(".json")
    receipt.write_text({
        "malformed": "{", "array": "[]", "null": '{"delivered": null}',
        "number": '{"delivered": 1}', "string": '{"delivered": "true"}',
        "unreadable": '{"delivered": true}', "symlink": '{"delivered": true}',
    }[damage])
    if damage == "symlink":
        target = local_state / "receipt-target.json"
        receipt.rename(target)
        receipt.symlink_to(target)
    open_file = os.open
    def guarded_open(path, flags, *args, **kwargs):
        if path == receipt:
            assert flags & os.O_NOFOLLOW
            if damage == "unreadable":
                raise PermissionError("receipt access denied")
        return open_file(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", guarded_open)
    before = snapshot(local_state)
    mtimes = {p: p.lstat().st_mtime_ns for p in local_state.rglob("*")}
    for options in ([], ["--all"]):
        assert cli.main(["jobs", *options]) == 1
        output = capsys.readouterr()
        assert inventory_rows(output.out) == [
            f"{directory.name}  proposals  owner/sample#7  finished (delivery pending)",
            f"{failed.name}  proposals  owner/sample#7  failed",
            f"{healthy.name}  proposals  owner/sample#8  stopped",
        ]
        assert "Hidden historical jobs:" not in output.out
        assert str(receipt) in output.err
        assert output.err.count("unreadable job:") == 1
    assert snapshot(local_state) == before
    assert {p: p.lstat().st_mtime_ns for p in local_state.rglob("*")} == mtimes
    if damage == "symlink":
        assert receipt.is_symlink() and receipt.resolve() == target


@pytest.mark.scenario("background-job-control/receipt-aware-delivery-display/delivered-item-history-reads-delivered")
def test_delivered_record_with_diagnostics_stays_visible(local_state, capsys):
    directory = attempt(local_state)
    jobs.save(directory / "result.json", {"result": {}})
    jobs.save(directory.parent.with_suffix(".json"), {"delivered": True})
    (directory / "request.json").write_text("{")
    before = snapshot(local_state)
    for options in ([], ["--all"]):
        assert cli.main(["jobs", *options]) == 1
        output = capsys.readouterr()
        assert inventory_rows(output.out) == [f"{directory.name}  proposals  owner/sample#7  unknown"]
        assert "No active background jobs." not in output.out and "Hidden historical jobs:" not in output.out
        assert str(directory) in output.err and "request.json" in output.err
    assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/local-job-inventory/list-jobs-after-daemon-restart")
@pytest.mark.scenario("specloop-documentation/current-limitations/fixed-limitations-leave-the-open-list")
@pytest.mark.parametrize("options", [[], ["--all"]])
def test_jobs_lists_persisted_fields_history_and_numeric_order(local_state, capsys, options):
    directories = []
    with ExitStack() as locks:
        for stage, command, digit, number in [
            ("revisions", "revise", "c", 5), ("proposals", "propose", "b", 10),
            ("admissions", "admit", "a", 9), ("proposals", "propose", "d", 2),
        ]:
            directory = attempt(local_state, stage, number, digit, command=[command, "a change"])
            jobs.save(directory / "process.json", {
                "version": 1, "job_id": directory.name, "pid": 1234 + number,
                "pgid": 1234 + number, "start_identity": "persisted identity",
                "started_at": "2026-10-21T12:00:00+00:00",
            })
            lock = locks.enter_context((directory / "lock").open())
            fcntl.flock(lock, fcntl.LOCK_EX)
            directories.append((directory, stage, command, number))
        historical = attempt(local_state, number=10, digit="0")
        jobs.save(historical / "result.json", {"result": {"outcome": "drafted"}})
        jobs.save(historical.parent.parent / "10.json", {"job": "b" * 32})
        before = snapshot(local_state)
        assert not jobs._children
        assert cli.main(["jobs", *options]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        for directory, stage, command, number in directories:
            assert f"{directory.name}  {stage}  owner/sample#{number}  running" in output.out
            assert f"command: {command} 'a change'" in output.out
            assert f"PID={1234 + number} PGID={1234 + number}" in output.out
            assert f"log: {directory / 'log'}" in output.out
        assert f"{historical.name}  proposals  owner/sample#10  finished" in output.out
        assert "finished (delivery pending)" in output.out
        rows = [line.split()[0] for line in output.out.splitlines() if not line.startswith(" ")]
        assert rows == [digit * 32 for digit in ("a", "d", "0", "b", "c")]
        assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/local-job-inventory/empty-inventory")
@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("options", [[], ["--all"]])
def test_jobs_empty_does_not_create_state(local_state, capsys, exists, options):
    if exists:
        local_state.mkdir()
    assert cli.main(["jobs", *options]) == 0
    output = capsys.readouterr()
    assert output.out == "No background jobs.\n" and output.err == ""
    assert local_state.exists() == exists
    assert snapshot(local_state) == {}


@pytest.mark.scenario("background-job-control/local-job-inventory/legacy-and-unreadable-records")
@pytest.mark.parametrize("broken", ["malformed", "unreadable"])
@pytest.mark.parametrize("options", [[], ["--all"]])
def test_jobs_partial_inventory_reports_errors_and_keeps_legacy(local_state, monkeypatch, capsys, broken, options):
    legacy = attempt(local_state)
    jobs.save(legacy / "result.json", {"result": {"outcome": "drafted"}})
    valid = attempt(local_state, digit="b")
    jobs.save(valid / "stop.json", {"state": "stopped"})
    bad = attempt(local_state, digit="c")
    if broken == "malformed":
        (bad / "request.json").write_text("{broken")
    before = snapshot(local_state)
    read_text = Path.read_text
    with monkeypatch.context() as patch:
        if broken == "unreadable":
            def read(path, *args, **kwargs):
                if path == bad / "request.json":
                    raise PermissionError("permission denied reading request.json")
                return read_text(path, *args, **kwargs)
            patch.setattr(Path, "read_text", read)
        assert cli.main(["jobs", *options]) == 1
    output = capsys.readouterr()
    assert f"{legacy.name}  proposals  owner/sample#7  finished" in output.out
    assert "PID=? PGID=? (process identity unavailable)" in output.out
    assert f"{valid.name}  proposals  owner/sample#7  stopped" in output.out
    assert f"{bad.name}  proposals  owner/sample#7  unknown" in output.out
    assert "command: unavailable" in output.out
    assert "unreadable job:" in output.err and str(bad) in output.err
    assert "request.json" in output.err
    assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/local-job-inventory/only-history-remains")
@pytest.mark.parametrize("options", [[], ["--all"]])
def test_inventory_errors_without_rows_are_not_reported_as_empty(local_state, capsys, options):
    attempt(local_state, digit="z")
    assert cli.main(["jobs", *options]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "unreadable job:" in output.err


@pytest.mark.parametrize("broken_config", [False, True])
@pytest.mark.parametrize("action", ["list", "all", "stop"])
def test_jobs_work_with_missing_or_broken_config_offline(local_state, tmp_path, capsys, broken_config, action):
    path = tmp_path / "config.toml"
    if broken_config:
        path.write_text("[broken TOML")
    directory = attempt(local_state)
    jobs.save(directory / "result.json", {"result": {}})
    args = ["--config", str(path), "jobs"]
    if action == "stop":
        args += ["stop", directory.name]
    elif action == "all":
        args += ["--all"]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert directory.name in output.out and output.err == ""
    assert not (directory / "stop.json").exists()


@pytest.mark.scenario("background-job-control/job-command-syntax/reject-malformed-command")
@pytest.mark.parametrize("args", [
    ["jobs", "unknown"], ["jobs", "stop"], ["jobs", "list"],
    ["jobs", "stop", "a" * 32, "extra"], ["jobs", "unknown", "a" * 32],
    ["jobs", "--unknown"], ["status", "stop", "a" * 32], ["pause", "extra"],
    ["jobs", "release"], ["jobs", "release", "a" * 32, "extra"],
    ["jobs", "compact", "a" * 32], ["jobs", "compact", "--all"],
    ["jobs", "stop", "a" * 32, "--all"], ["jobs", "--all", "release", "a" * 32],
    *[[command, "--all"] for command in (
        "status", "pause", "resume", "run", "reconcile", "preflight", "digest", "attention")],
    ["--all"],
    pytest.param(["jobs", "retry", "a" * 32], marks=pytest.mark.scenario(
        "specloop-documentation/current-limitations/remaining-ledger-gaps-are-explicit")),
])
def test_malformed_jobs_syntax_has_no_side_effects(local_state, monkeypatch, capsys, args):
    attempt(local_state)
    before = snapshot(local_state)
    monkeypatch.setattr(jobs, "list_jobs", forbidden)
    monkeypatch.setattr(jobs, "stop_job", forbidden)
    monkeypatch.setattr(jobs, "release_job", forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "usage: nightshift" in output.err and "error:" in output.err
    assert output.out == ""
    assert snapshot(local_state) == before


@pytest.mark.scenario("background-job-control/job-command-syntax/reject-path-identifier")
@pytest.mark.parametrize("action", ["stop", "release"])
@pytest.mark.parametrize("identifier", ["../outside", "/tmp/outside", "./" + "a" * 32,
                                         "a/../" + "a" * 32, "..", "a" * 8])
def test_path_ids_are_rejected_before_reading_state(local_state, monkeypatch, capsys, identifier, action):
    attempt(local_state)
    before = snapshot(local_state)
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "list_jobs", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        assert cli.main(["jobs", action, identifier]) == 1
    output = capsys.readouterr()
    assert "exact full job ID" in output.err and output.out == ""
    assert snapshot(local_state) == before


@pytest.mark.parametrize("kind, expected, diagnostic", [
    ("missing", 1, "unknown job ID"), ("legacy", 1, "process identity unavailable"),
    ("broken", 1, "unreadable job records"), ("finished", 0, "already finished"),
    ("failed", 0, "already finished"), ("stopped", 0, "stopped"),
])
def test_stop_exit_codes_and_terminal_states(local_state, capsys, kind, expected, diagnostic):
    job_id = "a" * 32
    if kind != "missing":
        directory = attempt(local_state)
        if kind in ("finished", "failed"):
            jobs.save(directory / "result.json", {"result": {}} if kind == "finished" else {"error": "failed"})
        elif kind == "stopped":
            jobs.save(directory / "stop.json", {"state": "stopped", "stopped_at": "earlier"})
        elif kind == "broken":
            (directory / "request.json").write_text("{")
    before = snapshot(local_state)
    assert cli.main(["jobs", "stop", job_id]) == expected
    output = capsys.readouterr()
    assert diagnostic in (output.err if expected else output.out)
    assert job_id in (output.err if expected else output.out)
    # Refusal may create the serialization lock; evidence and state stay intact.
    after = snapshot(local_state)
    assert {p: data for p, data in after.items() if p.name != "control.lock"} == before


def test_stop_operational_failure_is_actionable(local_state, monkeypatch, capsys):
    def fail(state, job_id):
        assert state == local_state and job_id == "a" * 32
        raise jobs.StopJobError("permission denied; stop intent retained; see /job/log")
    monkeypatch.setattr(jobs, "stop_job", fail)
    assert cli.main(["jobs", "stop", "a" * 32]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "permission denied; stop intent retained; see /job/log" in output.err


@pytest.mark.scenario("background-job-control/active-jobs-in-status/status-preserves-compatibility")
@pytest.mark.parametrize("args, expected, fragment", [
    ([], 0, "status sentinel"), (["status"], 0, "status sentinel"),
    (["pause"], 0, "paused"), (["resume"], 0, "resumed"),
    (["run"], 0, "shipped=0"), (["run", "--once"], 0, "shipped=0"),
    (["reconcile"], 0, "clean"), (["preflight", "--deep"], 0, "ready"),
    (["digest"], 2, "not implemented"),
    (["attention", "--offline", "--json"], 0, '"offline": true'),
])
def test_existing_commands_and_options_remain_compatible(tmp_path, monkeypatch, capsys, args, expected, fragment):
    cfg = config.Config(repos=[])
    loaded = []
    config_path = tmp_path / "chosen.toml"
    monkeypatch.setattr(config, "load_env_file", lambda: loaded.append("env"))
    monkeypatch.setattr(config, "load", lambda path: loaded.append(path) or cfg)
    monkeypatch.setattr(cli, "_liveness", lambda cfg: "status sentinel")
    def loop(actual, repos, *, once):
        assert actual is cfg and repos == {} and once == ("--once" in args)
        return daemon.Tally()
    monkeypatch.setattr(daemon, "loop", loop)
    monkeypatch.setattr(daemon, "startup", lambda cfg, repos: [])
    def preflight(actual, env, *, deep):
        assert actual is cfg and deep
        return []
    monkeypatch.setattr(cli.preflight, "run", preflight)
    monkeypatch.setattr(cli.outcomes, "tasks", lambda: [])
    monkeypatch.setattr(cli.outcomes, "refresh", forbidden)
    def attention(*, offline):
        assert offline
        return {"entries": [], "error": None}
    monkeypatch.setattr(cli.specloop_attention, "snapshot", attention)
    inventory_reads = []
    def inventory(root):
        assert args in ([], ["status"])
        inventory_reads.append(root)
        return [], []
    monkeypatch.setattr(jobs, "list_jobs", inventory)
    monkeypatch.setattr(jobs, "stop_job", forbidden)
    monkeypatch.setattr(jobs, "release_job", forbidden)
    if args == ["resume"]:
        daemon.PAUSE_FILE.touch()
    assert cli.main(["--config", str(config_path), *args]) == expected
    output = capsys.readouterr()
    assert fragment in output.out + output.err
    assert loaded == (["env"] if args in (["pause"], ["resume"]) else ["env", config_path])
    assert inventory_reads == ([daemon.STATE_DIR] if args in ([], ["status"]) else [])
    if args in ([], ["status"]):
        assert output.out == "status sentinel\n  alerts: NOT CONFIGURED\n" and output.err == ""
    if args in (["pause"], ["resume"]):
        assert daemon.PAUSE_FILE.exists() == (args == ["pause"])


@pytest.fixture
def status_state(local_state, monkeypatch, capsys):
    cfg = config.Config(repos=[config.Repo("enrolled/repo")])
    monkeypatch.setattr(config, "load_env_file", lambda: None)
    monkeypatch.setattr(config, "load", lambda path: cfg)
    monkeypatch.setattr(cli, "_liveness", lambda cfg: "status sentinel")
    monkeypatch.setattr(cli.time, "time", lambda: 1000)
    monkeypatch.setattr(jobs, "stop_job", forbidden)
    monkeypatch.setattr(jobs, "release_job", forbidden)
    monkeypatch.setattr(jobs, "collect", forbidden)
    claim = cli.queue.Claim("enrolled/repo", 42, "worker-branch", "/wt", "bad clock")
    monkeypatch.setattr(cli.queue, "in_flight", lambda repo: ([claim], []))
    monkeypatch.setattr(cli.queue, "title_of", lambda *args: "Worker title")
    monkeypatch.setattr(cli, "_progress_of", lambda *args: "worker progress")
    monkeypatch.setattr(cli.queue, "contradictions", lambda *args: [])
    reads, before_network = [], []
    list_jobs = jobs.list_jobs
    def inventory(root, *, include_delivery=False):
        reads.append(root)
        return list_jobs(root, include_delivery=include_delivery)
    def ready(*args):
        assert reads == [local_state]
        before_network.append(capsys.readouterr())
        return []
    monkeypatch.setattr(jobs, "list_jobs", inventory)
    monkeypatch.setattr(cli.queue, "ready", ready)
    return local_state, before_network


@pytest.mark.scenario("background-job-control/active-jobs-in-status/status-preserves-compatibility")
@pytest.mark.parametrize("args", [[], ["status"]])
def test_status_empty_inventory_preserves_worker_output(status_state, capsys, args):
    root, before_network = status_state
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert before_network[0].out == "status sentinel\n  alerts: NOT CONFIGURED\n"
    assert before_network[0].err == output.err == ""
    assert output.out == (
        "  enrolled/repo: 1 working, 0 ready, 0 blocked\n"
        "    >> #42 Worker title  (claimed, ? on worker-branch)\n"
        "       worker progress\n"
    )
    assert not root.exists()


@pytest.mark.scenario("background-job-control/active-jobs-in-status/active-attempts-are-visible")
@pytest.mark.scenario("specloop-documentation/active-job-status-guidance/operator-reads-job-status")
@pytest.mark.parametrize("args", [[], ["status"], *[
    shlex.split(re.search(r"`nightshift (status)`", (Path(__file__).resolve().parents[1] / path).read_text())[1])
    for path in ("README.md", "docs/specloop/README.md")
]])
def test_status_active_jobs_before_network(status_state, capsys, args):
    root, before_network = status_state
    with ExitStack() as locks:
        for digit, stage, command, state, start, deadline in [
            ("a", "admissions", "admit", "running", 900, 1200),
            ("b", "proposals", "propose", "starting", None, None),
            ("c", "proposals", "propose", "running", 1100, 4700),
            ("d", "revisions", "revise", "stopping", 0, 1000),
            ("e", "revisions", "revise", "running", 0, 990),
        ]:
            directory = attempt(root, stage, digit=digit, command=[command, "--idea", "SECRET IDEA"])
            if start is not None:
                jobs.save(directory / "request.json", {
                    "stage": stage, "args": [command, "--idea", "SECRET IDEA"],
                    "started_at": start, "timeout_seconds": deadline - start, "deadline_at": deadline,
                })
                jobs.save(directory / "process.json", {
                    "version": 1, "job_id": directory.name, "pid": 1234, "pgid": 1234,
                    "start_identity": "identity", "started_at": "2026-10-21T12:00:00Z",
                })
            if state == "stopping":
                jobs.save(directory / "stop.json", {"state": state})
            fcntl.flock(locks.enter_context((directory / "lock").open()), fcntl.LOCK_EX)
        for digit, record, value in [("f", "result", {"result": {}}),
                                     ("0", "result", {"error": "failed"}),
                                     ("1", "stop", {"state": "stopped"})]:
            directory = attempt(root, digit=digit)
            jobs.save(directory / f"{record}.json", value)
        before = snapshot(root)
        assert cli.main(args) == 0
        output = capsys.readouterr()
        early = before_network[0]
        rows = [line.strip() for line in early.out.splitlines() if "owner/sample#" in line]
        assert rows == [
            f"{'a' * 32}  admissions  owner/sample#7  admit  running  elapsed 100s; 200s remaining",
            f"{'b' * 32}  proposals  owner/sample#7  propose  starting  elapsed unavailable; deadline unavailable",
            f"{'c' * 32}  proposals  owner/sample#7  propose  running  elapsed 0s; 3700s remaining",
            f"{'d' * 32}  revisions  owner/sample#7  revise  stopping  elapsed 1000s; OVERDUE by 0s",
            f"{'e' * 32}  revisions  owner/sample#7  revise  running  elapsed 1000s; OVERDUE by 10s",
        ]
        assert early.err == output.err == ""
        assert "SECRET IDEA" not in early.out + output.out and "--idea" not in early.out + output.out
        assert "enrolled/repo: 1 working, 0 ready, 0 blocked" in output.out
        assert ">> #42 Worker title  (claimed, ? on worker-branch)" in output.out
        assert "worker progress" in output.out
        assert cli.main(["jobs", "--all"]) == 0
        history = capsys.readouterr().out
        for digit, state in [("f", "finished"), ("0", "failed"), ("1", "stopped")]:
            assert digit * 32 not in early.out + output.out
            assert f"{digit * 32}  proposals  owner/sample#7  {state}" in history
        assert cli.main(["jobs"]) == 0
        default = capsys.readouterr().out
        assert "0" * 32 not in default
        for digit, state in [("f", "finished (delivery pending)"), ("1", "stopped")]:
            assert f"{digit * 32}  proposals  owner/sample#7  {state}" in default
        assert default.endswith("Hidden historical jobs: 1 (use --all to show).\n")
        assert snapshot(root) == before


@pytest.mark.scenario("background-job-control/active-jobs-in-status/status-tolerates-damaged-inventory")
@pytest.mark.parametrize("args", [[], ["status"]])
@pytest.mark.parametrize("damage", ["malformed", "unreadable", "timing"])
def test_status_damaged_inventory_is_read_only(status_state, monkeypatch, capsys, args, damage):
    root, before_network = status_state
    healthy = attempt(root)
    bad = attempt(root, digit="b")
    if damage == "malformed":
        (bad / "request.json").write_text("{broken")
    elif damage == "timing":
        jobs.save(bad / "request.json", {"args": ["propose"], "started_at": 100})
    else:
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == bad / "request.json":
                raise PermissionError("permission denied reading request.json")
            return read_text(path, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", read)
    before = snapshot(root)
    mtimes = {p: p.stat().st_mtime_ns for p in root.rglob("*")}
    with (healthy / "lock").open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert cli.main(args) == 0
        output = capsys.readouterr()
        early = before_network[0]
        assert f"{healthy.name}  proposals  owner/sample#7  propose  starting" in early.out
        assert "elapsed unavailable; deadline unavailable" in early.out
        assert "unreadable job:" in early.err and str(bad) in early.err
        assert "request.json" in early.err
        assert early.out.startswith("status sentinel\n  alerts: NOT CONFIGURED\n")
        assert "enrolled/repo: 1 working, 0 ready, 0 blocked" in output.out
        assert "worker progress" in output.out and output.err == ""
        assert cli.main(["jobs"]) == 1
        assert "unreadable job:" in capsys.readouterr().err
    assert snapshot(root) == before
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*")} == mtimes
