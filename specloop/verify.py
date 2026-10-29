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
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock

from nightshift.host_paths import tool_path
from specloop.agents import homes_dir
from specloop.manifest import Manifest, Snapshot, Step, expand, validate_env, validate_loopback

CREDENTIAL_DIRS = (".codex", ".claude", ".specloop/homes", ".nightshift/homes",
                   ".ssh", ".config", ".aws", "Library/Keychains")


@dataclass
class SnapshotContext:
    root: Path
    event: Callable[..., None] = lambda *args, **kwargs: None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def emit(self, kind: str, **data) -> None:
        with self._lock:
            self.event(kind, **data)


def _payload_paths(root: Path, *, readonly: bool = False) -> list[Path]:
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("payload must be a directory")
    paths = [root]
    for path in paths:
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            paths.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("payload contains a link or special file")
        if readonly and info.st_mode & 0o222:
            raise ValueError("entry is writable")
    return paths


def _snapshot_entries(root: Path, *, strict_io: bool = False) -> list[tuple[Path, dict]]:
    result = []
    for entry in (root / "entries").iterdir():
        try:
            _payload_paths(entry, readonly=True)
            data = json.loads((entry / "metadata.json").read_text())
            if (data["schema"] == 1 and type(data["sequence"]) is int and data["sequence"] > 0
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", data["id"])
                    and re.fullmatch(r"[0-9a-f]{64}", data["key"])
                    and re.fullmatch(r"[0-9a-f]{40,64}", data["base_sha"])
                    and entry.name == f'{data["sequence"]}-{data["id"]}-{data["key"]}'):
                _payload_paths(entry / "payload", readonly=True)
                result.append((entry, data))
        except OSError:
            if strict_io:  # Retention cannot establish its bound from an incomplete scan.
                raise
        except (ValueError, KeyError, TypeError):
            continue
    return result


def _remove_snapshot_tree(root: Path) -> None:
    # Restore directory access without following or chmodding symlinks.
    pending = [root]
    for parent in pending:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("snapshot cleanup target must be a directory")
        parent.chmod(parent.lstat().st_mode | 0o700)
        pending.extend(p for p in parent.iterdir() if stat.S_ISDIR(p.lstat().st_mode))
    shutil.rmtree(root)


def _retain_snapshots(context: SnapshotContext) -> None:
    """Recover recorded deletions and keep three publications; caller holds run lock."""
    root = context.root
    try:
        # Record validated deletion intent before chmod/rmtree can invalidate an
        # entry. A crash at any later point leaves this marker for the next sync.
        for marker in (root / "entries").glob(".prune-*"):
            name = marker.name.removeprefix(".prune-")
            if (re.fullmatch(r"[1-9][0-9]*-[A-Za-z0-9_-]+-[0-9a-f]{64}", name)
                    and stat.S_ISREG(marker.lstat().st_mode)):
                entry = root / "entries" / name
                if entry.is_symlink() or entry.exists():
                    _remove_snapshot_tree(entry)
                marker.unlink()
        for entry, _ in sorted(_snapshot_entries(root, strict_io=True), key=lambda item: item[1]["sequence"])[:-3]:
            marker = root / "entries" / f".prune-{entry.name}"
            marker.touch(exist_ok=False)
            _remove_snapshot_tree(entry)
            marker.unlink()
    except (OSError, ValueError) as exc:
        context.emit("snapshot_retention_failed", error=str(exc), root=str(root))


