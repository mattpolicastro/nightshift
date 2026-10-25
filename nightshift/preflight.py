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
from dataclasses import dataclass

from .config import Config


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


def run(cfg: Config, env: dict[str, str], *, runner=None,
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

    for repo in cfg.repos:
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
