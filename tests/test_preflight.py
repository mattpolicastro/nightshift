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
from importlib.resources import files
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote

import pytest

from manifest_fixtures import manifest_text
from nightshift import cli, config, preflight
from nightshift.config import Config, Repo
from nightshift.forge import http
from fake_gitea import FakeGitea, Reply
from specloop import openspec

pytestmark = pytest.mark.usefixtures("openspec_host")

SANDBOX = "matt/sandbox"
OFF_LIMITS = "matt/dotfiles"


@pytest.mark.scenario("openspec-binary/installed-binary-preflight/verified-executable-passes")
@pytest.mark.scenario("openspec-binary/installed-binary-preflight/wrong-checksum-blocks-readiness")
@pytest.mark.parametrize("deep", [False, True])
def test_openspec_binary_checksum_and_version(openspec_host, monkeypatch, deep):
    host = openspec_host
    monkeypatch.setattr(openspec, "_checked_paths", set())
    env = {"GH_TOKEN": "github_pat_x", "NIGHTSHIFT_OPENSPEC_BIN": str(host.binary),
           "NIGHTSHIFT_BIN_DIR": "invalid but unused", "OPENSPEC_TELEMETRY": "1"}
    configuration = cfg(forbidden_probe=OFF_LIMITS, slack_webhook="https://hooks/x")
    before = (host.binary.read_bytes(), host.binary.stat().st_mode, host.binary.stat().st_mtime_ns)
    baseline = preflight.run(configuration, env, runner=reader({SANDBOX}), deep=deep)
    assert all(c.ok for c in baseline)
    assert host.calls[0][1]["env"] == {**env, **openspec.ENV}
    assert not openspec._checked_paths
    assert (host.binary.read_bytes(), host.binary.stat().st_mode, host.binary.stat().st_mtime_ns) == before
    openspec._checked_paths.add(str(host.binary))
    assert preflight.run(configuration, env, runner=reader({SANDBOX}), deep=deep) == baseline
    assert len(host.calls) == 2  # A runtime cache hit still requires a fresh probe.
    host.binary.write_bytes(b"wrong bytes, same claimed version")
    failed = preflight.run(configuration, env, runner=reader({SANDBOX}), deep=deep)
    assert not find(failed, "openspec").ok and len(host.calls) == 2
    assert [c for c in failed if c.name != "openspec"] == [c for c in baseline if c.name != "openspec"]
    assert "docs/deploy.md#install-openspec" in find(failed, "openspec").detail


