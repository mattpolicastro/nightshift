"""Offline container tool-executor prototype; not wired into worker execution.

Runs one argv in an empty disposable workspace using an already-local immutable
image. This is a qualification building block, not a production sandbox claim.
A trusted local Docker daemon is required; no image pull or source transfer occurs.
"""
from __future__ import annotations

import json
import math
import os
import posixpath
import re
import selectors
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass

_OWNER = "nightshift.prototype.owner"
_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp", "TMPDIR": "/tmp"}
_TMPFS = {"/workspace": "rw,nosuid,nodev,size=64m,uid=1000,gid=1000",
          "/tmp": "rw,nosuid,nodev,size=16m,uid=1000,gid=1000"}


@dataclass(frozen=True)
class ContainerResult:
    status: str
    exit_code: int | None = None
    output: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "succeeded" and self.exit_code == 0


def _cli(args: list[str], env: dict[str, str], deadline: float,
         limit: int = 65536) -> ContainerResult:
    if time.monotonic() >= deadline:
        return ContainerResult("timed_out")
    proc = subprocess.Popen(["docker", *args], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    output = bytearray()
    status = "completed"
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timed_out"
                    break
                if not selector.select(min(remaining, 0.05)):
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                remaining_bytes = limit - len(output)
                output.extend(chunk[:remaining_bytes])
                if len(chunk) > remaining_bytes:
                    status = "output_exhausted"
                    break
        if status == "completed":
            try:
                proc.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                status = "timed_out"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdout.close()
    return ContainerResult(status, proc.returncode, output.decode("utf-8", "replace"))


def _inspect(name: str, env: dict[str, str], deadline: float) -> dict | None:
    result = _cli(["inspect", "--type", "container", name], env, deadline)
    if result.status != "completed" or result.exit_code != 0:
        return None
    try:
        values = json.loads(result.output)
        return values[0] if isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict) else None
    except ValueError:
        return None


def _owned(data: dict | None, owner: str) -> bool:
    return bool(data and data.get("Config", {}).get("Labels", {}).get(_OWNER) == owner)


def _policy(data: dict, owner: str, image_id: str, argv: list[str]) -> bool:
    """Inspect effective settings, including image-inherited entrypoints/mounts."""
    config, host = data.get("Config", {}), data.get("HostConfig", {})
    mounts = data.get("Mounts", [])
    return bool(
        _owned(data, owner) and data.get("Image") == image_id
        and config.get("Image") == image_id and config.get("User") == "1000:1000"
        and data.get("State", {}).get("Running") is False
        and config.get("Healthcheck", {}).get("Test") == ["NONE"]
        and isinstance(config.get("Env"), list)
        and sorted(config["Env"]) == sorted(key + "=" + value for key, value in _ENV.items())
        and config.get("WorkingDir") == "/workspace"
        and config.get("Entrypoint") == [argv[0]]
        and (config.get("Cmd") or []) == argv[1:]
        and host.get("NetworkMode") == "none" and host.get("ReadonlyRootfs") is True
        and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd")
        and host.get("SecurityOpt") == ["no-new-privileges"]
        and host.get("PidsLimit") == 64 and host.get("Memory") == 128 * 1024 * 1024
        and host.get("NanoCpus") == 1_000_000_000 and host.get("Init") is True
        and host.get("LogConfig", {}).get("Type") == "none"
        and host.get("Tmpfs") == _TMPFS
        and not any(host.get(key) for key in ("Binds", "Mounts", "VolumesFrom", "Devices",
                    "DeviceRequests", "Privileged", "PidMode", "UTSMode", "UsernsMode"))
        and host.get("IpcMode") == "private"
        and isinstance(mounts, list)
        and all(m.get("Type") == "tmpfs" and m.get("Destination") in _TMPFS for m in mounts)
    )


