"""Private landing admission and read-only evidence, without live services."""

from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess

import pytest

from nightshift import factory_land as landing


IDENTITY = landing.Offer("reviewed-change", "https://github.com/Example/Repo/pull/7",
                         "human_checks:", "a" * 40, 0)


def status(offer=IDENTITY):
    return {"version": 1, "runs": [{
        "change": offer.change, "pr": offer.pr, "head": offer.head, "status": "paused",
        "pause": {"reason": "human_checks", "since": offer.since},
        "action": {"pause_id": offer.pause_id, "actions": ["finish", "abort"]},
    }]}


@pytest.fixture
def admission(tmp_path):
    return dict(root=tmp_path, pause_file=tmp_path / "pause", now=lambda: 100,
                read_status=lambda change: status(), remote_head=lambda pr: IDENTITY.head)


def directory(root, attempt_id):
    return landing.attempt_directory(root, IDENTITY, attempt_id)


def retained(root, number, *, created_at=None):
    attempt_id = f"{number:032x}"
    path = directory(root, attempt_id)
    landing.write_record(path / "request.json", {
        "version": 1, "attempt_id": attempt_id, "offer": asdict(IDENTITY),
        "checks_done": True, "created_at": number if created_at is None else created_at,
    })
    return path


def process(path):
    landing.write_record(path / "process.json", {
        "version": 1, "pid": 1234, "pgid": 1234, "sid": 1234,
        "start_identity": "Mon Oct 26 12:00:00 2026", "started_at": 0,
    })


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("invalid_field", [
    {"attempt_id": "2" * 32}, {"checks_done": False},
])
def test_load_attempt_rejects_each_invalid_request_field_independently(tmp_path, invalid_field):
    path = retained(tmp_path, 1)
    assert landing.load_attempt(path.name, tmp_path) == (path, IDENTITY)
    request_path = path / "request.json"
    request = json.loads(request_path.read_text())
    # Keep the other fields valid so either identity or confirmation alone must refuse.
    landing.write_record(request_path, {**request, **invalid_field})
    with pytest.raises(landing.Refusal, match="invalid retained landing request"):
        landing.load_attempt(path.name, tmp_path)


@pytest.mark.parametrize("limit", [0, 21, True, 1.0, "1", None])
def test_status_rejects_out_of_range_and_non_integer_limits(tmp_path, limit):
    # Exercise the API directly: argparse independently rejects invalid CLI limits.
    with pytest.raises(ValueError, match="limit must be between 1 and 20"):
        landing.landing_status(root=tmp_path, limit=limit)


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/only-verifiable-human-checks-qualify")
@pytest.mark.parametrize("since", [0, 0.125, 1800000000.75])
def test_complete_offer_preserves_timestamp_and_status(since):
    offer = replace(IDENTITY, since=since)
    document = status(offer)
    before = deepcopy(document)
    assert landing.offer_from_status(document, offer.change) == offer
    assert document == before


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/only-verifiable-human-checks-qualify")
@pytest.mark.parametrize("mutation", [
    lambda doc: doc.update(version=True),
    lambda doc: doc.update(version=2),
    lambda doc: doc.update(error="status failed"),
    lambda doc: doc.update(runs=[]),
    lambda doc: doc["runs"].append(deepcopy(doc["runs"][0])),
    *[lambda doc, state=state: doc["runs"][0].update(status=state)
      for state in ("running", "done", "aborted", "error")],
    lambda doc: doc["runs"][0].update(error="broken run"),
    lambda doc: doc["runs"][0]["pause"].update(reason="requested"),
    lambda doc: doc["runs"][0].pop("action"),
    lambda doc: doc["runs"][0].pop("head"),
    lambda doc: doc["runs"][0]["pause"].pop("since"),
    *[lambda doc, value=value: doc["runs"][0]["pause"].update(since=value)
      for value in (None, True, False, "0", [], {}, float("nan"), float("inf"), 10**400)],
])
def test_ineligible_or_incomplete_status_is_only_a_landing_diagnostic(tmp_path, mutation):
    document = status()
    mutation(document)
    before = json.dumps(document)
    result = landing.landing_status(IDENTITY.change, root=tmp_path, read_status=lambda _: document)
    assert result["offer"] is None
    assert result["diagnostics"]
    assert result["attempts"] == []
    assert json.dumps(document) == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/republication-invalidates-confirmation")
