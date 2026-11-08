"""The OpenSpec CLI, used only for validate / show / archive.

Spike S5 (2026-10-19) found three things this module exists to absorb:
- `archive --yes` archives a change whose tasks are unfinished, so the caller
  must gate on tasks itself; nothing in the JSON says they were unfinished.
- telemetry is on by default (PostHog); OPENSPEC_TELEMETRY=0 stops it.
- a `### Scenario:` header (three hashes) is silently dropped, and near-
  duplicate scenario names validate. Both are linted here instead.
"""

import json
import os
import re
import subprocess
import threading
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from nightshift.host_paths import host_path

VERSION = "1.16.1"
ENV = {"OPENSPEC_TELEMETRY": "0", "OPENSPEC_NO_UPDATE_CHECK": "1", "DO_NOT_TRACK": "1"}
_checked_paths: set[str] = set()
_version_lock = threading.Lock()


class SpecError(RuntimeError):
    pass


class ValidatorUnavailable(SpecError):
    """The validator could not run because of a transient infrastructure failure."""


def resolve_binary(env: Mapping[str, str] | None = None) -> str:
    """Select a literal trusted host path; never search PATH or inspect the file."""
    env = os.environ if env is None else env
    try:
        if "NIGHTSHIFT_OPENSPEC_BIN" in env:
            return host_path("NIGHTSHIFT_OPENSPEC_BIN", "", env)
        return host_path("NIGHTSHIFT_BIN_DIR", "/opt/homebrew/bin", env).rstrip("/") + "/openspec"
    except ValueError as e:
        raise SpecError(str(e)) from e


def _install_hint(binary: str) -> str:
    return f"install OpenSpec {VERSION} at {binary}; see docs/deploy.md#install-openspec"


def _process(repo: Path, argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            cwd=repo, env=env, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        raise ValidatorUnavailable(f"{' '.join(argv)}: timed out after {e.timeout} seconds") from e
    except UnicodeDecodeError as e:
        raise SpecError(f"cannot decode OpenSpec output; {_install_hint(argv[0])}") from e
    except OSError as e:
        raise SpecError(f"cannot launch OpenSpec: {e}; {_install_hint(argv[0])}") from e


def _run(repo: Path, *args: str) -> tuple[int, dict]:
    env = {**os.environ, **ENV}
    binary = resolve_binary(env)
    # Cache only success. Serialize first use so concurrent callers share one probe.
    # Replacing a checked binary at the same path requires a consumer restart.
    with _version_lock:
        if binary not in _checked_paths:
            probe = _process(repo, [binary, "--version"], env)
            if probe.returncode != 0 or probe.stdout.strip() != VERSION:
                raise SpecError(f"OpenSpec version check failed; {_install_hint(binary)}")
            _checked_paths.add(binary)
    p = _process(repo, [binary, *args, "--json"], env)
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SpecError(f"openspec {' '.join(args)}: exit {p.returncode}, no JSON: {p.stderr.strip()[:400]}")


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def change_dir(repo: Path, change: str) -> Path:
    return repo / "openspec" / "changes" / change


def validate(repo: Path, change: str) -> list[str]:
    """Problems that block admission; empty when the change is admissible."""
    code, out = _run(repo, "validate", change, "--strict")
    problems = [f"{i['path']}: {i['message']}" for item in out.get("items", []) for i in item.get("issues", [])]
    problems += [s["message"] for s in out.get("status", [])]
    if code != 0 and not problems:
        problems.append(f"openspec validate exited {code}")
    for spec in (change_dir(repo, change) / "specs").rglob("spec.md"):
        for n, line in enumerate(spec.read_text().splitlines(), 1):
            if re.match(r"^#{1,3}\s+Scenario:", line):
                problems.append(f"{spec.relative_to(repo)}:{n}: scenario needs exactly four '#' (OpenSpec drops it silently)")
    return problems


def scenarios(repo: Path, change: str) -> dict[str, str]:
    """slug id -> scenario text, for every scenario the change adds or modifies."""
    code, out = _run(repo, "show", change)
    if code != 0:
        raise SpecError(f"openspec show {change}: {out.get('status')}")
    found: dict[str, str] = {}
    for delta in out.get("deltas", []):
        if delta.get("operation") == "REMOVED":
            continue
        reqs = delta.get("requirements") or ([delta["requirement"]] if delta.get("requirement") else [])
        for req in reqs:
            for sc in req.get("scenarios", []):
                sid = "/".join(slug(x) for x in (delta["spec"], req["name"], sc["name"]))
                if sid in found:
                    raise SpecError(f"duplicate scenario id {sid}: rename one of them")
                found[sid] = f"{req['name']} — {sc['name']}\n{sc.get('rawText', '')}"
    return found


def archive(repo: Path, change: str, all_tasks_done: bool) -> dict:
    if not all_tasks_done:
        raise SpecError("refusing to archive: tasks are unfinished (openspec --yes would archive anyway)")
    code, out = _run(repo, "archive", change, "--yes")
    if code != 0 or not out.get("archive"):
        raise SpecError(f"openspec archive {change}: {out.get('status')}")
    return out["archive"]