@pytest.mark.scenario("openspec-binary/installed-binary-preflight/unavailable-prerequisite-fails-closed")
@pytest.mark.parametrize("fault", [
    "missing", "directory", "non-executable", "unreadable", "manifest-missing", "manifest-unreadable",
    "empty", "missing-key", "duplicate", "unknown-key", "unknown-binary", "spacing", "uppercase",
    "short-hash", "whitespace", "indented-comment", "non-ascii", "crlf", "invalid-path",
    "version", "banner", "exit", "timeout", "launch", "decode",
    ("Darwin", "x86_64"), ("Linux", "aarch64"), ("Linux", "x86_64"),
    ("Windows", "ARM64"), ("Windows", "AMD64"), ("Darwin", "ARM64"),
])
def test_openspec_failures_ignore_cached_success(openspec_host, monkeypatch, fault):
    host = openspec_host
    env = {"NIGHTSHIFT_OPENSPEC_BIN": str(host.binary)}
    monkeypatch.setattr(openspec, "_checked_paths", {str(host.binary)})
    assert preflight._OPENSPEC_HOSTS == {("Darwin", "arm64"): "darwin-arm64"}
    data = host.sums.read_text()
    entry = next(line for line in data.splitlines() if "  binary/" in line)
    changed = {
        "empty": "", "missing-key": data.replace(entry, ""), "duplicate": data + entry + "\n",
        "unknown-key": data + entry.replace("binary/", "other/") + "\n",
        "unknown-binary": data + entry.replace("darwin-arm64", "linux-arm64") + "\n",
        "spacing": data.replace("  binary/", " binary/"), "uppercase": data.replace(entry, entry.upper()),
        "short-hash": data.replace(entry, entry[1:]), "whitespace": data + " \n",
        "indented-comment": data + " # ignored?\n", "non-ascii": data + "é\n",
        "crlf": data.replace("\n", "\r\n"),
    }
    if isinstance(fault, tuple):
        monkeypatch.setattr(preflight.platform, "system", lambda: fault[0])
        monkeypatch.setattr(preflight.platform, "machine", lambda: fault[1])
    elif fault in changed:
        host.sums.write_bytes(changed[fault].encode())
    elif fault in ("missing", "directory"):
        host.binary.unlink()
        if fault == "directory":
            host.binary.mkdir()
    elif fault == "non-executable":
        host.binary.chmod(0o600)
    elif fault == "manifest-missing":
        host.sums.unlink()
    elif fault in ("unreadable", "manifest-unreadable"):
        original = Path.open
        denied = host.binary if fault == "unreadable" else host.sums
        def read(path, *args, **kwargs):
            if path == denied:
                raise PermissionError("unreadable fixture")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", read)
    elif fault == "invalid-path":
        env["NIGHTSHIFT_OPENSPEC_BIN"] = "relative-secret"
    calls = []
    def probe(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 120
        errors = {"timeout": subprocess.TimeoutExpired(args, 120), "launch": PermissionError(),
                  "decode": UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid")}
        if fault in errors:
            raise errors[fault]
        return subprocess.CompletedProcess(args, int(fault == "exit"),
            "other" if fault == "version" else "OpenSpec " + openspec.VERSION if fault == "banner" else openspec.VERSION, "")
    check = preflight._openspec_check(env, runner=probe)
    assert not check.ok and "docs/deploy.md#install-openspec" in check.detail
    assert "relative-secret" not in check.detail
    assert len(calls) == int(fault in ("version", "banner", "exit", "timeout", "launch", "decode"))


def test_openspec_symlink_and_default_bounded_runner(openspec_host, monkeypatch, tmp_path):
    link = tmp_path / "selected"
    link.symlink_to(openspec_host.binary)
    def probe(args, **kwargs):
        assert args == [str(link), "--version"] and kwargs["timeout"] == 120
        assert kwargs["env"] == {"NIGHTSHIFT_OPENSPEC_BIN": str(link), **openspec.ENV}
        return subprocess.CompletedProcess(args, 0, openspec.VERSION, "")
    monkeypatch.setattr(subprocess, "run", probe)
    assert openspec_host.check({"NIGHTSHIFT_OPENSPEC_BIN": str(link)}).ok


@pytest.mark.scenario("openspec-binary/release-checksum-provenance/supplied-release-evidence-is-copied-offline")
def test_openspec_committed_checksum_resource():
    sums = preflight._openspec_checksums(files("specloop").joinpath("openspec-SHA256SUMS").read_text())
    assert sums == {
        "archive/openspec-darwin-arm64.tar.gz": "30e9f4b0a6ca4d9025a3c78a85343e15f60fa03ce042c358d6667d177a03ee19",
        "archive/openspec-darwin-x64.tar.gz": "9c8fb2cec0b118f0dddd9db2455ee8c46fc458ccc60aa20230e1baeee85549a5",
        "archive/openspec-linux-arm64-musl.tar.gz": "e72004a0ef31c8c32868a729fb3ce4e401b97ec7034dabdf5262aa10f6472b33",
        "archive/openspec-linux-arm64.tar.gz": "06d2a38a322b4da993500edd6726a3b554adfe565d7e4834d140f72102c5c0b3",
        "archive/openspec-linux-x64-musl.tar.gz": "c355b8d4b06b5734330f2e098d8c9fc1f3cf8b0b5fa1573f30d91e710a934e56",
        "archive/openspec-linux-x64.tar.gz": "9e5215b8f9a7024fa635f693a6a691d82220703868f1949927e675b87503c608",
        "archive/openspec-windows-arm64.zip": "b9a0d9eee8c36c06fab86bd89cd87138855b29a3afd2e47346991f8787a823b1",
        "archive/openspec-windows-x64.zip": "6c00846a7e4813366ff8395cef71cadcc11d66eb7a112fe332358f8de5ae230e",
        "binary/darwin-arm64/openspec": "bf0fbb541e79c3990906254be7d1b075c95efceb41e9e28899e72357ba4268a0",
    }


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
@pytest.mark.scenario("openspec-binary/installed-binary-preflight/wrong-checksum-blocks-readiness")
@pytest.mark.parametrize("failure", [None, "missing-token", "scope", "reach", "negative-control", "alerts", "openspec"])
def test_cli_preflight_needs_only_front_door_prerequisites(tmp_path, monkeypatch, capsys, deep, failure, openspec_host):
    if failure == "openspec":
        openspec_host.binary.write_bytes(b"tampered")
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
            "openspec": "openspec",
        }[failure]
        assert f"FAIL  {failed_check}:" in output.out
    if failure == "openspec":
        assert openspec_host.calls == [] and "docs/deploy.md#install-openspec" in output.out


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
@pytest.mark.scenario("forge-preflight/clone-credential-identity/clone-resolves-the-bot")
@pytest.mark.scenario("forge-preflight/clone-credential-identity/wrong-or-unavailable-clone-identity-fails")
@pytest.mark.parametrize("control, outcome", [
    (None, "pass"), ("token", "missing"), ("token", "empty"), ("transport", "invalid"),
    (None, "bare-origin"), (None, "terminator"), (None, "equals"),
    *[("identity", value) for value in ("admin", "wrong", "missing", "missing-login", "numeric", "malformed", 401)],
    *[("forbidden probe", value) for value in ("absent", 200, 401, 403, 500, "connection")],
    *[("labels", value) for value in ("missing", "duplicate", "conflict", "repeat", "later-error",
                                     "bound", "malformed", "boolean-id", "bad-page")],
    *[("protection", value) for value in (403, 404, 500, "mismatch", "missing", "malformed", "bad-json")],
    *[("clone identity", value) for value in (
        "origin-missing", "origin-host", "origin-prefix", "origin-repo", "origin-port", "origin-scheme",
        "origin-ssh", "origin-userinfo", "origin-query", "origin-fragment", "origin-newline",
        "origin-duplicate", "origin-conflict", "origin-failure", "origin-timeout", "origin-unavailable",
        "fill-failure", "fill-timeout", "fill-unavailable", "wrong-user", "no-user", "no-password",
        "empty-user", "empty-password", "duplicate-user", "duplicate-password", "duplicate-extra",
        "malformed", "empty-output", "empty-key", "bad-key", "blank-middle", "carriage-return", "nul",
    )],
])
def test_preflight_gitea_negative_controls(monkeypatch, capsys, caplog, control, outcome):
    definitions = [config.ForgeConfig(
        name=name, kind="gitea", url=f"https://{name}.example:8443/install", token_env=f"{name}_BOT",
        land_token_env=f"{name}_LAND", bot_login="Robot", forbidden_probe="private/no#access",
    ) for name in ("first", "second")]
    repositories = [Repo(name=f"{f.name}/café#repo", forge=f.name, base="release/v1#tag", dir=f"/fixture/{f.name}/bot clone")
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
    made, github, git_calls = [], [], []
    def credentials(args, **kwargs):
        repo = next(r for r in repositories if str(r.clone_dir()) == args[2])
        forge = configuration.forge_for(repo)
        path = "install/" + quote(repo.name, safe="/") + ("" if outcome == "bare-origin" else ".git")
        origin = f"https://{forge.name}.example:8443/{path}"
        lookup_path = "install/" + repo.name + ("" if outcome == "bare-origin" else ".git")
        remote = args[3:] == ["remote", "get-url", "--all", "origin"]
        assert args == ["git", "-C", str(repo.clone_dir()), *(
            ["remote", "get-url", "--all", "origin"] if remote else ["credential", "fill"])]
        assert kwargs == dict(
            input="" if remote else f"protocol=https\nhost={forge.name}.example:8443\npath={lookup_path}\n\n",
            env={**env, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GCM_INTERACTIVE": "Never"},
            timeout=10, capture_output=True, text=True, check=False)
        git_calls.append((forge.name, remote))
        stage = "origin" if remote else "fill"
        bad = control == "clone identity" and forge.name == "first"
        if bad and outcome == stage + "-timeout":
            raise subprocess.TimeoutExpired(args, 10, output=sensitive, stderr=sensitive)
        if bad and outcome == stage + "-unavailable":
            raise OSError(sensitive)
        evidence = "protocol=https\nusername=rObOt\npassword=" + sensitive + "\n"
        if outcome == "terminator":
            evidence += "\n"
        if outcome == "equals":
            evidence = evidence.replace(sensitive, sensitive + "=suffix")
        origins = {
            "origin-missing": "", "origin-host": origin.replace("first.example", "elsewhere.example"),
            "origin-prefix": origin.replace("/install/", "/other/"), "origin-repo": origin + "extra",
            "origin-port": origin.replace(":8443", ":443"), "origin-scheme": origin.replace("https:", "http:"),
            "origin-ssh": "git@first.example:first/repo.git",
            "origin-userinfo": origin.replace("://", "://Robot:" + env[forge.token_env] + "@"),
            "origin-query": origin + "?" + env[forge.token_env], "origin-fragment": origin + "#" + env[forge.token_env],
            "origin-newline": origin.replace("/install", "/in\nstall"), "origin-duplicate": origin + "\n" + origin,
            "origin-conflict": origin + "\nhttps://other.example/owner/repo",
        }
        fields = {
            "wrong-user": evidence.replace("rObOt", sensitive), "no-user": "password=" + sensitive,
            "no-password": "username=Robot", "empty-user": evidence.replace("rObOt", ""),
            "empty-password": evidence.replace(sensitive, ""), "duplicate-user": evidence + "username=Robot\n",
            "duplicate-password": evidence + "password=" + sensitive, "duplicate-extra": evidence + "protocol=https\n",
            "malformed": evidence + sensitive, "empty-output": "", "empty-key": evidence + "=" + sensitive,
            "bad-key": evidence + "bad key=" + sensitive, "blank-middle": evidence.replace("password=", "\npassword="),
            "carriage-return": evidence + "extra=x\ry", "nul": evidence + "extra=x\x00y",
        }
        stdout = (origins.get(outcome, origin) + "\n" if remote else fields.get(outcome, evidence)) if bad else (
            origin + "\n" if remote else evidence)
        return subprocess.CompletedProcess(args, int(bad and outcome == stage + "-failure"), stdout, sensitive)
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
    supplied_env = env.copy()
    checks = preflight.run(configuration, env, runner=github_reader, transport_factory=factory,
                           credential_runner=credentials, deep=True)
    assert env == supplied_env
    failed = [c for c in checks if not c.ok]
    assert len(failed) == int(control is not None)
    if control:
        assert "gitea first" in failed[0].name and failed[0].name.endswith(control)
    assert github == [SANDBOX, "explicit/github", OFF_LIMITS]
    assert len(made) == (1 if control == "token" else 2)
    assert len([c for c in checks if c.name.startswith("gitea second") and c.ok]) == 5
    assert git_calls[-2:] == [("second", True), ("second", False)]
    first_calls = [] if control in ("token", "transport") else [("first", True)]
    if first_calls and not (control == "clone identity" and outcome.startswith("origin-")):
        first_calls.append(("first", False))
    assert git_calls[:-2] == first_calls
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
    if (control == "protection" and outcome == 403) or control == "clone identity":
        assert "unverified" in failed[0].detail
        monkeypatch.setattr(config, "load", lambda _: configuration)
        monkeypatch.setattr(config, "load_env_file", lambda: [])
        monkeypatch.setattr(preflight, "run", lambda *args, **kwargs: checks)
        assert cli.main(["preflight"]) == 1
        output = capsys.readouterr().out
        assert "unverified" in output and "checks failed" in output and "\nready\n" not in output
    diagnostics = repr(checks) + "\n".join(c.line for c in checks) + caplog.text + output + capsys.readouterr().out
    assert all(secret not in diagnostics for secret in [sensitive, "ambient-personal-secret", *filter(None, env.values())])


@pytest.mark.scenario("forge-preflight/clone-credential-identity/clone-resolves-the-bot")
@pytest.mark.parametrize("root", ["http://127.0.0.1:3000", "https://forge.example/prefix/"])
def test_clone_credentials_override_prompts_without_mutating_environment(tmp_path, monkeypatch, root):
    forge = config.ForgeConfig(name="tea", kind="gitea", url=root, bot_login="Robot")
    repo = Repo(name="owner/repo.git", dir=str(tmp_path / "bot clone"), forge="tea")
    env = {"PATH": "/fixture/bin", "HOME": str(tmp_path), "GIT_TERMINAL_PROMPT": "1",
           "GIT_ASKPASS": "supplied-askpass", "SSH_ASKPASS": "supplied-ssh-askpass", "GCM_INTERACTIVE": "Always"}
    original = env.copy()
    monkeypatch.setenv("PERSONAL_CREDENTIAL", "must-not-inherit")
    calls = []
    def git(args, **kwargs):
        calls.append(args)
        assert kwargs["env"] == {**original, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "",
                                 "SSH_ASKPASS": "", "GCM_INTERACTIVE": "Never"}
        assert kwargs["timeout"] == 10
        assert kwargs["capture_output"] and kwargs["text"] and not kwargs["check"]
        if len(calls) == 1:
            assert kwargs["input"] == ""
            output = root.rstrip("/") + "/owner/repo.git\n"
        else:
            protocol, host_path = root.split("://")
            host, _, prefix = host_path.partition("/")
            path = prefix.rstrip("/") + "/owner/repo.git"
            assert kwargs["input"] == f"protocol={protocol}\nhost={host}\npath={path.lstrip('/')}\n\n"
            output = "username=ROBOT\npassword=fixture-secret\n"
        return subprocess.CompletedProcess(args, 0, output, "private helper diagnostic")
    ok, detail = preflight._clone_identity(repo, forge, env, git)
    assert ok and "fixture-secret" not in detail and "private helper diagnostic" not in detail
    assert env == original
    assert calls == [["git", "-C", repo.dir, *command] for command in (
        ["remote", "get-url", "--all", "origin"], ["credential", "fill"])]


@pytest.mark.scenario("forge-preflight/clone-credential-identity/clone-resolves-the-bot")
@pytest.mark.scenario("forge-preflight/clone-credential-identity/wrong-or-unavailable-clone-identity-fails")
@pytest.mark.parametrize("prefix, decoded_user", [
    ("%69nstall", "Robot"), ("%69nstall", "Personal"), ("%2569nstall", "Robot"),
    *[("install" + escape, "Robot") for escape in ("%00", "%0a", "%0D", "%1f", "%7f", "%C2%85")],
])
def test_clone_credentials_use_git_decoded_path(prefix, decoded_user, monkeypatch):
    forge = config.ForgeConfig(name="tea", kind="gitea", url=f"https://forge.example/{prefix}", bot_login="Robot")
    repo = Repo(name="owner/repo", dir="/fixture/clone", forge="tea")
    calls = []
    def no_subprocess(*args, **kwargs):
        pytest.fail("real credential helper must not run")
    monkeypatch.setattr(subprocess, "run", no_subprocess)
    def git(args, **kwargs):
        calls.append((args[3:], kwargs["input"]))
        assert kwargs["timeout"] == 10 and kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        output = forge.url + "/owner/repo.git\n"
        if args[3:] == ["credential", "fill"]:
            # Model path-specific helper entries: the encoded key is a bot,
            # while Git's actual decoded key may resolve a personal account.
            decoded = "install" if prefix == "%69nstall" else "%69nstall"
            expected = f"protocol=https\nhost=forge.example\npath={decoded}/owner/repo.git\n\n"
            user = decoded_user if kwargs["input"] == expected else "Robot"
            output = f"username={user}\npassword=fixture-secret\n"
        return subprocess.CompletedProcess(args, 0, output, "private helper diagnostic")
    ok, detail = preflight._clone_identity(repo, forge, {}, git)
    valid_path = prefix in ("%69nstall", "%2569nstall")
    assert ok == (valid_path and decoded_user == "Robot")
    expected_calls = [(["remote", "get-url", "--all", "origin"], "")]
    if valid_path:
        decoded = "install" if prefix == "%69nstall" else "%69nstall"
        expected_calls.append((["credential", "fill"], f"protocol=https\nhost=forge.example\npath={decoded}/owner/repo.git\n\n"))
    assert calls == expected_calls
    assert "fixture-secret" not in detail and "private helper diagnostic" not in detail


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
    def credentials(args, **kwargs):
        repo = next(r for r in repos if str(r.clone_dir()) == args[2])
        return subprocess.CompletedProcess(args, 0, (
            f"{forge.url}/{repo.name}\n" if args[3:] == ["remote", "get-url", "--all", "origin"]
            else "username=Robot\npassword=helper-secret\n"), "")
    monkeypatch.setattr(subprocess, "run", credentials)
    checks = preflight.run(configuration, {"BOT": "supplied-bot", "LAND": "human-secret"}, runner=no_github)
    assert [c.name for c in checks if not c.ok] == [
        "GH_TOKEN", "negative control", *(["gitea local owner/repo0 labels"] if repo_count else []), "alerts"]
    remote = [c for c in checks if c.name.startswith("gitea local")]
    assert len(remote) == 2 + 3 * repo_count
    assert all(c.ok for c in remote if not c.name.endswith("repo0 labels"))
    assert sum(c.path.endswith("/user") for c in fake.calls) == 1
    assert sum(c.path.endswith("/private/probe") for c in fake.calls) == 1
    assert all(c.method == "GET" and c.body is None for c in fake.calls)
