"""Host-owned verification of a committed candidate in a disposable checkout.

The command format is intentionally limited to argv commands joined by ``&&``.
It is not a shell, nor a sandbox for hostile repository code. A clean environment
prevents inherited credentials; filesystem/network isolation remains separate.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT_S = 600


def parse_commands(command: str) -> list[list[str]]:
    """Parse the supported operator configuration; reject shell control syntax.

    Shell expansion, pipelines, redirections, background jobs, and compound
    commands are unsupported. Move complex checks into a repository script.
    That script, like the repository itself, must be trusted by the operator.
    """
    if any(char in command for char in ("\n", "\r", "`")) or "$(" in command:
        raise ValueError("verification requires argv commands joined only by &&")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    clauses: list[list[str]] = [[]]
    for token in lexer:
        if token == "&&":
            if not clauses[-1]:
                raise ValueError("empty verification clause")
            clauses.append([])
        elif re.fullmatch(r"[;&|<>()]+", token):
            raise ValueError("unsupported verification shell operator: " + token)
        else:
            clauses[-1].append(token)
    if not clauses[-1]:
        raise ValueError("empty verification clause")
    return clauses


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int | None
    duration_s: float
    timed_out: bool = False
    backgrounded: bool = False
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.backgrounded


@dataclass
class VerificationResult:
    candidate_sha: str
    command: str
    clauses: list[CommandResult] = field(default_factory=list)
    bootstrap: CommandResult | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        try:
            expected = parse_commands(self.command)
        except ValueError:
            return False
        return (not self.error and bool(self.candidate_sha)
                and (self.bootstrap is None or self.bootstrap.ok)
                and [list(c.argv) for c in self.clauses] == expected
                and all(c.ok for c in self.clauses))


def environment(scratch: Path) -> dict[str, str]:
    """No inherited tokens, agent sockets, provider settings, or user HOME."""
    home = scratch / "home"
    home.mkdir(exist_ok=True)
    temporary = scratch / "tmp"
    temporary.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home), "TMPDIR": str(temporary),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "CI": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(worktree: Path, *args: str) -> str:
    # Git inspection must not inherit GIT_DIR/WORK_TREE or credential helpers.
    env = {"PATH": os.environ.get("PATH", os.defpath),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=worktree, env=env,
                          capture_output=True, text=True, check=True,
                          timeout=30).stdout.strip()


def candidate_sha(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD")


def unchanged(worktree: Path, sha: str, *, branch: str | None = None) -> bool:
    """Reject new commits and tracked/untracked edits after verification."""
    return ((branch is None or _git(worktree, "rev-parse", "refs/heads/" + branch) == sha)
            and candidate_sha(worktree) == sha
            and not _git(worktree, "status", "--porcelain", "--untracked-files=all"))


def _command(argv: list[str], cwd: Path, env: dict[str, str],
             deadline: float) -> CommandResult:
    start = time.monotonic()
    if start >= deadline:
        return CommandResult(tuple(argv), None, 0, timed_out=True)
    timed_out = False
    backgrounded = False
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as exc:
            return CommandResult(tuple(argv), None, time.monotonic() - start,
                                 output=str(exc))
        try:
            proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # A command returning while its process group remains is not a
            # completed verification. Kill owned descendants on every exit.
            try:
                os.killpg(proc.pid, 0)
                backgrounded = not timed_out
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 16_384))
        tail = output.read().decode("utf-8", errors="replace")
    return CommandResult(tuple(argv), proc.returncode, time.monotonic() - start,
                         timed_out, backgrounded, tail)


def run(worktree: Path, command: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
        ) -> VerificationResult:
    """Execute every clause at exact HEAD, fail closed, and retain bounded output.

    pnpm projects receive the same frozen-lockfile bootstrap as task worktrees,
    in the disposable checkout with the same deadline and clean environment.
    Other repositories must supply self-contained verification commands.
    """
    result = VerificationResult(candidate_sha="", command=command)
    try:
        clauses = parse_commands(command)
        result.candidate_sha = candidate_sha(worktree)
        if not unchanged(worktree, result.candidate_sha):
            raise ValueError("candidate has uncommitted or untracked changes")
        deadline = time.monotonic() + timeout_s
        with tempfile.TemporaryDirectory(prefix="nightshift-verify-") as directory:
            scratch = Path(directory)
            env = environment(scratch)
            checkout = scratch / "candidate"
            for argv in (["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout",
                          "--", str(worktree.resolve()), str(checkout)],
                         ["git", "-C", str(checkout), "checkout", "--quiet", "--detach",
                          result.candidate_sha]):
                prepared = _command(argv, scratch, env, deadline)
                if not prepared.ok:
                    raise ValueError("candidate checkout failed: " + prepared.output)
            if (checkout / "pnpm-lock.yaml").exists():
                result.bootstrap = _command(
                    ["pnpm", "install", "--frozen-lockfile"], checkout, env, deadline)
                if not result.bootstrap.ok:
                    raise ValueError("verification dependency bootstrap failed")
            for argv in clauses:
                completion = _command(argv, checkout, env, deadline)
                result.clauses.append(completion)
                if not completion.ok:
                    raise ValueError("verification clause failed, timed out, or left background work")
                if not unchanged(checkout, result.candidate_sha):
                    raise ValueError("verification modified the candidate checkout")
            if not unchanged(worktree, result.candidate_sha):
                raise ValueError("candidate changed during verification")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result.error = str(exc)
    return result


def save(result: VerificationResult, path: Path) -> None:
    """Persist host evidence next to private transcripts, never in the worktree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(asdict(result), indent=2) + "\n")
