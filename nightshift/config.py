"""Config loading. `config.toml` next to the package root, gitignored."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .queue import Labels

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"

# launchd reads no shell config, so `bin/nightshift-daemon` sources this before
# exec. Everything that is not the daemon has to parse it.
ENV_FILE = Path.home() / ".config" / "nightshift" / "env"


def parse_env_file(path: Path | None = None) -> dict[str, str]:
    """`export K=V` lines from the credentials file, quotes removed.

    The quote stripping is the point. README tells you to write
    `export GH_TOKEN='github_pat_...'`, and bash drops those quotes when the
    wrapper sources the file — so a parser that keeps them hands a worker a
    different value than the daemon around it is using. That is latent for
    GH_TOKEN (workers are not allowed to run `gh`) and fatal the day someone
    quotes CLAUDE_CODE_OAUTH_TOKEN the way README quotes the other two: workers
    would fail to authenticate, and per CLAUDE.md an auth test run on a
    logged-in machine cannot tell you why.
    """
    path = path or ENV_FILE
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_env_file(path: Path | None = None) -> list[str]:
    """Inject the credentials file into `os.environ`. The file wins.

    The daemon has these because the wrapper sourced them. Nothing sourced them
    for a human running `status` or `preflight`, so both reported on a shell
    with no relationship to the environment workers actually run in: `alerts:
    NOT CONFIGURED` with a webhook sitting correctly in the file, and a
    negative control probing the personal login rather than GH_TOKEN — the
    exact "asserted, not proven" failure preflight exists to prevent.

    File-wins matches `worker._env()`, so what preflight checks is what a
    worker would get.
    """
    values = parse_env_file(path)
    os.environ.update(values)
    return sorted(values)


DEFAULT_CLONE_ROOT = Path.home() / "Projects"


@dataclass(frozen=True)
class Repo:
    name: str
    # Inert constructor compatibility for historical shared utilities.
    verify: str = ""
    base: str = "main"
    # Empty means the `~/Projects/<repo-name>` convention. See `clone_dir`.
    dir: str = ""
    # Inert constructor compatibility; specloop quotas come from its manifest.
    concurrency: int = 1
    engine: str = "specloop"
    manifest: Path | None = None

    def __post_init__(self) -> None:
        _validate_engine(self.name, self.engine)
        if self.manifest is not None:
            if not isinstance(self.manifest, (str, Path)) or not str(self.manifest).strip():
                raise ValueError(f"{self.name}: manifest must be a non-empty path")
            manifest = Path(self.manifest)
            try:
                manifest = manifest.expanduser().absolute()
                manifest = manifest.resolve()
            except (OSError, RuntimeError, ValueError):
                # Enrollment artifacts are checked on each poll. Retain the
                # path so an unavailable filesystem or symlink can be repaired.
                pass
            object.__setattr__(self, "manifest", manifest)

    @property
    def base_ref(self) -> str:
        """What to branch and diff against: the remote ref, not the local one.

        The local `main` in the daemon's clone is whatever it last pulled, and
        nothing in the daemon's flow updates it — it pushes task branches and
        never checks `main` out. Diffing against it is the worse half of the
        bug: a stale base makes an unrelated merge show up as the worker's own
        churn, which is exactly what the reviewer's "no unrelated churn" rubric
        reads.

        `base` itself stays the plain branch name, because `gh pr create
        --base` wants a branch and would reject `origin/main`.
        """
        return f"origin/{self.base}"

    def clone_dir(self, root: Path | None = None) -> Path:
        """The clone worktrees are cut from. Explicit `dir` wins, else convention.

        The convention (`~/Projects/<repo-name>`) was written when the only
        enrolled repo was the sandbox — somewhere nobody sits interactively. It
        stops being free the moment a repo you work in daily is enrolled,
        because the daemon does all of this IN that clone: `fetch --prune` per
        task, `worktree add`/`remove` per task, and `branch -D` on teardown.

        The collision is not hypothetical. A branch checked out in a worktree
        is locked against checkout elsewhere, so a worker holding `claude/23`
        blocks you from checking it out to look at its PR — and a crashed
        teardown leaves a worktree whose directory is gone but whose lock is
        not, which surfaces as `git checkout` failing with a `fatal:` naming a
        path that no longer exists.

        Pointing this at a clone that exists only for the daemon keeps its
        bookkeeping out of the repo you are editing in. The fallback is
        unchanged so an existing config keeps working untouched.
        """
        if self.dir:
            return Path(self.dir).expanduser()
        return (root or DEFAULT_CLONE_ROOT) / self.name.split("/")[-1]


@dataclass(frozen=True)
class Config:
    repos: list[Repo]
    labels: Labels = Labels()
    # Well above the ~5s `gh issue list` index lag. queue.claim() guards against
    # a double-claim locally, but a tight loop still fights a stale index.
    poll_seconds: int = 120
    worktree_root: Path = Path.home() / "Projects" / "nightshift-wt"
    slack_webhook: str = ""
    # A repo the token must NOT be able to read. Without one, `preflight`
    # can only show that scoping did not break the daemon — never that it
    # actually scoped anything.
    forbidden_probe: str = ""
    # Historical compatibility only; configuration cannot enable mirroring.
    mirror_to_paseo: bool = field(default=False, init=False)
    notes: list[str] = field(default_factory=list)


def _validate_engine(name: str, engine: str) -> None:
    if engine == "worker":
        raise ValueError(
            f'{name}: worker engine is retired; use engine = "specloop" '
            '(or omit engine) and configure a trusted manifest'
        )
    if engine != "specloop":
        raise ValueError(f"{name}: unknown engine {engine!r}; engine must be 'specloop'")


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    raw = tomllib.loads(path.read_text())

    # Reject retired/unknown engines before interpreting any legacy settings
    # or manifest paths, even when the invalid enrollment appears last.
    for repo in raw.get("repos", []):
        _validate_engine(repo["name"], repo.get("engine", "specloop"))

    daemon = raw.get("daemon", {})
    labels = raw.get("labels", {})

    cfg = Config(
        repos=[
            Repo(
                name=r["name"],
                base=r.get("base", "main"),
                dir=r.get("dir", ""),
                engine=r.get("engine", "specloop"),
                manifest=_manifest_path(r, path.parent),
            )
            for r in raw.get("repos", [])
        ],
        labels=Labels(
            ready=labels.get("ready", "agent:ready"),
            working=labels.get("working", "agent:working"),
            done=labels.get("done", "agent:done"),
            needs_human=labels.get("needs_human", "needs-human"),
            spec_draft=labels.get("spec_draft", "spec:draft"),
            spec_review=labels.get("spec_review", "spec:review"),
            spec_approved=labels.get("spec_approved", "spec:approved"),
        ),
        poll_seconds=daemon.get("poll_seconds", 120),
        worktree_root=_worktree_root(
            daemon.get("worktree_root", str(Path.home() / "Projects" / "nightshift-wt")),
            path.parent,
        ),
        slack_webhook=_webhook(raw.get("notify", {})),
        forbidden_probe=daemon.get("forbidden_probe", ""),
        notes=_obsolete_notes(raw),
    )
    for note in cfg.notes:
        log.warning("%s", note)
    return cfg


def _worktree_root(value: object, config_dir: Path) -> Path:
    """Normalize placement without inspecting or creating worktrees or state."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(char in value for char in "\0\n\r")
        or (value.startswith("~") and not value.startswith("~/"))
    ):
        raise ValueError(
            "daemon.worktree_root must be a non-empty path with no unsupported tilde or NUL/line breaks"
        )
    path = Path(value)
    # Check the original spelling: Path strips './', whose following tilde
    # names a literal directory rather than the host user's home.
    if value.startswith("~/"):
        path = path.expanduser()
    # Do not resolve symlinks or require the destination to exist. Relative
    # values belong to the selected config's directory, not the daemon cwd.
    return path if path.is_absolute() else config_dir.absolute() / path


