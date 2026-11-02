"""Trusted manifest selection and pre-effect destination checks, without network."""

import os
import subprocess

import pytest

from nightshift import config
from nightshift.cli import main
from specloop import cli, seal
from specloop.critic import open_pr_files
from test_specloop_act import act, admitted
from test_specloop_command_json import BRANCH, CHANGE, TASKS, git, template


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path, env = tmp_path / "forges.toml", tmp_path / "env"
    path.write_text('''[[forges]]
name = "internal"
kind = "gitea"
url = "https://forge.example:8443/git"
token_env = "TEST_FORGE_BOT"
land_token_env = "TEST_FORGE_LAND"
bot_login = "Robot"
trusted_logins = ["Reviewer"]
wip_prefix = "Draft: "
''')
    env.write_text("export TEST_FORGE_BOT='file-bot'\nexport TEST_FORGE_LAND='file-land'\n")
    monkeypatch.setattr(config, "DEFAULT_PATH", path)
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.delenv("NIGHTSHIFT_FORGE_CONFIG", raising=False)
    monkeypatch.setenv("TEST_FORGE_BOT", "process-bot")
    monkeypatch.setenv("GH_TOKEN", "ambient-gh")
    monkeypatch.setattr(cli.GiteaForge, "_request", lambda *a, **kw: pytest.fail("unexpected API request"))
    return path, env


@pytest.mark.scenario("specloop-forge/manifest-forge-selection/gitea-host-selects-configured-credentials")
@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("file_token", [False, True])
@pytest.mark.parametrize("repository", ["forge.example/Owner/Repo", "FORGE.EXAMPLE/Owner/Repo/"])
def test_selection_uses_trusted_bot_configuration(configured, monkeypatch, override, file_token, repository):
    path, env = configured
    if override:
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", str(path))
        monkeypatch.setattr(config, "DEFAULT_PATH", path.parent / "absent")
    if not file_token:
        env.unlink()
    before = dict(os.environ)
    guarded = []
    guard = lambda: guarded.append(True)
    target = cli._forge_target(repository, guard=guard)
    assert target.repository == "Owner/Repo"
    forge = target.forge
    assert isinstance(forge, cli.GiteaForge)
    assert forge._transport._base == "https://forge.example:8443/git"
    assert forge._transport._token == ("file-bot" if file_token else "process-bot")
    assert forge._bot_login == "robot" and forge._trusted_logins == {"reviewer"}
    assert forge._wip_prefix == "Draft: " and forge._transport._timeout == 30
    assert guarded == []
    forge._transport._guard()
    assert guarded == [True]
    assert dict(os.environ) == before


@pytest.mark.scenario("specloop-forge/manifest-forge-selection/github-stays-compatible")
@pytest.mark.parametrize("github", ["Owner/Repo", "github.com/Owner/Repo", "GITHUB.COM/Owner/Repo/"])
def test_github_selection_precedes_config_reads(configured, monkeypatch, github):
    monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", "invalid-relative-path")
    monkeypatch.setattr(config, "parse_forges", lambda *a: pytest.fail("read config"))
    monkeypatch.setattr(config, "parse_env_file", lambda: pytest.fail("read credentials"))
    before = dict(os.environ)
    target = cli._forge_target(github)
    assert target.repository == "Owner/Repo" and target.forge is target.definition is None
    assert dict(os.environ) == before


@pytest.fixture
def destination(admitted, configured, monkeypatch):
    run, source = admitted
    run.manifest_path.write_text(run.manifest_path.read_text().replace(
        '"example/repo"', '"forge.example/example/repo"'))
    assert cli._manifest(run).github == "forge.example/example/repo"
    bare = run.dir / "repo.git"
    completed = seal.commit_files(bare, git(bare, "rev-parse", BRANCH), {
        f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
    }, "complete task")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", completed)
    git(source, "remote", "add", "origin", "https://forge.example:8443/git/example/repo.git")
    run.state.pr = "https://forge.example:8443/git/example/repo/pulls/42"
    monkeypatch.setattr(cli, "load_env", lambda _: {"TEST_FORGE_BOT": "manifest-bot",
                                                  "NIGHTSHIFT_FORGE_CONFIG": "/untrusted/SECRET"})
    # Even a validation regression must reach the effect spies without network.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: ctx.head())
    effects = []
    real_run = subprocess.run

    def process(argv, **kw):
        if "push" in argv:
            effects.append(argv)
            raise RuntimeError("unexpected push")
        return real_run(argv, **kw)

    def effect(*args, **kw):
        effects.append(args)
        raise RuntimeError("unexpected publication effect")

    monkeypatch.setattr(cli.subprocess, "run", process)
    for obj, name in [(cli, "_push"), (cli, "_gh"), (cli.openspec, "archive"), (cli.GiteaForge, "_request")]:
        monkeypatch.setattr(obj, name, effect)
    return run, source, effects


