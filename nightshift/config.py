"""Config loading. `config.toml` next to the package root, gitignored."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .queue import Labels

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
class Endpoint:
    """Where an invocation is sent, and what it may be trusted with.

    An endpoint is a wire protocol, a base URL, a credential and a billing
    kind — the four travel together because separating them is how a
    subscription token ends up pointed at a third-party host (SPEC §3).

    Declaring none keeps today's behaviour exactly: one implicit Anthropic
    endpoint on the subscription OAuth token, which is `DEFAULT`.
    """

    name: str
    # The WIRE FORMAT, not the vendor. `claude -p` speaks Anthropic and
    # nothing else, so an `openai` endpoint is reachable only through a
    # translating proxy (SPEC §1.5) and `proxy_url` is mandatory for one.
    protocol: str = "anthropic"
    # subscription | metered | none. Advisory only — what actually decides
    # whether a run consumed quota is the run's own telemetry
    # (`trace.Result.subscription_billed`), by the same principle that checks
    # the Bash commands a worker ran instead of believing its prose.
    billing: str = "subscription"
    base_url: str = ""
    # The NAME of the env var holding this endpoint's token, never the token.
    auth_env: str = ""
    models: tuple[str, ...] = ()
    # REQUIRED per model, and not cosmetic. Claude Code has no catalog entry
    # for a local model, so it assumes a 200k window and auto-compacts to it —
    # which wastes a 203k/262k model and would silently overrun a smaller one.
    context_tokens: dict[str, int] = field(default_factory=dict)
    # Local models are slower per turn. Without this a local implement pass
    # inherits a budget tuned for sonnet and truncates, which is the worst
    # outcome available: full cost, nothing to merge, nothing even to read.
    max_turns_multiplier: float = 1.0
    # Cold-load tolerance. A 17-19GB model takes minutes to load and answers
    # in under a second warm, so a short probe reports a healthy endpoint as
    # down — that false negative has already happened once (SPEC §1.1).
    warmup_timeout_s: int = 180
    proxy_url: str = ""
    # Driver is independent of endpoint protocol: legacy OpenAI endpoints still
    # use Claude plus a proxy. Native Codex remains disabled until qualified.
    driver: str = "claude-code"
    auth: str = ""
    reasoning_effort: str = "medium"
    max_runtime_s: int = 1800
    max_tool_calls: int = 100
    max_output_tokens_total: int = 32000

    def configuration_errors(self) -> list[str]:
        """Reject ambiguous native routing before a request can carry a key."""
        if not isinstance(self.driver, str) or self.driver not in {"claude-code", "codex-app-server"}:
            return ["unsupported worker driver"]
        if self.driver == "claude-code":
            errors = []
            if not isinstance(self.protocol, str) or self.protocol not in {"anthropic", "openai"}:
                errors.append("Claude Code requires anthropic or legacy openai protocol")
            if self.auth != "":
                errors.append("explicit auth is only supported for native driver configuration")
            return errors
        errors = []
        if self.protocol != "responses":
            errors.append("codex-app-server requires protocol = responses")
        if self.base_url != "https://api.openai.com/v1":
            errors.append("native OpenAI requires the exact official API base URL")
        if self.proxy_url:
            errors.append("native OpenAI does not accept proxy_url")
        if self.auth != "api_key" or self.billing != "metered":
            errors.append("native OpenAI requires explicit api_key auth and metered billing")
        if not isinstance(self.auth_env, str) or not self.auth_env or not self.auth_env.isidentifier():
            errors.append("native OpenAI requires a credential environment-variable name")
        if isinstance(self.auth_env, str) and self.auth_env in {"GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                             "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                             "NIGHTSHIFT_SLACK_WEBHOOK"}:
            errors.append("native OpenAI cannot reuse another service's credential")
        if not isinstance(self.models, (tuple, list)) or not self.models or any(not isinstance(m, str) or not m.strip()
                                  or m == "OPERATOR_SELECTED_MODEL_ID" for m in self.models):
            errors.append("native OpenAI requires explicit model identifiers")
        for field_name in ("max_runtime_s", "max_tool_calls", "max_output_tokens_total"):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                errors.append(f"{field_name} must be a positive integer")
        if not isinstance(self.reasoning_effort, str) or not self.reasoning_effort.strip():
            errors.append("reasoning_effort must be explicit")
        return errors

    def execution_blocker(self) -> str | None:
        errors = self.configuration_errors()
        if errors:
            return "; ".join(errors)
        if self.driver == "codex-app-server":
            return ("native Codex execution is disabled pending sandbox and "
                    "credential-isolation qualification; no fallback will run")
        return None

    @property
    def is_default(self) -> bool:
        """The implicit subscription endpoint: no redirect, the OAuth token.

        A URL is what makes an endpoint a different place, not its name. §4
        says the default may be DECLARED in order to change it — and one
        declared with a `base_url` is somewhere else, so it needs its own
        credential and must not inherit the subscription's, whatever it is
        called.
        """
        return (self.driver == "claude-code" and self.auth == "" and self.protocol == "anthropic"
                and self.name == DEFAULT_ENDPOINT_NAME and not self.url)

    @property
    def url(self) -> str:
        """What `ANTHROPIC_BASE_URL` must be to reach this endpoint.

        For an `openai` endpoint that is the proxy, never the endpoint itself:
        the worker talks Anthropic to the proxy and the proxy talks OpenAI
        onward. Empty for the default endpoint, which needs no redirect.
        """
        return self.proxy_url if self.protocol == "openai" else self.base_url


DEFAULT_ENDPOINT_NAME = "anthropic"

# The implicit endpoint. Everything that exists today runs here.
DEFAULT_ENDPOINT = Endpoint(name=DEFAULT_ENDPOINT_NAME)


@dataclass(frozen=True)
class Assignment:
    """The endpoint, model and budget for one phase of one task."""

    endpoint: Endpoint
    model: str

    @property
    def context_tokens(self) -> int:
        """0 means "say nothing and let the CLI use its catalog"."""
        return self.endpoint.context_tokens.get(self.model, 0)

    def max_turns(self, base: int) -> int:
        return max(1, round(base * self.endpoint.max_turns_multiplier))


@dataclass(frozen=True)
class Repo:
    name: str
    verify: str
    base: str = "main"
    # Empty means the `~/Projects/<repo-name>` convention. See `clone_dir`.
    dir: str = ""
    # How many of THIS repo's tasks may run at once. One slot per repo is the
    # default because the alternative — a global pool handed out in config
    # order — starves everything below the first repo: `claim_next` refills
    # from the top of the list on every free slot, so a repo with a deep queue
    # holds them all indefinitely. Observed 2026-08-07, when swift-app sat
    # at "0 working, 2 ready" for a full day behind sample.
    #
    # This is the FAIRNESS knob. `Config.concurrency` is still the machine-wide
    # ceiling; the sum of these may exceed it and whichever binds first wins.
    concurrency: int = 1
    # Per-repo `[repos.models]`, overriding the global `[models]` for this repo
    # alone. This is the setting that makes endpoints worth having: a
    # low-stakes repo can run local while anything that matters stays on the
    # subscription.
    models: dict[str, str] = field(default_factory=dict)

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
    # Tasks per SUBSCRIPTION WINDOW, not per night and not per process. The
    # loop holds here and resumes on the `resetsAt` the runs report, so with a
    # five-hour window this authorises ~4.8x its face value per day. The name
    # is kept for config compatibility and is now a poor description.
    max_tasks_per_night: int = 8
    concurrency: int = 1
    implement_model: str = "sonnet"
    review_model: str = "opus"
    # One retry with the reviewer's feedback, then escalate. Two agents
    # disagreeing twice is a human's problem, not a budget to keep spending.
    review_attempts: int = 2
    # `claude -p --max-turns` for the implement phase. Was hardcoded 100 in two
    # places (worker.implement's default and task.py's call site) until
    # 2026-08-08, when three sample runs died at exactly 101 in one day —
    # #65, #67 and #106 — costing ~$25 and producing no branch at all.
    #
    # A truncation is the worst outcome the daemon can produce: it spends the
    # entire budget and yields nothing, not even a diff to read. So the marginal
    # turns are cheap against the downside, which is why the default moves up
    # rather than staying put.
    #
    # It is NOT the real fix, and raising it further is not either. All three of
    # those issues were oversized, and #106 was already a split. The cost is
    # ORIENTATION — dashboard's MatrixView.tsx is 1,469 lines, and an issue that
    # says "wire a nav deck" makes the worker find everything before it can
    # start. The fix is smaller issues with file:line anchors; this only stops
    # a task that was nearly done from dying on the last turn.
    #
    # Do not read the turn counts in merged PR bodies (104, 114, 147, 227) as
    # evidence this cap is not binding — those are implement + review summed
    # across attempts (task.py's Report.turns), not one phase.
    implement_max_turns: int = 140
    # The reviewer reads a finished diff and writes a verdict; it has never
    # truncated. Separate knob so raising the implementer never quietly widens
    # the thing that is working.
    review_max_turns: int = 60
    worktree_root: Path = Path.home() / "Projects" / "nightshift-wt"
    slack_webhook: str = ""
    # A repo the token must NOT be able to read. Without one, `preflight`
    # can only show that scoping did not break the daemon — never that it
    # actually scoped anything.
    forbidden_probe: str = ""
    # Declared `[[endpoints]]`. Empty is today's behaviour: every phase runs on
    # the implicit subscription endpoint.
    endpoints: list[Endpoint] = field(default_factory=list)
    # Read by nobody yet — there is no chores phase. Parsed so preflight can
    # validate the assignment before something starts running it.
    chores_model: str = ""
    #: Legacy PR #6 option; parsed for compatibility, currently inactive.
    #: Task outcomes replace automatic phase mirroring (SPEC-paseo-outcomes.md).
    mirror_to_paseo: bool = False
    notes: list[str] = field(default_factory=list)

    def endpoint(self, name: str) -> Endpoint:
        """A declared endpoint by name, else the implicit default.

        The default is returned rather than raised on, because a config with no
        `[[endpoints]]` at all must resolve `"sonnet"` to somewhere. A name
        that was MEANT to be an endpoint and is not declared is caught by
        `undeclared_endpoint_refs`, not here — see `parse_model_ref`.
        """
        for ep in self.endpoints:
            if ep.name == name:
                return ep
        return DEFAULT_ENDPOINT

    def parse_model_ref(self, spec: str) -> tuple[str, str]:
        """`endpoint:model` → (endpoint, model). A bare name → the default.

        The colon is ambiguous and must be resolved against the declared
        endpoints rather than by splitting: Ollama model names carry colons of
        their own (`qwen3.8:27b`, `glm-4.7-flash:latest`), so a blind split
        would read `qwen3.8` as an endpoint. The prefix is an endpoint only if
        one is DECLARED under that name; otherwise the whole string is a model
        on the default endpoint, which is also what makes every existing config
        keep its meaning.
        """
        prefix, sep, rest = spec.partition(":")
        if sep and any(ep.name == prefix for ep in self.endpoints):
            return prefix, rest
        return DEFAULT_ENDPOINT_NAME, spec

    def model_spec(self, phase: str, repo: Repo | None = None) -> str:
        """The raw `[models]` entry for a phase, per-repo override winning."""
        if repo is not None and phase in repo.models:
            return repo.models[phase]
        return {
            "implement": self.implement_model,
            "review": self.review_model,
            "chores": self.chores_model,
        }.get(phase, "")

    def assign(self, phase: str, repo: Repo | None = None) -> Assignment:
        """Where this phase runs and on what."""
        name, model = self.parse_model_ref(self.model_spec(phase, repo))
        return Assignment(endpoint=self.endpoint(name), model=model)

    def foreign_auth_envs(self, endpoint: Endpoint) -> tuple[str, ...]:
        """Every OTHER endpoint's credential var, for the worker env to drop.

        A worker holds exactly the credential belonging to its endpoint. The
        subscription token is handled separately in `worker._env` because it
        is not named by an `auth_env`.
        """
        return tuple(
            ep.auth_env
            for ep in self.endpoints
            if ep.auth_env and ep.name != endpoint.name
        )

    def models_for(self, phase: str, endpoint: str) -> list[str]:
        """The models this endpoint actually runs `phase` with.

        Narrower than `endpoint.models`, and the difference is minutes: the
        read-only probe costs a full turn budget per model, so probing an
        endpoint's whole catalogue to check a contract that applies to one of
        them spends the time proving nothing.
        """
        scopes = self.repos or [None]
        return sorted(
            {
                a.model
                for a in (self.assign(phase, repo) for repo in scopes)
                if a.endpoint.name == endpoint and a.model
            }
        )

    def undeclared_endpoint_refs(self) -> list[str]:
        """`[models]` entries that look like `endpoint:model` but name nothing.

        A typo'd endpoint would otherwise resolve to the whole string as a
        model name on the SUBSCRIPTION endpoint — a silent fallback that turns
        free work into billed work, which is the failure §6 refuses. Cheap
        enough to be a pure config check, so preflight runs it with no network.
        """
        declared = {ep.name for ep in self.endpoints}
        specs = [self.implement_model, self.review_model, self.chores_model]
        specs += [m for r in self.repos for m in r.models.values()]
        return [
            spec
            for spec in specs
            if spec
            and ":" in spec
            and spec.split(":")[0] not in declared
            and spec.split(":")[0] not in {"claude", "anthropic"}
        ]


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    raw = tomllib.loads(path.read_text())

    daemon = raw.get("daemon", {})
    endpoints = [_endpoint(e) for e in raw.get("endpoints", [])]
    if len({e.name for e in endpoints}) != len(endpoints):
        raise ValueError("endpoint names must be unique")
    for endpoint in endpoints:
        errors = endpoint.configuration_errors()
        if errors:
            raise ValueError(f"invalid endpoint {endpoint.name!r}: {'; '.join(errors)}")
    models = raw.get("models", {})
    labels = raw.get("labels", {})

    cfg = Config(
        repos=[
            Repo(
                name=r["name"],
                verify=r["verify"],
                base=r.get("base", "main"),
                dir=r.get("dir", ""),
                concurrency=r.get("concurrency", 1),
                models=dict(r.get("models", {})),
            )
            for r in raw.get("repos", [])
        ],
        labels=Labels(
            ready=labels.get("ready", "agent:ready"),
            working=labels.get("working", "agent:working"),
            done=labels.get("done", "agent:done"),
            needs_human=labels.get("needs_human", "needs-human"),
        ),
        poll_seconds=daemon.get("poll_seconds", 120),
        max_tasks_per_night=daemon.get("max_tasks_per_night", 8),
        concurrency=daemon.get("concurrency", 1),
        implement_model=models.get("implement", "sonnet"),
        review_model=models.get("review", "opus"),
        review_attempts=daemon.get("review_attempts", 2),
        implement_max_turns=daemon.get("implement_max_turns", 140),
        review_max_turns=daemon.get("review_max_turns", 60),
        worktree_root=Path(
            daemon.get("worktree_root", str(Path.home() / "Projects" / "nightshift-wt"))
        ),
        slack_webhook=_webhook(raw.get("notify", {})),
        forbidden_probe=daemon.get("forbidden_probe", ""),
        endpoints=endpoints,
        chores_model=models.get("chores", ""),
        mirror_to_paseo=daemon.get("mirror_to_paseo", False),
    )
    unresolved = cfg.undeclared_endpoint_refs()
    if unresolved:
        raise ValueError("model assignment names an undeclared endpoint")
    return cfg


def _endpoint(raw: dict) -> Endpoint:
    if not isinstance(raw.get("models", []), list):
        raise ValueError("endpoint models must be a list of model identifiers")
    if not isinstance(raw.get("name"), str) or not raw["name"] or ":" in raw["name"]:
        raise ValueError("endpoint name must be nonempty and cannot contain ':'")
    return Endpoint(
        name=raw["name"],
        protocol=raw.get("protocol", "anthropic"),
        billing=raw.get("billing", "subscription"),
        base_url=raw.get("base_url", ""),
        auth_env=raw.get("auth_env", ""),
        models=tuple(raw.get("models", ())),
        context_tokens={k: int(v) for k, v in (raw.get("context_tokens") or {}).items()},
        max_turns_multiplier=float(raw.get("max_turns_multiplier", 1.0)),
        warmup_timeout_s=int(raw.get("warmup_timeout_s", 180)),
        proxy_url=raw.get("proxy_url", ""),
        driver=raw.get("driver", "claude-code"),
        auth=raw.get("auth", ""),
        reasoning_effort=raw.get("reasoning_effort", "medium"),
        max_runtime_s=raw.get("max_runtime_s", 1800),
        max_tool_calls=raw.get("max_tool_calls", 100),
        max_output_tokens_total=raw.get("max_output_tokens_total", 32000),
    )


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
