"""Opt-in real Codex transport + Docker, with a deterministic LOCAL fake model.

NIGHTSHIFT_TEST_CODEX_EXEC_IMAGE=sha256:... uv run pytest -q tests/test_remote_environment.py

Never logs in or contacts a model provider. Supply the already-built immutable
Linux executor image. Normal CI skips these host qualification tests. Every run
has its own provider HOME, synthetic credentials, and named disposable container.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_IMAGE = os.environ.get("NIGHTSHIFT_TEST_CODEX_EXEC_IMAGE", "")
pytestmark = pytest.mark.skipif(not _IMAGE, reason="explicit pinned executor image required")


def _docker(*args):
    return subprocess.run([shutil.which("docker"), *args], capture_output=True,
                          text=True, check=True, timeout=15).stdout.strip()


def _sse(item, sequence):
    response = {"id": f"response_{sequence}", "status": "completed", "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
    events = [
        ("response.created", {"response": {**response, "status": "in_progress", "output": []}}),
        ("response.output_item.added", {"output_index": 0, "item": item}),
        ("response.output_item.done", {"output_index": 0, "item": item}),
        ("response.completed", {"response": response}),
    ]
    return "".join("event: " + name + "\ndata: " + json.dumps({"type": name, **data}) + "\n\n"
                   for name, data in events).encode()


@pytest.mark.parametrize("mode", ["remote_exec", "no_host_fallback", "apply_patch", "write_stdin"])
def test_actual_model_tool_routing_is_remote_only(tmp_path, mode):
    remote_unavailable = mode == "no_host_fallback"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", _IMAGE), "Pin an immutable local image ID"
    codex, docker = shutil.which("codex"), shutil.which("docker")
    assert codex and docker
    provider_home = tmp_path / "provider"
    provider_home.mkdir()
    host_sentinel = tmp_path / "host-only-sentinel"
    host_sentinel.write_text("UNCHANGED")
    name = "nightshift-remote-fixture-" + uuid.uuid4().hex[:12]
    state = {"requests": 0, "outputs": [], "commands": [], "file_changes": [], "inventory": [], "error": None}
    command = (
        "pwd; printf 'CONTAINER_EXECUTOR_OK\\n'; "
        "if test -e " + shlex.quote(str(host_sentinel)) + "; then "
        "printf 'HOST_FALLBACK' > " + shlex.quote(str(host_sentinel)) + "; exit 42; fi; "
        "printf 'HOST_FILE_ABSENT\\n'; "
        "test -z \"${NIGHTSHIFT_PROVIDER_SENTINEL:-}\" && printf 'PROVIDER_ENV_ABSENT\\n'; "
        "test -z \"${NIGHTSHIFT_FAKE_KEY:-}\" && printf 'PROVIDER_KEY_ABSENT\\n'"
    )

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/responses"
                assert self.headers.get("Authorization") == "Bearer synthetic-fixture-key"
                length = int(self.headers["Content-Length"])
                assert length <= 4 * 1024 * 1024
                body = json.loads(self.rfile.read(length))
                state["requests"] += 1
                assert state["requests"] <= 4, "Unexpected provider retry"
                state["outputs"].extend(i for i in body.get("input", [])
                                        if i.get("type") in {"function_call_output", "custom_tool_call_output"})
                inventory = [(tool.get("type"), tool.get("name")) for tool in body.get("tools", [])]
                assert inventory == [("function", "exec_command"), ("function", "write_stdin"),
                                     ("custom", "apply_patch"), ("namespace", "skills")]
                skills = body["tools"][-1]
                assert [tool.get("name") for tool in skills["tools"]] == ["list", "read"]
                state["inventory"] = inventory
                if state["requests"] == 1:
                    assert "exec_command" in [t.get("name") for t in body.get("tools", [])]
                    if remote_unavailable:
                        _docker("rm", "-f", name)
                    item = {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
                            "name": "exec_command", "status": "completed",
                            "arguments": json.dumps({"cmd": command, "workdir": "/work/source",
                                                     "max_output_tokens": 1000})}
                elif state["requests"] == 2 and mode == "apply_patch":
                    assert _docker("exec", name, "/bin/cat", "/work/source/remote-patch.txt") == "PATCH_ROUTED_TO_CONTAINER"
                    item = {"type": "function_call", "id": "fc_read_patch", "call_id": "call_read_patch",
                            "name": "exec_command", "status": "completed", "arguments": json.dumps({
                            "cmd": "cat /work/source/remote-patch.txt", "workdir": "/work/source", "max_output_tokens": 1000})}
                elif state["requests"] == 2 and mode == "write_stdin":
                    match = re.search(r"session ID (\d+)", state["outputs"][-1]["output"])
                    assert match, "Expected a live remote terminal"
                    item = {"type": "function_call", "id": "fc_stdin", "call_id": "call_stdin",
                            "name": "write_stdin", "status": "completed", "arguments": json.dumps({
                            "session_id": int(match[1]), "chars": "isolation\n", "yield_time_ms": 1000,
                            "max_output_tokens": 1000})}
                else:
                    item = {"type": "message", "id": "msg_fixture", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text",
                            "text": "NIGHTSHIFT_REMOTE_DONE", "annotations": []}]}
                if state["requests"] == 1 and mode == "apply_patch":
                    item = {"type": "custom_tool_call", "id": "ctc_fixture", "call_id": "call_patch",
                            "name": "apply_patch", "status": "completed", "input":
                            "*** Begin Patch\n*** Add File: /work/source/remote-patch.txt\n+PATCH_ROUTED_TO_CONTAINER\n*** End Patch\n"}
                if state["requests"] == 1 and mode == "write_stdin":
                    item["arguments"] = json.dumps({"cmd": "pwd; printf 'WAITING\\n'; read probe; printf 'STDIN_ROUTED:%s\\n' \"$probe\"",
                                                     "workdir": "/work/source", "tty": True,
                                                     "yield_time_ms": 1000, "max_output_tokens": 1000})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(_sse(item, state["requests"]))
                self.wfile.flush()
            except Exception as exc:
                state["error"] = type(exc).__name__
                self.send_error(500, "Synthetic fixture failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    (provider_home / "config.toml").write_text(
        'model_provider="fake"\nmodel="gpt-5.4"\nweb_search="disabled"\n'
        '[model_providers.fake]\nname="Synthetic fixture"\nbase_url=' + json.dumps(url) + '\n'
        'wire_api="responses"\nenv_key="NIGHTSHIFT_FAKE_KEY"\nrequires_openai_auth=false\n'
        'supports_websockets=false\nrequest_max_retries=0\nstream_max_retries=0\n'
        '[tools]\nexperimental_request_user_input={enabled=false}\n[features]\napps=false\nplugins=false\nhooks=false\nmulti_agent=false\n'
        'browser_use=false\ncomputer_use=false\nshell_snapshot=false\nview_image=false\nimage_generation=false\n')
    docker_host = _docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    container_args = [docker, "run", "--rm", "-i", "--name", name,
                      "--label", "nightshift.qualification=remote-fixture", "--pull", "never",
                      "--network", "none", "--read-only", "--cap-drop", "ALL",
                      "--security-opt", "no-new-privileges", "--pids-limit", "64",
                      "--memory", "256m", "--cpus", "1",
                      "--tmpfs", "/tmp:rw,nosuid,nodev,size=67108864",
                      "--tmpfs", "/work/source:rw,nosuid,nodev,size=8388608,uid=65534,gid=65534,mode=0700", _IMAGE]
    # Restarting an unavailable remote must fail. Otherwise recovery could just
    # start a fresh remote and fail to exercise the host-fallback negative case.
    launcher = tmp_path / "launch-once.sh"
    launched = tmp_path / "launched"
    launcher.write_text("#!/bin/sh\nset -eu\nif test -e " + shlex.quote(str(launched)) +
                        "; then exit 7; fi\ntouch " + shlex.quote(str(launched)) + "\nexec " +
                        shlex.join(container_args) + "\n")
    launcher.chmod(0o700)
    (provider_home / "environments.toml").write_text(
        'default="remote"\ninclude_local=false\n[[environments]]\nid="remote"\nprogram=' +
        json.dumps(str(launcher)) + '\ninitialize_timeout_sec=3\n[environments.env]\nDOCKER_HOST=' +
        json.dumps(docker_host) + '\n')
    env = {"PATH": os.defpath, "HOME": str(provider_home), "CODEX_HOME": str(provider_home),
           "NIGHTSHIFT_FAKE_KEY": "synthetic-fixture-key",
           "NIGHTSHIFT_PROVIDER_SENTINEL": "must-not-reach-tools"}
    version = subprocess.run([codex, "--version"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert version == "codex-cli 0.153.4", "This fixture qualifies exactly the pinned version"

    async def exercise():
        process = await asyncio.create_subprocess_exec(
            codex, "app-server", "--stdio", env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True, limit=4 * 1024 * 1024)

        async def receive():
            row = await asyncio.wait_for(process.stdout.readline(), 15)
            assert row, "Unexpected app-server EOF"
            return json.loads(row)

        async def rpc(identifier, method, params):
            process.stdin.write((json.dumps({"id": identifier, "method": method, "params": params}) + "\n").encode())
            await process.stdin.drain()
            while True:
                row = await receive()
                if row.get("id") == identifier:
                    return row

        try:
            await rpc(1, "initialize", {"clientInfo": {"name": "nightshift-synthetic", "version": "1"},
                                         "capabilities": {"experimentalApi": True}})
            info = await rpc(2, "environment/info", {"environmentId": "remote"})
            assert info["result"]["cwd"] == "file:///work/source"
            assert info["result"]["shell"]["name"] == "sh"
            assert "error" in await rpc(3, "environment/info", {"environmentId": "local"})
            # Omission intentionally exercises default='remote', include_local=false.
            response = await rpc(4, "thread/start", {
                "model": "gpt-5.4", "modelProvider": "fake", "cwd": "/work/source",
                "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only"})
            thread_id = response["result"]["thread"]["id"]
            await rpc(5, "turn/start", {"threadId": thread_id,
                "sandboxPolicy": {"type": "externalSandbox", "networkAccess": "restricted"},
                "input": [{"type": "text", "text": "Run the synthetic verification command."}]})
            while True:
                row = await receive()
                if row.get("method") == "item/completed":
                    item = row["params"]["item"]
                    if item.get("type") == "commandExecution":
                        state["commands"].append(item)
                    elif item.get("type") == "fileChange":
                        state["file_changes"].append(item)
                if row.get("method") == "turn/completed":
                    state["terminal_status"] = row["params"]["turn"]["status"]
                    return
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    try:
        asyncio.run(asyncio.wait_for(exercise(), 30))
        assert state["error"] is None, state["outputs"]
        assert state["requests"] == (3 if mode in {"apply_patch", "write_stdin"} else 2)
        assert host_sentinel.read_text() == "UNCHANGED"
        outputs = "\n".join(i["output"] for i in state["outputs"])
        if remote_unavailable:
            assert "CONTAINER_EXECUTOR_OK" not in outputs
            assert not any(c.get("exitCode") == 0 for c in state["commands"])
            assert "failed" in outputs.lower() or "unavailable" in outputs.lower() or "error" in outputs.lower()
        elif mode == "apply_patch":
            assert state["terminal_status"] == "completed"
            assert len(state["file_changes"]) == 1
            assert state["file_changes"][0]["status"] == "completed"
            assert state["commands"][0]["exitCode"] == 0
            assert "PATCH_ROUTED_TO_CONTAINER" in state["commands"][0]["aggregatedOutput"]
            assert not Path("/work/source/remote-patch.txt").exists()
        elif mode == "write_stdin":
            assert state["terminal_status"] == "completed"
            assert "STDIN_ROUTED:isolation" in outputs
            assert state["commands"][-1]["exitCode"] == 0
            assert state["commands"][-1]["cwd"] == "/work/source"
        else:
            assert state["terminal_status"] == "completed"
            assert len(state["commands"]) == 1
            completion = state["commands"][0]
            assert completion["exitCode"] == 0 and completion["cwd"] == "/work/source"
            for marker in ("CONTAINER_EXECUTOR_OK", "HOST_FILE_ABSENT", "PROVIDER_ENV_ABSENT", "PROVIDER_KEY_ABSENT"):
                assert marker in completion["aggregatedOutput"] and marker in outputs
    finally:
        server.shutdown()
        server.server_close()
        subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=15)
        assert name not in _docker("ps", "-a", "--filter", "name=" + name, "--format", "{{.Names}}")
