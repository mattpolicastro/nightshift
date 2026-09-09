"""Owned, bounded container sessions; not connected to native worker dispatch.

Requires a trusted local Docker engine and a compatible immutable image with
/bin/sleep and /bin/tar. Image-inherited environment beyond the fixed allowlist
is rejected. No provider auth, image pull, automatic recovery, or host execution
of candidate code occurs here. Source is mutable; this is not a reviewer mount.
"""
from __future__ import annotations

import json
import math
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import re
from dataclasses import dataclass
from pathlib import Path

from . import snapshot
from .container_exec import ContainerResult

OWNER_LABEL = "nightshift.session.owner"
ENVIRONMENT = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "HOME": "/tmp", "TMPDIR": "/tmp"}
VOLUME_OPTIONS = {"type": "tmpfs", "device": "tmpfs", "o": "size=64m,nosuid,nodev,uid=1000,gid=1000,mode=0700"}
SCRATCH = {"/tmp": "rw,nosuid,nodev,size=16m,uid=1000,gid=1000"}
CLEANUP_SECONDS = 15


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BinaryResult:
    status: str
    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""


def _binary(args, env, deadline, *, input_bytes=b"", stdout_limit=65536,
            stderr_limit=16384, combined_limit=None):
    """Simultaneously stream bounded stdin/stdout/stderr without pipe deadlock."""
    if time.monotonic() >= deadline:
        return BinaryResult("timed_out", None)
    combined_limit = stdout_limit + stderr_limit if combined_limit is None else combined_limit
    process = subprocess.Popen(["docker", *args], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    position = 0
    status = "completed"
    try:
        with selectors.DefaultSelector() as selector:
            for name in ("stdout", "stderr"):
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if input_bytes:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timed_out"
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    name = key.data
                    if name == "stdin":
                        try:
                            sent = os.write(key.fd, memoryview(input_bytes)[position:position + 65536])
                        except BrokenPipeError:
                            status = "input_rejected"
                            break
                        except BlockingIOError:
                            continue
                        position += sent
                        if position == len(input_bytes):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        capacity = min(limits[name] - len(buffers[name]),
                                       combined_limit - sum(map(len, buffers.values())))
                        buffers[name].extend(chunk[:max(0, capacity)])
                        if len(chunk) > capacity:
                            status = "output_exhausted"
                            break
                if status != "completed":
                    break
        if status == "completed":
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                status = "timed_out"
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if process.poll() is None:
                process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
    return BinaryResult(status, process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"]))


def _json(result):
    if result.status != "completed" or result.returncode != 0:
        raise SessionError("Docker control operation failed")
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise SessionError("Invalid Docker control response") from None


def _directory_sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Session:
    def __init__(self, image_id: str, files: list[snapshot.SourceFile], *, docker_host: str,
                 recovery_dir: Path, timeout_s: float = 30, deadline: float | None = None,
                 max_output_bytes: int = 1024 * 1024):
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("An immutable local image ID is required")
        if not isinstance(docker_host, str) or not re.fullmatch(r"unix:///[^\0\r\n]+", docker_host):
            raise ValueError("A local unix Docker socket is required")
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("A positive finite timeout is required")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError("A finite absolute deadline is required")
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("A positive session output budget is required")
        self.files = snapshot.validate(files)
        self.image_id, self.docker_host = image_id, docker_host
        self.recovery_dir = Path(recovery_dir)
        self.timeout_s, self.deadline = timeout_s, deadline
        self.max_output_bytes = max_output_bytes
        self.owner = uuid.uuid4().hex
        self.name = "nightshift-session-" + self.owner
        self.volume_name = self.name + "-source"
        self.record_path = self.recovery_dir / (self.owner + ".json")
        self.closed = False
        self.cleanup_succeeded = False
        self._entered = False
        self._poisoned = False
        self._recorded = False
        self._cleanup_deadline = None
        self._creation_uncertain = False
        self._record_payload = b""
        self._home = None
        self._output_bytes = 0
        self._baseline_processes = set()

    def _call(self, args, *, deadline=None, **kwargs):
        return _binary(args, self._env, self.deadline if deadline is None else deadline, **kwargs)

    def _checked(self, args, **kwargs):
        result = self._call(args, **kwargs)
        if result.status != "completed" or result.returncode != 0:
            raise SessionError("Docker session operation failed: " + result.status)
        return result

    def _create(self, args):
        # A lost CLI cannot cancel a daemon mutation already submitted.
        self._creation_uncertain = True
        self._checked(args)
        self._creation_uncertain = False

    def _inspect(self, kind, name, *, deadline=None):
        result = self._call(["inspect", "--type", kind, name], deadline=deadline)
        value = _json(result)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise SessionError("Invalid Docker inspect response")
        return value[0]

    def _volume_policy(self, data):
        return (data.get("Name") == self.volume_name and data.get("Driver") == "local"
                and data.get("Scope") == "local" and data.get("Options") == VOLUME_OPTIONS
                and data.get("Labels", {}).get(OWNER_LABEL) == self.owner)

    def _container_policy(self, data, *, running):
        config, host, state = data.get("Config", {}), data.get("HostConfig", {}), data.get("State", {})
        configured_mounts = host.get("Mounts", [])
        volume_mounts = [m for m in data.get("Mounts", []) if m.get("Type") == "volume"]
        return bool(
            config.get("Labels", {}).get(OWNER_LABEL) == self.owner and data.get("Image") == self.image_id
            and config.get("Image") == self.image_id and config.get("User") == "1000:1000"
            and config.get("WorkingDir") == "/workspace" and config.get("Entrypoint") == ["/bin/sleep"]
            and config.get("Cmd") == ["2147483647"]
            and config.get("Healthcheck", {}).get("Test") == ["NONE"]
            and sorted(config.get("Env", [])) == sorted(k + "=" + v for k, v in ENVIRONMENT.items())
            and state.get("Running") is running and state.get("Paused") is False
            and not state.get("Error") and not state.get("OOMKilled")
            and host.get("NetworkMode") == "none" and host.get("ReadonlyRootfs") is True
            and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd")
            and host.get("SecurityOpt") == ["no-new-privileges"]
            and host.get("PidsLimit") == 64 and host.get("Memory") == 128 * 1024 * 1024
            and host.get("NanoCpus") == 1_000_000_000 and host.get("Init") is True
            and host.get("IpcMode") == "private" and host.get("LogConfig", {}).get("Type") == "none"
            and host.get("Tmpfs") == SCRATCH
            and not any(host.get(key) for key in ("Binds", "VolumesFrom", "Devices", "DeviceRequests",
                        "Privileged", "PidMode", "UTSMode", "UsernsMode"))
            and len(configured_mounts) == 1
            and configured_mounts[0].get("Type") == "volume"
            and configured_mounts[0].get("Source") == self.volume_name
            and configured_mounts[0].get("Target") == "/workspace"
            and configured_mounts[0].get("VolumeOptions") == {"NoCopy": True}
            and not configured_mounts[0].get("ReadOnly")
            and len(volume_mounts) == 1 and volume_mounts[0].get("Name") == self.volume_name
            and volume_mounts[0].get("Destination") == "/workspace" and volume_mounts[0].get("RW") is True
            and all(m.get("Type") == "volume" or (m.get("Type") == "tmpfs" and m.get("Destination") == "/tmp")
                    for m in data.get("Mounts", [])))

    def _healthy(self):
        if not self._container_policy(self._inspect("container", self.name), running=True):
            raise SessionError("Effective running container policy differs")

    def _processes(self):
        result = self._checked(["top", self.name, "-eo", "pid"])
        try:
            lines = result.stdout.decode("ascii").splitlines()
            if not lines or lines[0].strip() != "PID":
                raise ValueError()
            values = {int(line.strip()) for line in lines[1:]}
            if not values:
                raise ValueError()
            return values
        except ValueError:
            raise SessionError("Invalid container process inventory") from None

    def _record(self):
        self.recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.recovery_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SessionError("Recovery directory must be private and owned")
        value = {"version": 1, "owner": self.owner, "container": self.name, "volume": self.volume_name,
                 "image_id": self.image_id, "docker_host": self.docker_host}
        descriptor = os.open(self.record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self._recorded = True
        self._record_payload = json.dumps(value, sort_keys=True).encode() + b"\n"
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(self._record_payload)
            stream.flush()
            os.fsync(stream.fileno())
        _directory_sync(self.recovery_dir)
        self._recorded = True

    def __enter__(self):
        if self._entered or self.closed:
            raise SessionError("Session cannot be reused")
        self._entered = True
        if self.deadline is None:
            self.deadline = time.monotonic() + self.timeout_s
        self._home = tempfile.TemporaryDirectory(prefix="nightshift-session-docker-")
        self._env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": self._home.name,
                     "DOCKER_CONFIG": self._home.name, "DOCKER_HOST": self.docker_host}
        try:
            archive = snapshot.encode(self.files)
            self._record()
            args = ["volume", "create", "--label", OWNER_LABEL + "=" + self.owner, "--driver", "local"]
            for key, value in VOLUME_OPTIONS.items():
                args.extend(["--opt", key + "=" + value])
            self._create([*args, self.volume_name])
            if not self._volume_policy(self._inspect("volume", self.volume_name)):
                raise SessionError("Effective source volume policy differs")
            args = ["create", "--pull", "never", "--name", self.name, "--label", OWNER_LABEL + "=" + self.owner,
                    "--network", "none", "--user", "1000:1000", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "128m",
                    "--cpus", "1", "--log-driver", "none", "--init", "--ipc", "private", "--no-healthcheck",
                    "--workdir", "/workspace", "--entrypoint", "/bin/sleep",
                    "--mount", "type=volume,src=" + self.volume_name + ",dst=/workspace,volume-nocopy"]
            for key, value in ENVIRONMENT.items():
                args.extend(["--env", key + "=" + value])
            for target, value in SCRATCH.items():
                args.extend(["--tmpfs", target + ":" + value])
            self._create([*args, self.image_id, "2147483647"])
            if not self._container_policy(self._inspect("container", self.name), running=False):
                raise SessionError("Effective container policy differs before start")
            self._checked(["start", self.name])
            self._healthy()
            self._checked(["exec", "-i", "--", self.name, "/bin/tar", "-xf", "-", "-C", "/workspace"],
                          input_bytes=archive)
            self._baseline_processes = self._processes()
            if len(self._baseline_processes) != 2:
                raise SessionError("Unexpected initial container processes")
            if self.checkpoint() != self.files:
                raise SessionError("Imported source differs from the requested snapshot")
            return self
        except BaseException as exc:
            try:
                self.close()
            except SessionError as cleanup:
                exc.add_note(str(cleanup))
            if isinstance(exc, (KeyboardInterrupt, SystemExit, SessionError)):
                raise
            raise SessionError("Unable to initialize container session") from exc

    def run(self, argv: list[str]) -> ContainerResult:
        if not self._entered or self.closed or self._poisoned:
            raise SessionError("Session is unavailable")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and "\0" not in a for a in argv) or not argv[0]:
            raise ValueError("A nonempty container argv is required")
        try:
            self._healthy()
            if self._processes() != self._baseline_processes:
                raise SessionError("Unfinished container operations before command")
            remaining = self.max_output_bytes - self._output_bytes
            result = self._call(["exec", "--workdir", "/workspace", "--", self.name, *argv],
                                stdout_limit=remaining, stderr_limit=min(16384, remaining), combined_limit=remaining)
            self._output_bytes += len(result.stdout) + len(result.stderr)
            output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
            if result.status != "completed":
                self._poisoned = True
                return ContainerResult(result.status, result.returncode, output)
            self._healthy()
            if self._processes() != self._baseline_processes:
                self._poisoned = True
                return ContainerResult("backgrounded", result.returncode, output)
            # A failed test or grep is a completed command; implementation may repair.
            return ContainerResult("succeeded" if result.returncode == 0 else "failed", result.returncode, output)
        except (OSError, ValueError, TypeError, AttributeError, SessionError) as exc:
            self._poisoned = True
            raise SessionError("Container command completion could not be confirmed") from exc

    def _export(self) -> list[snapshot.SourceFile]:
        if self._poisoned:
            raise SessionError("Session failed; candidate cannot be accepted")
        self._healthy()
        if self._processes() != self._baseline_processes:
            raise SessionError("Unfinished container operations before export")
        self._checked(["pause", self.name])
        state = self._inspect("container", self.name)
        if (state.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != self.owner
                or state.get("State", {}).get("Paused") is not True
                or state.get("State", {}).get("Running") is not True):
            raise SessionError("Paused container state could not be confirmed")
        if self._processes() != self._baseline_processes:
            raise SessionError("Unfinished operations in paused container")
        result = self._checked(["cp", self.name + ":/workspace", "-"],
                               stdout_limit=snapshot.MAX_ARCHIVE_BYTES,
                               combined_limit=snapshot.MAX_ARCHIVE_BYTES)
        return snapshot.decode_container_archive(result.stdout)

    def checkpoint(self) -> list[snapshot.SourceFile]:
        """Provisional paused snapshot; acceptance still requires finish cleanup."""
        if not self._entered or self.closed:
            raise SessionError("Session is unavailable")
        try:
            files = self._export()
            self._checked(["unpause", self.name])
            self._healthy()
            if self._processes() != self._baseline_processes:
                raise SessionError("Unfinished operations after checkpoint")
            return files
        except BaseException as exc:
            self._poisoned = True
            if isinstance(exc, (KeyboardInterrupt, SystemExit, SessionError)):
                raise
            raise SessionError("Invalid or incomplete checkpoint") from exc

    def finish(self) -> list[snapshot.SourceFile]:
        if not self._entered or self.closed:
            raise SessionError("Session is unavailable")
        try:
            files = self._export()
        except BaseException as exc:
            self._poisoned = True
            try:
                self.close()
            except SessionError as cleanup:
                exc.add_note(str(cleanup))
            if isinstance(exc, (KeyboardInterrupt, SystemExit, SessionError)):
                raise
            raise SessionError("Invalid or incomplete candidate export") from exc
        self.close()
        return files

    def _exists(self, kind, name, deadline):
        args = (["ps", "-a"] if kind == "container" else ["volume", "ls"])
        field = ".Names" if kind == "container" else ".Name"
        result = self._checked([*args, "--filter", "name=" + name, "--format", "{{json " + field + "}}"], deadline=deadline)
        try:
            names = [json.loads(line) for line in result.stdout.splitlines()]
            if not all(isinstance(n, str) for n in names):
                raise ValueError()
        except ValueError:
            raise SessionError("Docker resource absence could not be confirmed") from None
        return name in names

    def close(self):
        if self.closed:
            return
        if self._cleanup_deadline is None:
            self._cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        deadline = self._cleanup_deadline
        try:
            if self._recorded:
                for kind, name in (("container", self.name), ("volume", self.volume_name)):
                    if self._exists(kind, name, deadline):
                        data = self._inspect(kind, name, deadline=deadline)
                        labels = data.get("Config", {}).get("Labels", {}) if kind == "container" else data.get("Labels", {})
                        if labels.get(OWNER_LABEL) != self.owner:
                            raise SessionError("Refusing cleanup of a resource with another owner")
                        args = ["rm", "--force", name] if kind == "container" else ["volume", "rm", name]
                        self._checked(args, deadline=deadline)
                    if self._exists(kind, name, deadline):
                        raise SessionError("Owned resource remains after removal")
                if self._creation_uncertain:
                    raise SessionError("A daemon creation may still be in flight")
                self.record_path.unlink()
                try:
                    _directory_sync(self.recovery_dir)
                except OSError:
                    # Restore the recovery clue if durable deletion cannot be proven.
                    descriptor = os.open(self.record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(self._record_payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    raise
                self._recorded = False
            self.closed = True
            self.cleanup_succeeded = True
        except (OSError, ValueError, TypeError, AttributeError, SessionError) as exc:
            raise SessionError("Session cleanup unconfirmed; inspect ownership record before recovery") from exc
        finally:
            if self._home is not None:
                self._home.cleanup()

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except SessionError as cleanup:
            if exc is None:
                raise
            exc.add_note(str(cleanup))
        return False
