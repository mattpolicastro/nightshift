"""When a usage-limited provider will be usable again.

For Codex on a ChatGPT login this asks `codex app-server` over its read-only
JSON-RPC (initialize, then account/rateLimits/read; no thread or turn is ever
created). For anything else, or
when the read fails, the answer is a fixed back-off.
"""

import json
import os
import select
import subprocess
import time
from pathlib import Path

from nightshift.host_paths import tool_path

FALLBACK_SECONDS = 30 * 60


def codex_limits(home: Path, timeout: float = 20) -> dict | None:
    env = {"HOME": str(Path.home()), "PATH": tool_path("/usr/bin:/bin"), "CODEX_HOME": str(home)}
    try:
        child = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, env=env, bufsize=0)
    except OSError:
        return None
    buffer = b""

    def send(method, ident=None, params=None):
        msg = {"method": method, **({"id": ident} if ident is not None else {}),
               **({"params": params} if params is not None else {})}
        child.stdin.write((json.dumps(msg) + "\n").encode())
        child.stdin.flush()

    def receive(ident, deadline):
        nonlocal buffer
        while time.monotonic() < deadline:
            if b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("id") == ident:
                    return msg.get("result")
                continue
            ready, _, _ = select.select([child.stdout], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                return None
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                return None
            buffer += chunk
        return None

    try:
        deadline = time.monotonic() + timeout
        send("initialize", 1, {"clientInfo": {"name": "specloop", "title": "specloop", "version": "1"}})
        if receive(1, deadline) is None:
            return None
        send("initialized", params={})
        send("account/rateLimits/read", 2)
        return receive(2, deadline)
    except (OSError, ValueError):
        return None
    finally:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()


def resume_at(limits: dict | None, now: float | None = None) -> float:
    """Epoch seconds when the exhausted window resets; a back-off if unknown."""
    now = now or time.time()
    bucket = (limits or {}).get("rateLimits") or {}
    windows = [w for w in (bucket.get("primary"), bucket.get("secondary")) if isinstance(w, dict)]
    full = [w["resetsAt"] for w in windows
            if isinstance(w.get("resetsAt"), (int, float)) and w.get("usedPercent", 0) >= 100]
    if not full and bucket.get("rateLimitReachedType") is not None:
        full = [w["resetsAt"] for w in windows if isinstance(w.get("resetsAt"), (int, float))]
    future = [t for t in full if t > now]
    return max(future) + 60 if future else now + FALLBACK_SECONDS


def headroom(limits: dict | None) -> list[tuple[float, int]]:
    """(used percent, resets at) per window, for status display."""
    bucket = (limits or {}).get("rateLimits") or {}
    return [(w.get("usedPercent", 0), w.get("resetsAt", 0))
            for w in (bucket.get("primary"), bucket.get("secondary")) if isinstance(w, dict)]
