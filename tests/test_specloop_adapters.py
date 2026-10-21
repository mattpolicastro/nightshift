"""Pin the adapter flags that spike S1 showed are load-bearing.

Each assertion is a sandbox property the harness probed on 2026-10-19; read the
adapter module's docstring before changing one.
"""

import json
from pathlib import Path

from specloop import agents
from specloop.agents import claude, codex
from specloop.manifest import Profile


def call(profile, access="write", tmp=Path("/tmp/x")):
    return agents.Call(profile, access, "p", tmp / "work", {"type": "object"}, tmp / "t.jsonl", 60, 30, tmp / "home")


def test_codex_write_session_is_sandboxed_and_stripped():
    a = codex.argv(call(Profile("c", "codex", "gpt-6-astra", "high")), Path("s.json"), Path("o.json"))
    assert a[a.index("-s") + 1] == "workspace-write"
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert flag in a
    assert "sandbox_workspace_write.network_access=false" in a
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in a
    assert 'approval_policy="never"' in a and 'web_search="disabled"' in a
    disabled = {a[i + 1] for i, x in enumerate(a) if x == "--disable"}
    assert {"apps", "browser_use", "computer_use", "hooks", "plugins", "memories", "multi_agent",
            "goals", "image_generation"} <= disabled


def test_codex_review_session_is_read_only():
    a = codex.argv(call(Profile("c", "codex", "m"), "read"), Path("s.json"), Path("o.json"))
    assert a[a.index("-s") + 1] == "read-only" and "sandbox_workspace_write.network_access=false" not in a


def test_claude_tools_are_pinned_and_sandboxed():
    a = claude.argv(call(Profile("c", "claude", "sonnet")))
    tools = a[a.index("--tools") + 1:a.index("--disallowedTools")]
    assert tools == ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
    settings = json.loads(a[a.index("--settings") + 1])["sandbox"]
    assert settings == {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}
    ro = claude.argv(call(Profile("c", "claude", "sonnet"), "read"))
    assert "Edit" not in ro and "Write" not in ro and "acceptEdits" not in ro


def test_claude_env_never_carries_an_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    local = claude.env(call(Profile("q", "claude", "qwen", base_url="http://box:11434"), tmp=tmp_path))
    sub = claude.env(agents.Call(Profile("s", "claude", "sonnet"), "write", "p", tmp_path, {}, tmp_path / "t", 1, 1,
                                 tmp_path / "h", {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}))
    for env in (local, sub):
        assert "ANTHROPIC_API_KEY" not in env
    assert local["ANTHROPIC_BASE_URL"] == "http://box:11434" and "CLAUDE_CODE_OAUTH_TOKEN" not in local
    assert sub["CLAUDE_CODE_OAUTH_TOKEN"] == "tok" and "ANTHROPIC_BASE_URL" not in sub


def test_claude_is_granted_the_context_directory():
    c = agents.Call(Profile("c", "claude", "sonnet"), "read", "p", Path("/r/a2/review"), {}, Path("/r/t.jsonl"),
                    60, 30, Path("/h"), read_dirs=(Path("/r/a2/context"),))
    a = claude.argv(c)
    assert a[a.index("--add-dir") + 1] == "/r/a2/context"
