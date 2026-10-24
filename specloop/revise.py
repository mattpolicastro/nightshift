"""Queue review feedback as harness-authored tasks on an admitted branch."""

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from specloop import seal, tasks
from specloop.manifest import Manifest
from specloop.revision_store import RevisionStore, fingerprint
from specloop.state import Busy, Run

# Human stops are resolved with resume or accept, never cleared by new feedback.
REVISABLE_PAUSES = ("ready_to_publish", "human_checks")


@dataclass(frozen=True)
class RevisionResult:
    commit: str
    round_id: str | None
    replayed: bool
    status: str
    pause: dict | None


def _eligible(run: Run, m: Manifest) -> None:
    st = run.state
    reason = st.pause.reason if st.pause else ""
    if not (st.status == "running" or (st.status == "paused" and reason in REVISABLE_PAUSES)):
        raise ValueError(f"{m.change} is {st.status}" + (f" ({reason})" if reason else "")
                         + "; a review round is only added to a running run or one waiting to publish "
                           "or for its human checks")


def _append(text: str, blocks: list[str]) -> tuple[int, str]:
    rounds = [int(r.group(1)) for line in text.splitlines()
              if (section := tasks.SECTION.match(line))
              and (r := re.fullmatch(r"Review round (\d+)", section.group(2)))]
    number = max(rounds, default=0) + 1
    # Quoting keeps embedded checklists on one line; ordinals distinguish duplicates.
    items = [f"Address review feedback {i}: {json.dumps(block)}"
             for i, block in enumerate(blocks, 1)]
    return number, tasks.append_section(text, f"Review round {number}", items)


def _git(repo: Path, *args: str, **kwargs) -> bytes:
    try:
        return seal.git(repo, *args, **kwargs)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Git operational failure: {exc}") from exc


