"""What must be true before the daemon is left alone overnight.

The check that carries its weight is the negative control. A token that can
read the sandbox has proven nothing about what else it can read — the same
shape as an earlier auth finding, where `claude -p` succeeded on a bogus
token because an interactive login was quietly answering instead.
"""

from __future__ import annotations

import os
import subprocess
import urllib.request
from dataclasses import fields, replace
from urllib.error import URLError
from urllib.parse import quote

import pytest

from manifest_fixtures import manifest_text
from nightshift import cli, config, preflight
from nightshift.config import Config, Repo
from nightshift.forge import http
from fake_gitea import FakeGitea, Reply

SANDBOX = "matt/sandbox"
OFF_LIMITS = "matt/dotfiles"


def cfg(**kw) -> Config:
    # Legacy verify is no longer checked against worker permissions.
    return Config(repos=[Repo(name=SANDBOX, verify="retired-unrunnable")], **kw)


def reader(readable: set[str]):
    def runner(args):
        if args[:3] == ["gh", "repo", "view"] and args[3] in readable:
            return '{"name":"x"}'
        raise RuntimeError("not found")

    return runner


def find(checks, fragment: str) -> preflight.Check:
    for c in checks:
        if fragment in c.name:
            return c
    raise AssertionError(f"no check matching {fragment!r} in {[c.name for c in checks]}")


def test_a_gho_token_is_flagged_however_well_it_works():
    """`gh auth login`'s credential reaches every repo the account owns."""
    checks = preflight.run(
        cfg(), {"GH_TOKEN": "gho_abc"}, runner=reader({SANDBOX})
    )
    assert find(checks, "GH_TOKEN scope").ok is False


def test_a_fine_grained_pat_passes_the_shape_check():
    checks = preflight.run(
        cfg(), {"GH_TOKEN": "github_pat_abc"}, runner=reader({SANDBOX})
    )
    assert find(checks, "GH_TOKEN scope").ok is True


def test_an_unreachable_enrolled_repo_fails():
    checks = preflight.run(cfg(), {"GH_TOKEN": "github_pat_x"}, runner=reader(set()))
    assert find(checks, f"reach {SANDBOX}").ok is False


def test_a_token_that_reaches_the_forbidden_repo_fails():
    """The whole point: over-scoping is invisible until something reads it."""
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX, OFF_LIMITS}),
    )
    assert find(checks, "cannot reach").ok is False


def test_a_properly_scoped_token_passes_both_directions():
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS, slack_webhook="https://hooks/x"),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX}),
    )
    assert [c for c in checks if not c.ok] == []


def test_no_forbidden_probe_is_itself_a_failure():
    """Asserted scoping is not proven scoping."""
    checks = preflight.run(
        cfg(slack_webhook="https://hooks/x"),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "negative control").ok is False


@pytest.mark.parametrize("variable", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
])
def test_retired_worker_environment_does_not_affect_preflight(variable):
    configuration = cfg(forbidden_probe=OFF_LIMITS, slack_webhook="https://hooks/x")
    env = {"GH_TOKEN": "github_pat_x"}
    baseline = preflight.run(configuration, env, runner=reader({SANDBOX}))
    checks = preflight.run(
        configuration, {**env, variable: "unused-worker-value"},
        runner=reader({SANDBOX}),
    )
    assert checks == baseline and all(check.ok for check in checks)


def test_unconfigured_alerting_fails_rather_than_passing_quietly():
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "alerts").ok is False


@pytest.mark.parametrize("token", ["", "ghp_classic"])
def test_a_missing_or_classic_token_still_reports_presence(token):
    checks = preflight.run(cfg(), {"GH_TOKEN": token}, runner=reader({SANDBOX}))
    assert find(checks, "GH_TOKEN").ok is bool(token)


