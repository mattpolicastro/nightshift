"""Versioned revision journals outside RunState. Call operations under run.lock().

Preparation must finish before advancing a ref; completion follows state/event
bookkeeping. Git object and ancestry validation belong to the revise caller.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def validate_key(key: str) -> None:
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
        raise ValueError("invalid round_id: use 1–128 ASCII letters, digits, '.', '_', ':' or '-', starting with a letter or digit")


def payload_digest(blocks: list[str]) -> str:
    """Hash parsed blocks without further stripping; order and duplicates matter."""
    if not isinstance(blocks, list) or not blocks or not all(isinstance(b, str) and b.strip() for b in blocks):
        raise ValueError("revision feedback must contain nonempty string blocks")
    return hashlib.sha256(json.dumps(blocks, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def fingerprint(path: Path) -> str | None:
    """None means absent, distinct from the digest of an existing empty file."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RevisionStore:
    def __init__(self, directory: Path, change: str, branch: str, round_id: str, blocks: list[str]):
        validate_key(round_id)
        if not all(isinstance(value, str) and value.strip() for value in (change, branch)):
            raise ValueError("revision change and branch must be nonempty strings")
        self.directory = directory
        self.identity = dict(change=change, branch=branch, round_id=round_id,
                             payload_digest=payload_digest(blocks), tasks=len(blocks))
        self.path = directory / "revision-rounds" / (hashlib.sha256(round_id.encode("ascii")).hexdigest() + ".json")

    def _validate(self, record: dict) -> dict:
        required = {*self.identity, "version", "parent", "commit", "round", "status", "state_digest", "events_digest"}
        if not isinstance(record, dict) or not required <= record.keys():
            raise ValueError("malformed revision record: missing required fields")
        if type(record["version"]) is not int or record["version"] != 1:
            raise ValueError("unsupported revision record version")
        for field in ("round", "tasks"):
            if type(record[field]) is not int or record[field] < 1:
                raise ValueError(f"malformed revision record: {field}")
        for field in ("parent", "commit", "payload_digest", "state_digest", "events_digest"):
            value = record[field]
            if field in ("state_digest", "events_digest") and value is None:
                continue
            pattern = r"(?:[0-9a-f]{40}|[0-9a-f]{64})" if field in ("parent", "commit") else r"[0-9a-f]{64}"
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError(f"malformed revision record: {field}")
        if record["parent"] == record["commit"] or len(record["parent"]) != len(record["commit"]):
            raise ValueError("malformed revision record: parent/commit identity")
        if record["status"] not in ("prepared", "completed"):
            raise ValueError("malformed revision record: status")
        for field, expected in self.identity.items():
            if record[field] != expected:
                raise ValueError(f"revision record conflict: {field}")
        return record

    def load(self) -> dict | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeError as exc:
            raise ValueError(f"malformed revision record at {self.path}") from exc
        try:
            return self._validate(json.loads(raw))
        except ValueError as exc:
            raise ValueError(f"invalid revision record at {self.path}: {exc}") from exc

    def _write(self, record: dict) -> dict:
        self._validate(record)
        self.path.parent.mkdir(exist_ok=True)
        # Persist the journal directory entry, including on the first round.
        _fsync_directory(self.directory)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".revision-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return record

    def prepare(self, *, parent: str, commit: str, number: int) -> dict:
        """Persist the candidate identity; never replace existing evidence."""
        if self.load() is not None:
            raise ValueError("revision record already exists; load it for recovery")
        return self._write(dict(version=1, **self.identity, parent=parent, commit=commit,
                                round=number, status="prepared",
                                state_digest=fingerprint(self.directory / "state.json"),
                                events_digest=fingerprint(self.directory / "events.jsonl")))

    def complete(self) -> dict:
        """Mark persisted evidence complete, preserving identity and unknown fields."""
        record = self.load()
        if record is None:
            raise ValueError("revision record is missing; prepare before completion")
        if record["status"] == "completed":
            return record
        return self._write({**record, "status": "completed"})