def warm_snapshots(repo: Path, base_sha: str, m: Manifest, context: SnapshotContext) -> None:
    """Warm only the caller's pinned trusted base, under the run lock; never prepare."""
    if not m.snapshots:
        return
    root = context.root.resolve()
    (root / "entries").mkdir(parents=True, exist_ok=True)
    _retain_snapshots(context)
    for old in root.glob(".staging-*"):
        if re.fullmatch(r"\.staging-[0-9a-f]{32}", old.name) and stat.S_ISDIR(old.lstat().st_mode):
            _remove_snapshot_tree(old)
    for definition in m.snapshots:
        attempt = uuid.uuid4().hex
        stage, out = root / f".staging-{attempt}", root / "warms" / attempt
        out.mkdir(parents=True)
        key = None
        details = dict(id=definition.id, base_sha=base_sha, diagnostics=str(out))
        try:
            src, build = stage / "src", stage / "build"
            extract(repo, base_sha, src)
            key = snapshot_key(src, definition)
            if key is None:
                raise ValueError("lockfile key unavailable")
            if any(d["id"] == definition.id and d["key"] == key for _, d in _snapshot_entries(root)):
                context.emit("snapshot_reused", key=key, **details)
                continue
            for name in ("home", "tmp", "payload"):
                (build / name).mkdir(parents=True)
            sb = out / "prepare.sb"
            sb.write_text(profile(build, m, prepare=True, src=src, snapshots=context))
            values = {**m.vars, "src": str(src), "build": str(build), "sha": base_sha,
                      f"snapshot:{definition.id}": str(build / "payload")}
            receipts = _run_commands([(Step("warm", definition.warm, 0, 600), sb)], values, build, out, tool_path())
            if not receipts[0].passed:
                raise ValueError("warm command failed")
            _payload_paths(build / "payload")
            entry = stage / "entry"
            entry.mkdir()
            (build / "payload").rename(entry / "payload")
            sequence = max((int(p.name.split("-", 1)[0]) for p in (root / "entries").iterdir()
                            if re.fullmatch(r"[1-9][0-9]*-[A-Za-z0-9_-]+-[0-9a-f]{64}", p.name)), default=0) + 1
            metadata = dict(schema=1, id=definition.id, key=key, base_sha=base_sha, sequence=sequence)
            (entry / "metadata.json").write_text(json.dumps(metadata))
            for payload_path in reversed(_payload_paths(entry)[1:]):
                payload_path.chmod(payload_path.stat().st_mode & ~0o222)
            # macOS needs rename write permission; locked readers reject unfinished entries.
            published = entry.rename(root / "entries" / f"{sequence}-{definition.id}-{key}")
            published.chmod(published.stat().st_mode & ~0o222)
            context.emit("snapshot_warmed", key=key, **details)
        except BaseException as exc:
            (out / "failure.txt").write_text(str(exc))
            context.emit("snapshot_warm_failed", key=key, **details)
            if not isinstance(exc, (OSError, ValueError, subprocess.SubprocessError)):
                raise
        else:
            _retain_snapshots(context)


def snapshot_key(src: Path, definition: Snapshot) -> str | None:
    """Hash framed raw lockfiles; unavailable or symlink-traversing inputs miss."""
    digest = hashlib.sha256(b"specloop-snapshot-v1\0")
    try:
        for path in sorted(definition.lockfiles):
            parts = path.split("/")
            if any(part in ("", ".", "..") for part in parts):
                return None
            with ExitStack() as stack:
                parent = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, parent)
                for part in parts[:-1]:
                    parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    stack.callback(os.close, parent)
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                stack.callback(os.close, fd)
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return None
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    content = stream.read()
            for value in (path.encode("utf-8"), content):
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
    except (OSError, ValueError):
        return None
    return digest.hexdigest()


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
        mode = os.lstat(path).st_mode
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


def _listening_loopback_ports() -> frozenset[int]:
    """Take a bounded numeric listener snapshot; uncertain output fails closed."""
    try:
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"],
                                capture_output=True, encoding="utf-8", errors="strict", timeout=5)
        if result.returncode == 1 and not result.stdout and not result.stderr:
            return frozenset()
        if result.returncode != 0 or result.stderr:
            raise ValueError(f"lsof exit {result.returncode}: {result.stderr.strip()}")
        if result.stdout and not result.stdout.endswith("\n"):
            raise ValueError("incomplete lsof output")
        ports: set[int] = set()
        state = "process"
        for line in result.stdout.split("\n")[:-1]:
            if re.fullmatch(r"p[0-9]+", line) and state in ("process", "complete"):
                state = "file"
                continue
            # lsof emits file-set identifiers even when only p/n are selected.
            if re.fullmatch(r"f[0-9]+", line) and state in ("file", "complete"):
                state = "name"
                continue
            match = re.fullmatch(r"n(?:\[([^\[\]\s]+)\]|([^\[\]\s]+)):([0-9]+)", line)
            if state != "name" or match is None:
                raise ValueError("malformed lsof process/file/listener record")
            host, port = match[1] or match[2], int(match[3])
            if not 1 <= port <= 65535:
                raise ValueError("invalid listener port")
            address = None if host == "*" else ipaddress.ip_address(host)
            if match[1] and not isinstance(address, ipaddress.IPv6Address):
                raise ValueError("brackets require an IPv6 listener address")
            mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
            if address is None or address.is_loopback or address.is_unspecified or (mapped and mapped.is_loopback):
                ports.add(port)
            state = "complete"
        if state not in ("process", "complete"):
            raise ValueError("incomplete lsof process/file record")
        return frozenset(ports)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"loopback listener discovery failed: {exc}") from exc