@pytest.mark.parametrize("replacement", [
    {"head": "b" * 40}, {"since": 0.25}, {"pause_id": "human_checks:republished"},
    {"pr": "https://github.com/Example/Repo/pull/8"}, {"change": "another-run"},
])
def test_fresh_admission_and_independent_startup_validation(admission, replacement):
    launched = []
    accepted = landing.start_attempt(IDENTITY, launch=launched.append, **admission)
    assert accepted["accepted"] and launched == [accepted["attempt_id"]]
    revised = replace(IDENTITY, **replacement)
    admission.update(read_status=lambda _: status(revised), remote_head=lambda _: revised.head)
    refused = landing.start_attempt(IDENTITY, launch=launched.append, **admission)
    assert not refused["accepted"]
    assert refused["attempt_id"] != accepted["attempt_id"]
    assert len(launched) == 1
    # Worker startup must call this same fresh check independently, after its
    # own lifetime lock. Admission success does not cache permission to act.
    with pytest.raises(landing.Refusal):
        landing.validate_offer(IDENTITY, **{k: admission[k] for k in ("read_status", "remote_head", "pause_file")})
    result = json.loads((directory(admission["root"], refused["attempt_id"]) / "result.json").read_text())
    assert result["outcome"] == "refused"
    assert result["reason"]


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/pause-blocks-new-execution")
@pytest.mark.parametrize("failure", ["paused", "status", "remote", "mismatch"])
def test_pause_and_unreadable_fresh_identity_refuse_without_launch(admission, failure):
    def unavailable(_):
        raise OSError("identity unavailable")

    if failure == "paused":
        admission["pause_file"].touch()
        admission["read_status"] = lambda _: pytest.fail("pause must refuse before reads")
    elif failure == "mismatch":
        admission["remote_head"] = lambda _: "b" * 40
    else:
        admission["read_status" if failure == "status" else "remote_head"] = unavailable
    result = landing.start_attempt(IDENTITY, launch=lambda _: pytest.fail("refusal launched"), **admission)
    assert result["accepted"] is False
    with pytest.raises(landing.Refusal):
        landing.validate_offer(IDENTITY, **{k: admission[k] for k in ("read_status", "remote_head", "pause_file")})
    before = snapshot(admission["root"])
    view = landing.landing_status(root=admission["root"], now=5000)
    assert view["attempts"][0]["outcome"] == "refused"
    assert view["attempts"][0]["reason"] == result["reason"]
    assert snapshot(admission["root"]) == before


def test_request_is_durable_before_launch_and_spawn_failure_is_retained(admission, monkeypatch):
    synced = []
    fsync = landing.os.fsync

    def sync(fd):
        synced.append(fd)
        fsync(fd)

    monkeypatch.setattr(landing.os, "fsync", sync)

    def launch(attempt_id):
        request = json.loads((directory(admission["root"], attempt_id) / "request.json").read_text())
        assert request["offer"] == asdict(IDENTITY)
        assert request["checks_done"] is True
        assert len(synced) >= 4  # request and progress: file + containing directory
        raise OSError("spawn denied")

    result = landing.start_attempt(IDENTITY, launch=launch, **admission)
    view = landing.landing_status(root=admission["root"], now=100)
    assert not result["accepted"]
    assert view["attempts"][0]["outcome"] == "failed"
    assert "spawn denied" in view["attempts"][0]["reason"]


def argv(offer=IDENTITY):
    return ["start", offer.change, "--pr", offer.pr, "--pause-id", offer.pause_id,
            "--head", offer.head, "--since", str(offer.since), "--checks-done", "--json"]


