"""Observe and repair deployment drift: python -m nightshift.deploy."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from nightshift.specloop_job import list_jobs

FAILURES = (RuntimeError, OSError, ValueError, subprocess.SubprocessError)


def default_runner(argv, *, cwd, env, timeout, text):
    return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, text=text,
                          capture_output=True, check=False)


@dataclass
class Context:
    cwd: Path = field(default_factory=Path.cwd)
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    head: str = ""
    daemon_state: Path = field(default_factory=lambda: Path.home() / ".nightshift")
    supervisor_state: Path = field(default_factory=lambda: Path.home() / ".specloop")
    uid: int = field(default_factory=os.getuid)
    monotonic: Callable = time.monotonic
    sleep: Callable = time.sleep
    inventory: Callable = list_jobs

    def run(self, runner, argv, *, env=None, check=True, timeout=120, text=True):
        result = runner(argv, cwd=self.cwd, env=self.env if env is None else env,
                        timeout=timeout, text=text)
        if check and result.returncode:
            raise RuntimeError(f"{shlex.join(argv)}: {result.stderr or result.stdout}")
        return result


def parse_options(argv, context):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--plugin-dir", default=context.env.get("NIGHTSHIFT_PLUGIN_DIR"))
    parser.add_argument("--reload-cmd", default="paseo plugin reload nightshift")
    parser.add_argument("--daemon-label", default="com.mattpolicastro.nightshift")
    parser.add_argument("--supervisor-label", default="com.mattpolicastro.specloop")
    args = parser.parse_args(argv)
    if args.force and not args.apply:
        parser.error("--force requires --apply")
    if args.plugin_dir == "":
        parser.error("--plugin-dir cannot be empty")
    try:
        args.reload_cmd = shlex.split(args.reload_cmd)
        if args.plugin_dir is not None:
            args.plugin_dir = Path(os.path.abspath(context.cwd / Path(args.plugin_dir).expanduser()))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    return args


def checkout(context, runner, base_branch, *, fetch):
    def git(*args):
        return context.run(runner, ["git", *args]).stdout.strip()

    unsafe = []
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        unsafe.append("dirty checkout")
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != base_branch:
        unsafe.append(f"wrong branch: {branch} (expected {base_branch})")
    for diagnostic in unsafe:
        print(f"checkout: {diagnostic}")
    git("rev-parse", "--verify", "@{upstream}")
    remote = git("config", "--get", f"branch.{branch}.remote")
    if not remote:
        raise RuntimeError("upstream remote unavailable")
    if fetch:
        git("fetch", "--", remote)
    ahead, behind = map(int, git("rev-list", "--left-right", "--count", "HEAD...@{upstream}").split())
    if ahead < 0 or behind < 0:
        raise ValueError("invalid upstream counts")
    if ahead:
        unsafe.append("diverged" if behind else "ahead of upstream")
    if unsafe:
        raise RuntimeError("; ".join(unsafe))
    print(f"checkout: {'behind upstream' if behind else 'current'}")
    return behind


def uv_environment(context):
    env = dict(context.env)
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX", "UV_PROJECT", "UV_WORKING_DIR",
                "UV_NO_DEV", "UV_NO_GROUP", "UV_NO_DEFAULT_GROUPS"):
        env.pop(key, None)
    env["UV_PROJECT_ENVIRONMENT"] = str(context.cwd / ".venv")
    return env


def environment(context, runner, *, apply):
    env = uv_environment(context)

    def probe():
        result = context.run(runner, ["uv", "sync", "--check", "--frozen", "--offline"],
                             env=env, check=False)
        diagnostic = (result.stdout + result.stderr).strip()
        # uv uses exit 1 for drift; other failures must not authorize repair.
        if result.returncode and not (result.returncode == 1 and
                "The environment is outdated;" in diagnostic):
            raise RuntimeError(f"environment probe: {diagnostic or 'failed without diagnostic'}")
        stale = bool(result.returncode) or not (context.cwd / ".venv").is_dir()
        print(f"environment: {'stale or missing' if stale else 'current'}")
        if diagnostic:
            print(diagnostic)
        return stale

    stale = probe()
    if stale and apply:
        context.run(runner, ["uv", "sync", "--frozen"], env=env)
        print("environment: frozen sync completed")
        stale = probe()
    return stale


def plugin_manifest(context, runner):
    prefix = "plugins/nightshift/"
    tree = context.run(runner, ["git", "ls-tree", "-rz", "HEAD", "--", "plugins/nightshift"], text=False).stdout
    manifest = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = os.fsdecode(name)
        relative = name.removeprefix(prefix)
        if (not name.startswith(prefix) or any(p in {"", ".", ".."} for p in relative.split("/"))
                or kind != "blob" or mode not in {"100644", "100755", "120000"}):
            raise ValueError(f"unsafe plugin entry: {name}")
        manifest[relative] = (mode, context.run(runner, ["git", "cat-file", "blob", oid], text=False).stdout)
    if not manifest:
        raise ValueError("committed plugin tree is empty")
    if any(str(parent) in manifest for name in manifest for parent in Path(name).parents):
        raise ValueError("plugin entry has a file or link as parent")
    return manifest


def plugin_differs(context, runner, destination, manifest):
    if not destination.is_dir():
        return True
    for name, (mode, data) in manifest.items():
        path = destination / name
        if any((destination / parent).is_symlink() for parent in Path(name).parents):
            return True
        if mode == "120000":
            if not path.is_symlink() or os.fsencode(os.readlink(path)) != data:
                return True
        elif (path.is_symlink() or not path.exists() or not stat.S_ISREG(path.stat().st_mode)
              or bool(path.stat().st_mode & 0o111) != (mode == "100755") or path.read_bytes() != data):
            return True

    def leaves(directory):
        for path in directory.iterdir():
            if path.is_dir() and not path.is_symlink():
                yield from leaves(path)
            else:
                yield path.relative_to(destination).as_posix()

    for name in leaves(destination):
        if name not in manifest:
            result = context.run(runner, ["git", "check-ignore", "--quiet", "--no-index", "--",
                                         f"plugins/nightshift/{name}"], check=False)
            if result.returncode == 1:
                return True
            if result.returncode:
                raise RuntimeError(f"plugin ignore probe: {result.stderr}")
    return False


def rollback_copies(state):
    return sorted(p for p in state.iterdir() if re.fullmatch(r"rollback-\d{20}-[0-9a-f]{32}", p.name)
                  and p.is_dir() and not p.is_symlink())


def reload_plugin(context, runner, destination, marker, command):
    if not command:
        print("plugin reload skipped: empty reload command; reload remains pending")
        return
    try:
        context.run(runner, command)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        copies = rollback_copies(marker.parent)
        if copies:
            aside = destination.with_name(f"{destination.name}.failed-{uuid.uuid4().hex}")
            print("Manual restore and reload (pending marker remains):")
            print(" && ".join(shlex.join(argv) for argv in (
                ["mv", "--", str(destination), str(aside)],
                ["mv", "--", str(copies[-1]), str(destination)], command)))
        else:
            print("No rollback available; retry reload:")
            print(shlex.join(command))
        raise
    marker.unlink()
    print("plugin: reload completed")


def plugin(context, runner, destination, *, apply, reload_cmd):
    root, resolved = context.cwd.resolve(), destination.resolve()
    if destination.is_symlink() or resolved.is_relative_to(root) or root.is_relative_to(resolved):
        raise ValueError(f"unsafe plugin destination: {destination}")
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"plugin destination is not a directory: {destination}")
    state = destination.parent / f".{destination.name}.nightshift-deploy"
    marker = state / "pending-reload"
    if state.is_symlink() or marker.is_symlink():
        raise ValueError("unsafe plugin deployment state")
    if marker.exists() and not marker.is_file():
        raise ValueError("plugin pending marker is not a file")
    if marker.exists():
        print("plugin installed but not reloaded")
    manifest = plugin_manifest(context, runner)
    stale = plugin_differs(context, runner, destination, manifest)
    print(f"plugin: {'differs from committed HEAD' if stale else 'current'}")
    if stale and apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{destination.name}.stage-", dir=destination.parent) as temporary:
            stage = (Path(temporary) / "plugin").resolve()
            stage.mkdir()
            for name, (mode, data) in manifest.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if mode == "120000":
                    path.symlink_to(os.fsdecode(data))
                else:
                    path.write_bytes(data)
                    path.chmod(0o755 if mode == "100755" else 0o644)
            for name, (mode, _) in manifest.items():
                if mode == "120000":
                    try:
                        (stage / name).resolve(strict=True)
                    except FileNotFoundError:
                        pass  # Dangling internal links are valid committed entries.
                    if not (stage / name).resolve().is_relative_to(stage):
                        raise ValueError(f"plugin link escapes staging: {name}")
            if plugin_differs(context, runner, stage, manifest):
                raise RuntimeError("staged plugin verification failed")
            state.mkdir(exist_ok=True)
            if not marker.exists():
                with marker.open("xb"):
                    pass
            if destination.exists():
                destination.rename(state / f"rollback-{time.time_ns():020d}-{uuid.uuid4().hex}")
            stage.rename(destination)
        copies = rollback_copies(state)
        for old in copies[:-3]:
            shutil.rmtree(old)
        print("plugin: committed tree installed")
    if apply and marker.exists():
        reload_plugin(context, runner, destination, marker, reload_cmd)
    return (stale and not apply) or marker.exists()


def read_object(text):
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def process_probe(context, runner, service, *, activity=False, deadline=None):
    def run(argv, **kwargs):
        remaining = 30 if deadline is None else deadline - context.monotonic()
        if remaining <= 0:
            raise RuntimeError("confirmation deadline elapsed")
        return context.run(runner, argv, timeout=remaining, **kwargs).stdout.strip()

    idle = False
    if service == "daemon":
        pid = read_object((context.daemon_state / "heartbeat.json").read_text()).get("pid")
    else:
        status = read_object(run(["uv", "run", "--no-sync", "specloop", "status", "--json"],
                                 env=uv_environment(context)))
        supervisor = status.get("supervisor")
        state = supervisor.get("state") if isinstance(supervisor, dict) else None
        match = re.fullmatch(r"(?:running \(pid ([1-9]\d*), last poll \d+s ago(?:, driving (.+))?\)|"
                             r"WEDGED \(pid ([1-9]\d*) alive, but no poll for \d+s\))",
                             state if isinstance(state, str) else "")
        if type(status.get("version")) is not int or status["version"] != 1 or not match:
            raise ValueError(f"unsupported supervisor status: {status}")
        pid = int(match[1] or match[3])
        if activity:
            driving = match[2]
            idle = driving == "nothing"
            try:
                heartbeat = read_object((context.supervisor_state / "serve.json").read_text())
                idle = (type(heartbeat.get("pid")) is int and heartbeat["pid"] == pid and heartbeat.get("driving") == []
                        and driving in (None, "nothing"))
            except FileNotFoundError:
                pass
            except FAILURES:
                idle = False
            rows = status.get("runs")
            idle = idle and isinstance(rows, list) and all(isinstance(row, dict) and
                row.get("status") in ("paused", "done", "aborted") and "error" not in row for row in rows)
    if type(pid) is not int or pid <= 0:
        raise ValueError(f"invalid {service} PID: {pid}")
    started = datetime.strptime(run(["/bin/ps", "-p", str(pid), "-o", "lstart="],
        env={**context.env, "LC_ALL": "C", "TZ": "UTC"}), "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
    committed = int(run(["git", "log", "-1", "--format=%ct", "HEAD", "--",
                         "nightshift/" if service == "daemon" else "specloop/"]))
    if activity and service == "daemon":
        try:
            jobs, diagnostics = context.inventory(context.daemon_state)
            idle = not diagnostics and all(job.state in {"finished", "failed", "stopped"} for job in jobs)
        except FAILURES:
            idle = False
    return pid, int(started.timestamp()) < committed, idle


def restart(context, runner, service, pid, command):
    errors = []
    diagnostic = "no new current PID"
    for killing in (True, False):
        try:
            context.run(runner, command[:2] + (["-k"] if killing else []) + command[2:], timeout=30)
        except FAILURES as exc:
            errors.append(str(exc))
            print(f"{service} restart command failed: {exc}")
        deadline = context.monotonic() + 30
        while context.monotonic() < deadline:
            try:
                new_pid, stale, _ = process_probe(context, runner, service, deadline=deadline)
            except FAILURES as exc:
                diagnostic = str(exc)
            else:
                if new_pid != pid and not stale and context.monotonic() <= deadline:
                    print(f"{service}: replacement confirmed (pid {new_pid})")
                    if errors:
                        raise RuntimeError("; ".join(errors))
                    return
                diagnostic = "no new current PID"
            context.sleep(min(1, max(0, deadline - context.monotonic())))
    raise RuntimeError(f"{service} restart failed: {'; '.join(errors) or diagnostic}")


def service(context, runner, name, label, *, apply, force):
    command = ["launchctl", "kickstart", f"gui/{context.uid}/{label}"]
    try:
        _, stale, _ = process_probe(context, runner, name)
        print(f"{name}: {'process older than code' if stale else 'current'}")
        if stale and apply:
            pid, stale, idle = process_probe(context, runner, name, activity=True)
            if stale and not idle and not force:
                print(f"{name} restart pending: busy or unknown activity")
            elif stale:
                restart(context, runner, name, pid, command)
                stale = False
        return stale
    except FAILURES:
        print(f"Manual {name} start: {shlex.join(command)}")
        raise


def main(argv=None, *, runner=default_runner, context=None) -> int:
    context = context or Context()
    args = parse_options(argv, context)
    if args.plugin_dir is None:
        print("plugin check skipped: no plugin directory configured")
    failures = FAILURES
    try:
        root = context.run(runner, ["git", "rev-parse", "--show-toplevel"]).stdout.strip()
        if not root:
            raise RuntimeError("checkout root unavailable")
        context.cwd = Path(root)
    except failures as exc:
        print(f"checkout unavailable: {exc}")
        return 1
    failed = False
    try:
        behind = checkout(context, runner, args.base_branch, fetch=True)
        if args.apply:
            behind = checkout(context, runner, args.base_branch, fetch=False)
            if behind:
                context.run(runner, ["git", "pull", "--ff-only"])
                print("checkout: fast-forward completed")
        else:
            failed = bool(behind)
        context.head = context.run(runner, ["git", "rev-parse", "HEAD"]).stdout.strip()
    except failures as exc:
        print(f"checkout refused: {exc}")
        if args.apply:
            return 1
        failed = True
    try:
        failed = environment(context, runner, apply=args.apply) or failed
    except failures as exc:
        print(exc)
        failed = True
    if args.apply and failed:
        return 1
    if args.plugin_dir is not None:
        try:
            failed = plugin(context, runner, args.plugin_dir, apply=args.apply,
                            reload_cmd=args.reload_cmd) or failed
        except failures as exc:
            print(f"plugin failed: {exc}")
            return 1
    for name in ("daemon", "supervisor"):
        try:
            failed = service(context, runner, name, getattr(args, f"{name}_label"),
                             apply=args.apply, force=args.force) or failed
        except failures as exc:
            print(f"{name} unavailable: {exc}")
            failed = True
            if args.apply:
                break
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