@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/codex-enrollment-needs-no-worker-login")
@pytest.mark.parametrize("deep", [False, True])
@pytest.mark.parametrize("failure", [None, "missing-token", "scope", "reach", "negative-control", "alerts"])
def test_cli_preflight_needs_only_front_door_prerequisites(tmp_path, monkeypatch, capsys, deep, failure):
    manifest = tmp_path / "repository.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    selected = tmp_path / "config.toml"
    selected.write_text(f'''[daemon]
forbidden_probe = "{OFF_LIMITS}"
[notify]
slack_webhook = "{'' if failure == 'alerts' else 'https://hooks/x'}"
[[repos]]
name = "{SANDBOX}"
manifest = "repository.toml"
verify = "retired-unrunnable"
[repos.models]
implement = "unused:retired"
[[endpoints]]
name = "unused"
base_url = "http://unused.invalid"
models = ["retired"]
''')
    token = "" if failure == "missing-token" else "gho_bad" if failure == "scope" else "github_pat_x"
    env_file = tmp_path / "env"
    env_file.write_text(f"export GH_TOKEN='{token}'\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setattr(os, "environ", {"HOME": str(tmp_path), "PATH": ""})
    calls = []

    def run(args, **kwargs):
        assert args[:3] == ["gh", "repo", "view"]
        assert args[4:] == ["--json", "name"]
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        assert os.environ["GH_TOKEN"] == token
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
        calls.append(args[3])
        readable = {SANDBOX} if failure != "reach" else set()
        if failure == "negative-control":
            readable.add(OFF_LIMITS)
        return subprocess.CompletedProcess(args, 0 if args[3] in readable else 1, '{"name":"x"}', "not found")

    def no_probe(*args, **kwargs):
        pytest.fail("preflight launched a worker/model probe")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", no_probe)
    monkeypatch.setattr(urllib.request, "urlopen", no_probe)
    assert cli.main(["preflight", "--config", str(selected), *(["--deep"] if deep else [])]) == int(failure is not None)
    output = capsys.readouterr()
    assert calls == [SANDBOX, OFF_LIMITS]
    assert output.err == ""
    assert ("worker probes are retired" in output.out) == deep
    assert "verify runnable" not in output.out and "CLAUDE_CODE_OAUTH_TOKEN" not in output.out
    assert ("checks failed" in output.out) == (failure is not None)
    if failure is None:
        assert output.out.endswith("\nready\n")
    else:
        failed_check = {
            "missing-token": "GH_TOKEN",
            "scope": "GH_TOKEN scope",
            "reach": f"reach {SANDBOX}",
            "negative-control": f"cannot reach {OFF_LIMITS}",
            "alerts": "alerts",
        }[failure]
        assert f"FAIL  {failed_check}:" in output.out


def test_deep_help_describes_the_retired_probe(capsys):
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["--help"])
    assert help_exit.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--deep" in help_text and "worker probes are retired" in help_text


