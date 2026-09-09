"""Opt-in pinned native transport + owned Docker session + loopback fake provider.

NIGHTSHIFT_TEST_NATIVE_EXECUTOR_IMAGE pins the already-local Dockerfile.session
image by immutable ID. No login, real credentials, model service, or image pull.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nightshift.workers import codex, snapshot
from nightshift.workers.base import ReviewerVerdict, WorkerBudgets, WorkerRequest, WorkerResult

IMAGE = os.environ.get("NIGHTSHIFT_TEST_NATIVE_EXECUTOR_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="explicit immutable local native session image required")


def _sse(item, sequence):
    response = {"id": f"response_{sequence}", "status": "completed", "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
    events = [("response.created", {"response": {**response, "status": "in_progress", "output": []}}),
              ("response.output_item.added", {"output_index": 0, "item": item}),
              ("response.output_item.done", {"output_index": 0, "item": item}),
              ("response.completed", {"response": response})]
    return "".join("event: " + name + "\ndata: " + json.dumps({"type": name, **data}) + "\n\n"
                   for name, data in events).encode()


@pytest.mark.parametrize("mode", ["edits", "missing_executor", "cancelled", "coordinator"])
def test_native_tools_use_owned_session_and_cleanup(tmp_path, mode):
    from nightshift.workers.container_session import Session, SessionError
    from nightshift.workers.native_executor import NativeExecutor

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE)
    binary, docker_binary = shutil.which("codex"), shutil.which("docker")
    assert binary and docker_binary
    host = os.environ.get("NIGHTSHIFT_TEST_DOCKER_HOST")
    if not host:
        host = subprocess.run([docker_binary, "context", "inspect", "--format",
                               "{{.Endpoints.docker.Host}}"], capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    assert re.fullmatch(r"unix:///[^\0\r\n]+", host)
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir(mode=0o700)
    docker_env = {"PATH": os.defpath, "DOCKER_HOST": host, "DOCKER_CONFIG": str(docker_config)}

    def docker(*args, check=True):
        completed = subprocess.run([docker_binary, *args], env=docker_env, capture_output=True,
                                   text=True, timeout=10)
        if check:
            assert completed.returncode == 0, completed.stderr[:2048]
        return completed

    docker("image", "inspect", IMAGE)
    provider_home = tmp_path / "provider"
    provider_home.mkdir(mode=0o700)
    sentinel = tmp_path / "host-only"
    sentinel.write_text("SYNTHETIC_HOST_UNCHANGED")
    state = {"requests": 0, "outputs": [], "error": None}
    container_name = None
    edits = mode in {"edits", "coordinator"}
    command = ("set -eu; if test -e " + shlex.quote(str(sentinel)) + "; then "
               "printf HOST_FALLBACK > " + shlex.quote(str(sentinel)) + "; exit 42; fi; "
               'test -z "${NIGHTSHIFT_FAKE_KEY:-}${NIGHTSHIFT_PROVIDER_SENTINEL:-}"; '
               'test ! -e /var/run/docker.sock; test -z "$(ip route show)"; '
               "printf '42\\n' > /workspace/answer; printf HOST_ENV_NETWORK_BOUNDARY_OK")
    if mode == "cancelled":
        command = "printf STARTED > /tmp/native-command-started; sleep 30"

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/responses"
                assert self.headers.get("Authorization") == "Bearer synthetic-fixture-key"
                length = int(self.headers["Content-Length"])
                assert 0 < length <= 4 * 1024 * 1024
                body = json.loads(self.rfile.read(length))
                state["requests"] += 1
                assert state["requests"] <= 3
                inventory = [(tool.get("type"), tool.get("name")) for tool in body.get("tools", [])]
                assert inventory == [("function", "exec_command"), ("function", "write_stdin"),
                                     ("custom", "apply_patch"), ("namespace", "skills")]
                assert [tool["name"] for tool in body["tools"][-1]["tools"]] == ["list", "read"]
                state["outputs"].extend(item for item in body.get("input", [])
                                        if item.get("type") in {"function_call_output", "custom_tool_call_output"})
                if state["requests"] == 1:
                    if mode == "missing_executor":
                        docker("rm", "-f", container_name)
                    item = {"type": "function_call", "id": "fc_boundary", "call_id": "call_boundary",
                            "name": "exec_command", "status": "completed", "arguments": json.dumps({
                                "cmd": command, "workdir": "/workspace", "max_output_tokens": 1000})}
                elif state["requests"] == 2 and edits:
                    item = {"type": "custom_tool_call", "id": "ctc_patch", "call_id": "call_patch",
                            "name": "apply_patch", "status": "completed", "input":
                            "*** Begin Patch\n*** Add File: /workspace/native-patch.txt\n+OWNED_NATIVE_PATCH\n*** End Patch\n"}
                else:
                    item = {"type": "message", "id": "msg_done", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "SYNTHETIC_DONE", "annotations": []}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(_sse(item, state["requests"]))
                self.wfile.flush()
            except Exception as exc:
                state["error"] = repr(exc)
                self.send_error(500, "Synthetic fixture failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    (provider_home / "config.toml").write_text(
        'model_provider="fake"\nmodel="gpt-5.4"\nweb_search="disabled"\n'
        '[model_providers.fake]\nname="Synthetic fixture"\nbase_url=' +
        json.dumps(f"http://127.0.0.1:{server.server_port}/v1") + '\n'
        'wire_api="responses"\nenv_key="NIGHTSHIFT_FAKE_KEY"\nrequires_openai_auth=false\n'
        'supports_websockets=false\nrequest_max_retries=0\nstream_max_retries=0\n'
        '[tools]\nexperimental_request_user_input={enabled=false}\n'
        '[features]\napps=false\nplugins=false\nhooks=false\nmulti_agent=false\n'
        'browser_use=false\ncomputer_use=false\nshell_snapshot=false\nview_image=false\n'
        'image_generation=false\nskip_host_skill_discovery=true\nskill_search=true\n')
    env = {"PATH": os.defpath, "HOME": str(provider_home), "CODEX_HOME": str(provider_home),
           "NIGHTSHIFT_FAKE_KEY": "synthetic-fixture-key", "NIGHTSHIFT_PROVIDER_SENTINEL": "provider-only"}
    version = subprocess.run([binary, "--version"], env=env, capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
    assert version == "codex-cli 0.153.4"
    request = WorkerRequest("implement", Path("/workspace"), "Run the synthetic task.", "gpt-5.4",
                            budgets=WorkerBudgets(max_runtime_s=20, max_tool_calls=6,
                                                  max_output_tokens_total=4000))

    async def invoke():
        task = asyncio.create_task(codex._run_stdio(request, [binary, "app-server", "--stdio"],
                                                   env=env, external_executor=True, provider_cwd=provider_home))
        if mode == "cancelled":
            try:
                async with asyncio.timeout(12):
                    while not task.done():
                        if not state["requests"]:
                            await asyncio.sleep(0.05)
                            continue
                        observed = await asyncio.to_thread(docker, "exec", container_name,
                            "/bin/cat", "/tmp/native-command-started", check=False)
                        if observed.returncode == 0:
                            assert observed.stdout == "STARTED"
                            task.cancel()
                            break
                        await asyncio.sleep(0.05)
                    else:
                        pytest.fail("Worker completed before the synthetic cancellation control")
            finally:
                if not task.done():
                    task.cancel()
        return await task

    def run_owned(files):
        nonlocal container_name
        recovery = tmp_path / "recovery"
        returned = None
        result = None
        with Session(IMAGE, files, docker_host=host,
                     recovery_dir=recovery, timeout_s=40, max_output_bytes=1024 * 1024) as session:
            container_name, volume_name = session.name, session.volume_name
            expected = pytest.raises(SessionError) if mode == "missing_executor" else nullcontext()
            with expected:
                with NativeExecutor(session, provider_home) as executor:
                    result = asyncio.run(invoke())
                    assert state["error"] is None, state["error"]
                    if edits:
                        assert result.ok, result.diagnostics
                        executor.quiesce()
                        returned = session.finish()
                    elif mode == "missing_executor":
                        assert not any(command.exit_code == 0 for command in result.commands)
                        executor.quiesce()
                    else:
                        assert result.status == "interrupted", result.diagnostics
                        # No candidate is accepted after cancellation; the contexts
                        # must stop the owned server and destroy its process namespace.
        assert result is not None, "The native worker must actually run"
        assert state["requests"] >= 1, "The synthetic provider must actually request a tool"
        assert session.cleanup_succeeded
        assert docker("inspect", "--type", "container", container_name, check=False).returncode != 0
        assert docker("volume", "inspect", volume_name, check=False).returncode != 0
        assert not list(recovery.glob("*.json"))
        assert sentinel.read_text() == "SYNTHETIC_HOST_UNCHANGED"
        outputs = "\n".join(item["output"] for item in state["outputs"])
        assert "SYNTHETIC_HOST_UNCHANGED" not in outputs
        if edits:
            assert state["requests"] == 3
            assert "HOST_ENV_NETWORK_BOUNDARY_OK" in outputs
            assert returned == snapshot.validate([entry for entry in files if entry.path != "answer"] + [
                snapshot.SourceFile("answer", b"42\n"),
                snapshot.SourceFile("native-patch.txt", b"OWNED_NATIVE_PATCH\n")])
        else:
            assert returned is None
            if mode == "missing_executor":
                assert "HOST_ENV_NETWORK_BOUNDARY_OK" not in outputs
        return result, returned

    try:
        if mode != "coordinator":
            run_owned([snapshot.SourceFile("answer", b"41\n")])
        else:
            from nightshift.workers import candidate_pipeline, isolated_verification

            repository = tmp_path / "candidate"
            repository.mkdir()
            git_env = {"PATH": os.environ.get("PATH", os.defpath),
                       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

            def git(*args):
                return subprocess.run(["git", *args], cwd=repository, env=git_env,
                    capture_output=True, text=True, check=True, timeout=10).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Synthetic Fixture")
            git("config", "user.email", "fixture@example.invalid")
            (repository / "answer").write_text("41\n")
            (repository / "verify.sh").write_text('set -eu\ntest "$(cat answer)" = 42\n')
            git("add", ".")
            git("commit", "-qm", "Synthetic baseline")
            baseline_sha = git("rev-parse", "HEAD")
            calls = []
            reviewed_paths = []

            def implement(value):
                calls.append("implement")
                assert value.base_sha == baseline_sha
                assert list(value.files) == snapshot.from_git(repository, baseline_sha)
                result, files = run_owned(list(value.files))
                return candidate_pipeline.ImplementationOutcome(result, files)

            def verify(value):
                calls.append("verify")
                assert value.repository == repository.resolve()
                return isolated_verification.run(value.repository, value.candidate_sha,
                    value.command, image_id=IMAGE, docker_host=host,
                    recovery_dir=tmp_path / "verification-recovery", timeout_s=30)

            def review(value):
                # This is an explicitly trusted synthetic callback. Mode bits
                # and a fresh identity do not qualify a hostile reviewer sandbox.
                calls.append("synthetic_review")
                reviewed_paths.append(value.source_path)
                assert value.readonly_mount_required is True
                assert value.base_sha == baseline_sha and value.candidate_sha != baseline_sha
                assert value.verification.ok
                assert value.source_path.stat().st_mode & 0o777 == 0o555
                expected = snapshot.from_git(repository, value.candidate_sha)
                assert {p.relative_to(value.source_path).as_posix() for p in value.source_path.rglob("*")} == {
                    entry.path for entry in expected}
                for entry in expected:
                    path = value.source_path / entry.path
                    assert path.read_bytes() == entry.content
                    assert path.stat().st_mode & 0o777 == (0o555 if entry.executable else 0o444)
                changes = {change.path: change for change in value.diff}
                assert set(changes) == {"answer", "native-patch.txt"}
                assert changes["answer"].before.content == b"41\n"
                assert changes["answer"].after.content == b"42\n"
                assert changes["native-patch.txt"].before is None
                assert changes["native-patch.txt"].after.content == b"OWNED_NATIVE_PATCH\n"
                return WorkerResult(status="succeeded", runtime="synthetic-trusted-reviewer",
                    thread_id=value.review_id, reviewer_verdict=ReviewerVerdict("PASS", (), ()))

            result = candidate_pipeline._run_offline(repository, baseline_sha,
                "sh verify.sh && /bin/sh verify.sh", implement=implement, verify=verify, review=review)
            assert result.fixture_passed and result.qualification_only, result.detail
            assert result.ready_for_shipping, result.detail
            assert result.candidate_sha == git("rev-parse", "HEAD")
            assert git("status", "--porcelain") == ""
            assert calls == ["implement", "verify", "synthetic_review"]
            assert all(not path.exists() for path in reviewed_paths)
            assert not list((tmp_path / "verification-recovery").glob("*.json"))
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