def refuses(destination, command, capfd):
    run, source, effects = destination
    run.pause("ready_to_publish" if command == "publish" else "human_checks")
    before = (run.dir / "state.json").read_bytes()
    head = git(run.dir / "repo.git", "rev-parse", BRANCH)
    parent = dict(os.environ)
    detail = act(capfd, run, command, code=1)["detail"]
    assert detail and not any(secret in detail for secret in (
        "file-bot", "file-land", "process-bot", "manifest-bot", "ambient-gh", "SECRET"))
    if command == "finish":
        with pytest.raises(cli.PublishTarget) as refusal:
            main([command, CHANGE])
        assert str(refusal.value) == detail
    else:
        assert main([command, CHANGE]) == 1
        assert detail in capfd.readouterr().err
    assert not effects
    assert dict(os.environ) == parent
    assert (run.dir / "state.json").read_bytes() == before
    assert git(run.dir / "repo.git", "rev-parse", BRANCH) == head
    return detail


@pytest.mark.scenario("specloop-forge/manifest-forge-selection/invalid-selection-refuses")
@pytest.mark.parametrize("command", ["publish", "finish"])
@pytest.mark.parametrize("failure", ["ambiguous", "missing", "default_missing", "invalid", "malformed", "unreadable",
                                     "relative", "blank", "no_token", "empty_token", "invalid_token", "env_unreadable"])
def test_invalid_selection_refuses(destination, configured, monkeypatch, capfd, command, failure):
    path, env = configured
    if failure == "ambiguous":
        path.write_text(path.read_text() + path.read_text().replace('"internal"', '"other"').replace(":8443/git", ":9443"))
    elif failure in ("missing", "default_missing", "invalid", "malformed", "unreadable"):
        path.unlink()
        if failure == "missing":
            monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", str(path))
        elif failure == "invalid":
            path.write_text('[[forges]]\nname = "SECRET"\nkind = 42')
        elif failure == "malformed":
            path.write_text("SECRET = [")
        elif failure == "unreadable":
            path.mkdir()
    elif failure in ("relative", "blank"):
        monkeypatch.setenv("NIGHTSHIFT_FORGE_CONFIG", "SECRET" if failure == "relative" else " ")
    elif failure in ("no_token", "empty_token", "invalid_token"):
        env.write_text("export TEST_FORGE_LAND='file-land'\n" +
                       ("export TEST_FORGE_BOT=\n" if failure == "empty_token" else ""))
        if failure == "no_token":
            monkeypatch.delenv("TEST_FORGE_BOT")
        elif failure == "invalid_token":
            monkeypatch.setenv("TEST_FORGE_BOT", "bad\rSECRET")
    else:
        env.unlink()
        env.mkdir()
    assert "Gitea" in refuses(destination, command, capfd) or failure in ("relative", "blank")
    # The admission template stubs critic.open_pr_files; exercise the real dispatcher.
    files, notes = open_pr_files("forge.example/example/repo", BRANCH,
                                runner=lambda *a, **kw: pytest.fail("GitHub fallback"))
    assert files == {} and len(notes) == 1 and "unavailable" in notes[0]
    assert not any(secret in notes[0] for secret in ("SECRET", "file-land", "process-bot", "ambient-gh"))


@pytest.mark.scenario("specloop-forge/manifest-forge-selection/invalid-selection-refuses")
@pytest.mark.parametrize("command", ["publish", "finish"])
@pytest.mark.parametrize("host", ["unknown.example", "forge.example:8443", "https://forge.example"])
def test_unknown_manifest_host_has_no_github_fallback(destination, capfd, command, host):
    run, _, _ = destination
    run.manifest_path.write_text(run.manifest_path.read_text().replace("forge.example", host))
    expected = "exactly one configured Gitea forge" if host == "unknown.example" else "invalid manifest repository"
    assert expected in refuses(destination, command, capfd)
    files, notes = open_pr_files(f"{host}/example/repo", BRANCH,
                                runner=lambda *a, **kw: pytest.fail("GitHub fallback"))
    assert files == {} and len(notes) == 1 and expected in notes[0]