def _manifest_path(repo: dict, config_dir: Path) -> Path | None:
    """Resolve manifest paths beside the config, independent of daemon cwd."""
    if "manifest" not in repo:
        return None
    value = repo["manifest"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{repo['name']}: manifest must be a non-empty path")
    path = Path(value)
    try:
        path = path.expanduser()
    except (OSError, RuntimeError):
        return path  # Retry failed home expansion during repository readiness.
    return path if path.is_absolute() else config_dir / path


def _obsolete_notes(raw: dict) -> list[str]:
    """Report presence only: legacy values never become runtime settings."""
    keys = {key for key in ("models", "endpoints") if key in raw}
    keys.update(
        f"daemon.{key}"
        for key in (
            "max_tasks_per_night", "concurrency", "review_attempts",
            "implement_max_turns", "review_max_turns", "mirror_to_paseo",
        )
        if key in raw.get("daemon", {})
    )
    keys.update(
        f"repos.{key}"
        for repo in raw.get("repos", [])
        for key in ("models", "verify", "concurrency")
        if key in repo
    )
    return [f"{key} is ignored; configure a trusted specloop manifest" for key in sorted(keys)]


def _webhook(notify: dict) -> str:
    """The Slack webhook, env first.

    A webhook URL is a credential — anyone holding it can post into the
    channel — so its home is `~/.config/nightshift/env` (chmod 600) alongside
    the OAuth and GitHub tokens, which the launchd wrapper already sources.
    `config.toml` is gitignored and so is safe, but keeping secrets in one
    place means there is one file to audit and one to rotate.
    """
    return notify.get("slack_webhook") or os.environ.get(
        "NIGHTSHIFT_SLACK_WEBHOOK", ""
    )
