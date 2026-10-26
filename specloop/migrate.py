"""Internal migration preflight and destination-preserving entry relocation.

The yielded intent is JSON-serializable. A relocation caller must persist it
before its first mutation and stay inside preflight to retain every lock.
Completion is explicit; CLI/startup activation belongs to separate callers.
"""

from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

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


def _intent(source, destination, path=None):
    identity = {"version": 1, "source_root": str(source), "destination_root": str(destination)}
    path = journal_path() if path is None else path
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
                    src.name in (".", "..") or "\0" in str(src) or str(src) != entry["source"] or
                    dst != Path(MAPPING[src.parts[0]]) / src.name or
                    str(dst) != entry["destination"] or str(src) in seen or
                    any(type(entry[k]) is not int or entry[k] < 0 for k in ("device", "inode"))):
                raise ValueError("invalid entry identity")
            seen.add(str(src))
            if entry["phase"] not in ("intent", "planned", "rewritten", "moved", "linked"):
                raise ValueError("invalid phase")
            if not isinstance(entry["symlinks"], list):
                raise ValueError("invalid symlink plan")
            links = set()
            for link in entry["symlinks"]:
                relative = Path(link["path"])
                if (relative.is_absolute() or not relative.parts or ".." in relative.parts or
                        str(relative) != link["path"] or link["path"] in links or
                        not isinstance(link["original"], str) or not link["original"] or
                        os.path.isabs(link["original"]) or not os.path.isabs(link["destination"]) or
                        any("\0" in link[k] for k in ("path", "original", "destination"))):
                    raise ValueError("invalid symlink plan")
                links.add(link["path"])
            if entry["phase"] == "intent" and entry["symlinks"]:
                raise ValueError("unplanned symlinks")
        return intent
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise Refusal(f"Invalid migration journal: {path}") from exc


def _completed(source, destination):
    path = source / MARKER
    if _metadata(path) is None:
        return False
    marker = _intent(source, destination, path)
    if marker.get("completed") is not True or any(e["phase"] != "linked" for e in marker["entries"]):
        raise Refusal(f"Invalid migration marker: {path}")
    if _metadata(journal_path()) is not None:
        intent = _intent(source, destination)
        if marker != {**intent, "completed": True}:
            raise Refusal(f"Migration marker/journal mismatch: {path}")
        journal_path().unlink()  # Interrupted after publication; no entry rescan.
    return True


def _location(source, destination, entry):
    src, dst = source / entry["source"], destination / entry["destination"]
    linked = src.is_symlink() and os.readlink(src) == str(dst)
    if _matches(src, entry) and _metadata(dst) is None:
        if entry["phase"] not in ("moved", "linked"):
            return src
    elif _matches(dst, entry) and (_metadata(src) is None or linked):
        if entry["phase"] in ("rewritten", "moved", "linked") and (linked or entry["phase"] != "linked"):
            return dst
        if linked and entry["phase"] == "intent":
            return dst  # An already complete compatibility link discovered at preflight.
    raise Refusal(f"Conflicting migration phase/identity: {src} -> {dst}")


def resolve_recorded(relative):
    """Read-only identity resolution, including a rename without its old link."""
    if _metadata(journal_path()) is None:
        return None
    source, destination = state.legacy_root().resolve(), state.state_root().resolve()
    intent = _intent(source, destination)
    for entry in intent["entries"]:
        if entry["source"] == relative:
            for root, rel in ((source, entry["source"]), (destination, entry["destination"])):
                _directory(root / Path(rel).parent)
            return _location(source, destination, entry)
    return None


@contextmanager
def preflight(owned=None):
    """Hold serve → exclusive coordination → sorted existing run lock inodes.

    Only stable global lock files may be created. No intent, entry, per-run
    lock, destination directory or success marker is written by preflight.
    Yield None for valid completion, clearing only a matching leftover journal.
    """
    source, destination = state.legacy_root(), state.state_root()
    for root in (source, destination):
        _directory(root)
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise Refusal(f"Overlapping migration roots: {source} and {destination}")
    try:
        with serve_lock(owned), coordination(exclusive=True), ExitStack() as locks:
            if _completed(source, destination):
                yield None
                return
            recovering = _metadata(journal_path()) is not None
            intent = _intent(source, destination)
            recorded = {entry["source"]: entry for entry in intent["entries"]}
            candidates = dict(recorded)
            for old, new in MAPPING.items():
                _directory(source / old)
                _directory(destination / new)
                if not recovering and (source / old).exists():
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
                linked = src.is_symlink() and os.readlink(src) == str(dst) and dst_info is not None
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


def _save(intent, path=None):
    """Publish a journal or marker atomically, without following a temp link."""
    path = journal_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(intent, handle)
            handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _original_destination(parent, target):
    """Resolve existing prefixes, retaining raw directory-traversal suffixes."""
    destination = str(parent)
    components = target.split("/")
    for index, component in enumerate(components):
        destination += "/" + component
        try:
            # pathlib drops trailing '/' and '/.' before probing. Check the
            # raw spelling first so regular-file/ remains ENOTDIR, not readable.
            os.stat(destination)
            if component not in ("", "."):
                destination = str(Path(destination).resolve(strict=True))
        except (FileNotFoundError, NotADirectoryError):
            # missing/../file (or regular-file/../file) cannot be traversed.
            # Non-strict resolution would remove that obstruction and could
            # make a dangling credential link open an unrelated existing file.
            return "/".join([destination, *components[index + 1:]])
    return destination


