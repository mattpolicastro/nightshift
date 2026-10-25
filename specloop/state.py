"""Durable run state: one JSON document plus an append-only event log.

state.json is rewritten atomically after every transition; events.jsonl is the
history. Per-attempt evidence lives in its own directory beside them.
"""

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT: Path | None = None  # Explicit local overrides remain supported; defaults are lazy.
SAFE_CHANGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _root(variable: str, default: str) -> Path:
    value = os.environ.get(variable)
    if value is None:
        return Path.home() / default
    if not value.strip() or not Path(value).is_absolute():
        raise ValueError(f"{variable} must be a nonblank absolute path")
    return Path(value)


def state_root() -> Path:
    return Path(ROOT) if ROOT is not None else _root("NIGHTSHIFT_STATE_ROOT", ".nightshift")


def legacy_root() -> Path:
    return _root("NIGHTSHIFT_LEGACY_STATE_ROOT", ".specloop")


def runs_dir() -> Path:
    return state_root() / "runs"


def proposals_dir() -> Path:
    """Disposable build workspaces, separate from daemon proposal receipts."""
    return state_root() / "drafts"


def homes_dir() -> Path:
    return state_root() / "homes"


def heartbeat_path(*, existing: bool = False) -> Path:
    canonical = state_root() / "serve.json"
    legacy = legacy_root() / "serve.json"
    return legacy if existing and not canonical.exists() and legacy.exists() else canonical


def _present(path: Path) -> bool:
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def run_dir(change: str, *, check_conflict: bool = True) -> Path:
    if not SAFE_CHANGE.fullmatch(change or "") or change in (".", ".."):
        raise ValueError(f"unsafe change name {change!r}")
    canonical, legacy = runs_dir() / change, legacy_root() / "runs" / change
    if _present(canonical):
        if check_conflict and _present(legacy) and not canonical.samefile(legacy):
            raise ValueError(f"{change}: conflicting run directories: {canonical} and {legacy}")
        return canonical
    return legacy if _present(legacy) else canonical


def iter_run_dirs():
    """Name-sorted read-only union; loading each run reports its own errors."""
    found = {}
    for parent in (legacy_root() / "runs", runs_dir()):
        for directory in parent.glob("*"):
            try:
                admitted = _present(directory / "state.json")
            except OSError:
                admitted = True  # Keep unreadable entries visible to readers.
            if admitted:
                found[directory.name] = directory
    yield from (found[name] for name in sorted(found))


@dataclass
class TaskState:
    attempts: int = 0  # counted attempts (infra retries are not counted)
    infra: int = 0
    review_invalid: int = 0
    sessions: int = 0  # every agent session, counted or not; names attempt dirs
    status: str = "pending"  # pending | passed | accepted | stuck | blocked
    candidate: str = ""  # sha of the latest sealed candidate
    candidate_base: str = ""  # the branch head that candidate was built on
    pending_review: str = ""  # verified candidate whose review must (re)run
    feedback: str = ""  # what the next attempt is told
    violations: list[str] = field(default_factory=list)  # from the previous attempt
    waived: list[str] = field(default_factory=list)
    note: str = ""  # human note from `resume --note`, consumed by the next attempt
    auto_resumes: int = 0  # infra pauses the supervisor has lifted for this task
    arbitrated: int = 0  # times an arbiter ruled on this task being stuck (at most once)
    sealed: str = ""  # sealed commit whose verification has not finished (a killed driver re-verifies it)
    sealed_base: str = ""


@dataclass
class Pause:
    reason: str
    task: str = ""
    detail: str = ""
    resume_at: float = 0  # when set, `specloop serve` may lift the pause after this time


@dataclass
class RunState:
    change: str
    status: str = "running"  # running | paused | done | aborted
    pause: Pause | None = None
    tasks: dict[str, TaskState] = field(default_factory=dict)
    gates_passed: list[int] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    coverage_rounds: int = 0
    acceptance_rounds: int = 0
    pr: str = ""
    exhausted: dict[str, float] = field(default_factory=dict)  # profile -> usable again at
    section_base: dict[str, str] = field(default_factory=dict)  # section -> head when its first task began
    evidenced: list[int] = field(default_factory=list)
    strengthen_rounds: dict[str, int] = field(default_factory=dict)
    evidence_summary: dict[str, str] = field(default_factory=dict)
    evidence_scores: dict[str, float] = field(default_factory=dict)  # section -> score before strengthening
    # Integration intent/verification: base, previous_head, merged_head,
    # verified_head, receipts, retry_verify. Keep base for evidence attribution.
    base_sync: dict = field(default_factory=dict)

    def task(self, key: str) -> TaskState:
        return self.tasks.setdefault(key, TaskState())


