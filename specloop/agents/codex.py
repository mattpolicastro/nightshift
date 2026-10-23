"""`codex exec`, with the ChatGPT login or an OpenAI-compatible local backend.

Spike S1 (2026-10-19), probed by the harness through this exact argv:
workspace-write lets the agent write only its workspace — /tmp, $TMPDIR, .git,
commits and all network (internet and tailnet) are refused; read-only refuses
every write. Reads are unrestricted, credential files included. The --disable
list is what it took to strip apps, browser, computer-use, hooks, plugins,
memories, multi-agent, goals and image tools from the session.
"""

import json
import os
import re
from pathlib import Path

from specloop.agents import Call, Result, base_env, read_jsonl, spawn

DISABLE = ("apps", "browser_use", "browser_use_external", "browser_use_full_cdp_access", "computer_use",
           "in_app_browser", "hooks", "plugins", "remote_plugin", "memories", "skill_mcp_dependency_install",
           "multi_agent", "goals", "image_generation")
USAGE = re.compile(r"usage limit|rate limit|quota|too many requests|\b429\b", re.I)
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


def _home(call: Call) -> Path:
    home = call.home
    home.mkdir(parents=True, exist_ok=True)
    auth = home / "auth.json"
    if not call.profile.base_url and not auth.exists():
        os.symlink(Path.home() / ".codex" / "auth.json", auth)
    return home


def argv(call: Call, schema_path: Path, out_path: Path) -> list[str]:
    p = call.profile
    a = ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
         "-s", "workspace-write" if call.access == "write" else "read-only",
         "-c", 'approval_policy="never"', "-c", 'web_search="disabled"', "-m", p.model]
    if call.access == "write":
        a += ["-c", "sandbox_workspace_write.network_access=false",
              "-c", "sandbox_workspace_write.exclude_slash_tmp=true"]
    if p.effort:
        a += ["-c", f'model_reasoning_effort="{p.effort}"']
    if p.base_url:
        # Untested as of 2026-10-19: S1 drove a local inference box through the claude adapter.
        a += ["-c", 'model_provider="specloop_local"',
              "-c", f'model_providers.specloop_local={{name="local", base_url="{p.base_url}", wire_api="chat"}}']
    for f in DISABLE:
        a += ["--disable", f]
    return a + ["--output-schema", str(schema_path), "--json", "-o", str(out_path), "-C", str(call.cwd), "-"]


def run(call: Call) -> Result:
    d = call.transcript.parent
    d.mkdir(parents=True, exist_ok=True)
    schema_path, out_path = d / f"{call.transcript.stem}.schema.json", d / f"{call.transcript.stem}.out.json"
    schema_path.write_text(json.dumps(call.schema))
    out_path.unlink(missing_ok=True)
    # Compilers need a writable temp dir; /tmp stays shut, so each session gets
    # a private one as $TMPDIR, which workspace-write leaves writable.
    tmp = d / f"{call.transcript.stem}.tmp"
    tmp.mkdir(exist_ok=True)
    env = base_env("CODEX_HOME", _home(call), TMPDIR=str(tmp))
    code, timeout = spawn(argv(call, schema_path, out_path), env, call, stdin=call.prompt)

    session, errors, usage = "", [], {}
    diagnostics: list[str] = []
    for e in read_jsonl(call.transcript):
        if e.get("type") == "thread.started":
            session = e.get("thread_id", "")
        elif e.get("type") in ("error", "turn.failed"):
            errors.append(json.dumps(e)[:500])
            error = e.get("error")
            # Match decoded messages: JSON escaping hides boundaries after newlines.
            diagnostics.extend(value for value in (e.get("message"),
                error.get("message") if isinstance(error, dict) else error) if isinstance(value, str))
        elif e.get("type") == "turn.completed":
            usage = e.get("usage", {})
    stderr = call.transcript.with_suffix(".stderr").read_text(errors="replace")[-2000:]
    if timeout:
        return Result("infra", session=session, detail=timeout, usage=usage)
    if code != 0:
        text = " ".join(errors) + stderr
        diagnostic = "\n".join([*diagnostics, stderr])
        kind = "usage_limit" if USAGE.search(text) else "unavailable" if UNAVAILABLE.search(diagnostic) else "infra"
        return Result(kind, session=session, detail=f"exit {code}: {text[-600:]}", usage=usage)
    try:
        return Result("ok", json.loads(out_path.read_text()), session, usage=usage)
    except (OSError, json.JSONDecodeError) as e:
        return Result("invalid_output", session=session, detail=f"final message is not JSON: {e}", usage=usage)