def _symlink_plan(entry):
    """Inspect metadata only, including dangling links; never walk linked homes."""
    plan = []
    if stat.S_ISREG(entry.lstat().st_mode):
        return plan

    def unreadable(error):
        raise error

    for parent, directories, files in os.walk(entry, followlinks=False, onerror=unreadable):
        for name in sorted(directories + files):
            path = Path(parent) / name
            if not path.is_symlink():
                continue
            original = os.readlink(path)
            if os.path.isabs(original):
                continue
            # Classify the link's path, not its final referent: an external
            # alias may point back inside, but its relative spelling still
            # breaks when this entry moves. Internal aliases move together.
            prefix = path.parent
            for component in original.split("/"):
                prefix = prefix / component
                if component == "..":
                    # '..' acts on the directory reached through intermediate
                    # links, not on their lexical depth (alias -> '.' may occur
                    # repeatedly). Resolve metadata before deciding it escapes.
                    prefix = prefix.resolve(strict=False)
                if not prefix.is_relative_to(entry):
                    break  # Do not normalize away an escape and later reentry.
            else:
                continue
            destination = _original_destination(path.parent, original)
            plan.append({"path": str(path.relative_to(entry)), "original": original,
                         "destination": destination})
    return plan


def relocate(intent):
    """Relocate a yielded preflight plan while its caller retains every lock.

    Leave the journal for the completion caller. No marker or activation occurs
    here. All symlink plans are persisted before the first entry is changed.
    """
    source, destination = Path(intent["source_root"]), Path(intent["destination_root"])
    try:
        # Check every recorded transition before rewriting any entry.
        for entry in intent["entries"]:
            actual = _location(source, destination, entry)
            for link in entry["symlinks"]:
                path = actual / link["path"]
                allowed = (link["original"], link["destination"]) if entry["phase"] == "planned" else (link["destination"],)
                if (not path.parent.resolve().is_relative_to(actual) or
                        not path.is_symlink() or os.readlink(path) not in allowed):
                    raise Refusal(f"Conflicting migration symlink: {path}")
        for entry in intent["entries"]:
            if entry["phase"] == "intent":
                src, dst = source / entry["source"], destination / entry["destination"]
                if _matches(src, entry):
                    entry["symlinks"] = _symlink_plan(src)
                    entry["phase"] = "planned"
                elif src.is_symlink() and os.readlink(src) == str(dst) and _matches(dst, entry):
                    entry["phase"] = "linked"
                else:
                    raise Refusal(f"Missing symlink plan for moved entry: {src} -> {dst}")
        _save(intent)
        for entry in intent["entries"]:
            src, dst = source / entry["source"], destination / entry["destination"]
            if entry["phase"] == "linked":
                continue
            actual = src if _matches(src, entry) else dst
            if not _matches(actual, entry):
                raise Refusal(f"Conflicting migration identity: {actual}")
            for link in entry["symlinks"]:
                relative = Path(link["path"])
                if (relative.is_absolute() or ".." in relative.parts or not relative.parts or
                        not Path(link["destination"]).is_absolute()):
                    raise Refusal(f"Invalid symlink plan for {actual}")
                path = actual / relative
                if not path.parent.resolve().is_relative_to(actual):
                    raise Refusal(f"Symlink parent escapes entry: {path}")
                if not path.is_symlink() or os.readlink(path) not in (link["original"], link["destination"]):
                    raise Refusal(f"Conflicting migration symlink: {path}")
                if os.readlink(path) == link["original"]:
                    # A same-filesystem temporary link keeps the old link readable
                    # until replacement; no credential target is ever opened.
                    with tempfile.TemporaryDirectory(dir=destination) as temporary:
                        replacement = Path(temporary) / "link"
                        replacement.symlink_to(link["destination"])
                        replacement.replace(path)
            if entry["phase"] == "planned":
                entry["phase"] = "rewritten"
                _save(intent)
            if actual == src:
                if _metadata(dst) is not None:
                    raise Refusal(f"Migration destination already exists: {dst}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)  # EXDEV is a refusal, never a copy/delete fallback.
            if entry["phase"] == "rewritten":
                entry["phase"] = "moved"
                _save(intent)
            if _metadata(src) is None:
                src.symlink_to(dst, target_is_directory=dst.is_dir())
            elif not src.is_symlink() or os.readlink(src) != str(dst):
                raise Refusal(f"Conflicting legacy compatibility link: {src}")
            entry["phase"] = "linked"
            _save(intent)
    except (OSError, UnicodeError, RuntimeError) as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal(f"Cannot relocate migration evidence: {exc}") from exc


def migrate(owned=None):
    """Complete or resume migration only; return a command-compatible status."""
    from specloop.state_locks import MigrationBusy, SupervisorBusy

    try:
        with preflight(owned) as intent:
            if intent is not None:
                relocate(intent)
                _save({**intent, "completed": True}, Path(intent["source_root"]) / MARKER)
                journal_path().unlink()
        return 0
    except (Refusal, MigrationBusy, SupervisorBusy) as exc:
        print(f"Migration refused: {exc}", file=sys.stderr)
        return 1
