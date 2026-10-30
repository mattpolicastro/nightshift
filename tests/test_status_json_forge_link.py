"""Offline status links use only the run identity and public forge configuration."""

import json
import socket
import subprocess
from pathlib import Path

import pytest

from nightshift import config
from nightshift.cli import main
from specloop import cli, loop, seal
from test_specloop_status_json import TASKS, admitted, home
from test_state_roots import tree_snapshot


FORGES = '''[[forges]]
name = "other"
kind = "gitea"
url = "https://other.example/prefix%2Dkept/"
token_env = "TEST_BOT"
land_token_env = "TEST_LAND"
bot_login = "Robot"
[[forges]]
name = "selected"
kind = "gitea"
url = "https://Forge.Example:8443/git"
token_env = "TEST_BOT"
land_token_env = "TEST_LAND"
bot_login = "Robot"
'''


def forbidden(*args, **kwargs):
    pytest.fail("status attempted credentials, execution, or remote access")


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_PATH", tmp_path / "forges.toml")
    for key in ("TEST_BOT", "TEST_LAND", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for obj, names in ((config, ("load_env_file", "parse_env_file")),
                       (cli, ("_forge_target", "_ctx", "_gh", "_push", "load_env")),
                       (cli.GiteaForge, ("__init__",)), (seal, ("credential_values",)),
                       (socket.socket, ("connect",))):
        for name in names:
            monkeypatch.setattr(obj, name, forbidden)


def document(capsys, change=None):
    assert main(["status", "--json", *([change] if change else [])]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert set(result) == {"version", "supervisor", "runs"}
    assert result["version"] == 1
    return result


def published(admitted, change="change", github="forge.example/Owner/Repo", **kwargs):
    run, head = admitted(change, github=github, proposal="## Why\n\nUseful context.\n", **kwargs)
    # An adopted PR is a publication signal, never a destination selector.
    run.state.pr = "https://unrelated.example/arbitrary/pr"
    run.pause("human_checks", detail="Inspect the change")
    return run, head


@pytest.mark.scenario("run-context/forge-neutral-status-link/run-forge-selects-destination")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("host, base", [("forge.example", "https://Forge.Example:8443/git"),
    ("FoRgE.ExAmPlE", "https://Forge.Example:8443/git"),
    ("other.example", "https://other.example/prefix%2Dkept")])
def test_status_json_link_uses_run_forge(admitted, tmp_path, monkeypatch, capsys, named, override, host, base):
    published(admitted, github=f"{host}/Owner/Repo/")
    published(admitted, "neighbor", "other.example/Other/Repo")
    path = config.DEFAULT_PATH
    if override:
        path = tmp_path / "override.toml"
        config.DEFAULT_PATH.write_text(FORGES.replace(":8443/git", ":9999/wrong"))
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", str(path))
    path.write_text(FORGES)
    entries = document(capsys, "change" if named else None)["runs"]
    assert entries[0]["context"]["github"] == ""
    assert entries[0]["context"]["link"] == f"{base}/Owner/Repo/src/branch/spec/change/openspec/changes/change"
    if not named:
        assert entries[1]["context"]["link"] == (
            "https://other.example/prefix%2Dkept/Other/Repo/src/branch/spec/neighbor/openspec/changes/neighbor")


@pytest.mark.scenario("run-context/forge-neutral-status-link/github-context-remains-compatible")
@pytest.mark.parametrize("github", ["Owner/Repo", "github.com/Owner/Repo", "GitHub.COM/Owner/Repo", ""])
@pytest.mark.parametrize("saved_pr", [False, True])
def test_github_context_unchanged(admitted, monkeypatch, capsys, github, saved_pr):
    run, _ = published(admitted, github=github)
    if not saved_pr:
        run.state.pr = ""
        run.save()
    before = document(capsys)
    monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", "invalid-relative")
    monkeypatch.setattr(config, "parse_forges", forbidden)
    assert document(capsys) == before
    context = before["runs"][0]["context"]
    assert "link" not in context
    assert context["github"] == ("https://github.com/Owner/Repo/tree/spec/change/openspec/changes/change"
                                 if github and saved_pr else "")


@pytest.mark.scenario("run-context/forge-neutral-status-link/unpublished-forge-run-has-no-destination")
def test_unpublished_link_is_empty(admitted, monkeypatch, capsys):
    admitted("change", github="forge.example/Owner/Repo")
    before = document(capsys)
    config.DEFAULT_PATH.write_text(FORGES)
    monkeypatch.setattr(config, "parse_forges", forbidden)
    assert document(capsys) == before
    assert before["runs"][0]["context"]["github"] == before["runs"][0]["context"]["link"] == ""


@pytest.mark.scenario("run-context/forge-neutral-status-link/unavailable-forge-metadata-preserves-context")
@pytest.mark.parametrize("failure", ["missing", "unreadable", "toml", "encoding", "schema", "blank", "relative",
    "no-match", "duplicate", "unsupported", "github-kind", "dot", "unicode", "reserved"])
def test_unavailable_metadata_is_isolated(admitted, monkeypatch, capsys, failure):
    identity = {"dot": "forge.example/../Repo", "unicode": "forge.example/Öwner/Repo",
                "reserved": "forge.example/Owner/R%23epo"}.get(failure, "forge.example/Owner/Repo")
    published(admitted, github=identity)
    published(admitted, "healthy", "Owner/Repo")
    before = document(capsys)
    path = config.DEFAULT_PATH
    text = {"toml": "[", "schema": "forges = 1", "no-match": FORGES.replace("Forge.Example", "unknown.example"),
            "duplicate": FORGES.replace("other.example", "forge.example"),
            "unsupported": FORGES.replace('kind = "gitea"', 'kind = "unknown"'),
            "github-kind": FORGES.replace('kind = "gitea"', 'kind = "github"')}.get(failure, FORGES)
    if failure != "missing":
        path.write_text(text)
    if failure == "encoding":
        path.write_bytes(b"\xff")
    if failure in ("blank", "relative"):
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", " " if failure == "blank" else "relative.toml")
    if failure == "unreadable":
        read = Path.read_text
        def denied(p, *args, **kwargs):
            if p == path:
                raise PermissionError("config unavailable")
            return read(p, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", denied)
    assert document(capsys) == before
    entries = before["runs"]
    assert [e["change"] for e in entries] == ["change", "healthy"]
    assert entries[0]["context"]["link"] == entries[0]["context"]["github"] == ""
    assert entries[0]["context"]["summary"] == "Useful context."
    assert entries[1]["context"]["github"].startswith("https://github.com/Owner/Repo/tree/")


@pytest.mark.scenario("run-context/forge-neutral-status-link/archived-change-uses-resolved-directory")
@pytest.mark.scenario("run-context/forge-neutral-status-link/run-forge-selects-destination")
@pytest.mark.parametrize("failed_push", [False, True])
def test_archived_link_quotes_resolved_path(admitted, monkeypatch, capsys, failed_push):
    run, head = published(admitted, task_text=TASKS.replace("[ ]", "[x]"))
    branch = "spec/topic#percent%é"
    run.manifest_path.write_text(run.manifest_path.read_text().replace("spec/change", branch))
    seal.git(run.dir / "repo.git", "update-ref", f"refs/heads/{branch}", head)
    def archive(work, change, **kwargs):
        directory = work / "openspec/changes" / change
        target = directory.parent / "archive" / "2026-10-20-change"
        target.parent.mkdir()
        directory.rename(target)
    def push(*args):
        if failed_push:
            raise RuntimeError("external push failed")
    with monkeypatch.context() as m:
        m.setattr(seal, "credential_values", lambda _: [])
        m.setattr(cli, "_ctx", lambda r: loop.Ctx(r, cli._manifest(r), {}, lambda _: None))
        m.setattr(cli, "_forge_target", lambda _: cli.ForgeTarget("Owner/Repo"))
        m.setattr(cli, "_remote", lambda *a: "https://forge.example/Owner/Repo.git")
        m.setattr(cli.openspec, "archive", archive)
        m.setattr(cli, "_push", push)
        m.setattr(cli, "_gh", lambda *a: "")
        if failed_push:
            with pytest.raises(RuntimeError, match="external push failed"):
                main(["finish", "change"])
        else:
            assert main(["finish", "change"]) == 0
    capsys.readouterr()
    before = document(capsys)
    config.DEFAULT_PATH.write_text(FORGES)
    after = document(capsys)
    entry = after["runs"][0]
    assert entry["context"].pop("link") == (
        "https://Forge.Example:8443/git/Owner/Repo/src/branch/spec/topic%23percent%25%C3%A9/"
        "openspec/changes/archive/2026-10-20-change")
    assert cli._status_forge_link("forge.example/Owner/Repo", branch, "folder # %/tasks.md") == (
        "https://Forge.Example:8443/git/Owner/Repo/src/branch/spec/topic%23percent%25%C3%A9/folder%20%23%20%25")
    before["runs"][0]["context"].pop("link")
    assert after == before
    assert entry["head"] != head and entry["tasks"] == {"done": 3, "total": 3}
    assert entry["status"] == ("paused" if failed_push else "done")
    if failed_push:
        assert entry["action"]["actions"] == ["finish", "abort"]


@pytest.mark.scenario("run-context/forge-neutral-status-link/link-lookup-remains-read-only")
@pytest.mark.parametrize("named", [False, True])
def test_status_link_is_read_only(admitted, home, monkeypatch, capsys, named):
    published(admitted)
    change = "change" if named else None
    before = document(capsys, change)
    assert main(["status"]) == 0
    text_before = capsys.readouterr()
    snapshot = tree_snapshot(home)
    real_run = subprocess.run
    def local_reads(argv, *args, **kwargs):
        assert argv[0] == "git" and argv[argv.index("-C") + 2] in ("rev-parse", "show", "ls-tree")
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", local_reads)
    config.DEFAULT_PATH.write_text(FORGES)
    after = document(capsys, change)
    assert after["runs"][0]["context"].pop("link")
    before["runs"][0]["context"].pop("link")
    assert after == before
    assert main(["status"]) == 0
    assert capsys.readouterr() == text_before
    assert tree_snapshot(home) == snapshot


@pytest.mark.scenario("run-context/forge-neutral-status-link/link-lookup-remains-read-only")
@pytest.mark.parametrize("core", [False, True])
def test_unrelated_failures_have_no_link(admitted, monkeypatch, capsys, core):
    run, _ = published(admitted)
    if core:
        run.manifest_path.write_text("[")
    else:
        def broken(*args):
            raise OSError("unreadable context")
        monkeypatch.setattr(cli, "_context_summary", broken)
    before = document(capsys)
    config.DEFAULT_PATH.write_text(FORGES)
    assert document(capsys) == before
    entry = before["runs"][0]
    assert "link" not in entry["context"]
    assert ("error" in entry) == core