def _ancestor(repo: Path, commit: str, head: str) -> None:
    try:
        result = subprocess.run(
            ["git", *seal.SAFE_FLAGS, "-C", str(repo), "merge-base", "--is-ancestor", commit, head],
            capture_output=True, timeout=60, env={**os.environ, **seal.SAFE_GIT},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Git operational failure checking revision ancestry: {exc}") from exc
    if result.returncode == 1:
        raise ValueError("revision evidence conflict: candidate is not on the current branch")
    if result.returncode != 0:
        raise RuntimeError("Git operational failure checking revision ancestry: "
                           + result.stderr.decode(errors="replace").strip()[:400])


def _validate_candidate(repo: Path, record: dict, path: str, blocks: list[str]) -> None:
    parent, commit = record["parent"], record["commit"]
    # batch-check reports absent objects without conflating them with a Git error.
    kinds = _git(repo, "cat-file", "--batch-check=%(objecttype)",
                 input=f"{parent}\n{commit}\n".encode()).splitlines()
    if kinds != [b"commit", b"commit"]:
        raise ValueError("revision evidence conflict: missing parent or candidate commit")
    metadata = _git(repo, "show", "-s", "--format=%P%n%an%n%ae%n%cn%n%ce%n%B", commit).decode()
    expected = [parent, "specloop", "specloop@localhost", "specloop", "specloop@localhost",
                f"specloop: append review round {record['round']}"]
    if metadata.rstrip("\n").split("\n") != expected:
        raise ValueError("revision evidence conflict: candidate parent or harness identity differs")
    trees = []
    for sha in (parent, commit):
        entries = _git(repo, "ls-tree", "-r", "-z", sha).split(b"\0")
        trees.append({entry.split(b"\t", 1)[1]: entry.split(b"\t", 1)[0] for entry in entries if entry})
    if path.encode() not in trees[0]:
        raise ValueError("revision evidence conflict: original tasks are missing")
    original = _git(repo, "show", f"{parent}:{path}").decode()
    number, text = _append(original, blocks)
    blob = _git(repo, "hash-object", "--stdin", input=text.encode()).strip()
    trees[0][path.encode()] = b"100644 blob " + blob
    if number != record["round"] or trees[0] != trees[1]:
        raise ValueError("revision evidence conflict: candidate does not contain the original task append")


def _has_event(run: Run, record: dict) -> bool:
    try:
        raw = (run.dir / "events.jsonl").read_text()
        if raw and not raw.endswith("\n"):
            raise ValueError("unterminated event record")
        events = [json.loads(line) for line in raw.splitlines()]
        if any(not isinstance(e, dict) or not isinstance(e.get("event"), str) for e in events):
            raise ValueError("invalid event object")
    except FileNotFoundError:
        return False
    except (ValueError, UnicodeError) as exc:
        raise ValueError("revision evidence conflict: corrupt events.jsonl; repair the event log before retrying") from exc
    matching = [e for e in events if e["event"] == "revised" and e.get("round_id") == record["round_id"]]
    if len(matching) > 1 or any(any(e.get(k) != record[k] for k in ("commit", "round", "tasks")) for e in matching):
        raise ValueError("revision evidence conflict: revised event differs from the prepared record")
    return bool(matching)


def _finish(run: Run, store: RevisionStore, record: dict, has_event: bool) -> None:
    # Both snapshots must match: an intervening event with identical saved state
    # can also be a later control decision. Never clear a pause request.
    if (fingerprint(run.dir / "state.json") == record["state_digest"]
            and fingerprint(run.dir / "events.jsonl") == record["events_digest"]):
        run.state.status, run.state.pause = "running", None
        run.save()
    if not has_event:
        run.event("revised", commit=record["commit"], round=record["round"],
                  tasks=record["tasks"], round_id=record["round_id"])
    store.complete()


def revise(run: Run, m: Manifest, feedback: str, *, feedback_format: str = "text",
           round_id: str | None = None) -> str:
    return revise_result(run, m, feedback, feedback_format=feedback_format, round_id=round_id).commit


def revise_result(run: Run, m: Manifest, feedback: str, *, feedback_format: str = "text",
                  round_id: str | None = None) -> RevisionResult:
    """Append or recover a round, returning control state observed under the lock.

    replayed identifies an existing journal, including a prepared candidate
    landed by this invocation. The pause value is a detached snapshot.
    """
    if feedback_format == "json":
        blocks = json.loads(feedback)
        if not isinstance(blocks, list) or not all(isinstance(block, str) for block in blocks):
            raise ValueError("JSON feedback must be an array of strings")
        blocks = [block for block in blocks if block.strip()]
    elif feedback_format == "text":
        blocks = [block.strip() for block in re.split(r"(?m)^---\r?$", feedback) if block.strip()]
    else:
        raise ValueError(f"unknown feedback format: {feedback_format}")
    if not blocks:
        raise ValueError("feedback is empty; no revision tasks to append")
    store = RevisionStore(run.dir, m.change, m.branch, round_id, blocks) if round_id is not None else None
    with run.lock() as mine:
        if not mine:
            raise Busy(f"{m.change} is already being driven; retry when it is idle")
        if store and not (run.dir / "state.json").is_file():
            raise ValueError("revision requires existing saved run state")
        run.reload()
        if store and run.state.change != m.change:
            raise ValueError("revision record conflict: saved run identity differs")
        record = store.load() if store else None
        replayed = record is not None
        repo, ref = run.dir / "repo.git", f"refs/heads/{m.branch}"
        path = f"openspec/changes/{m.change}/tasks.md"
        if record is None:
            _eligible(run, m)
        head = _git(repo, "rev-parse", ref).decode().strip()
        if store and record is not None:
            _validate_candidate(repo, record, path, blocks)
            has_event = _has_event(run, record) if record["status"] == "prepared" else False
            if head == record["parent"] and record["status"] == "prepared":
                _eligible(run, m)
                _git(repo, "update-ref", ref, record["commit"], head)
            else:
                _ancestor(repo, record["commit"], head)
            if record["status"] == "prepared":
                _finish(run, store, record, has_event)
            commit = record["commit"]
        else:
            text = _git(repo, "show", f"{head}:{path}").decode()
            number, text = _append(text, blocks)
            commit = seal.commit_files(repo, head, {path: text}, f"specloop: append review round {number}")
            if store:
                record = store.prepare(parent=head, commit=commit, number=number)
                has_event = _has_event(run, record)
            _git(repo, "update-ref", ref, commit, head)
            if store:
                assert record is not None
                _finish(run, store, record, has_event)
            else:
                run.state.status, run.state.pause = "running", None
                run.save()
                run.event("revised", commit=commit, round=number, tasks=len(blocks))
        return RevisionResult(commit, round_id, replayed, run.state.status,
                              asdict(run.state.pause) if run.state.pause else None)
