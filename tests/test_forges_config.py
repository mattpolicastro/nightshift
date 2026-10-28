"""Forge enrollment is pure configuration; GitHub consumers respect its boundary."""

import json
from pathlib import Path
import socket
import subprocess

import pytest

from nightshift import cli, config, land, queue
from test_land import EMPTY_STATUS, MAIN, MAIN_REF, PR, STATUS, URL, VIEW, ScriptedRunner, invoke

GITEA = dict(name="tea", kind="gitea", url="https://forge.example/prefix",
             token_env="TEA_BOT", land_token_env="TEA_HUMAN", bot_login="robot")
GITHUB = dict(name="hub", kind="github")


def tables(kind, rows):
    return "".join(f"[[{kind}]]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in row.items()) for row in rows)


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("configuration/status must not access credentials or dispatch work")
    for owner, key in ((subprocess, "run"), (subprocess, "Popen"), (socket, "create_connection"),
                       (config, "parse_env_file")):
        monkeypatch.setattr(owner, key, forbidden)


@pytest.mark.scenario("forge-configuration/named-forge-definitions/legacy-and-explicit-selection")
@pytest.mark.parametrize("definitions", [[], [GITHUB], [GITEA], [GITEA, GITHUB], [GITHUB, GITEA],
    *[[GITEA | {"url": f"http://{host}:3000/gitea"}] for host in ("localhost", "127.0.0.1", "[::1]")]])
def test_forges_config_parses_and_defaults_github(tmp_path, definitions):
    rows = [dict(name="owner/legacy", manifest="missing.toml", dir="/clone", base="dev", verify=False)]
    rows += [dict(name=f"owner/{d['name']}", forge=d["name"]) for d in definitions]
    path = tmp_path / "config.toml"
    path.write_text('unknown = true\n[daemon]\nunknown = true\n[labels]\nunknown = true\n'
                    '[notify]\nunknown = true\n' + tables("forges", [d | {"unknown": 42} for d in definitions])
                    + tables("repos", [r | {"unknown": True} for r in rows]))
    cfg = config.load(path)
    assert cfg.forges == [config.ForgeConfig(**d) for d in definitions]
    assert [cfg.forge_for(r).kind for r in cfg.repos] == ["github", *[d["kind"] for d in definitions]]
    assert cfg.forge_for(cfg.repos[0]) == config.ForgeConfig(name="", kind="github")
    for d in cfg.forges:
        assert d.trusted_logins == [] and d.wip_prefix == "WIP: "
        if d.kind == "github":
            assert (d.url, d.token_env, d.land_token_env, d.bot_login, d.forbidden_probe) == (
                "https://github.com", "GH_TOKEN", "", "", "")
    repo = cfg.repos[0]
    assert (repo.engine, repo.manifest, repo.clone_dir(), repo.base_ref) == (
        "specloop", tmp_path / "missing.toml", Path("/clone"), "origin/dev")
    assert cfg.notes == ["repos.verify is ignored; configure a trusted specloop manifest"]


@pytest.mark.scenario("forge-configuration/named-forge-definitions/invalid-definitions-refuse")
@pytest.mark.parametrize("definition, reference, field", [
    *[(GITEA | {key: 42}, "tea", key) for key in GITEA | dict(trusted_logins=[], forbidden_probe="", wip_prefix="")],
    *[(GITEA | {"url": url}, "tea", "url") for url in (
        "http://forge.example", "https://user:secret@forge.example", "https://forge.example?secret",
        "https://forge.example#secret", "https://forge.example/api/v1", "https://forge.example:bad",
        "https://forge.example/%2e%2e", "https://forge.example/\\secret")],
    *[(GITEA | {key: value}, "tea", key) for key, value in (
        ("name", " "), ("kind", "unknown"), ("token_env", "BAD-NAME"),
        ("land_token_env", "TEA_BOT"), ("bot_login", ""), ("wip_prefix", ""), ("trusted_logins", [""]))],
    *[({k: v for k, v in GITEA.items() if k != key}, "tea", key) for key in GITEA],
    *[(GITEA, ref, "repos.forge") for ref in ("", "Tea", "missing", 42)],
    ([GITEA, GITEA], "tea", "name"),
])
def test_forges_config_refusals(tmp_path, definition, reference, field):
    path = tmp_path / "config.toml"
    text = tables("forges", definition if isinstance(definition, list) else [definition])
    text += tables("repos", [dict(name="owner/repo", forge=reference)])
    path.write_text(text)
    with pytest.raises(ValueError, match=field) as error:
        config.load(path)
    assert "secret" not in str(error.value)
    assert path.read_text() == text and list(tmp_path.iterdir()) == [path]
    path.write_text(text + '[[repos]]\nname = "owner/retired"\nengine = "worker"\n')
    with pytest.raises(ValueError, match="worker engine is retired"):
        config.load(path)


@pytest.mark.scenario("forge-configuration/unique-repository-enrollment/cross-forge-names-collide")
@pytest.mark.scenario("forge-configuration/unique-repository-enrollment/legacy-refusals-stay-recognizable")
@pytest.mark.parametrize("selection", [{}, {"forge": "hub"}, {"forge": "tea"}])
@pytest.mark.parametrize("name, diagnostic", [("OWNER/Repo", "ambiguous enrollment"),
    (42, "invalid repository identity"), ("owner/", "invalid repository identity"),
    ("owner/widget..js", None), ("other/repo", None)])
def test_repo_names_unique_across_forges(tmp_path, selection, name, diagnostic):
    path = tmp_path / "config.toml"
    text = tables("forges", [GITHUB, GITEA]) + tables("repos", [dict(name="owner/repo"), dict(name=name) | selection])
    path.write_text(text)
    if diagnostic:
        with pytest.raises(land.Refusal, match=diagnostic):
            land.load_config(path)
    else:
        assert len(config.load(path).repos) == 2
    assert path.read_text() == text and list(tmp_path.iterdir()) == [path]


@pytest.mark.scenario("reviewed-pr-landing/github-kind-enrollment-boundary/gitea-enrollment-grants-no-github-authority")
@pytest.mark.parametrize("selection", [{}, {"forge": "hub"}, {"forge": "tea"}])
def test_gitea_enrollment_does_not_authorize_github_landing(tmp_path, capsys, selection):
    path = tmp_path / "config.toml"
    path.write_text(tables("forges", [GITEA, GITHUB]) + tables("repos", [dict(name="owner/project") | selection]))
    runner = ScriptedRunner()
    if selection.get("forge") != "tea":
        runner.expect(VIEW, PR | {"isDraft": True}).expect(MAIN, MAIN_REF).expect(STATUS, EMPTY_STATUS)
    assert invoke(path, runner, URL, "--checks-done") == 1
    assert ("not enrolled" if selection.get("forge") == "tea" else "draft") in capsys.readouterr().err
    assert not runner.steps
    assert len(runner.calls) == (0 if selection.get("forge") == "tea" else 3)


@pytest.mark.scenario("nightshift-cli/forge-aware-daemon-status/gitea-status-is-explicitly-unqueried")
@pytest.mark.parametrize("selection", [{}, {"forge": "hub"}])
def test_daemon_status_skips_gitea_rows(tmp_path, monkeypatch, capsys, selection):
    path = tmp_path / "config.toml"
    path.write_text(tables("forges", [GITEA, GITHUB]) + tables("repos", [dict(name="owner/tea", forge="tea"),
                    dict(name="owner/hub") | selection, dict(name="other/tea", forge="tea")]))
    monkeypatch.setattr(config, "load_env_file", lambda: [])
    monkeypatch.setattr(cli, "_liveness", lambda cfg: "running")
    monkeypatch.setattr(cli, "_active_jobs", lambda: print("jobs"))
    calls = []
    def query(kind, result):
        def run(repo, *args):
            assert repo == "owner/hub"
            calls.append(kind)
            return result
        return run
    monkeypatch.setattr(queue, "ready", query("ready", [queue.Issue("owner/hub", 1, "ready", ""),
        queue.Issue("owner/hub", 2, "blocked", "blocked-by: #3")]))
    monkeypatch.setattr(queue, "open_blockers", lambda repo, ids: query("blockers", ids)(repo))
    monkeypatch.setattr(queue, "contradictions", query("contradictions", [(4, {"agent:done"})]))
    assert cli.main(["daemon", "status", "--config", str(path)]) == 0
    output = capsys.readouterr().out
    assert "owner/tea: not queried (gitea)" in output and "other/tea: not queried (gitea)" in output
    assert "owner/hub: 1 ready, 1 blocked" in output and "#1 ready" in output and "waiting on #3" in output
    assert "#4 is also labelled agent:done" in output and "alerts: NOT CONFIGURED" in output
    assert "running" in output and "jobs" in output
    assert calls == ["ready", "blockers", "blockers", "contradictions"]


@pytest.mark.scenario("forge-configuration/bounded-enrollment-guidance/examples-preserve-existing-consumers")
def test_forge_example_uses_existing_enrollment():
    cfg = config.load(Path(__file__).resolve().parents[1] / "config.example.toml")
    repo, = cfg.repos
    assert cfg.forge_for(repo).kind == "github" and cfg.forges == []
