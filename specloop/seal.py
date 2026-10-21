"""Turn an agent's work tree into a harness-owned commit.

A disallowed path is reverted and reported, not fatal. The
next attempt is told what was dropped; only a repeat pauses the run.

The agent's .git is never trusted. Spike S1 showed Claude Code can commit and
rewrite .git; Codex cannot. Either way the snapshot is the files on disk
compared with the base tree, and the commit is built in the harness's own
repository with `commit-tree`.
"""

import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from specloop.manifest import Manifest, matches

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_CHANGED = 400
SAFE_GIT = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
SAFE_FLAGS = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"]
IDENTITY = {
    "GIT_AUTHOR_NAME": "specloop", "GIT_AUTHOR_EMAIL": "specloop@localhost",
    "GIT_COMMITTER_NAME": "specloop", "GIT_COMMITTER_EMAIL": "specloop@localhost",
}


def git(repo: Path, *args: str, input: bytes | None = None, env: dict | None = None) -> bytes:
    p = subprocess.run(
        ["git", *SAFE_FLAGS, "-C", str(repo), *args],
        input=input, capture_output=True, timeout=60,
        env={**os.environ, **SAFE_GIT, **(env or {})},
    )
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {p.stderr.decode(errors='replace').strip()[:400]}")
    return p.stdout


def commit_files(repo: Path, parent: str, files: dict[str, str], message: str) -> str:
    """Commit harness-authored files without a checkout or moving a ref."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(repo, "read-tree", parent, env=env)
        info = ""
        for path, text in files.items():
            blob = git(repo, "hash-object", "-w", "--stdin", input=text.encode()).decode().strip()
            info += f"100644 {blob}\t{path}\0"
        git(repo, "update-index", "-z", "--index-info", input=info.encode(), env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    return git(repo, "commit-tree", tree, "-p", parent, "-m", message, env=IDENTITY).decode().strip()


@dataclass
class Sealed:
    sha: str | None  # None when nothing admissible changed
    changed: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    secret_paths: list[str] = field(default_factory=list)


def _unsafe(path: str) -> str | None:
    parts = path.split("/")
    if path.startswith("/") or "\\" in path or len(path.encode()) > 200:
        return "unsafe path"
    if any(p in ("", ".", "..") for p in parts):
        return "unsafe path"
    if any(p.lower() in (".git", ".codex") for p in parts):
        return "tool metadata path"
    if any(ord(c) < 32 for c in path):
        return "control character in path"
    return None


def _base_tree(repo: Path, base: str) -> dict[str, tuple[str, str]]:
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", base)
    tree = {}
    for entry in out.split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            mode, _kind, sha = meta.decode().split()
            tree[path.decode()] = (mode, sha)
    return tree


def _base_sizes(repo: Path, base: str) -> dict[str, int]:
    out = git(repo, "ls-tree", "-r", "-z", "-l", "--full-tree", base)
    sizes = {}
    for entry in out.split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            size = meta.split()[3]
            sizes[path.decode()] = int(size) if size.isdigit() else 0
    return sizes


def _listed(work: Path) -> set[str]:
    try:
        out = git(work, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return {p.decode() for p in out.split(b"\0") if p}
    except RuntimeError:
        # The agent broke its clone's .git. Fall back to the raw tree; build
        # output then shows up as reverted paths rather than being lost silently.
        found = set()
        for root, dirs, names in os.walk(work):
            dirs[:] = [d for d in dirs if d != ".git"]
            found |= {str((Path(root) / n).relative_to(work)) for n in names}
        return found


def seal(repo: Path, work: Path, base: str, m: Manifest, message: str, secrets: list[str] = ()) -> Sealed:
    base_tree = _base_tree(repo, base)
    # The cap bounds what the agent wrote, not the checkout: a large repository
    # (the 113 MB eurorack tree, 2026-10-21) is not the agent's doing.
    base_sizes = _base_sizes(repo, base)
    result = Sealed(None)
    changes: dict[str, tuple[str, Path] | None] = {}  # path -> (mode, file) | None for delete
    total = 0
    for path in sorted(_listed(work) | set(base_tree)):
        if why := _unsafe(path):
            if path not in base_tree:
                result.violations.append(f"{path}: {why}")
            continue
        f = work / path
        try:
            st = os.lstat(f)
        except FileNotFoundError:
            if path in base_tree:
                changes[path] = None
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            # A base submodule (160000) checks out as an empty directory; it is
            # not the agent's, and the commit keeps the base's entry.
            if base_tree.get(path, ("",))[0] not in ("120000", "160000"):
                result.violations.append(f"{path}: not a regular file")
            continue
        if st.st_size > MAX_FILE:
            # A large file the base already has, unchanged, is not the agent's.
            if not (path in base_tree and base_sizes.get(path) == st.st_size
                    and git(repo, "hash-object", "--no-filters", "--", str(f)).decode().strip() == base_tree[path][1]):
                result.violations.append(f"{path}: larger than {MAX_FILE} bytes")
            continue
        mode = "100755" if st.st_mode & 0o111 else "100644"
        changes[path] = (mode, f)
        total += max(0, st.st_size - base_sizes.get(path, 0))
    if total > MAX_TOTAL:
        raise RuntimeError(f"the agent added {total} bytes to the work tree, over the {MAX_TOTAL} cap")

    files = [(p, v) for p, v in changes.items() if v is not None]
    shas = git(repo, "hash-object", "-w", "--no-filters", "--stdin-paths",
               input="".join(f"{v[1]}\n" for _, v in files).encode()).decode().split()
    edits: list[tuple[str, str, str] | tuple[str, None, None]] = []
    for (path, (mode, f)), sha in zip(files, shas):
        if base_tree.get(path) == (mode, sha):
            continue
        edits.append((path, mode, sha))
    edits += [(p, None, None) for p, v in changes.items() if v is None]

    for path, mode, sha in sorted(edits):
        if matches(path, m.frozen):
            result.violations.append(f"{path}: frozen path (change reverted)")
        elif not matches(path, m.allowed):
            result.violations.append(f"{path}: outside the allowed paths (change reverted)")
        else:
            if sha and secrets:
                body = (work / path).read_bytes()
                if any(s.encode() in body for s in secrets):
                    result.secret_paths.append(path)
                    continue
            result.changed.append(path)
    if len(result.changed) > MAX_CHANGED:
        raise RuntimeError(f"{len(result.changed)} files changed, over the {MAX_CHANGED} cap")
    if not result.changed or result.secret_paths:
        return result

    keep = set(result.changed)
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(repo, "read-tree", base, env=env)
        # --index-info works in a bare repository; mode 0 removes the path.
        info = "".join(f"{mode} {sha}\t{path}\0" if sha else f"0 {'0' * 40}\t{path}\0"
                       for path, mode, sha in edits if path in keep)
        git(repo, "update-index", "-z", "--index-info", input=info.encode(), env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    result.sha = git(repo, "commit-tree", tree, "-p", base, "-m", message, env=IDENTITY).decode().strip()
    return result


def credential_values(extra_env: dict[str, str] | None = None) -> list[str]:
    """Literal token values a diff must never contain."""
    values = []
    auth = Path.home() / ".codex" / "auth.json"

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, str) and len(x) >= 24:
            values.append(x)

    try:
        walk(json.loads(auth.read_text()))
    except (OSError, ValueError):
        pass
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "SPECLOOP_SLACK_WEBHOOK"):
        v = (extra_env or {}).get(k) or os.environ.get(k)
        if v and len(v) >= 16:
            values.append(v)
    return values