@pytest.mark.parametrize("field,value", [
    ("change", "../bad"), ("pr", "https://github.com/a/b/pull/01"),
    ("pr", "https://user@github.com/a/b/pull/1"), ("pr", "https://github.com/a/b/pull/1?q=x"),
    ("pr", "https://github.com/a/b/pull/1#fragment"), ("pr", "https://github.com/a/b/pull/1/"),
    ("head", "abc"), ("pause_id", "requested:"), ("pause_id", "human_checks:\n"),
    ("since", "true"), ("since", "NaN"), ("since", "Infinity"), ("since", "1e999"),
])
def test_malformed_start_creates_no_evidence(admission, field, value):
    with pytest.raises(SystemExit) as error:
        landing.main(argv(replace(IDENTITY, **{field: value})),
                     launch=lambda _: pytest.fail("malformed input launched"), **admission)
    assert error.value.code == 2
    assert not list(admission["root"].iterdir())


def test_private_parser_requires_confirmation_and_bounds_status(admission, capsys):
    for args in ([a for a in argv() if a != "--checks-done"],
                 ["status", "--limit", "0", "--json"], ["status", "--limit", "21", "--json"],
                 ["status", "../bad", "--json"], ["status", "--json", "--unknown"]):
        with pytest.raises(SystemExit) as error:
            landing.main(args, launch=lambda _: pytest.fail("syntax error launched"), **admission)
        assert error.value.code == 2
    assert landing.main(argv(), launch=lambda _: None, **admission) == 0
    assert json.loads(capsys.readouterr().out)["accepted"] is True
    assert landing.main(["status", "--json"], root=admission["root"], now=101) == 0
    assert len(json.loads(capsys.readouterr().out)["attempts"]) == 1


@pytest.mark.scenario("factory-landing-jobs/bounded-retained-status/long-history-retains-current-progress")
@pytest.mark.parametrize("diagnostic", ["\x00" * 30000, "😀" * 20000])
def test_history_bound_keeps_older_current_and_bounds_escaped_output(tmp_path, monkeypatch, diagnostic):
    current = retained(tmp_path, 1)
    process(current)
    landing.record_progress(current, "checking CI", reason="waiting for checks", now=2401)
    # JSON escapes consume six bytes for controls and twelve for astral Unicode.
    diagnostic += '"\\'
    for number in range(2, 28):
        path = retained(tmp_path, number)
        (path / "result.json").write_text(json.dumps({
            "version": 1, "outcome": "refused", "phase": "validating",
            "finished_at": number, "reason": diagnostic, "exit_code": 1,
        }))
        (path / "output.log").write_bytes(b"full retained output\x00\xff")
    before = snapshot(tmp_path)
    opened = []
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.name != "output.log", "status must never read a full log"
        if path.name == "result.json":
            opened.append(int(path.parent.name, 16))
            assert int(path.parent.name, 16) >= 8 or path.parent == current
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        global_view = landing.landing_status(root=tmp_path, now=2500, is_alive=lambda _: True)
        named = landing.landing_status(IDENTITY.change, root=tmp_path, now=2500,
                                       read_status=lambda _: status(), is_alive=lambda _: True)
    assert global_view["total"] == named["total"] == 27
    assert [int(row["attempt_id"], 16) for row in named["attempts"]] == list(range(27, 7, -1))
    assert named["current"]["attempt_id"] == current.name
    assert named["current"]["phase"] == "checking CI"
    assert named["current"]["reason"] == "waiting for checks"
    assert named["current"]["overdue"] is True
    for view in (global_view, named):
        assert view["truncated"] is True
        assert len(json.dumps(view).encode("utf-8")) < 1024 * 1024 < 2 * 1024 * 1024
        assert all("[truncated]" in row["reason"] for row in view["attempts"])
    assert opened
    assert snapshot(tmp_path) == before
    assert len(landing.landing_status(root=tmp_path, limit=1)["attempts"]) == 1


