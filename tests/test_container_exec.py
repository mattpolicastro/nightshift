"""Offline lifecycle fixtures plus real bounded CLI subprocesses; no Docker needed."""
import copy
import json
import os
import sys
import time

import pytest

from nightshift.workers import container_exec as executor

IMAGE = "sha256:" + "a" * 64
ARGV = ["/bin/echo", "hello"]


class Engine:
    def __init__(self, *, mutate=None, start_status="completed", exit_code=0,
                 running=False, create_status="completed", remove_ok=True, cancel=False):
        self.calls = []
        self.environments = []
        self.mutate = mutate
        self.start_status = start_status
        self.exit_code = exit_code
        self.running = running
        self.create_status = create_status
        self.remove_ok = remove_ok
        self.cancel = cancel
        self.started = False

    def __call__(self, args, env, deadline, limit=65536):
        self.calls.append(args)
        self.environments.append(env.copy())
        assert deadline > time.monotonic()
        if args[0] == "create":
            self.name = args[args.index("--name") + 1]
            self.owner = args[args.index("--label") + 1].split("=", 1)[1]
            self.data = {
                "Image": IMAGE, "State": {"Running": False, "ExitCode": 0},
                "Config": {"Image": IMAGE, "User": "1000:1000", "WorkingDir": "/workspace",
                           "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                                   "HOME=/tmp", "TMPDIR=/tmp"],
                           "Entrypoint": ["/bin/echo"], "Cmd": ["hello"],
                           "Healthcheck": {"Test": ["NONE"]},
                           "Labels": {"nightshift.prototype.owner": self.owner}},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                    "PidsLimit": 64, "Memory": 134217728, "NanoCpus": 1000000000,
                    "Init": True, "LogConfig": {"Type": "none"}, "IpcMode": "private",
                    "Tmpfs": {"/workspace": "rw,nosuid,nodev,size=64m,uid=1000,gid=1000",
                              "/tmp": "rw,nosuid,nodev,size=16m,uid=1000,gid=1000"}},
                "Mounts": [],
            }
            if self.mutate:
                self.mutate(self.data)
            return executor.ContainerResult(self.create_status, 0, "container-id")
        assert args[-1] == self.name
        if args[0] == "inspect":
            data = copy.deepcopy(self.data)
            if self.started:
                data["State"] = {"Running": self.running, "ExitCode": self.exit_code}
            return executor.ContainerResult("completed", 0, json.dumps([data]))
        if args[0] == "start":
            self.started = True
            if self.cancel:
                raise KeyboardInterrupt
            return executor.ContainerResult(self.start_status, 0, "hello\n")
        if args[0] == "rm":
            return executor.ContainerResult("completed", 0 if self.remove_ok else 1)
        return executor.ContainerResult("completed", 0)


def execute(monkeypatch, engine, **kwargs):
    monkeypatch.setattr(executor, "_cli", engine)
    return executor.execute(IMAGE, ARGV, docker_host="unix:///tmp/test-engine.sock", **kwargs)


def test_fixed_create_policy_and_private_cli_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-sentinel")
    monkeypatch.setenv("GH_TOKEN", "synthetic-sentinel")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-context")
    engine = Engine()
    result = execute(monkeypatch, engine)
    assert result.ok
    create = engine.calls[0]
    assert create[create.index("--pull") + 1] == "never"
    assert create[-2:] == [IMAGE, "hello"]
    assert create[create.index("--entrypoint") + 1] == "/bin/echo"
    assert not any(a in create for a in ("--volume", "--mount", "--privileged", "--pid"))
    assert [call[0] for call in engine.calls][-3:] == ["inspect", "kill", "rm"]
    assert engine.calls[-1] == ["rm", "--force", engine.name]
    assert all(set(env) == {"PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST"}
               for env in engine.environments)
    assert all(env["DOCKER_HOST"] == "unix:///tmp/test-engine.sock" for env in engine.environments)


@pytest.mark.parametrize("mutation", [
    lambda d: d["HostConfig"].update(NetworkMode="host"),
    lambda d: d["HostConfig"].update(ReadonlyRootfs=False),
    lambda d: d["HostConfig"].update(PidMode="host"),
    lambda d: d["HostConfig"].update(Privileged=True),
    lambda d: d["HostConfig"].update(Binds=["/:/host"]),
    lambda d: d.update(Mounts=[{"Type": "volume", "Destination": "/workspace"}]),
    lambda d: d["Config"].update(User="0"),
    lambda d: d["Config"].update(Entrypoint=["/unexpected"]),
    lambda d: d["Config"].update(Healthcheck={"Test": ["CMD", "unexpected"]}),
    lambda d: d.update(Image="sha256:" + "b" * 64),
])
def test_effective_policy_mismatch_never_starts_and_cleans_owned_container(monkeypatch, mutation):
    engine = Engine(mutate=mutation)
    assert execute(monkeypatch, engine).status == "policy_rejected"
    assert not engine.started
    assert engine.calls[-1][0] == "rm"