@pytest.mark.scenario("specloop-forge/full-gitea-origin-match/port-or-scheme-mismatch-refuses")
@pytest.mark.scenario("specloop-forge/full-gitea-origin-match/unverifiable-destination-refuses")
@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/local-origin-compatibility-is-github-only")
@pytest.mark.parametrize("command", ["publish", "finish"])
@pytest.mark.parametrize("origin", [
    "https://forge.example:9443/git/example/repo", "http://localhost/git/example/repo",
    "https://forge.example:8443/wrong/example/repo", "https://forge.example:8443/git/upstream/repo",
    "https://SECRET@forge.example:8443/git/example/repo", "https://forge.example:8443/git/example/repo?SECRET",
    "https://forge.example:8443/git/example/repo#SECRET", "https://forge.example:8443/git/example/%zz",
    "https://forge.example:8443/git/../example/repo", "https://forge.example:8443/git/%2e%2e/example/repo",
    "https://forge.example:8443/git/example/Repo", "https://forge.example:8443/git/example/repo/",
    "https://forge.example:8443/git/example/repo%0a", "https://forge.example:8443/git/example\\repo",
    "https://forge.example:8443/git/example/%72epo", "https://forge.example:8443/git/example/repo/extra",
    "/tmp/local.git", "git@forge.example:example/repo", "ssh://git@forge.example:8443/git/example/repo",
    "multiple", "missing", "pr_url", "pr_port", "pr_scheme", "pr_repository", "pr_prefix",
])
def test_invalid_destination_refuses(destination, configured, capfd, command, origin):
    run, source, _ = destination
    if origin == "multiple":
        git(source, "config", "--add", "remote.origin.url", "https://SECRET/other/repo")
    elif origin == "missing":
        git(source, "remote", "remove", "origin")
    elif origin.startswith("pr_"):
        old, new = {"pr_url": ("https://", "https://SECRET@"), "pr_port": (":8443", ":9443"),
                    "pr_scheme": ("https://", "http://"), "pr_repository": ("example/repo", "upstream/repo"),
                    "pr_prefix": ("/git/", "/wrong/")}[origin]
        run.state.pr = run.state.pr.replace(old, new)
    else:
        if origin.startswith("http://localhost"):
            path, _ = configured
            path.write_text(path.read_text().replace("forge.example:8443", "localhost"))
            run.manifest_path.write_text(run.manifest_path.read_text().replace("forge.example", "localhost"))
            run.state.pr = run.state.pr.replace("forge.example:8443", "localhost")
        # Origin failures must not be masked by an independently invalid PR URL.
        cli._forge_target(cli._manifest(run).github).forge._pr_url_number("example/repo", run.state.pr)
        git(source, "remote", "set-url", "origin", origin)
    assert "Gitea origin or recorded PR" in refuses(destination, command, capfd)


@pytest.mark.scenario("specloop-forge/full-gitea-origin-match/matching-origin-permits-publication")
@pytest.mark.parametrize("port,suffix", [("", ""), (":443", ".git"), (":443", "")])
def test_matching_gitea_origin_permits_saved_pr_publication(destination, configured, monkeypatch, capfd, port, suffix):
    run, source, effects = destination
    path, _ = configured
    path.write_text(path.read_text().replace(":8443", ""))
    run.state.pr = run.state.pr.replace(":8443", "")
    origin = f"https://FORGE.EXAMPLE{port}/git/example/repo{suffix}"
    git(source, "remote", "set-url", "origin", origin)
    monkeypatch.setattr(cli, "_push", lambda *args, **kwargs: effects.append(args))
    run.pause("ready_to_publish")
    assert cli._remote(cli._ctx(run)) == origin
    act(capfd, run, "publish")
    assert effects == [(run.dir / "repo.git", origin, f"refs/heads/{BRANCH}")]
    assert run.state.pause.reason == "human_checks"


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/local-origin-compatibility-is-github-only")
@pytest.mark.parametrize("command", ["publish", "finish"])
def test_missing_repository_retains_diagnostic(destination, capfd, command):
    run, _, _ = destination
    run.manifest_path.write_text(run.manifest_path.read_text().replace("forge.example/example/repo", ""))
    detail = refuses(destination, command, capfd)
    assert "names no GitHub repository" in detail and str(run.manifest_path) in detail