@pytest.mark.scenario("factory-landing-jobs/published-pause-identity/pause-blocks-new-execution")
def test_pause_does_not_change_current_progress_or_signal_worker(tmp_path, monkeypatch):
    path = retained(tmp_path, 1, created_at=0)
    process(path)
    landing.record_progress(path, "checking CI", reason="waiting", now=20)
    (tmp_path / "pause").touch()
    before = snapshot(tmp_path)
    monkeypatch.setattr(landing.os, "killpg", lambda *args: pytest.fail("read must not signal"))
    result = landing.landing_status(IDENTITY.change, root=tmp_path, now=2500,
                                    read_status=lambda _: status(), is_alive=lambda _: True)
    assert result["current"]["phase"] == "checking CI"
    assert result["current"]["outcome"] is None
    assert result["current"]["overdue"] is True
    assert snapshot(tmp_path) == before


@pytest.mark.scenario("factory-landing-jobs/bounded-retained-status/long-history-retains-current-progress")
@pytest.mark.scenario("factory-landing-jobs/bounded-retained-status/progress-and-refusal-survive-reads")
@pytest.mark.parametrize("damage", [
    "{", "[]", '{"version": true}', "x" * (landing.RECORD_LIMIT + 1),
    {"outcome": "unknown"}, {"phase": "unknown"}, {"finished_at": True},
    {"reason": []}, {"exit_code": False},
], ids=["invalid-json", "non-object", "invalid-version", "oversized",
        "invalid-outcome", "invalid-phase", "invalid-time", "invalid-reason", "invalid-exit"])
