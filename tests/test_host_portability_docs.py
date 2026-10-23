"""Offline operator guidance checks; real second-install checks remain manual."""

import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from nightshift import config
from nightshift.host_paths import host_path, tool_path
from test_specloop_documentation_integrity import LINK, heading_ids


ROOT = Path(__file__).resolve().parents[1]
DOCS = ("KNOWN-LIMITATIONS.md", "README.md", "docs/specloop/prepare.md",
        "plugins/nightshift/README.md")


def read(path):
    return (ROOT / path).read_text()


def require(text, *phrases):
    normalized = " ".join(text.split())
    for phrase in phrases:
        assert phrase.lower() in normalized.lower(), f"Missing guidance: {phrase}"


@pytest.mark.scenario(
    "specloop-documentation/host-portability-guidance/operator-configures-an-alternate-layout"
)
def test_settings_and_inheritance_are_documented():
    limits = read("KNOWN-LIMITATIONS.md").split("## Platform assumptions", 1)[1]
    rows = re.findall(r"^\| `(NIGHTSHIFT_\w+)` \| `([^`]+)` \| (.+) \|$",
                      limits, re.MULTILINE)
    assert {name: default for name, default, _ in rows} == {
        "NIGHTSHIFT_BIN_DIR": "/opt/homebrew/bin",
        "NIGHTSHIFT_NODE_BIN": "~/n/bin",
        "NIGHTSHIFT_UV": "<resolved NIGHTSHIFT_BIN_DIR>/uv",
        "NIGHTSHIFT_CHECKOUT": "~/Projects/nightshift",
    }
    for name, _, _ in rows:
        assert name in read("README.md")
        assert name in read("plugins/nightshift/README.md")
        assert name in read("config.example.toml")
    require(limits, "Python consumes only `NIGHTSHIFT_BIN_DIR`",
            "file values win over inherited values", "after bootstrap",
            "`bin/specloop-serve` does not source that file",
            "restart the affected daemon, supervisor or Paseo host",
            "no hot reload", "Candidate files and manifest env files cannot configure",
            "no `$VAR`, glob or shell-substitution expansion",
            "colons in the two PATH directory settings", "without falling back")
    for path in ("README.md", "docs/specloop/prepare.md"):
        require(read(path), "`NIGHTSHIFT_BIN_DIR` (default `/opt/homebrew/bin`)",
                "`/usr/bin:/bin:/usr/sbin:/sbin`", "`NIGHTSHIFT_NODE_BIN`",
                "PATH selection grants no sandbox read access",
                "manifest variables or `[sandbox].prepare_read`", "credential denials")
        assert "fixed `PATH`" not in read(path)
    require(read("plugins/nightshift/README.md"), "Paseo host process environment",
            "Restart the Paseo host", "never RPC inputs",
            "Attention's installation check uses the same configured checkout")


@pytest.mark.scenario(
    "specloop-documentation/host-portability-guidance/operator-configures-an-alternate-layout"
)
def test_alternate_layout_examples_resolve_without_launching_services(tmp_path, monkeypatch):
    # Parse the published shell examples as literal argv; never execute a service.
    plugin = read("plugins/nightshift/README.md")
    exports = next(block for block in re.findall(r"```sh\n(.*?)```", plugin, re.S)
                   if "export NIGHTSHIFT_BIN_DIR=" in block)
    env = {}
    for line in exports.splitlines():
        command, assignment = shlex.split(line)
        assert command == "export"
        key, value = assignment.split("=", 1)
        env[key] = host_path(key, "/unused", {key: value}, tmp_path)
    assert tool_path(env=env) == "/alternate/tools:/usr/bin:/bin:/usr/sbin:/sbin"
    direct = next(block for block in re.findall(r"```sh\n(.*?)```", read("README.md"), re.S)
                  if 'cd "/alternate/nightshift"' in block)
    cd, invocation = direct.strip().splitlines()
    assert shlex.split(cd) == ["cd", env["NIGHTSHIFT_CHECKOUT"]]
    assert shlex.split(invocation) == [
        f"NIGHTSHIFT_BIN_DIR={env['NIGHTSHIFT_BIN_DIR']}", env["NIGHTSHIFT_UV"],
        "run", "--no-sync", "specloop", "serve",
    ]
    for snippet in (exports, direct):
        subprocess.run(["/bin/sh", "-n"], input=snippet, text=True, check=True)

    preparation = []
    for path in ("README.md", "docs/specloop/prepare.md"):
        snippet = next(block for block in re.findall(r"```toml\n(.*?)```", read(path), re.S)
                       if "[[prepare]]" in block)
        preparation.append(tomllib.loads(snippet))
    assert preparation[0] == preparation[1]
    assert preparation[0]["vars"]["node_bin"] == "~/n/bin"
    assert preparation[0]["sandbox"]["prepare_read"] == ["~/n"]

    example = read("config.example.toml")
    tomllib.loads(example)  # The full example must remain valid TOML.
    default = re.search(r'^# worktree_root = "([^"]+)"$', example, re.M).group(1)
    assert default == "~/Projects/nightshift-wt"
    home = tmp_path / "host home"
    monkeypatch.setenv("HOME", str(home))
    selected = tmp_path / "selected.toml"
    selected.write_text(example)
    assert config.load(selected).worktree_root == home / "Projects/nightshift-wt"
    selected.write_text(example.replace(f'# worktree_root = "{default}"',
                                        f'worktree_root = "{default}"'))
    assert config.load(selected).worktree_root == home / "Projects/nightshift-wt"
    selected.write_text('[daemon]\nworktree_root = "worker trees"\n')
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert config.load(selected).worktree_root == selected.parent / "worker trees"
    assert not (selected.parent / "worker trees").exists()
    for path in ("README.md", "KNOWN-LIMITATIONS.md", "config.example.toml"):
        require(read(path), "~/Projects/nightshift-wt", "selected config file",
                "absolute path before restart")