@pytest.mark.scenario("forge-preflight/isolated-forge-checks/mixed-configuration-remains-isolated")
@pytest.mark.scenario("forge-preflight/bot-identity-negative-control/only-a-proven-nonadmin-bot-passes")
@pytest.mark.scenario("forge-preflight/forbidden-repository-negative-control/only-forbidden-404-passes")
@pytest.mark.scenario("forge-preflight/complete-unique-labels/complete-map-establishes-labels")
@pytest.mark.scenario("forge-preflight/complete-unique-labels/invalid-or-incomplete-map-fails")
@pytest.mark.scenario("forge-preflight/base-protection-evidence/protection-is-established-or-remains-unverified")
@pytest.mark.parametrize("control, outcome", [
    (None, "pass"), ("token", "missing"), ("token", "empty"), ("transport", "invalid"),
    *[("identity", value) for value in ("admin", "wrong", "missing", "missing-login", "numeric", "malformed", 401)],
    *[("forbidden probe", value) for value in ("absent", 200, 401, 403, 500, "connection")],
    *[("labels", value) for value in ("missing", "duplicate", "conflict", "repeat", "later-error",
                                     "bound", "malformed", "boolean-id", "bad-page")],
    *[("protection", value) for value in (403, 404, 500, "mismatch", "missing", "malformed", "bad-json")],
])
def test_preflight_gitea_negative_controls(monkeypatch, capsys, caplog, control, outcome):
    definitions = [config.ForgeConfig(
        name=name, kind="gitea", url=f"https://{name}.example/install", token_env=f"{name}_BOT",
        land_token_env=f"{name}_LAND", bot_login="Robot", forbidden_probe="private/no#access",
    ) for name in ("first", "second")]
    repositories = [Repo(name=f"{f.name}/café#repo", forge=f.name, base="release/v1#tag")
                    for f in definitions]
    configuration = Config(
        repos=[Repo(name=SANDBOX), Repo(name="explicit/github", forge="github"), *repositories],
        forges=[*definitions, config.ForgeConfig(name="github", kind="github")],
        labels=replace(config.Labels(), ready="custom:queue", working="custom:queue", revise="custom:revise"),
        forbidden_probe=OFF_LIMITS, slack_webhook="https://hooks/x",
    )
    required = {getattr(configuration.labels, f.name) for f in fields(configuration.labels)}
    rows = [dict(id=i, name=name) for i, name in enumerate(sorted(required | {"merge:ready", "merge:blocked"}), 1)]
    env = {"GH_TOKEN": "github_pat_test", "first_BOT": "first-bot-secret", "second_BOT": "second-bot-secret",
           "first_LAND": "first-landing-secret", "second_LAND": "second-landing-secret"}
    sensitive = "remote-body " + " ".join(env.values())
    fakes, roots = {}, {}
    for forge, repo in zip(definitions, repositories):
        fake = fakes[forge.url] = FakeGitea(forge.url)
        root = roots[forge.name] = "/install/api/v1/repos/" + "/".join(quote(p, safe="") for p in repo.name.split("/"))
        fake.repository(repo.name, labels=list(rows), page_size=1)
        fake.route("GET", "/install/api/v1/user", Reply(dict(login="rOBOT", is_admin=False)))
        fake.route("GET", "/install/api/v1/repos/private/no%23access", Reply(sensitive, 404))
        fake.route("GET", root + "/branch_protections/release%2Fv1%23tag", Reply(dict(branch_name=repo.base)))
    fake = fakes[definitions[0].url]
    root = roots["first"]
    state = fake.repositories[repositories[0].name]
    if control == "token":
        env.pop("first_BOT")
        if outcome == "empty":
            env["first_BOT"] = ""
    elif control == "transport":
        env["first_BOT"] = "invalid\rsecret"
    elif control == "identity":
        evidence = {"admin": dict(login="Robot", is_admin=True), "wrong": dict(login=sensitive, is_admin=False),
                    "missing": dict(login="Robot"), "missing-login": dict(is_admin=False),
                    "numeric": dict(login="Robot", is_admin=0),
                    "malformed": [sensitive]}.get(outcome, sensitive)
        fake.route("GET", "/install/api/v1/user", Reply(evidence, outcome if isinstance(outcome, int) else 200))
    elif control == "forbidden probe":
        if outcome == "absent":
            configuration.forges[0] = replace(definitions[0], forbidden_probe="")
        else:
            reply = URLError(sensitive) if outcome == "connection" else Reply(sensitive, outcome)
            fake.route("GET", "/install/api/v1/repos/private/no%23access", *([reply] * 3))
    elif control == "labels":
        if outcome == "missing":
            state["labels"] = [r for r in rows if r["name"] != "custom:revise"]
        elif outcome == "bound":
            state["labels"] += [dict(id=i, name=f"extra:{i}") for i in range(len(rows) + 1, 1001)]
        else:
            bad = {"duplicate": [{**rows[0], "id": 999}], "conflict": [{**rows[0], "name": "other"}],
                   "repeat": [rows[0]], "malformed": [dict(id=999, name=sensitive), {}],
                   "boolean-id": [dict(id=True, name="other")], "bad-page": {"body": sensitive}}
            fake.route("GET", root + "/labels", Reply(bad.get(outcome, sensitive),
                       403 if outcome == "later-error" else 200), page=len(rows) + 1)
    elif control == "protection":
        evidence = {"mismatch": dict(branch_name="release/*"), "missing": {}, "malformed": [sensitive]}
        reply = Reply(evidence.get(outcome, sensitive), outcome if isinstance(outcome, int) else 200,
                      raw=b"invalid-json" if outcome == "bad-json" else None)
        fake.route("GET", root + "/branch_protections/release%2Fv1%23tag", *([reply] * 3))

    # Ambient bot and landing credentials must never substitute for supplied values.
    monkeypatch.setattr(os, "environ", {**env, "first_BOT": "ambient-personal-secret"})
    monkeypatch.setattr(http, "sleep", lambda _: None)
    def no_network(*args, **kwargs):
        pytest.fail("unexpected network or subprocess")
    monkeypatch.setattr(http, "build_opener", no_network)
    monkeypatch.setattr(subprocess, "run", no_network)
    made, github = [], []
    def factory(url, token):
        forge = next(f for f in definitions if f.url == url)
        assert token == env[forge.token_env]
        made.append(url)
        opener = fakes[url]
        original = opener.open
        def authenticated(request, *, timeout):
            assert request.get_header("Authorization") == "token " + env[forge.token_env]
            return original(request, timeout=timeout)
        monkeypatch.setattr(opener, "open", authenticated)
        return http.GiteaTransport(url, token, opener=opener)
    def github_reader(args):
        github.append(args[3])
        return reader({SANDBOX, "explicit/github"})(args)
    checks = preflight.run(configuration, env, runner=github_reader, transport_factory=factory, deep=True)
    failed = [c for c in checks if not c.ok]
    assert len(failed) == int(control is not None)
    if control:
        assert "gitea first" in failed[0].name and failed[0].name.endswith(control)
    assert github == [SANDBOX, "explicit/github", OFF_LIMITS]
    assert len(made) == (1 if control == "token" else 2)
    assert len([c for c in checks if c.name.startswith("gitea second") and c.ok]) == 4
    for forge in definitions:
        calls = fakes[forge.url].calls
        assert all(c.method == "GET" and c.body is None for c in calls)
        pages = [int(c.query["page"][0]) for c in calls if c.path.endswith("/labels")]
        assert all(c.query["limit"] == ["50"] for c in calls if c.path.endswith("/labels"))
        if forge.name == "second" or control not in ("token", "transport", "labels"):
            assert pages == list(range(1, len(rows) + 2))  # Includes the final empty page.
        if forge.name == "first" and control in ("token", "transport"):
            assert calls == []
        if forge.name == "first" and outcome == "bound":
            assert pages == list(range(1, 1001))
    output = ""
    if control == "protection" and outcome == 403:
        assert "unverified" in failed[0].detail
        monkeypatch.setattr(config, "load", lambda _: configuration)
        monkeypatch.setattr(config, "load_env_file", lambda: [])
        monkeypatch.setattr(preflight, "run", lambda *args, **kwargs: checks)
        assert cli.main(["preflight"]) == 1
        output = capsys.readouterr().out
        assert "unverified" in output and "checks failed" in output and "\nready\n" not in output
    diagnostics = repr(checks) + "\n".join(c.line for c in checks) + caplog.text + output + capsys.readouterr().out
    assert all(secret not in diagnostics for secret in [sensitive, "ambient-personal-secret", *filter(None, env.values())])


