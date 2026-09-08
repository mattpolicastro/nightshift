"""Experimental 0.153.4 stdio protocol, exercised only with offline fixtures.

The public entrypoint deliberately cannot spawn Codex. Native sandboxing has
not demonstrated provider-credential isolation. `_run_stdio` is an internal
fixture seam, not an operator override or a qualified isolation boundary.
Schemas: `codex app-server generate-json-schema` from codex-cli 0.153.4.
"""
import asyncio
import json
import os
import signal
import time
from collections import deque
from dataclasses import asdict

from .base import CompletedCommand, ReviewerVerdict, WorkerRequest, WorkerResult

PINNED_VERSION = "0.153.4"
QUALIFICATION_BLOCKER = (
    "Native Codex workers are disabled: provider credential isolation, repository "
    "configuration isolation, and role filesystem/network restrictions have not "
    "been qualified. A separate CODEX_HOME is not an isolation boundary."
)
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "blocking": {"type": "array", "items": {"type": "string"}},
        "non_blocking": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "blocking", "non_blocking"],
}


def qualification_status() -> tuple[bool, str]:
    return False, QUALIFICATION_BLOCKER


class CodexWorker:
    def run(self, request: WorkerRequest) -> WorkerResult:
        return WorkerResult(status="unsupported", requested_model=request.model,
                            diagnostics=[QUALIFICATION_BLOCKER])


