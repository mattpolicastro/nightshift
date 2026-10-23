"""Observe and repair deployment drift: python -m nightshift.deploy."""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def default_runner(argv, *, cwd, env, timeout, text):
    return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, text=text,
                          capture_output=True, check=False)


@dataclass
class Context:
    cwd: Path = field(default_factory=Path.cwd)
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    head: str = ""

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
            args.plugin_dir = (context.cwd / Path(args.plugin_dir).expanduser()).resolve()
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


def environment(context, runner, *, apply):
    env = dict(context.env)
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX", "UV_PROJECT", "UV_WORKING_DIR",
                "UV_NO_DEV", "UV_NO_GROUP", "UV_NO_DEFAULT_GROUPS"):
        env.pop(key, None)
    env["UV_PROJECT_ENVIRONMENT"] = str(context.cwd / ".venv")

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


def main(argv=None, *, runner=default_runner, context=None) -> int:
    context = context or Context()
    args = parse_options(argv, context)
    if args.plugin_dir is None:
        print("plugin check skipped: no plugin directory configured")
    if not args.reload_cmd:
        print("plugin reload skipped: empty reload command")
    failures = (RuntimeError, OSError, ValueError, subprocess.SubprocessError)
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
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
