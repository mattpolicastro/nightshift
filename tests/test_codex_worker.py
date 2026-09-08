"""Offline stdio fixtures: never invoke Codex, auth stores, or a model API."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from dataclasses import replace

import pytest

from nightshift.workers.base import WorkerBudgets, WorkerRequest
from nightshift.workers.codex import CodexWorker, _run_stdio, qualification_status

FAKE = r'''
import json, sys, time, os
scenario = sys.argv[1]
def send(value):
    print(json.dumps(value), flush=True)
def event(method, **params):
    send(dict(method=method, params=dict(threadId="thread1", turnId="turn1", **params)))
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send(dict(id=msg["id"] + (1 if scenario == "wrong_id" else 0), result={}))
    elif method == "thread/start":
        assert msg["params"]["approvalPolicy"] == "never"
        assert msg["params"]["ephemeral"] is True
        send(dict(id=msg["id"], result=dict(thread=dict(id="thread1"), model=msg["params"]["model"])))
    elif method == "turn/start":
        send(dict(id=msg["id"], result=dict(turn=dict(id="turn1"))))
        if scenario in ("silent", "cancel"):
            time.sleep(30)
        if scenario == "descendant":
            pid = os.fork()
            if pid == 0:
                time.sleep(30)
                sys.exit()
            open("child.pid", "w").write(str(pid))
            time.sleep(30)
        if scenario == "large_line":
            print("x" * 10000, flush=True)
            time.sleep(30)
        if scenario == "bad_json":
            print("not json", flush=True)
            time.sleep(30)
        if scenario == "approval":
            send(dict(id="server1", method="item/commandExecution/requestApproval", params={}))
            continue
        if scenario == "auth_request":
            send(dict(id="server1", method="account/chatgptAuthTokens/refresh", params=dict(secret="synthetic-secret")))
            continue
        if scenario == "unknown_request":
            send(dict(id="server1", method="future/grantEverything", params={}))
            continue
        if scenario == "stderr":
            print(json.dumps(dict(method="turn/completed", params={})), file=sys.stderr, flush=True)
            sys.exit(0)
        if scenario.startswith("failed:"):
            send(dict(method="turn/completed", params=dict(threadId="thread1", turn=dict(id="turn1", status="failed", error=dict(codexErrorInfo=scenario.split(":")[1])))))
            continue
        cmd = dict(type="commandExecution", id="cmd1", command="python -m pytest", cwd=os.getcwd(), status="completed", exitCode=0, durationMs=5)
        event("item/started", item={**cmd, "status":"inProgress"}, startedAtMs=10)
        if scenario != "unfinished":
            if scenario == "missing_exit":
                del cmd["exitCode"]
            if scenario == "changed_item_type":
                cmd = dict(id="cmd1", type="plan")
            event("item/completed", item=cmd, completedAtMs=15)
            event("item/completed", item=cmd, completedAtMs=15)
        if scenario == "tools":
            event("item/started", item=dict(id="cmd2", type="commandExecution"))
        usage = dict(inputTokens=10, outputTokens=7, cachedInputTokens=0, reasoningOutputTokens=0, totalTokens=17)
        event("thread/tokenUsage/updated", tokenUsage=dict(total=usage))
        event("thread/tokenUsage/updated", tokenUsage=dict(total=usage))
        if scenario == "review":
            assert "outputSchema" in msg["params"]
            text = json.dumps(dict(verdict="PASS", blocking=[], non_blocking=["One note"]))
        elif scenario == "contradictory_review":
            text = json.dumps(dict(verdict="PASS", blocking=["Unsafe"], non_blocking=[]))
        else:
            text = "Implemented and tested."
        event("item/completed", item=dict(id="answer1", type="agentMessage", phase="final_answer", text=text))
        if scenario == "eof":
            sys.exit(0)
        terminal_items = []
        if scenario == "terminal_unfinished":
            terminal_items = [dict(id="hidden", type="commandExecution", status="inProgress")]
        if scenario == "terminal_unobserved":
            terminal_items = [{**cmd, "id": "hidden"}]
        if scenario == "terminal_conflicting":
            terminal_items = [{**cmd, "exitCode": 1}]
        if scenario == "terminal_matching":
            terminal_items = [cmd]
        if scenario == "terminal_duplicate":
            terminal_items = [cmd, cmd]
        send(dict(method="turn/completed", params=dict(threadId="other" if scenario == "wrong_thread" else "thread1", turn=dict(id="turn1", status="invented" if scenario == "unknown_status" else "completed", items=terminal_items))))
        if scenario == "crash_after_terminal":
            sys.exit(9)
'''


def request(tmp_path, **budget):
    return WorkerRequest("implement", tmp_path, "Task", "explicit-model", WorkerBudgets(
        max_runtime_s=budget.pop("max_runtime_s", 3), interrupt_grace_s=0.02, **budget))


def invoke(tmp_path, scenario="success", req=None):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    return asyncio.run(_run_stdio(req or request(tmp_path), [sys.executable, str(fake), scenario], env={}))


def test_real_execution_is_unconditionally_disabled(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Must not launch an unqualified runtime")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    result = CodexWorker().run(request(tmp_path))
    assert result.status == "unsupported"
    assert qualification_status()[0] is False
    assert "credential isolation" in result.diagnostics[0]


def test_success_requires_terminal_and_preserves_native_evidence(tmp_path):
    result = invoke(tmp_path)
    assert result.ok
    assert result.thread_id == "thread1" and result.turn_id == "turn1"
    assert result.observed_model == "explicit-model"
    assert result.runtime_version is None  # A fixture isn't proof of a runtime version.
    assert result.output_tokens == 7
    assert len(result.commands) == 1
    assert result.commands[0].exit_code == 0
    assert result.commands[0].started_at_ms == 10
    assert result.commands[0].completed_at_ms == 15
    assert result.duration_s > 0


@pytest.mark.parametrize("scenario", ["eof", "stderr", "wrong_id", "wrong_thread", "unfinished", "missing_exit", "bad_json", "unknown_status"])
def test_fail_closed_protocol(tmp_path, scenario):
    result = invoke(tmp_path, scenario)
    assert result.status == "protocol_error"
    assert not result.ok
    if scenario == "stderr":
        assert "turn/completed" in result.stderr


@pytest.mark.parametrize("scenario", ["approval", "unknown_request"])
def test_never_grants_server_requests(tmp_path, scenario):
    result = invoke(tmp_path, scenario)
    assert result.status == "needs_input"
    assert len(result.denied_actions) == 1


@pytest.mark.parametrize(("scenario", "budget"), [
    ("silent", {"max_runtime_s": 0.2}),
    ("large_line", {"max_line_bytes": 1024}),
    ("tools", {"max_tool_calls": 1}),
    ("success", {"max_output_tokens_total": 6}),
    ("success", {"max_stream_bytes": 400}),
])
def test_budgets_are_bounded(tmp_path, scenario, budget):
    started = time.monotonic()
    result = invoke(tmp_path, scenario, request(tmp_path, **budget))
    assert result.status == "budget_exhausted"
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(("info", "status"), [
    ("unauthorized", "auth_failed"), ("rateLimitExceeded", "rate_limited"),
    ("usageLimitExceeded", "rate_limited"), ("sessionBudgetExceeded", "budget_exhausted"),
    ("other", "failed"),
])
def test_native_failure_mapping(tmp_path, info, status):
    assert invoke(tmp_path, "failed:" + info).status == status


def test_structured_review(tmp_path):
    req = WorkerRequest("review", tmp_path, "Review", "explicit-model")
    result = invoke(tmp_path, "review", req)
    assert result.ok and result.reviewer_verdict.verdict == "PASS"
    assert result.reviewer_verdict.non_blocking == ("One note",)
    assert invoke(tmp_path, "success", req).status == "protocol_error"
    assert invoke(tmp_path, "contradictory_review", req).status == "protocol_error"


def test_cancellation_interrupts_silent_process(tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    async def run():
        task = asyncio.create_task(_run_stdio(request(tmp_path), [sys.executable, str(fake), "cancel"], env={}))
        await asyncio.sleep(0.15)
        task.cancel()
        return await asyncio.wait_for(task, 1)
    assert asyncio.run(run()).status == "interrupted"


def test_timeout_terminates_descendants(tmp_path):
    result = invoke(tmp_path, "descendant", request(tmp_path, max_runtime_s=0.3))
    assert result.status == "budget_exhausted"
    pid = int((tmp_path / "child.pid").read_text())
    # Dead children can briefly remain zombies awaiting platform init reaping.
    import subprocess
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith("Z")


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_budget_rejected(value):
    with pytest.raises(ValueError):
        WorkerBudgets(max_runtime_s=value)


def test_spawn_failure_is_a_typed_result(tmp_path):
    result = asyncio.run(_run_stdio(request(tmp_path), [str(tmp_path / "missing")], env={}))
    assert result.status == "protocol_error"
    assert result.requested_model == "explicit-model"


def test_nonzero_exit_after_terminal_is_not_success(tmp_path):
    assert invoke(tmp_path, "crash_after_terminal").status == "protocol_error"


def test_private_journals_are_incremental_deduplicated_and_exclusive(tmp_path):
    native, normalized = tmp_path / "native.jsonl", tmp_path / "normalized.jsonl"
    req = replace(request(tmp_path), transcript_path=native, normalized_transcript_path=normalized)
    assert invoke(tmp_path, req=req).ok
    assert native.stat().st_mode & 0o777 == 0o600
    assert normalized.stat().st_mode & 0o777 == 0o600
    events = [json.loads(line) for line in normalized.read_text().splitlines()]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert sum(e["kind"] == "command_completed" for e in events) == 1
    assert events[-1]["kind"] == "result"
    assert events[-1]["data"]["status"] == "succeeded"
    previous = native.read_bytes()
    assert invoke(tmp_path, req=req).status == "protocol_error"
    assert native.read_bytes() == previous


def test_native_auth_request_payload_is_redacted(tmp_path):
    native = tmp_path / "native.jsonl"
    req = replace(request(tmp_path), transcript_path=native)
    assert invoke(tmp_path, "auth_request", req).status == "needs_input"
    assert "synthetic-secret" not in native.read_text()
    assert "[redacted]" in native.read_text()


def test_journal_write_failure_fails_and_cleans_up(tmp_path, monkeypatch):
    from nightshift.workers import codex
    original = codex._Journal.write
    def fail(self, kind, data):
        if kind == "item_started":
            raise codex._Stop("protocol_error", "Synthetic disk failure")
        original(self, kind, data)
    monkeypatch.setattr(codex._Journal, "write", fail)
    req = replace(request(tmp_path), normalized_transcript_path=tmp_path / "normalized.jsonl")
    result = invoke(tmp_path, req=req)
    assert result.status == "protocol_error"
    assert result.duration_s < 1
    assert "Synthetic disk failure" in result.diagnostics


def test_journal_symlink_cannot_overwrite_target(tmp_path):
    target = tmp_path / "existing"
    target.write_text("preserve")
    journal = tmp_path / "journal"
    journal.symlink_to(target)
    req = replace(request(tmp_path), transcript_path=journal)
    assert invoke(tmp_path, req=req).status == "protocol_error"
    assert target.read_text() == "preserve"


def test_progress_is_visible_before_turn_finishes(tmp_path):
    # initialize/thread responses are recorded while a silent turn remains live.
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    journal = tmp_path / "native.jsonl"
    req = replace(request(tmp_path), transcript_path=journal)
    async def run():
        task = asyncio.create_task(_run_stdio(req, [sys.executable, str(fake), "silent"], env={}))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if journal.exists() and len(journal.read_text().splitlines()) == 3:
                break
        assert len(journal.read_text().splitlines()) == 3
        assert not task.done()
        task.cancel()
        return await task
    assert asyncio.run(run()).status == "interrupted"


@pytest.mark.parametrize("scenario", ["changed_item_type", "terminal_unfinished",
                                      "terminal_unobserved", "terminal_conflicting",
                                      "terminal_duplicate"])
def test_item_lifecycle_and_terminal_snapshot_cannot_forge_completion(tmp_path, scenario):
    result = invoke(tmp_path, scenario)
    assert result.status == "protocol_error"
    assert not result.ok


def test_matching_terminal_snapshot_preserves_single_command_evidence(tmp_path):
    result = invoke(tmp_path, "terminal_matching")
    assert result.ok
    assert len(result.commands) == 1
    assert result.commands[0].exit_code == 0
