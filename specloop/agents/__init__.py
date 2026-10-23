"""Provider adapters behind one call.

Each adapter turns (profile, access, prompt, schema) into an argv and an
environment, and reads the CLI's own event stream for the session id, the
structured result and the failure class. Everything else — process groups,
timeouts, idle detection, transcripts — is shared here.

Nothing an agent says about its own environment is trusted (spike S1: a model
reported results for sandbox probes it never ran). The harness validates the
output itself; the sandbox guarantees that matter are enforced by seal/verify.
"""

import json
import os
import signal
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from nightshift.host_paths import tool_path
from specloop.manifest import Profile


@dataclass
class Result:
    disposition: str  # ok | infra | usage_limit | invalid_output
    output: dict | None = None
    session: str = ""
    detail: str = ""
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Call:
    profile: Profile
    access: str  # "write" | "read"
    prompt: str
    cwd: Path
    schema: dict
    transcript: Path
    wall_seconds: int
    idle_seconds: int
    home: Path  # adapter-private config dir (CODEX_HOME / CLAUDE_CONFIG_DIR)
    secrets: dict = field(default_factory=dict)  # e.g. CLAUDE_CODE_OAUTH_TOKEN
    read_dirs: tuple = ()  # outside cwd but readable by the agent: the task's context directory


def homes_dir() -> Path:
    return Path.home() / ".specloop" / "homes"


def reachable(base_url: str) -> bool:
    """Local backends get a cheap liveness check before any dispatch."""
    if not base_url:
        return True
    for path in ("/api/tags", "/v1/models", "/health"):
        try:
            with urllib.request.urlopen(base_url.rstrip("/").removesuffix("/v1") + path, timeout=5):
                return True
        except Exception:
            continue
    return False


def spawn(argv: list[str], env: dict, call: Call, stdin: str | None = None) -> tuple[int | None, str]:
    """Run in its own process group; kill the group on wall or idle timeout.

    Returns (exit code or None on timeout, reason). The transcript is the
    CLI's stdout, written as it arrives so `specloop status` can watch it.
    """
    call.transcript.parent.mkdir(parents=True, exist_ok=True)
    with open(call.transcript, "wb") as out, open(call.transcript.with_suffix(".stderr"), "wb") as err:
        p = subprocess.Popen(argv, cwd=call.cwd, env=env, stdout=out, stderr=err,
                             stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             start_new_session=True)
        if stdin is not None:
            p.stdin.write(stdin.encode())
            p.stdin.close()
        start = last = time.monotonic()
        size = 0
        while True:
            try:
                return p.wait(timeout=5), ""
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            grown = call.transcript.stat().st_size
            if grown != size:
                size, last = grown, now
            reason = ("wall" if now - start > call.wall_seconds
                      else "idle" if now - last > call.idle_seconds else "")
            if reason:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                return None, f"{reason} timeout"


def validate(output, schema: dict) -> str | None:
    """A small subset of JSON Schema: enough for our own flat schemas."""
    def check(v, s, where):
        t = s.get("type")
        types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
        if t and not isinstance(v, types[t]) or (t == "integer" and isinstance(v, bool)):
            return f"{where}: expected {t}"
        if "enum" in s and v not in s["enum"]:
            return f"{where}: {v!r} not in {s['enum']}"
        if t == "object":
            for k in s.get("required", []):
                if k not in v:
                    return f"{where}: missing {k}"
            for k, sub in s.get("properties", {}).items():
                if k in v and (e := check(v[k], sub, f"{where}.{k}")):
                    return e
        if t == "array":
            for i, item in enumerate(v):
                if e := check(item, s.get("items", {}), f"{where}[{i}]"):
                    return e
        return None

    return check(output, schema, "$")


def run(call: Call) -> Result:
    from specloop.agents import claude, codex

    adapter = {"codex": codex, "claude": claude}[call.profile.adapter]
    if not reachable(call.profile.base_url):
        return Result("infra", detail=f"{call.profile.base_url} unreachable")
    result = adapter.run(call)
    if result.disposition == "ok":
        if (err := validate(result.output, call.schema)):
            return Result("invalid_output", result.output, result.session, err, result.usage)
    return result


def base_env(home_var: str, home: Path, **extra) -> dict:
    env = {"PATH": tool_path(), "HOME": str(Path.home()), "LANG": "en_US.UTF-8", home_var: str(home)}
    env.update({k: v for k, v in extra.items() if v})
    return env


def read_jsonl(path: Path):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