def profile(build: Path, m: Manifest, *, prepare: bool = False, home: Path | None = None,
            src: Path | None = None, snapshots: SnapshotContext | None = None,
            loopback: bool = False, listening_ports: frozenset[int] | None = None) -> str:
    # The sandbox matches real paths: /tmp/x is /private/tmp/x to it.
    build = build.resolve()
    network = "" if prepare else "(deny network*)"
    if not prepare and loopback:
        if listening_ports is None or any(type(p) is not int or not 1 <= p <= 65535 for p in listening_ports):
            raise ValueError("loopback profile requires a successful listener snapshot with valid ports")
        network += '\n(allow network-inbound (local tcp "localhost:*"))'
        network += '\n(allow network-outbound (remote tcp "localhost:*"))'
        network += "".join(f'\n(deny network-outbound (remote tcp "localhost:{p}"))' for p in sorted(set(listening_ports)))

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
{network}
(deny file-write*)
(allow file-write* (subpath {q(str(build))}) {writable}
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (regex #"^/dev/fd/"))
{reads}
{f"(deny file-read* {deny})" if deny else ""}
""" + (f'(deny file-write* (subpath {q(str((snapshots.root / "entries").resolve()))}))\n' if snapshots else "")


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


@functools.cache
def _clone_api():
    clone = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    clone.restype = ctypes.c_int
    return clone


def _clone_payload(payload: Path, destination: Path) -> None:
    clone = _clone_api()
    for source in _payload_paths(payload, readonly=True):
        target = destination / source.relative_to(payload)
        mode = source.stat().st_mode
        if stat.S_ISDIR(mode):
            target.mkdir()
        elif clone(os.fsencode(source), os.fsencode(target), 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
        target.chmod(stat.S_IMODE(mode) | stat.S_IWUSR)


def _consume_snapshots(src: Path, sha: str, m: Manifest, out: Path,
                       context: SnapshotContext | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not m.snapshots:
        return values
    with (out / "snapshot-events.jsonl").open("w") as events:
        for definition in m.snapshots:
            destination = (out / "build" / "snapshots" / definition.id).resolve()
            destination.parent.mkdir(exist_ok=True)
            values[f"snapshot:{definition.id}"] = str(destination)
            key, payload = snapshot_key(src, definition), None
            reason = "context_unavailable" if context is None else "key_unavailable"
            if context is not None and key is not None:
                try:
                    entries = list((context.root / "entries").iterdir())
                    valid = _snapshot_entries(context.root)
                    payload = next((p / "payload" for p, d in valid
                                    if d["id"] == definition.id and d["key"] == key), None)
                    reason = ("invalid" if any(re.fullmatch(rf"[1-9][0-9]*-{re.escape(definition.id)}-{key}", p.name) for p in entries)
                              else "stale" if any(d["id"] == definition.id for _, d in valid) else "missing")
                except FileNotFoundError:
                    reason = "missing"
                except (OSError, ValueError):
                    reason = "invalid"
            if payload is not None:
                try:
                    _clone_payload(payload, destination)
                    continue
                except (OSError, AttributeError, ValueError):
                    reason = "clone_failed"
            data = dict(id=definition.id, key=key, sha=sha, reason=reason, out=str(out))
            events.write(json.dumps(dict(event="snapshot_fallback", **data)) + "\n")
            events.flush()
            if context is not None:
                context.emit("snapshot_fallback", **data)
            # Never run commands with uncertain partial output. Cleanup errors propagate.
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir()
    return values


def run_steps(repo: Path, sha: str, steps: tuple[Step, ...], m: Manifest, out: Path, *,
              snapshots: SnapshotContext | None = None) -> list[Receipt]:
    """Extract once, prepare dependencies, then run the requested offline checks."""
    src = out / "src"
    extract(repo, sha, src)
    if snapshots is None:
        return run_extracted(src, sha, steps, m, out)
    return run_extracted(src, sha, steps, m, out, snapshots=snapshots)


def run_extracted(src: Path, sha: str, steps: tuple[Step, ...], m: Manifest, out: Path, *,
                  snapshots: SnapshotContext | None = None) -> list[Receipt]:
    """Prepare a fresh build from an already read-only source tree, then check it."""
    path = tool_path()
    build = out / "build"
    _make_writable(build)
    shutil.rmtree(build, ignore_errors=True)
    for d in ("home", "tmp"):
        (build / d).mkdir(parents=True)
    sb = out / "verify.sb"
    sb.write_text(profile(build, m, snapshots=snapshots))
    prep_sb = out / "prepare.sb"
    if m.prepare:
        prep_sb.write_text(profile(build, m, prepare=True, src=src, snapshots=snapshots))
    values = {**m.vars, "src": str(src), "build": str(build), "sha": sha,
              **_consume_snapshots(src, sha, m, out, snapshots)}
    scheduled = [(s, prep_sb) for s in m.prepare] + [(s, sb) for s in steps]
    return _run_commands(scheduled, values, build, out, path)


def _run_commands(scheduled: list[tuple[Step, Path]], values: dict[str, str],
                  build: Path, out: Path, path: str) -> list[Receipt]:
    base_env = {"PATH": path, "HOME": str(build / "home"), "TMPDIR": str(build / "tmp"),
                "LANG": "en_US.UTF-8", "COPYFILE_DISABLE": "1"}
    receipts = []
    for step, step_sb in scheduled:
        validate_loopback(step.loopback, f"step {step.id!r}")
        configured = validate_env(step.env, f"step {step.id!r}")
        expanded = {key: expand(value, values) for key, value in configured.items()}
        env = {**base_env, **validate_env(expanded, f"step {step.id!r}")}
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
            finally:
                # Descendants must not retain network access or mutate a payload.
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