@pytest.mark.parametrize("status", ["timed_out", "output_exhausted"])
def test_incomplete_attach_kills_and_removes_entire_container(monkeypatch, status):
    engine = Engine(start_status=status)
    assert execute(monkeypatch, engine).status == status
    assert engine.calls[-2:] == [["kill", engine.name], ["rm", "--force", engine.name]]


def test_timeout_during_create_still_removes_owned_container(monkeypatch):
    engine = Engine(create_status="timed_out")
    assert execute(monkeypatch, engine).status == "timed_out"
    assert not engine.started
    assert engine.calls[-1] == ["rm", "--force", engine.name]


def test_cancellation_cleans_up_then_propagates(monkeypatch):
    engine = Engine(cancel=True)
    with pytest.raises(KeyboardInterrupt):
        execute(monkeypatch, engine)
    assert engine.calls[-1] == ["rm", "--force", engine.name]


@pytest.mark.parametrize("kwargs", [{"exit_code": 17}, {"running": True}, {"remove_ok": False}])
def test_cli_success_cannot_override_container_failure_or_cleanup(monkeypatch, kwargs):
    assert not execute(monkeypatch, Engine(**kwargs)).ok


def test_foreign_container_is_never_killed_or_removed(monkeypatch):
    engine = Engine(mutate=lambda d: d["Config"]["Labels"].update({"nightshift.prototype.owner": "someone-else"}))
    assert not execute(monkeypatch, engine).ok
    assert not any(call[0] in ("kill", "rm", "start") for call in engine.calls)


@pytest.mark.parametrize("image", ["alpine:latest", "sha256:abc", "registry.invalid/image@sha256:" + "a" * 64])
def test_image_tags_and_registry_references_are_rejected(image):
    with pytest.raises(ValueError):
        executor.execute(image, ARGV)


def test_remote_engine_is_rejected():
    with pytest.raises(ValueError):
        executor.execute(IMAGE, ARGV, docker_host="tcp://example.invalid:2375")


def test_inherited_remote_engine_is_rejected(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://example.invalid")
    with pytest.raises(ValueError):
        executor.execute(IMAGE, ARGV)


def fake_docker(tmp_path, code):
    executable = tmp_path / "docker"
    executable.write_text("#!" + sys.executable + "\n" + code)
    executable.chmod(0o700)
    return {"PATH": str(tmp_path)}


def test_real_cli_output_flood_is_bounded_and_process_reaped(tmp_path):
    env = fake_docker(tmp_path, "import os\nwhile True: os.write(1, b'x'*65536)\n")
    result = executor._cli([], env, time.monotonic() + 3, limit=1000)
    assert result.status == "output_exhausted"
    assert len(result.output) == 1000
    assert result.exit_code is not None


def test_real_cli_deadline_kills_silent_process(tmp_path):
    env = fake_docker(tmp_path, "import time\ntime.sleep(30)\n")
    started = time.monotonic()
    result = executor._cli([], env, started + 0.2)
    assert result.status == "timed_out"
    assert time.monotonic() - started < 2
    assert result.exit_code is not None


@pytest.mark.parametrize("extra", ["OPENAI_API_KEY=synthetic-sentinel", "BASH_ENV=/evil",
                                  "PYTHONPATH=/unexpected", "PATH=/unexpected"])
def test_image_inherited_environment_never_reaches_execution(monkeypatch, extra):
    engine = Engine(mutate=lambda d: d["Config"]["Env"].append(extra))
    assert execute(monkeypatch, engine).status == "policy_rejected"
    assert not engine.started
    assert engine.calls[-1][0] == "rm"


def test_safe_tool_environment_is_explicitly_set(monkeypatch):
    engine = Engine()
    assert execute(monkeypatch, engine).ok
    create = engine.calls[0]
    values = [create[index + 1] for index, arg in enumerate(create) if arg == "--env"]
    assert set(values) == {"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                           "HOME=/tmp", "TMPDIR=/tmp"}


@pytest.mark.parametrize("executable", ["sh", "/", "//bin/sh", "/../../bin/sh", "/bin/../bin/sh", "/bin/sh/"])
def test_executable_requires_normalized_absolute_container_path(executable):
    with pytest.raises(ValueError):
        executor.execute(IMAGE, [executable])