def test_damaged_result_cannot_hide_older_live_progress(tmp_path, monkeypatch, damage):
    current = retained(tmp_path, 1)
    process(current)
    landing.record_progress(current, "checking CI", reason="waiting for checks", now=2401)
    landing.record_result(current, "succeeded", "unverified result", now=2402)
    if isinstance(damage, dict):
        result = json.loads((current / "result.json").read_text())
        damage = json.dumps({**result, **damage})
    (current / "result.json").write_text(damage)
    (current / "output.log").write_bytes(b"retained full log\x00\xff")
    (current / "drained.json").write_text('{"version":1,"drained":false}')
    for number in range(2, 23):
        path = retained(tmp_path, number)
        landing.record_result(path, "refused", "lock held", now=number)
    # Even a newer live process with a valid terminal result must not displace
    # the older worker whose result is damaged.
    process(path)
    before = snapshot(tmp_path)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.name != "output.log", "status must never read a full log"
        if path.name == "result.json":
            assert path.parent != directory(tmp_path, f"{2:032x}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(landing.os, "killpg", lambda *args: pytest.fail("read probed/signalled a group"))
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        for _ in range(2):
            view = landing.landing_status(IDENTITY.change, root=tmp_path, now=2500,
                                          read_status=lambda _: status(), is_alive=lambda _: True)
            assert view["total"] == 22
            assert [int(row["attempt_id"], 16) for row in view["attempts"]] == list(range(22, 2, -1))
            assert view["current"]["attempt_id"] == current.name
            assert view["current"]["phase"] == "checking CI"
            assert view["current"]["reason"] == "waiting for checks"
            assert view["current"]["outcome"] is None
            assert view["current"]["overdue"] is True
            assert any("damaged result.json" in item for item in view["current"]["diagnostics"])
            assert len(json.dumps(view).encode("utf-8")) < landing.OUTPUT_LIMIT
    assert snapshot(tmp_path) == before


def test_failed_request_persistence_cannot_launch(admission, monkeypatch):
    def failed_sync(fd):
        raise OSError("disk sync failed")

    monkeypatch.setattr(landing.os, "fsync", failed_sync)
    with pytest.raises(OSError, match="disk sync failed"):
        landing.start_attempt(IDENTITY, launch=lambda _: pytest.fail("unpersisted request launched"), **admission)
    assert not list(admission["root"].rglob("request.json"))


@pytest.mark.scenario("factory-landing-jobs/bounded-retained-status/progress-and-refusal-survive-reads")
def test_progress_terminal_refusal_interruption_and_damage_preserve_all_evidence(tmp_path, monkeypatch):
    paths = {i: retained(tmp_path, i, created_at=0) for i in range(1, 9)}
    for i in (1, 2, 3, 4):
        process(paths[i])
    landing.record_progress(paths[1], "merging", reason="merge request sent", now=10)
    landing.record_result(paths[2], "succeeded", "merge confirmed", phase="merging", exit_code=0, now=20)
    landing.record_result(paths[3], "refused", "duplicate marker matches", now=20)
    landing.record_progress(paths[4], "finishing", reason="finish in progress", now=10)
    (paths[5] / "result.json").write_text("{")
    (paths[6] / "request.json").write_text("[")
    (paths[7] / "process.json").write_text("{")
    (paths[8] / "result.json").write_bytes(b"x" * (landing.RECORD_LIMIT + 100))
    for path in paths.values():
        (path / "output.log").write_bytes(b"all evidence\x00\xff")
        # Display reads must not alter drainage observations or undrained groups.
        (path / "drained.json").write_text('{"version":1,"drained":false}')
    before = snapshot(tmp_path)
    monkeypatch.setattr(landing.os, "killpg", lambda *args: pytest.fail("read signalled/probed a group"))
    for alive in (True, False):
        view = landing.landing_status(root=tmp_path, now=2401, is_alive=lambda _: alive)
        rows = {int(row["attempt_id"], 16): row for row in view["attempts"]}
        assert view["total"] == 8
        assert view["diagnostics"]
        assert rows[1]["phase"] == "merging" and rows[1]["reason"] == "merge request sent"
        assert rows[1]["outcome"] == (None if alive else "failed")
        assert rows[2]["outcome"] == "succeeded" and not rows[2]["overdue"]
        assert rows[3]["outcome"] == "refused" and rows[3]["reason"] == "duplicate marker matches"
        if not alive:
            assert "interrupted" in " ".join(rows[4]["diagnostics"])
        assert "startup not established" in " ".join(rows[5]["diagnostics"])
        assert "damaged" in " ".join(rows[7]["diagnostics"])
        assert "[truncated]" in " ".join(rows[8]["diagnostics"])
        assert snapshot(tmp_path) == before


@pytest.mark.scenario("factory-landing-jobs/bounded-retained-status/progress-and-refusal-survive-reads")
def test_timing_is_display_only_and_progress_result_remain_readable(tmp_path):
    path = retained(tmp_path, 1, created_at=0)

    def read(now, alive=True):
        return landing.landing_status(root=tmp_path, now=now, is_alive=lambda _: alive)["attempts"][0]

    assert read(0)["phase"] == "starting"
    assert read(60)["outcome"] is None
    assert read(60.01)["outcome"] == "failed"
    process(path)
    for phase in ("validating", "recording checks", "finishing", "checking CI", "merging"):
        landing.record_progress(path, phase, now=100)
        assert read(2400)["phase"] == phase
        assert read(2400)["overdue"] is False
        assert read(2400.01)["overdue"] is True
        assert read(5000)["outcome"] is None
    landing.record_result(path, "succeeded", "merge confirmed", phase="merging", now=5001, exit_code=0)
    assert read(9000, alive=False)["outcome"] == "succeeded"
    assert read(9000)["overdue"] is False


def test_real_read_commands_validate_response_and_fail_closed(monkeypatch):
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        data = status() if "nightshift.cli" in args else {"url": IDENTITY.pr, "headRefOid": IDENTITY.head}
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(landing.subprocess, "run", runner)
    assert landing.read_run_status(IDENTITY.change) == status()
    assert landing.read_remote_head(IDENTITY.pr) == IDENTITY.head
    assert all("status" in call or call[:3] == ["gh", "pr", "view"] for call in calls)
    monkeypatch.setattr(landing.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0, "{}", ""))
    with pytest.raises(landing.Refusal):
        landing.read_remote_head(IDENTITY.pr)
