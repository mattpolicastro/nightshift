"""Checks to run BEFORE an unattended night, not after it goes wrong.

Every check here exists because its failure mode is silent. An unset webhook
is a no-op, an over-scoped token works perfectly until it does damage, and a
pause file left behind looks exactly like an empty queue.

The negative control is the point. `~/.config/nightshift/env` holding a token
that reaches the sandbox proves nothing about what else it reaches — the same
trap as an earlier auth finding, where a valid-looking `claude -p` run was a
false positive because an interactive login was answering instead of the
token. A credential test without a thing that must FAIL is not a test.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, fields
from urllib.parse import quote

from .config import Config
from .forge.http import GiteaHTTPError, GiteaTransport


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


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def _gitea_check(name, operation) -> Check:
    try:
        ok, detail = operation()
        return Check(name, ok, detail)
    except GiteaHTTPError as error:
        return Check(name, False, f"unverified — HTTP {error.status}" if error.status
                     else "unverified — connection failed")
    except Exception:
        # Neither remote bodies nor exception text are safe diagnostics.
        return Check(name, False, "unverified — request or evidence invalid")


def _repo_path(name: str) -> str:
    return "/api/v1/repos/" + "/".join(quote(part, safe="") for part in name.split("/"))


def _bot_identity(client, login: str) -> tuple[bool, str]:
    user = client.get_json("/api/v1/user")
    ok = (isinstance(user, dict) and isinstance(user.get("login"), str)
          and user["login"].casefold() == login.casefold() and user.get("is_admin") is False)
    return ok, "nonadmin bot verified" if ok else "configured nonadmin bot not established"


def _forbidden_repo(client, probe: str) -> tuple[bool, str]:
    guidance = "use an independently known real forbidden repository"
    if not probe:
        return False, "no forbidden_probe configured; " + guidance
    try:
        client.get_json(_repo_path(probe))
    except GiteaHTTPError as error:
        if error.status == 404:
            return True, "HTTP 404; " + guidance
        raise
    return False, "forbidden repository is readable"


def _complete_labels(client, path: str, required: set[str]) -> tuple[bool, str]:
    names: set[str] = set()
    ids: set[int] = set()
    for page in range(1, 1001):
        rows, _ = client.get_page(path + "/labels", {}, page=page, limit=50)
        if not rows:
            ok = required <= names
            return ok, "complete unique label map" if ok else "required labels missing"
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("id")) is not int
                    or row["id"] <= 0 or not isinstance(row.get("name"), str)
                    or not row["name"].strip() or row["id"] in ids or row["name"] in names):
                return False, "invalid or duplicate labels; completeness not established"
            ids.add(row["id"])
            names.add(row["name"])
    return False, "label pagination exhausted; completeness not established"


def _base_protection(client, path: str, base: str) -> tuple[bool, str]:
    record = client.get_json(path + "/branch_protections/" + quote(base, safe=""))
    ok = isinstance(record, dict) and record.get("branch_name") == base
    return ok, ("exact protection rule exists; policy adequacy not assessed" if ok
                else "unverified — exact base protection record required")


def _gitea_checks(cfg: Config, env: dict[str, str], transport_factory) -> list[Check]:
    checks = []
    required = {getattr(cfg.labels, f.name) for f in fields(cfg.labels)} | {"merge:ready", "merge:blocked"}
    for forge in cfg.forges:
        if forge.kind != "gitea":
            continue
        prefix = f"gitea {forge.name}"
        token = env.get(forge.token_env, "")
        if not token:
            checks.append(Check(prefix + " token", False, "bot token unset"))
            continue
        try:
            client = transport_factory(forge.url, token)
        except Exception:
            checks.append(Check(prefix + " transport", False, "bot transport unavailable"))
            continue
        checks.append(_gitea_check(prefix + " identity", lambda: _bot_identity(client, forge.bot_login)))
        checks.append(_gitea_check(prefix + " forbidden probe", lambda: _forbidden_repo(client, forge.forbidden_probe)))
        for repo in cfg.repos:
            if repo.forge != forge.name:
                continue
            path = _repo_path(repo.name)
            name = prefix + " " + repo.name
            checks.append(_gitea_check(name + " labels", lambda: _complete_labels(client, path, required)))
            checks.append(_gitea_check(name + " protection", lambda: _base_protection(client, path, repo.base)))
    return checks


def run(cfg: Config, env: dict[str, str], *, runner=None, transport_factory=None,
        deep: bool = False) -> list[Check]:
    """Everything that should be true before the daemon is left alone.

    transport_factory(url, token) supplies a GiteaTransport-compatible client.
    Gitea credentials come only from env; GitHub retains its ambient gh runner.
    """
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

    for repo in cfg.repos:
        if cfg.forge_for(repo).kind != "github":
            continue
        reachable = _gh_can_read(repo.name, runner=runner)
        checks.append(
            Check(f"reach {repo.name}", reachable, "readable" if reachable else "NOT readable — the token cannot see an enrolled repo")
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

    checks.extend(_gitea_checks(cfg, env, transport_factory or GiteaTransport))

    if deep:
        checks.append(Check("deep", True, "worker probes are retired; no model probes run"))

    checks.append(
        Check(
            "alerts",
            bool(cfg.slack_webhook),
            "slack webhook configured" if cfg.slack_webhook
            else "unconfigured — an unattended night reports nothing",
        )
    )
    return checks