class Busy(RuntimeError):
    """The run's lock is held: retry later. Not a refusal for a front end to escalate."""


def _known(cls, data: dict):
    """Build from the fields this code knows, ignoring the rest.

    A field added by newer code must not crash an older reader: on 2026-10-20
    a supervisor still running yesterday's code died on a state.json that
    carried a new `arbitrated` count.
    """
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


class Run:
    """One admitted change: its directory, state and log.

    require_existing makes missing state an error, including on reload. By
    default, missing state yields new in-memory state for admission.
    """

    def __init__(self, directory: Path, *, require_existing: bool = False):
        self.dir = directory
        self.require_existing = require_existing
        self._check_conflict()
        self.state = self._load()

    @classmethod
    def for_change(cls, change: str, *, require_existing: bool = False) -> "Run":
        if not SAFE_CHANGE.fullmatch(change or "") or change in (".", ".."):
            # An empty or path-like name would resolve to the runs directory or
            # outside it, and admission clears a run's directory.
            raise ValueError(f"unsafe change name {change!r}: use letters, digits, '.', '_' or '-'")
        return cls(run_dir(change), require_existing=require_existing)

    def _check_conflict(self) -> None:
        if self.dir.parent in (runs_dir(), legacy_root() / "runs"):
            run_dir(self.dir.name)

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.toml"

    def attempt_dir(self, key: str, n: int) -> Path:
        self._check_conflict()
        d = self.dir / key / f"a{n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load(self) -> RunState:
        p = self.dir / "state.json"
        try:
            raw = json.loads(p.read_text())
        except FileNotFoundError:
            if self.require_existing:
                raise
            return RunState(change=self.dir.name)
        pause = _known(Pause, raw["pause"]) if raw.get("pause") is not None else None
        tasks = {k: _known(TaskState, v) for k, v in raw.get("tasks", {}).items()}
        return RunState(raw["change"], raw["status"], pause, tasks, raw.get("gates_passed", []), raw.get("created", 0),
                        raw.get("coverage_rounds", 0), raw.get("acceptance_rounds", 0), raw.get("pr", ""),
                        raw.get("exhausted", {}), raw.get("section_base", {}), raw.get("evidenced", []),
                        raw.get("strengthen_rounds", {}), raw.get("evidence_summary", {}),
                        raw.get("evidence_scores", {}), raw.get("base_sync", {}))

    def save(self) -> None:
        self._check_conflict()
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(self.state), f, indent=2)
        os.replace(tmp, self.dir / "state.json")

    def event(self, kind: str, **data) -> None:
        self._check_conflict()
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"at": time.time(), "event": kind, **data}) + "\n")

    @contextlib.contextmanager
    def lock(self):
        """Exclusive for admission or a driver; yields False if another holds it.

        The directory and lock file must survive admission cleanup so all
        contenders keep locking the same inode.
        """
        self._check_conflict()
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "lock", "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def reload(self) -> None:
        self._check_conflict()
        self.state = self._load()

    def request_pause(self) -> None:
        """Ask the driver to stop at its next task boundary (`specloop pause`)."""
        self._check_conflict()
        (self.dir / "pause-requested").touch()

    def pause_requested(self) -> bool:
        return (self.dir / "pause-requested").exists()

    def clear_pause_request(self) -> None:
        self._check_conflict()
        (self.dir / "pause-requested").unlink(missing_ok=True)

    def pause(self, reason: str, task: str = "", detail: str = "", resume_at: float = 0) -> None:
        self.state.status = "paused"
        self.state.pause = Pause(reason, task, detail, resume_at)
        self.save()
        self.event("pause", reason=reason, task=task, detail=detail, resume_at=resume_at)