@pytest.mark.scenario("forge-preflight/isolated-forge-checks/mixed-configuration-remains-isolated")
@pytest.mark.parametrize("repo_count", [0, 2])
def test_gitea_default_transport_checks_unused_forges_and_each_repository(monkeypatch, repo_count):
    fake = FakeGitea()
    forge = config.ForgeConfig(name="local", kind="gitea", url=fake.base_url,
                               token_env="BOT", land_token_env="LAND", bot_login="Robot",
                               forbidden_probe="private/probe")
    repos = [Repo(name=f"owner/repo{i}", forge=forge.name) for i in range(repo_count)]
    configuration = Config(repos=repos, forges=[forge])
    names = {getattr(configuration.labels, f.name) for f in fields(configuration.labels)}
    rows = [dict(id=i, name=name) for i, name in enumerate(sorted(names | {"merge:ready", "merge:blocked"}), 1)]
    fake.route("GET", "/gitea/api/v1/user", Reply(dict(login="Robot", is_admin=False)))
    fake.route("GET", "/gitea/api/v1/repos/private/probe", Reply(status=404))
    for i, repo in enumerate(repos):
        fake.repository(repo.name, labels=rows if i else [])
        fake.route("GET", f"/gitea/api/v1/repos/{repo.name}/branch_protections/{repo.base}",
                   Reply(dict(branch_name=repo.base)))
    original = fake.open
    def authenticated(request, *, timeout):
        assert request.get_header("Authorization") == "token supplied-bot"
        return original(request, timeout=timeout)
    monkeypatch.setattr(fake, "open", authenticated)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setattr(os, "environ", {"BOT": "ambient-personal", "LAND": "human-secret"})
    def no_github(args):
        pytest.fail("no GitHub repository or probe was configured")
    checks = preflight.run(configuration, {"BOT": "supplied-bot", "LAND": "human-secret"}, runner=no_github)
    assert [c.name for c in checks if not c.ok] == [
        "GH_TOKEN", "negative control", *(["gitea local owner/repo0 labels"] if repo_count else []), "alerts"]
    remote = [c for c in checks if c.name.startswith("gitea local")]
    assert len(remote) == 2 + 2 * repo_count
    assert all(c.ok for c in remote if not c.name.endswith("repo0 labels"))
    assert sum(c.path.endswith("/user") for c in fake.calls) == 1
    assert sum(c.path.endswith("/private/probe") for c in fake.calls) == 1
    assert all(c.method == "GET" and c.body is None for c in fake.calls)
