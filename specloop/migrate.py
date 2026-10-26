"""Internal migration staging: identity and read-only preflight, before relocation.

The yielded intent is JSON-serializable. A relocation caller must persist it
before its first mutation and stay inside preflight to retain every lock.
There is deliberately no CLI/startup activation or relocation in this stage.
"""

from contextlib import ExitStack, contextmanager
import fcntl
import json
from pathlib import Path
import stat

from specloop import state
from specloop.state_locks import coordination, journal_path, serve_lock

MAPPING = {"runs": "runs", "proposals": "drafts", "homes": "homes"}
MARKER = ".migrated-to-nightshift-v1.json"


class Refusal(RuntimeError):
    """Unsafe or ambiguous evidence; nothing may be relocated."""


def _metadata(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _directory(path):
    info = _metadata(path)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise Refusal(f"Expected a real directory: {path}")


def _device(path):
    while _metadata(path) is None:
        path = path.parent
    _directory(path)
    return path.stat().st_dev


def _identity(path):
    info = path.lstat()
    return {"device": info.st_dev, "inode": info.st_ino}


def _matches(path, entry):
    return _metadata(path) is not None and _identity(path) == {
        key: entry[key] for key in ("device", "inode")}


def _intent(source, destination):
    identity = {"version": 1, "source_root": str(source), "destination_root": str(destination)}
    path = journal_path()
    if _metadata(path) is None:
        return {**identity, "entries": []}
    if not stat.S_ISREG(path.lstat().st_mode):
        raise Refusal(f"Invalid migration journal: {path}")
    try:
        intent = json.loads(path.read_text())
        if (type(intent["version"]) is not int or not isinstance(intent["entries"], list) or
                any(intent[key] != value for key, value in identity.items())):
            raise ValueError("root/version mismatch")
        seen = set()
        for entry in intent["entries"]:
            src, dst = Path(entry["source"]), Path(entry["destination"])
            if (len(src.parts) != 2 or src.parts[0] not in MAPPING or
                    src.name in (".", "..") or str(src) != entry["source"] or
                    dst != Path(MAPPING[src.parts[0]]) / src.name or
                    str(dst) != entry["destination"] or str(src) in seen or
                    any(type(entry[k]) is not int or entry[k] < 0 for k in ("device", "inode"))):
                raise ValueError("invalid entry identity")
            seen.add(str(src))
        return intent
    except (ValueError, TypeError, KeyError) as exc:
        raise Refusal(f"Invalid migration journal: {path}") from exc


@contextmanager
def preflight(owned=None):
    """Hold serve → exclusive coordination → sorted existing run lock inodes.

    Only stable global lock files may be created. No intent, entry, per-run
    lock, destination directory or success marker is written by preflight.
    """
    source, destination = state.legacy_root(), state.state_root()
    for root in (source, destination):
        _directory(root)
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise Refusal(f"Overlapping migration roots: {source} and {destination}")
    try:
        with serve_lock(owned), coordination(exclusive=True), ExitStack() as locks:
            intent = _intent(source, destination)
            recorded = {entry["source"]: entry for entry in intent["entries"]}
            candidates = dict(recorded)
            for old, new in MAPPING.items():
                _directory(source / old)
                _directory(destination / new)
                if (source / old).exists():
                    for path in (source / old).iterdir():
                        candidates.setdefault(f"{old}/{path.name}", {
                            "source": f"{old}/{path.name}", "destination": f"{new}/{path.name}",
                            "phase": "intent", "symlinks": [],
                        })
            run_paths = set()
            for entry in candidates.values():
                if entry["source"].startswith("runs/"):
                    run_paths.add(source / entry["source"])
                    if entry["source"] in recorded:
                        run_paths.add(destination / entry["destination"])
            for path in sorted(run_paths, key=lambda p: (p.name, str(p))):
                if path.is_symlink() or _metadata(path) is None:
                    continue
                lock = path / "lock"
                info = _metadata(lock)
                if info is None:
                    continue  # No inode exists for an old driver to own; do not create one.
                if not stat.S_ISREG(info.st_mode):
                    raise Refusal(f"Unsupported run lock: {lock}")
                handle = locks.enter_context(lock.open("rb"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Refusal(f"Run {path.name} is locked at {lock}; pause/stop its driver and retry") from exc
            entries = []
            for relative, entry in sorted(candidates.items()):
                src, dst = source / relative, destination / entry["destination"]
                src_info, dst_info = _metadata(src), _metadata(dst)
                linked = src.is_symlink() and src.readlink() == dst and dst_info is not None
                if src.is_symlink() and not linked:
                    raise Refusal(f"Unsupported legacy symlink: {src}")
                if relative in recorded:
                    if not ((_matches(src, entry) and dst_info is None) or
                            (_matches(dst, entry) and (src_info is None or linked))):
                        raise Refusal(f"Conflicting migration identity: {src} -> {dst}")
                elif dst_info is not None and not linked:
                    raise Refusal(f"Migration destination already exists: {dst} (source {src})")
                actual = dst if linked or src_info is None else src
                if not (stat.S_ISDIR(actual.lstat().st_mode) or stat.S_ISREG(actual.lstat().st_mode)):
                    raise Refusal(f"Unsupported migration entry: {actual}")
                if actual.stat().st_dev != _device(dst.parent):
                    raise Refusal(f"Cross-device migration refused: {src} -> {dst}")
                entries.append({**entry, **_identity(actual)})
            yield {**intent, "entries": entries}
    except (OSError, UnicodeError) as exc:
        raise Refusal(f"Cannot inspect migration evidence: {exc}") from exc
