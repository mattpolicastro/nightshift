"""Trusted verification of a sealed commit, under sandbox-exec.

Each step is a fixed argv from the manifest, run against a clean extract of
the commit (never the agent's work tree) with the source made read-only,
network denied, writes confined to the build directory and credential
directories unreadable. Agent-written build and test code runs here, so this
sandbox is what keeps it away from tokens and the network (spike S4).
Trusted prepare steps run first with network access, but the same read-only
source and credential restrictions, and no extra writable paths.

A receipt passes only when the exit status equals the expected one. A signal
or a timeout never satisfies an expected non-zero exit.
"""

import ctypes
import functools
import json
import os
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from nightshift.host_paths import tool_path
from specloop.agents import homes_dir
from specloop.manifest import Manifest, Step

CREDENTIAL_DIRS = (".codex", ".claude", ".specloop/homes", ".nightshift/homes",
                   ".ssh", ".config", ".aws", "Library/Keychains")


def _real_path(path: Path, links: frozenset[Path] = frozenset()) -> Path:
    """Resolve using metadata only; missing paths are OK, loops/errors are not.

    Path.resolve(strict=False) can suppress permission and loop errors on
    supported Python versions. Credential discovery must fail closed instead.
    """
    path = path.absolute()
    if path == path.parent:
        return path
    parent = _real_path(path.parent, links)
    path = parent / path.name
    if path.name == "..":
        return parent.parent
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return path
    if not stat.S_ISLNK(mode):
        return path
    if path in links or len(links) >= 40:
        raise ValueError(f"credential path symlink loop: {path}")
    return _real_path(parent / os.readlink(path), links | {path})


def _directories(root: Path, prefix: str = "") -> list[Path]:
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for child in children:
        if not child.name.startswith(prefix):
            continue
        resolved = _real_path(child)
        try:
            if stat.S_ISDIR(resolved.stat().st_mode):
                result.append(child)
        except FileNotFoundError:
            pass
    return result


def _credential_denials(m: Manifest) -> set[str]:
    home = Path.home()
    agent_roots = {home / ".specloop/homes", home / ".nightshift/homes", homes_dir()}
    codex_homes = {home / ".codex", *_directories(home, ".codex")}
    paths = {home / p for p in CREDENTIAL_DIRS} | agent_roots | codex_homes
    paths.update(p / "auth.json" for p in codex_homes)
    for root in agent_roots:
        paths.update(p / "auth.json" for p in _directories(root))
    # Also support manifests constructed directly rather than through parse().
    profiles = [*m.roles.values(), *(p for chain in m.chains.values() for p in chain)]
    paths.update(Path(p).expanduser() for p in (*m.auth_targets, *(p.auth for p in profiles)) if p)
    return {str(p) for path in paths for p in (path.absolute(), _real_path(path))}


@dataclass
class Receipt:
    id: str
    argv: list[str]
    expect: int
    exit: int | None  # None: timed out
    passed: bool
    seconds: float
    tail: str


def profile(build: Path, m: Manifest, *, prepare: bool = False, home: Path | None = None,
            src: Path | None = None) -> str:
    # The sandbox matches real paths: /tmp/x is /private/tmp/x to it.
    build = build.resolve()

    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    writable = " ".join(f"(literal {q(p)}) (subpath {q(p)})" for p in (() if prepare else m.writable))
    denied = set(m.deny_read) | _credential_denials(m)
    deny = " ".join(f"(subpath {q(p)})" for p in sorted(denied))
    reads = ""
    if prepare:
        # Prepare steps run candidate-controlled code (package lifecycle scripts)
        # with network access, so reads under $HOME are an allowlist: this
        # verification's own directory, the manifest's variables, and
        # [sandbox] prepare_read. Credential denials below still win. Later
        # rules take precedence in a sandbox profile.
        # The candidate's extracted source is readable too: for a mutant it sits
        # outside the mutant's own directory, and copying it is what prepare does.
        allowed = [str(build.parent), *([str(src.resolve())] if src else []),
                   *(v for v in m.vars.values() if v.startswith("/")), *m.prepare_read]
        reads = (f"(deny file-read* (subpath {q(str(home or Path.home()))}))\n(allow file-read-metadata)\n"
                 f"(allow file-read* {' '.join(f'(subpath {q(p)})' for p in allowed)})")
    return f"""(version 1)
(allow default)
{"" if prepare else "(deny network*)"}
(deny file-write*)
(allow file-write* (subpath {q(str(build))}) {writable}
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (regex #"^/dev/fd/"))
{reads}
{f"(deny file-read* {deny})" if deny else ""}
"""