class _Stop(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def _review(text):
    try:
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {"verdict", "blocking", "non_blocking"}:
            return None
        if value["verdict"] not in ("PASS", "FAIL"):
            return None
        for key in ("blocking", "non_blocking"):
            if not isinstance(value[key], list) or not all(isinstance(x, str) for x in value[key]):
                return None
        if value["verdict"] == "PASS" and value["blocking"]:
            return None
        return ReviewerVerdict(value["verdict"], tuple(value["blocking"]), tuple(value["non_blocking"]))
    except (ValueError, TypeError):
        return None


class _Journal:
    """Exclusive, private, bounded JSONL. Paths must be unique to this attempt.

    Files live wherever the harness explicitly selects; no directory is created
    and no existing file is overwritten. Native contents can be sensitive and
    must never be uploaded as public validation evidence.
    """
    def __init__(self, path, limit):
        self.fd = None
        self.size = 0
        self.sequence = 0
        self.limit = limit
        if path is not None:
            self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                              getattr(os, "O_NOFOLLOW", 0), 0o600)

    def write(self, kind, data):
        if self.fd is None:
            return
        self.sequence += 1
        record = {"sequence": self.sequence, "kind": kind, "data": data}
        encoded = (json.dumps(record, ensure_ascii=True) + "\n").encode()
        self.size += len(encoded)
        if self.size > self.limit:
            raise _Stop("budget_exhausted", "Private transcript byte budget exhausted")
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(self.fd, view)
                if not written:
                    raise OSError("No journal write progress")
                view = view[written:]
        except OSError:
            raise _Stop("protocol_error", "Unable to write private transcript")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class _Session:
    def __init__(self, request, process, native, normalized):
        self.request, self.process = request, process
        self.native, self.normalized = native, normalized
        self.result = WorkerResult(requested_model=request.model)
        self.pending = deque()
        self.sequence = 0
        self.stream_bytes = 0
        self.started = {}
        self.item_types = {}
        self.completed = {}
        self.actions = set()
        self.texts = {}

    async def send(self, message):
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await self.process.stdin.drain()

    async def receive(self):
        try:
            line = await self.process.stdout.readline()
        except ValueError:
            raise _Stop("budget_exhausted", "Protocol line exceeds byte budget")
        if not line:
            raise _Stop("protocol_error", "EOF before matching turn completion")
        self.stream_bytes += len(line)
        if self.stream_bytes > self.request.budgets.max_stream_bytes:
            raise _Stop("budget_exhausted", "Protocol stream exceeds byte budget")
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            raise _Stop("protocol_error", "Invalid protocol JSON")
        if not isinstance(message, dict):
            raise _Stop("protocol_error", "Invalid protocol envelope")
        method = message.get("method", "")
        sensitive = ("id" in message and "method" in message) or (
            isinstance(method, str) and any(word in method.lower() for word in ("auth", "account", "login")))
        native_record = ({"id": message.get("id"), "method": method, "params": "[redacted]"}
                         if sensitive else message)
        self.native.write("received", native_record)
        if "id" in message and "method" in message:
            # Never grant an authorization, invoke a dynamic tool, or solicit input.
            self.result.denied_actions.append(str(message["method"]))
            self.normalized.write("denied_action", {"method": message["method"]})
            await self.send({"id": message["id"], "error": {
                "code": -32601, "message": "Nightshift denies server-initiated requests"}})
            raise _Stop("needs_input", "Server requested an unauthorized action or user input")
        return message

    async def rpc(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = await self.receive()
            if "id" not in message:
                self.pending.append(message)
                continue
            if type(message["id"]) is not int or message["id"] != request_id:
                raise _Stop("protocol_error", "Unmatched response ID")
            if "error" in message:
                raise _Stop("protocol_error", "App-server RPC failed")
            if not isinstance(message.get("result"), dict):
                raise _Stop("protocol_error", "Missing RPC result")
            return message["result"]

    def item(self, item, completed, timestamp=None):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _Stop("protocol_error", "Malformed item")
        item_id, kind = item["id"], item.get("type")
        if not isinstance(kind, str):
            raise _Stop("protocol_error", "Missing item type")
        previous_kind = self.item_types.setdefault(item_id, kind)
        if previous_kind != kind:
            raise _Stop("protocol_error", "Item changed type during its lifecycle")
        # Count unfamiliar action types conservatively, too. Text is not a tool call.
        if kind not in {"agentMessage", "userMessage", "reasoning", "plan", "contextCompaction"}:
            self.actions.add(item_id)
            if len(self.actions) > self.request.budgets.max_tool_calls:
                raise _Stop("budget_exhausted", "Tool action budget exhausted")
        if not completed:
            if item_id not in self.completed:
                self.started.setdefault(item_id, timestamp)
                self.normalized.write("item_started", {"item_id": item_id, "type": kind, "started_at_ms": timestamp})
            return
        if item_id in self.completed:
            if self.completed[item_id] != item:
                raise _Stop("protocol_error", "Conflicting duplicate completion")
            return
        self.completed[item_id] = item
        started_at = self.started.pop(item_id, None)
        self.normalized.write("item_completed", {"item_id": item_id, "type": kind, "completed_at_ms": timestamp})
        if kind == "commandExecution":
            status, code = item.get("status"), item.get("exitCode")
            if status not in {"completed", "failed", "declined"}:
                raise _Stop("protocol_error", "Command has no terminal status")
            if code is not None and type(code) is not int:
                raise _Stop("protocol_error", "Invalid command exit code")
            if not isinstance(item.get("command"), str) or not isinstance(item.get("cwd"), str):
                raise _Stop("protocol_error", "Command lacks execution context")
            self.result.commands.append(CompletedCommand(
                item_id, item["command"], item["cwd"], code, status,
                started_at, timestamp, item.get("durationMs")))
            self.normalized.write("command_completed", asdict(self.result.commands[-1]))
        elif kind == "agentMessage":
            if not isinstance(item.get("text"), str):
                raise _Stop("protocol_error", "Invalid assistant message")
            if item.get("phase") != "commentary":
                self.texts[item_id] = item["text"]
        elif kind == "fileChange":
            self.result.file_changes.append(item)

    def notification(self, message):
        if "id" in message:
            raise _Stop("protocol_error", "Unexpected RPC response")
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            raise _Stop("protocol_error", "Malformed notification")
        handled = {"item/started", "item/completed", "thread/tokenUsage/updated", "turn/completed"}
        if method not in handled:
            return False
        if params.get("threadId") != self.result.thread_id:
            raise _Stop("protocol_error", "Notification for another thread")
        turn_id = params.get("turn", {}).get("id") if method == "turn/completed" else params.get("turnId")
        if turn_id != self.result.turn_id:
            raise _Stop("protocol_error", "Notification for another turn")
        if method in {"item/started", "item/completed"}:
            self.item(params.get("item"), method == "item/completed",
                      params.get("completedAtMs" if method == "item/completed" else "startedAtMs"))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage", {}).get("total")
            if not isinstance(usage, dict):
                raise _Stop("protocol_error", "Malformed token usage")
            for key in ("inputTokens", "outputTokens", "cachedInputTokens", "reasoningOutputTokens", "totalTokens"):
                if key in usage and (type(usage[key]) is not int or usage[key] < 0):
                    raise _Stop("protocol_error", "Invalid token usage")
            # Cumulative thread totals, never sum repeated snapshots. Fresh thread per run.
            if any(usage.get(k, v) < v for k, v in self.result.usage.items()
                   if type(v) is int and type(usage.get(k, v)) is int):
                raise _Stop("protocol_error", "Usage totals decreased")
            self.result.usage.update(usage)
            self.normalized.write("usage", {"unit": "tokens", "total": self.result.usage})
            if (self.result.output_tokens or 0) > self.request.budgets.max_output_tokens_total:
                raise _Stop("budget_exhausted", "Output token budget exhausted")
        else:
            turn = params["turn"]
            status = turn.get("status")
            if status == "failed":
                info = (turn.get("error") or {}).get("codexErrorInfo")
                mapped = {"unauthorized": "auth_failed", "rateLimitExceeded": "rate_limited",
                          "usageLimitExceeded": "rate_limited", "sessionBudgetExceeded": "budget_exhausted"}
                raise _Stop(mapped.get(info, "failed") if isinstance(info, str) else "failed", "Turn failed")
            if status == "interrupted":
                raise _Stop("interrupted", "Turn interrupted")
            if status != "completed":
                raise _Stop("protocol_error", "Unknown terminal turn status")
            if turn.get("error"):
                raise _Stop("protocol_error", "Successful turn contains an error")
            if self.started:
                raise _Stop("protocol_error", "Turn completed with unfinished items")
            # The terminal snapshot cannot introduce unobserved operations or
            # contradict completion events. Empty snapshots are allowed: native
            # notifications carry the execution evidence, not a claimed summary.
            terminal_items = turn.get("items")
            if not isinstance(terminal_items, list):
                raise _Stop("protocol_error", "Missing terminal item snapshot")
            terminal_ids = set()
            for item in terminal_items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise _Stop("protocol_error", "Malformed terminal item")
                item_id = item["id"]
                if item_id in terminal_ids:
                    raise _Stop("protocol_error", "Duplicate item in terminal snapshot")
                terminal_ids.add(item_id)
                if item_id not in self.completed:
                    raise _Stop("protocol_error", "Terminal item has no observed completion")
                self.item(item, completed=True)

            if any(c.exit_code is None and c.status != "declined" for c in self.result.commands):
                raise _Stop("protocol_error", "Completed command lacks exit code")
            self.result.text = "\n".join(self.texts.values())
            if not self.result.text.strip():
                raise _Stop("protocol_error", "Turn completed without final assistant text")
            if self.request.role == "review":
                self.result.reviewer_verdict = _review(self.result.text)
                if self.result.reviewer_verdict is None:
                    raise _Stop("protocol_error", "Missing or malformed structured reviewer verdict")
            self.result.status = "succeeded"
            return True
        return False

    async def execute(self):
        await self.rpc("initialize", {"clientInfo": {"name": "nightshift", "version": "0.1.0"}})
        await self.send({"method": "initialized"})
        thread = await self.rpc("thread/start", {
            "model": self.request.model, "cwd": str(self.request.cwd), "ephemeral": True,
            "approvalPolicy": "never", "approvalsReviewer": "user",
            "sandbox": "read-only" if self.request.role == "review" else "workspace-write",
        })
        self.result.thread_id = thread.get("thread", {}).get("id")
        self.result.observed_model = thread.get("model")
        if not isinstance(self.result.thread_id, str) or not self.result.thread_id:
            raise _Stop("protocol_error", "Missing thread ID")
        if self.result.observed_model != self.request.model:
            raise _Stop("protocol_error", "Thread did not confirm the requested model")
        self.normalized.write("thread_started", {"thread_id": self.result.thread_id, "observed_model": self.result.observed_model})
        params = {"threadId": self.result.thread_id,
                  "input": [{"type": "text", "text": self.request.prompt}]}
        if self.request.reasoning_effort is not None:
            params["effort"] = self.request.reasoning_effort
        if self.request.role == "review":
            params["outputSchema"] = REVIEW_SCHEMA
        turn = await self.rpc("turn/start", params)
        self.result.turn_id = turn.get("turn", {}).get("id")
        if not isinstance(self.result.turn_id, str) or not self.result.turn_id:
            raise _Stop("protocol_error", "Missing turn ID")
        self.normalized.write("turn_started", {"thread_id": self.result.thread_id, "turn_id": self.result.turn_id})
        while True:
            message = self.pending.popleft() if self.pending else await self.receive()
            if self.notification(message):
                return


async def _run_stdio(request: WorkerRequest, argv: list[str], *, env: dict[str, str]) -> WorkerResult:
    """PRIVATE offline fixture seam. No qualification claim or public activation flag.

    Caller supplies an explicit credential-free environment. This function does
    not authenticate or invoke a shell. A process group is owned for this run.
    """
    started = time.monotonic()
    if os.name != "posix":
        return WorkerResult(status="unsupported", diagnostics=["Fixture transport requires POSIX process groups"])
    journals = []
    try:
        for path in (request.transcript_path, request.normalized_transcript_path):
            journals.append(_Journal(path, request.budgets.max_stream_bytes))
    except OSError:
        for journal in journals:
            journal.close()
        return WorkerResult(status="protocol_error", requested_model=request.model,
                            diagnostics=["Unable to create exclusive private transcript"])
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=request.cwd, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=request.budgets.max_line_bytes,
        )
    except (OSError, ValueError):
        for journal in journals:
            journal.close()
        return WorkerResult(status="protocol_error", requested_model=request.model,
                            duration_s=time.monotonic() - started,
                            diagnostics=["Unable to start fixture app-server process"])
    session = _Session(request, process, *journals)
    stderr = bytearray()

    async def drain_stderr():
        while chunk := await process.stderr.read(4096):
            # Separate bounded tail: diagnostics cannot forge protocol messages.
            stderr.extend(chunk)
            del stderr[:-16384]

    stderr_task = asyncio.create_task(drain_stderr())
    try:
        async with asyncio.timeout(request.budgets.max_runtime_s):
            await session.execute()
            # A live app-server normally stays open after a turn. Give an already
            # exiting child a scheduling opportunity before intentional shutdown;
            # an observed crash cannot become successful verification evidence.
            await asyncio.sleep(0.02)
            if process.returncode not in (None, 0):
                raise _Stop("protocol_error", "App-server exited unsuccessfully after terminal notification")
    except TimeoutError:
        session.result.status = "budget_exhausted"
        session.result.diagnostics.append("Elapsed runtime budget exhausted")
    except asyncio.CancelledError:
        session.result.status = "interrupted"
        session.result.diagnostics.append("Worker cancelled")
    except _Stop as exc:
        session.result.status = exc.status
        session.result.diagnostics.append(exc.reason)
    except (OSError, TypeError, ValueError, AttributeError, KeyError):
        session.result.status = "protocol_error"
        session.result.diagnostics.append("Invalid or disconnected app-server protocol")
    finally:
        if session.result.status != "succeeded" and session.result.turn_id:
            try:
                async with asyncio.timeout(request.budgets.interrupt_grace_s):
                    await session.send({"id": session.sequence + 1, "method": "turn/interrupt", "params": {
                        "threadId": session.result.thread_id, "turnId": session.result.turn_id}})
                    await asyncio.sleep(request.budgets.interrupt_grace_s / 2)
            except (OSError, TimeoutError):
                pass
        # Always terminate the owned group, even if its original leader exited.
        # Descendants remaining in the group are killed. Preventing session/group
        # escape requires the still-unqualified external containment boundary.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                await asyncio.sleep(min(request.budgets.interrupt_grace_s, 0.1))
        await process.wait()
        stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)
        session.result.stderr = stderr.decode("utf-8", errors="replace")
        session.result.duration_s = time.monotonic() - started
        try:
            # Diagnostics/stderr/native text remain private; this journal provides
            # normalized execution evidence without copied tool output or secrets.
            journals[1].write("result", {
                "status": session.result.status, "thread_id": session.result.thread_id,
                "turn_id": session.result.turn_id, "duration_s": session.result.duration_s,
                "runtime": session.result.runtime, "runtime_version": session.result.runtime_version,
                "usage": session.result.usage,
            })
        except _Stop as exc:
            session.result.status = exc.status
            session.result.diagnostics.append(exc.reason)
        finally:
            for journal in journals:
                journal.close()
    return session.result