def execute(image_id: str, argv: list[str], *, docker_host: str | None = None,
            timeout_s: float = 30, max_output_bytes: int = 1024 * 1024) -> ContainerResult:
    """Run in a fixed empty-container policy and always remove the owned container.

    The execution budget covers create/inspect/start; cleanup has its own bounded
    allowance (three calls, at most five seconds each). Cancellation propagates
    after cleanup. Pass an explicit unix socket for non-default local engines.
    """
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("An exact local sha256 image ID is required; tags are forbidden")
    if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) and "\0" not in a for a in argv)
            or not argv[0].startswith("/") or argv[0].startswith("//")
            or argv[0] == "/" or posixpath.normpath(argv[0]) != argv[0]):
        raise ValueError("An argv list with a normalized absolute container executable is required")
    if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("A positive finite deadline is required")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("A positive output byte budget is required")
    host = docker_host or os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if not isinstance(host, str) or not re.fullmatch(r"unix:///[^\0\r\n]+", host):
        raise ValueError("Only an explicit local unix Docker socket is supported")
    owner = uuid.uuid4().hex
    name = "nightshift-tool-" + owner
    deadline = time.monotonic() + timeout_s
    result = ContainerResult("failed", detail="Container did not start")
    with tempfile.TemporaryDirectory(prefix="nightshift-docker-") as home:
        env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": home,
               "DOCKER_CONFIG": home, "DOCKER_HOST": host}
        args = ["create", "--pull", "never", "--name", name, "--label", _OWNER + "=" + owner,
                "--network", "none", "--user", "1000:1000", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "128m",
                "--cpus", "1", "--log-driver", "none", "--init", "--ipc", "private", "--no-healthcheck",
                "--workdir", "/workspace", "--entrypoint", argv[0]]
        for key, value in _ENV.items():
            args.extend(["--env", key + "=" + value])
        for target, options in _TMPFS.items():
            args.extend(["--tmpfs", target + ":" + options])
        args.extend([image_id, *argv[1:]])
        try:
            created = _cli(args, env, deadline)
            if created.status != "completed" or created.exit_code != 0:
                result = ContainerResult(created.status if created.status != "completed" else "failed",
                                         created.exit_code, created.output, "Container creation failed")
            else:
                inspected = _inspect(name, env, deadline)
                if not inspected or not _policy(inspected, owner, image_id, argv):
                    result = ContainerResult("policy_rejected", detail="Effective container policy differs")
                else:
                    result = _cli(["start", "--attach", name], env, deadline, max_output_bytes)
                    if result.status == "completed":
                        finished = _inspect(name, env, deadline)
                        state = (finished or {}).get("State", {})
                        code = state.get("ExitCode")
                        if not _owned(finished, owner) or state.get("Running") is not False or type(code) is not int:
                            result = ContainerResult("failed", output=result.output,
                                                     detail="Container completion not confirmed")
                        else:
                            success = (result.exit_code == 0 and code == 0
                                       and not state.get("Error") and not state.get("OOMKilled"))
                            result = ContainerResult("succeeded" if success else "failed", code, result.output)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            result = ContainerResult("failed", detail=type(exc).__name__)
        finally:
            try:
                # Name collisions and failed creates never authorize deleting a
                # different container. Ownership is checked even after timeout.
                inspected = _inspect(name, env, time.monotonic() + 5)
                if _owned(inspected, owner):
                    _cli(["kill", name], env, time.monotonic() + 5)
                    removed = _cli(["rm", "--force", name], env, time.monotonic() + 5)
                    if removed.status != "completed" or removed.exit_code != 0:
                        result = ContainerResult("cleanup_failed", detail="Owned container removal failed")
                else:
                    # An unreachable engine cannot prove that a timed-out
                    # create left nothing behind. Never claim successful cleanup.
                    result = ContainerResult("cleanup_failed", detail="Container absence/ownership could not be confirmed")
            except (OSError, ValueError, TypeError, AttributeError):
                result = ContainerResult("cleanup_failed", detail="Container cleanup could not be confirmed")
    return result