@pytest.mark.scenario(
    "specloop-documentation/host-portability-guidance/operator-configures-an-alternate-layout"
)
@pytest.mark.parametrize("path", DOCS)
def test_portability_documentation_links_resolve(path):
    document = ROOT / path
    for _, target in LINK.findall(document.read_text()):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = document.parent / unquote(url.path) if url.path else document
        assert destination.exists(), f"{path}: broken link {target}"
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text()), target


@pytest.mark.scenario(
    "specloop-documentation/host-portability-guidance/platform-claims-retain-their-evidence-limits"
)
def test_platform_limits_keep_pending_host_evidence_explicit():
    platform = read("KNOWN-LIMITATIONS.md").split("## Platform assumptions", 1)[1]
    require(platform, "**macOS only.**", "`sandbox-exec`", "launchd agents",
            "**The attention page and Factory section need a Paseo host.**",
            "[issue #15](https://github.com/mattpolicastro/nightshift/issues/15)",
            "Automated tests check configuration propagation, not end-to-end portability",
            "real second macOS installation", "remains unvalidated",
            "Record its layout, launch method and results", "disposable prepare/verify",
            "agent/quota tool lookup and real sandbox denials",
            "existing worktrees did not move", "new worker placement",
            "actual Paseo host", "action requiring `npx`",
            "finish only after its normal human checks", "confirmation/error behavior",
            "checks remain pending", "temporary directory on the original machine or CI")
    assert "/pull/" not in platform  # No invented implementation PR.
    assert "the path is fixed for now" not in read("plugins/nightshift/README.md")


@pytest.mark.scenario(
    "specloop-documentation/host-portability-guidance/frozen-service-wrappers-remain-explicit"
)
def test_fixed_bootstrap_commands_and_guarded_rollout_are_explicit():
    limits = read("KNOWN-LIMITATIONS.md")
    # The documented commands must agree with the unchanged, frozen wrappers.
    for name, command, path in (
        ("nightshift-daemon", "/opt/homebrew/bin/uv run nightshift run",
         "/opt/homebrew/bin:$HOME/n/bin:$PATH"),
        ("specloop-serve", "/opt/homebrew/bin/uv run --no-sync specloop serve",
         "$HOME/n/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"),
    ):
        wrapper = read(f"bin/{name}")
        assert f"exec {command}" in wrapper and f"`{command}`" in limits
        assert f'export PATH="{path}"' in wrapper and f"`{path}`" in limits
        assert 'cd "$HOME/Projects/nightshift"' in wrapper
    for path in ("README.md", "KNOWN-LIMITATIONS.md", "plugins/nightshift/README.md"):
        require(read(path), "`bin/nightshift-daemon`", "`bin/specloop-serve`",
                "`bin/**` is frozen", "settings do not relocate",
                "bundled service startup", "separate wrapper work",
                "independently provisioned operator service",
                "equivalent credential and preflight guards")
    require(limits, "changing a plist's wrapper pathname",
            "No new helper changes this", "OAuth/GitHub tokens",
            "clear conflicting Anthropic API credentials/base URL",
            "check required tools", "preserve startup refusal and preflight checks")
