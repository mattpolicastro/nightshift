"""`claude -p`, with the Anthropic subscription or a local /v1/messages backend.

Spike S1 (2026-10-19), probed by the harness: with the Bash sandbox enabled
through --settings, writes outside the workspace and all network from tools
are refused, but .git is writable and a commit succeeds — which is why the
sealer ignores the agent's git entirely. Without --tools the session exposes
26 tools (cron, remote triggers, web fetch/search, workflows); --tools pins it.
Driving qwen3-coder-next on a local inference box through ANTHROPIC_BASE_URL worked with
the same flags, structured output included.

Never set ANTHROPIC_API_KEY: it silently outranks the OAuth token and moves
the run onto metered billing (see the nightshift CLAUDE.md).
"""

import json
import re

from nightshift.trace import parse_rate_limit
from specloop.agents import Call, Result, base_env, read_jsonl, spawn

SETTINGS = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}}
TOOLS = {"write": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"], "read": ["Bash", "Read", "Glob", "Grep"]}
DENY = ["Bash(git commit:*)", "Bash(git push:*)", "Bash(git reset:*)", "Bash(git checkout:*)"]
USAGE = re.compile(r"usage limit|rate limit|limit reached|quota|\b429\b", re.I)
UNAVAILABLE = re.compile(
    r"\b(?:logged[ -]out|not logged[ -]in"
    r"|(?:authentication|login|log[ -]in|sign[ -]in)(?: is)? required"
    r"|(?:please|must|need to) (?:log[ -]?in|sign[ -]in)"
    r"|(?:expired|invalid) (?:(?:auth(?:entication)?|oauth|access|refresh) )?token"
    r"|(?:(?:auth(?:entication)?|oauth|access|refresh) )?token (?:is |has (?:been )?)?(?:expired|invalid)"
    r"|(?:inactive|expired|cancelled|canceled) (?:plan|subscription)"
    r"|(?:plan|subscription) (?:is |has (?:been )?)?(?:inactive|expired|cancelled|canceled)"
    r"|401|403)\b", re.I,
)


def argv(call: Call) -> list[str]:
    a = ["claude", "-p", "--model", call.profile.model, "--output-format", "stream-json", "--verbose",
         "--json-schema", json.dumps(call.schema), "--settings", json.dumps(SETTINGS),
         "--tools", *TOOLS[call.access], "--disallowedTools", *DENY, "--max-turns", "400"]
    if call.access == "write":
        a += ["--permission-mode", "acceptEdits"]
    # Read/Glob refuse paths outside the working directory unless granted
    # (2026-10-20: a reviewer could not open its context and failed the task).
    for d in call.read_dirs:
        a += ["--add-dir", str(d)]
    return a


def env(call: Call) -> dict:
    home = call.home
    home.mkdir(parents=True, exist_ok=True)
    if call.profile.base_url:
        return base_env("CLAUDE_CONFIG_DIR", home, ANTHROPIC_BASE_URL=call.profile.base_url,
                        ANTHROPIC_AUTH_TOKEN="local")
    return base_env("CLAUDE_CONFIG_DIR", home, CLAUDE_CODE_OAUTH_TOKEN=call.secrets.get("CLAUDE_CODE_OAUTH_TOKEN", ""))


def run(call: Call) -> Result:
    code, timeout = spawn(argv(call), env(call), call, stdin=call.prompt)
    session, final = "", None
    rate_limit = None
    for e in read_jsonl(call.transcript):
        if not isinstance(e, dict):
            continue
        if (decoded := parse_rate_limit(e)) is not None:
            rate_limit = decoded
        if e.get("type") == "system" and e.get("subtype") == "init":
            session = e.get("session_id", "")
        elif e.get("type") == "result":
            final = e
    usage = (final or {}).get("usage", {})
    overage = rate_limit is not None and rate_limit.using_overage
    status = rate_limit.status if rate_limit else "unknown"
    if timeout and not overage:
        return Result("infra", session=session, detail=timeout, usage=usage, rate_limit=rate_limit)
    limited = overage or status == "rejected"
    if limited or not final or final.get("is_error") or final.get("subtype") != "success":
        stderr = call.transcript.with_suffix(".stderr").read_text(errors="replace")[-800:]
        text = json.dumps(final or {})[:800] + stderr
        # Token counts and other result metadata are not provider diagnostics.
        errors = (final or {}).get("errors", [])
        if not isinstance(errors, list):
            errors = [errors]
        # Keep decoded whitespace so multiline messages retain their word boundaries.
        diagnostic = "\n".join(value for value in [(final or {}).get("result"), *errors, stderr]
                               if isinstance(value, str))
        usage_diagnostic = "\n".join(value for value in [*errors, stderr] if isinstance(value, str))
        limited = limited or (status not in ("allowed", "allowed_warning")
                              and USAGE.search(usage_diagnostic) is not None)
        kind = "usage_limit" if limited else "unavailable" if UNAVAILABLE.search(diagnostic) else "infra"
        return Result(kind, session=session, detail=f"exit {code}: {text}", usage=usage, rate_limit=rate_limit)
    out = final.get("structured_output")
    if not isinstance(out, dict):
        return Result("invalid_output", session=session, detail="no structured_output in result",
                      usage=usage, rate_limit=rate_limit)
    return Result("ok", out, session, usage=usage, rate_limit=rate_limit)
