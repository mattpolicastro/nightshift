"""Checks to run BEFORE an unattended night, not after it goes wrong.

Every check here exists because its failure mode is silent. An unset webhook
is a no-op, an over-scoped token works perfectly until it does damage, and a
pause file left behind looks exactly like an empty queue.

The negative control is the point. `~/.config/nightshift/env` holding a token
that reaches the sandbox proves nothing about what else it reaches — the same
trap as the vault's auth finding, where a valid-looking `claude -p` run was a
false positive because an interactive login was answering instead of the
token. A credential test without a thing that must FAIL is not a test.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import worker
from .config import Config, Endpoint


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    @property
    def line(self) -> str:
        return f"{'ok  ' if self.ok else 'FAIL'}  {self.name}: {self.detail}"


def _gh_can_read(repo: str, *, runner=None) -> bool:
    """Can the ambient GH_TOKEN see this repo at all?"""
    run = runner or _default_runner
    try:
        run(["gh", "repo", "view", repo, "--json", "name"])
        return True
    except Exception:
        return False


# A listing, not a generation: it does not load a model, so it answers fast on
# a healthy endpoint whatever the cold-load cost of the model itself. The
# generous `warmup_timeout_s` belongs to the tool-loop probe below, which does
# load one — a 17GB model takes minutes cold and under a second warm, and a
# short timeout there reports a healthy endpoint as down (SPEC §1.1).
_LIST_TIMEOUT_S = 30

_BANNED_VARS = {
    "ANTHROPIC_API_KEY": (
        "set — it silently outranks the OAuth token and moves workers onto "
        "metered billing. It is not a way to configure an endpoint."
    ),
    "ANTHROPIC_BASE_URL": (
        "set — an endpoint is configured with an `[[endpoints]]` block, not "
        "here. `worker._env` strips this AFTER merging the env file, so it is "
        "not an override; it is a redirect that will not happen."
    ),
    "ANTHROPIC_AUTH_TOKEN": (
        "set — a credential belongs to an endpoint, named by that endpoint's "
        "`auth_env`. Ambient, it would travel to whichever endpoint ran next."
    ),
}


def _default_prober(endpoint: Endpoint, token: str) -> list[str]:
    """Model names the endpoint reports. Raises if it cannot be reached."""
    request = urllib.request.Request(
        endpoint.url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    with urllib.request.urlopen(request, timeout=_LIST_TIMEOUT_S) as response:
        payload = json.loads(response.read().decode())
    return [str(m.get("id", "")) for m in (payload.get("data") or [])]


def _endpoint_checks(cfg: Config, env: dict[str, str], *, prober=None,
                     deep: bool = False) -> list[Check]:
    """Everything a second endpoint can get wrong before a task is claimed.

    Ordered cheapest-first and each one independent, because a config mistake
    should be legible without the endpoint being up: a missing
    `context_tokens` entry needs no network at all, and it is the one whose
    failure is silent — absent it the run inherits an assumed 200k window,
    wasting a 262k model or overrunning a smaller one.
    """
    checks: list[Check] = []

    stray = cfg.undeclared_endpoint_refs()
    if stray:
        # Left alone this resolves to the whole string as a MODEL name on the
        # subscription endpoint — a silent fallback that turns free work into
        # billed work, which is the failure §6 refuses.
        checks.append(
            Check(
                "endpoint refs",
                False,
                f"no endpoint declared for: {', '.join(stray)} — these would "
                "resolve to the default endpoint as literal model names",
            )
        )

    # Every place a phase can be routed, including per repo. A `[repos.models]`
    # override is the RECOMMENDED way to route — one repo local, everything
    # that matters on the subscription — so leaving it out of this loop would
    # have made the most likely routing the silent one.
    for repo in [None, *cfg.repos]:
        for phase in ("implement", "review", "chores"):
            if not cfg.model_spec(phase, repo):
                continue
            a = cfg.assign(phase, repo)
            if a.endpoint.is_default:
                continue
            if repo is not None and cfg.assign(phase).endpoint.name == a.endpoint.name:
                continue  # already said globally; do not say it twice per repo
            where = f"{phase} endpoint" + (f" {repo.name}" if repo else "")
            # Not a failure — a decision the operator has to see stated on the
            # night they made it rather than discover in a config file three
            # weeks later. `review` is the safety property: branch-only
            # autonomy is safe BECAUSE a separate skeptical reviewer sees the
            # diff, and it has caught a CI-green diff CI could not.
            checks.append(
                Check(
                    where,
                    True,
                    f"{a.endpoint.name}:{a.model} — NOT the default endpoint"
                    + (". The reviewer is the gate that makes branch-only autonomy "
                       "safe; a weaker one manufactures confidence." if phase == "review" else ""),
                )
            )

    for ep in cfg.endpoints:
        if ep.is_default:
            continue
        checks += _one_endpoint(cfg, ep, env, prober=prober, deep=deep)
    return checks


def _one_endpoint(cfg: Config, ep: Endpoint, env: dict[str, str], *,
                  prober=None, deep: bool = False) -> list[Check]:
    checks: list[Check] = []
    name = f"endpoint {ep.name}"

    if ep.protocol == "openai" and not ep.proxy_url:
        # `claude -p` speaks Anthropic and nothing else, so this endpoint
        # cannot be driven at all. Refusing here beats failing at invocation.
        checks.append(
            Check(name, False, "protocol = \"openai\" with no `proxy_url` — "
                               "`claude -p` speaks Anthropic only, so nothing can reach it")
        )
        return checks
    if not ep.url:
        checks.append(Check(name, False, "no `base_url`"))
        return checks

    token = env.get(ep.auth_env, "") if ep.auth_env else ""
    checks.append(
        Check(
            f"{name} credential",
            bool(token),
            f"{ep.auth_env} is set" if token
            else f"`auth_env = \"{ep.auth_env}\"` is unset — the CLI requires a "
                 "token even where the endpoint ignores its value",
        )
    )

    missing_windows = [m for m in ep.models if m not in ep.context_tokens]
    checks.append(
        Check(
            f"{name} context windows",
            not missing_windows,
            "every model declares one" if not missing_windows
            else f"no `context_tokens` for: {', '.join(missing_windows)} — the "
                 "run silently inherits an assumed 200k window",
        )
    )

    # Asserted isolation is not proven isolation — the lesson `forbidden_probe`
    # already encodes for the GitHub token. Build the environment a worker on
    # this endpoint would actually get, and look inside it.
    try:
        worker_env = worker._env(  # noqa: SLF001 — same package, and the point
            ep, foreign_auth_envs=cfg.foreign_auth_envs(ep)
        )
        leaked = sorted(
            k for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
            if worker_env.get(k)
        )
        leaked += sorted(
            k for k in cfg.foreign_auth_envs(ep) if worker_env.get(k)
        )
        pointed = worker_env.get("ANTHROPIC_BASE_URL", "")
        checks.append(
            Check(
                f"{name} isolation",
                not leaked and pointed == ep.url,
                f"worker env carries only this endpoint's credential, pointed at {pointed}"
                if not leaked and pointed == ep.url
                else f"LEAKED into a non-Anthropic worker: {', '.join(leaked)}"
                if leaked
                else f"points at {pointed or 'nothing'}, not {ep.url}",
            )
        )
    except ValueError as exc:
        checks.append(Check(f"{name} isolation", False, str(exc)))

    probe = prober or _default_prober
    try:
        available = probe(ep, token)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        checks.append(Check(f"{name} reachable", False, f"{ep.url}: {exc}"))
        return checks
    checks.append(Check(f"{name} reachable", True, f"{ep.url} answers"))

    # Advisory in config, checked here: a model absent from its endpoint fails
    # before a task is claimed rather than one wasted run later.
    absent = [m for m in ep.models if m not in available and f"{m}:latest" not in available]
    checks.append(
        Check(
            f"{name} models",
            not absent,
            "all present" if not absent
            else f"absent from the endpoint: {', '.join(absent)}",
        )
    )

    if deep:
        for model in ep.models:
            ok, detail = worker.probe(
                ep, model,
                context_tokens=ep.context_tokens.get(model, 0),
                foreign_auth_envs=cfg.foreign_auth_envs(ep),
            )
            checks.append(Check(f"{name} tool loop {model}", ok, detail))
        # Only the models that actually REVIEW here — not the endpoint's whole
        # catalogue, and nothing at all on an endpoint that only implements.
        # The read-only contract is the reviewer's, and this probe costs a full
        # turn budget per model, so the narrowing is minutes rather than tidiness.
        for model in cfg.models_for("review", ep.name):
            ok, detail = worker.probe_readonly(
                ep, model,
                context_tokens=ep.context_tokens.get(model, 0),
                foreign_auth_envs=cfg.foreign_auth_envs(ep),
            )
            checks.append(Check(f"{name} read-only {model}", ok, detail))
    return checks


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def run(cfg: Config, env: dict[str, str], *, runner=None, prober=None,
        deep: bool = False) -> list[Check]:
    """Everything that should be true before the daemon is left alone."""
    checks: list[Check] = []

    token = env.get("GH_TOKEN", "")
    checks.append(
        Check("GH_TOKEN", bool(token), "set" if token else "unset — gh will fall back to your personal login")
    )
    if token.startswith("gho_"):
        checks.append(
            Check(
                "GH_TOKEN scope",
                False,
                "this is a `gho_` OAuth credential from `gh auth login` — it reaches "
                "every repo the account owns. Replace with a fine-grained PAT.",
            )
        )
    elif token.startswith("github_pat_"):
        checks.append(Check("GH_TOKEN scope", True, "fine-grained PAT"))

    checks.append(
        Check(
            "CLAUDE_CODE_OAUTH_TOKEN",
            bool(env.get("CLAUDE_CODE_OAUTH_TOKEN")),
            "set" if env.get("CLAUDE_CODE_OAUTH_TOKEN") else "unset — workers cannot authenticate",
        )
    )
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        if env.get(k):
            checks.append(Check(k, False, _BANNED_VARS[k]))

    for repo in cfg.repos:
        reachable = _gh_can_read(repo.name, runner=runner)
        checks.append(
            Check(f"reach {repo.name}", reachable, "readable" if reachable else "NOT readable — the token cannot see an enrolled repo")
        )
        # A verify command the workers' own allow-list forbids. Silent in the
        # same way as everything else here: the daemon starts, the repo looks
        # enrolled, and the gap surfaces only as a worker escalation that reads
        # like task ambiguity rather than config. swift-app burned a full
        # run on this before anyone noticed the allow-list was all JS verbs.
        blocked = worker.unrunnable_verify_clauses(repo.verify)
        checks.append(
            Check(
                f"verify runnable {repo.name}",
                not blocked,
                "every clause is permitted" if not blocked
                else f"workers CANNOT run: {'; '.join(blocked)} — "
                     "add the verb to worker._IMPLEMENT_ALLOWED",
            )
        )

    # The half that actually proves scoping.
    if cfg.forbidden_probe:
        leaked = _gh_can_read(cfg.forbidden_probe, runner=runner)
        checks.append(
            Check(
                f"cannot reach {cfg.forbidden_probe}",
                not leaked,
                "correctly out of reach" if not leaked
                else "READABLE — the token is broader than the enrolled repos",
            )
        )
    else:
        checks.append(
            Check(
                "negative control",
                False,
                "no `forbidden_probe` configured — token scoping is asserted, not proven",
            )
        )

    checks += _endpoint_checks(cfg, env, prober=prober, deep=deep)

    checks.append(
        Check(
            "alerts",
            bool(cfg.slack_webhook),
            "slack webhook configured" if cfg.slack_webhook
            else "unconfigured — an unattended night reports nothing",
        )
    )
    return checks