@functools.cache
def already_sandboxed() -> bool:
    """True inside an enclosing sandbox, where sandbox-exec cannot nest.

    macOS refuses sandbox_apply in a sandboxed process unless the profile is
    identical, so a harness running inside specloop's own verification (its
    tests, verifying a specloop change) relies on the enclosing sandbox. The
    production supervisor never runs sandboxed, so it always applies its own.
    """
    try:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.sandbox_check.restype = ctypes.c_int
        lib.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        return lib.sandbox_check(os.getpid(), None, 0) == 1
    except (OSError, AttributeError):
        return False


def wrap(argv: list[str], sb: Path) -> list[str]:
    return argv if already_sandboxed() else ["sandbox-exec", "-f", str(sb), *argv]


def expand(arg: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        arg = arg.replace("{" + k + "}", v)
    return arg


def _make_writable(p: Path) -> None:
    if p.exists():
        subprocess.run(["chmod", "-R", "u+w", str(p)], check=False)


def extract(repo: Path, sha: str, src: Path) -> None:
    _make_writable(src)
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(repo), "archive", sha], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(src)], input=archive.stdout, check=True)
    subprocess.run(["chmod", "-R", "a-w", str(src)], check=True)


def run_steps(repo: Path, sha: str, steps: tuple[Step, ...], m: Manifest, out: Path) -> list[Receipt]:
    """Extract once, prepare dependencies, then run the requested offline checks."""
    src = out / "src"
    extract(repo, sha, src)
    return run_extracted(src, sha, steps, m, out)


def run_extracted(src: Path, sha: str, steps: tuple[Step, ...], m: Manifest, out: Path) -> list[Receipt]:
    """Prepare a fresh build from an already read-only source tree, then check it."""
    path = tool_path()
    build = out / "build"
    _make_writable(build)
    shutil.rmtree(build, ignore_errors=True)
    for d in ("home", "tmp"):
        (build / d).mkdir(parents=True)
    sb = out / "verify.sb"
    sb.write_text(profile(build, m))
    prep_sb = out / "prepare.sb"
    if m.prepare:
        prep_sb.write_text(profile(build, m, prepare=True, src=src))
    values = {**m.vars, "src": str(src), "build": str(build), "sha": sha}
    env = {"PATH": path, "HOME": str(build / "home"), "TMPDIR": str(build / "tmp"),
           "LANG": "en_US.UTF-8", "COPYFILE_DISABLE": "1"}
    receipts = []
    scheduled = [(s, prep_sb) for s in m.prepare] + [(s, sb) for s in steps]
    for step, step_sb in scheduled:
        argv = [expand(a, values) for a in step.run]
        log = out / f"{step.id}.log"
        t0 = time.monotonic()
        with open(log, "wb") as f:
            p = subprocess.Popen(wrap(argv, step_sb), stdout=f, stderr=subprocess.STDOUT,
                                 env=env, cwd=build, start_new_session=True)
            try:
                code = p.wait(timeout=step.timeout)
            except subprocess.TimeoutExpired:
                code = None
            # Nothing a step started outlives it: a prepare step's background
            # process would otherwise keep network access into the verify steps.
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
        tail = log.read_bytes()[-2000:].decode(errors="replace")
        passed = code is not None and code >= 0 and code == step.expect
        receipts.append(Receipt(step.id, argv, step.expect, code, passed, round(time.monotonic() - t0, 2), tail))
        if not passed:
            break
    (out / "receipts.json").write_text(json.dumps([asdict(r) for r in receipts], indent=2))
    return receipts


def ok(receipts: list[Receipt], steps: tuple[Step, ...]) -> bool:
    return len(receipts) == len(steps) and all(r.passed for r in receipts)


def summary(receipts: list[Receipt]) -> str:
    lines = []
    for r in receipts:
        got = "timeout" if r.exit is None else r.exit
        lines.append(f"- {r.id}: expected exit {r.expect}, got {got} ({'pass' if r.passed else 'FAIL'}, {r.seconds}s)")
    bad = next((r for r in receipts if not r.passed), None)
    if bad:
        lines.append(f"\nOutput tail of `{bad.id}`:\n```\n{bad.tail[-1500:]}\n```")
    return "\n".join(lines)


def cleanup(out: Path) -> None:
    """Drop the extracted source and build tree; keep logs, receipts and build/artifacts."""
    if (out / "build" / "artifacts").exists():
        shutil.rmtree(out / "artifacts", ignore_errors=True)
        shutil.move(out / "build" / "artifacts", out / "artifacts")
    for d in ("src", "build"):
        _make_writable(out / d)
        shutil.rmtree(out / d, ignore_errors=True)
