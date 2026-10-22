"""Read-only discovery and identity normalization for local touchpoint history."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path


def _safe_change(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value) or value in (".", ".."):
        raise ValueError("unsafe change name")
    return value


def _repository(directory: str, explicit: object = None) -> str:
    if explicit is None:
        if len(re.findall(r"(?=__)", directory)) != 1:
            raise ValueError("ambiguous repository directory without explicit repo")
        explicit = directory.replace("__", "/")
    if not isinstance(explicit, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", explicit):
        raise ValueError("invalid repository identity")
    if explicit.replace("/", "__") != directory:
        raise ValueError("repository conflicts with directory identity")
    return explicit


class _Reader:
    def __init__(self):
        self.errors = []

    def error(self, path, exc):
        self.errors.append(dict(path=str(path), line=None, message=f"{type(exc).__name__}: {exc}"))

    def children(self, path):
        try:
            return sorted(path.iterdir())
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.error(path, exc)
            return []

    def read(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def is_directory(self, path):
        try:
            # Path.is_dir() suppresses stat errors on newer Python versions.
            return stat.S_ISDIR(path.stat().st_mode)
        except OSError as exc:
            self.error(path, exc)
            return False

    def record(self, path, kind, directory, number, job=None):
        try:
            data = self.read(path)
            repo = _repository(directory, data.get("repo"))
            command = data.get("command", "admit" if kind == "admissions" else "propose")
            head = data.get("head")
            change = data.get("change")
            result, wrapper_error = data.get("result"), None
            if job is not None:
                args = data.get("args")
                if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
                    raise ValueError("request args must be a nonempty string array")
                command = args[0]

                def option(flag):
                    if args.count(flag) != 1 or args.index(flag) + 1 >= len(args):
                        raise ValueError(f"request needs one {flag} value")
                    return args[args.index(flag) + 1]

                change = args[1] if command == "revise" and len(args) > 1 else option("--change")
                head = option("--head") if "--head" in args else None
                result_path = path.with_name("result.json")
                try:
                    wrapper = self.read(result_path)
                    result, wrapper_error = wrapper.get("result"), wrapper.get("error")
                    if result is None and wrapper_error is None:
                        raise ValueError("job wrapper needs result or error")
                except FileNotFoundError:
                    pass  # Retain pending requests for identity and draft-date provenance.
                except (OSError, ValueError) as exc:
                    self.error(result_path, exc)
            if not isinstance(command, str):
                raise ValueError("command must be a string")
            if command not in ({"propose", "revise"} if kind == "revisions" else
                               {"admit" if kind == "admissions" else "propose"}):
                raise ValueError("command conflicts with intake source")
            change = _safe_change(change)
            issue = data.get("issue", number if kind == "proposals" else None)
            if issue is not None and (type(issue) is not int or issue <= 0):
                raise ValueError("issue must be a positive integer")
            if kind == "proposals" and issue != number:
                raise ValueError("issue conflicts with proposal filename")
            if result is not None and not isinstance(result, dict):
                raise ValueError("result must be an object")
            return dict(path=path, kind=kind, repo=repo, number=number, issue=issue,
                        change=change, command=command, head=head, result=result,
                        job=job if job is not None else data.get("job"),
                        retained=job is not None, wrapper_error=wrapper_error,
                        mtime=path.stat().st_mtime)
        except (OSError, ValueError) as exc:
            self.error(path, exc)
            return None

    def records(self, root):
        for kind in ("proposals", "admissions", "revisions"):
            for directory in self.children(root / kind):
                if not self.is_directory(directory):
                    continue
                for path in self.children(directory):
                    match = re.fullmatch(r"([1-9][0-9]*)\.(json|jobs)", path.name)
                    if not match:
                        continue
                    number, suffix = int(match[1]), match[2]
                    if suffix == "json":
                        record = self.record(path, kind, directory.name, number)
                        if record is not None:
                            yield record
                    else:
                        for job in self.children(path):
                            if self.is_directory(job):
                                record = self.record(job / "request.json", kind, directory.name, number, job.name)
                                if record is not None:
                                    yield record


def collect_history(nightshift_root: Path, runs_root: Path) -> dict:
    """Return identity rows, normalized records and uniquely attributable log paths.

    Records retain their source number (issue for proposals, PR otherwise), job
    ID, command, head, result and mtime for later accounting. No deduplication or
    event replay happens here; events_path is None for ambiguous run identities.
    """
    reader, rows = _Reader(), {}
    records = list(reader.records(nightshift_root))
    for record in records:
        try:
            repo, change = record["repo"], record["change"]
            matches = [key for key in rows if key[0] == repo and key[2] == change]
            if record["kind"] == "proposals":
                issue = record["issue"]
            elif len(matches) == 1:
                issue = matches[0][1]
            elif matches:
                raise ValueError("ambiguous proposal identity for change")
            else:
                pattern = re.escape(repo.split("/")[1]) + r"-issue-([1-9][0-9]*)-([a-z0-9]+(?:-[a-z0-9]+)*)"
                match = re.fullmatch(pattern, change)
                if not match or len(match[2]) > 40:
                    raise ValueError("missing proposal and no front-door change identity")
                issue = int(match[1])
            if record["issue"] is not None and record["issue"] != issue:
                raise ValueError("issue conflicts with joined change identity")
            key = repo, issue, change
            if key not in rows:
                warnings = ([] if record["kind"] == "proposals" else
                            ["Missing proposal; inferred issue from change name; draft date unknown."])
                rows[key] = dict(repo=repo, issue=issue, change=change, drafted_at=None,
                                 drafted_at_source=None, warnings=warnings, records=[], events_path=None)
            rows[key]["records"].append(record)
        except ValueError as exc:
            reader.error(record["path"], exc)
    by_change = {}
    for row in rows.values():
        by_change.setdefault(row["change"], []).append(row)
    for change, matches in by_change.items():
        path = runs_root / change / "events.jsonl"
        if len(matches) > 1:
            reader.error(path, ValueError("ambiguous run name belongs to multiple rows; events not read"))
        else:
            matches[0]["events_path"] = path
    return dict(changes=[rows[key] for key in sorted(rows)],
                errors=sorted(reader.errors, key=lambda error: (error["path"], error["message"])))
