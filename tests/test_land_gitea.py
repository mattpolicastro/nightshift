"""Socket-free landing scripts using real confined HTTP serialization."""

from collections import deque
from dataclasses import replace
from email.message import Message
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
from urllib.response import addinfourl
from urllib.error import HTTPError, URLError

import pytest

from nightshift import config, land
from nightshift.forge import http
from test_land import BASE, HEAD, EMPTY_STATUS, RUN, STATUS, FakeClock, scripted_runner


ROOT = "https://forge.example/team"
REPO = "Owner/Project"
URL = f"{ROOT}/{REPO}/pulls/78"
PR_PATH = f"{ROOT}/api/v1/repos/{REPO}/pulls/78"
MAIN_PATH = f"{ROOT}/api/v1/repos/{REPO}/branches/main"
HUMAN = "LAND_HUMAN"
BOT = "LAND_BOT"
PR = {"number": 78, "html_url": URL, "state": "open", "title": "Reviewed change",
      "head": {"sha": HEAD}, "base": {"ref": "main"}, "body": "- [ ] Review this head"}
MAIN = {"name": "main", "commit": {"id": BASE}}


class RecordingOpener:
    """Exact scripts reusable by later stages; unexpected reads/writes fail.

    Responses may be JSON, bytes, exceptions or callbacks for races and clocks.
    No network or subprocess fallback is permitted.
    """

    def __init__(self):
        self.steps, self.calls, self.handlers = deque(), [], []

    def add_handler(self, handler):
        self.handlers.append(handler)

    def expect(self, method, url, response, *, body=None, status=200, headers=None, timeout=30):
        self.steps.append((method, url, response, body, status, headers, timeout))
        return self

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        assert self.steps, f"unexpected HTTP operation: {request.get_method()} {request.full_url}"
        method, url, response, body, status, headers, expected_timeout = self.steps.popleft()
        assert request.get_method() == method and request.full_url == url
        assert (json.loads(request.data) if request.data is not None else None) == body
        assert timeout == expected_timeout
        if callable(response):
            response = response()
        if isinstance(response, Exception):
            raise response
        if hasattr(response, "read"):
            return response
        message = Message()
        for key, value in (headers or {}).items():
            message[key] = value
        payload = response if isinstance(response, bytes) else json.dumps(response).encode()
        return addinfourl(BytesIO(payload), message, url, status)


@pytest.fixture
def opener():
    result = RecordingOpener()
    yield result
    assert not result.steps, "unconsumed HTTP script"


@pytest.fixture(autouse=True)
def isolated_operator(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(HUMAN, "process-human-secret")
    monkeypatch.setenv(BOT, "process-bot-secret")
    monkeypatch.setenv("GH_TOKEN", "ambient-gh-secret")

    def forbidden(*args, **kwargs):
        pytest.fail("landing must use injected HTTP and subprocess scripts")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(config, "load_env_file", forbidden)
    monkeypatch.setattr(http, "sleep", lambda _: None)
    return tmp_path


def forge_table(name="gitea", root=ROOT):
    return (f'[[forges]]\nname = "{name}"\nkind = "gitea"\nurl = "{root}"\n'
            f'token_env = "{BOT}"\nland_token_env = "{HUMAN}"\n'
            'bot_login = "automation"\ntrusted_logins = ["operator", "automation"]\n'
            'wip_prefix = "Draft: "\n')


def enrollment(name=REPO, forge="gitea"):
    return f'[[repos]]\nname = "{name}"\nforge = "{forge}"\n'


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(forge_table() + enrollment())
    return path


def invoke(config_path, runner, opener, pr=URL, *args):
    return land.main([pr, "--config", str(config_path), *args], runner=runner, opener=opener)


def resolved(config_path, runner, opener):
    cfg = config.load(config_path)
    clock = FakeClock()
    ops = land.Operations(runner, clock.sleep, clock.monotonic)
    identity, definition = land.resolve_target(URL, None, cfg, ops)
    ops.forge = land.GiteaLandingForge(definition, land.load_land_token(definition), opener=opener)
    ops.gitea_forges = tuple(cfg.forges)
    return identity, ops


@pytest.mark.scenario("gitea-landing/configured-landing-target/configured-url-selects-gitea")
@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/repository-resolves")
@pytest.mark.parametrize("repo", [REPO, "My_Team/Project"])
@pytest.mark.parametrize("root", [ROOT, "https://forge.example:443/team/",
                                  "https://forge.example/%74eam",
                                  "http://localhost:3000/git", "http://127.0.0.1:3000",
                                  "http://[::1]:3000/git"])
def test_configured_url_selects_gitea(config_path, scripted_runner, opener, capsys, root, repo):
    config_path.write_text(forge_table(root=root) + enrollment(name=repo))
    root = root.rstrip("/")
    canonical = f"{root}/{repo}/pulls/78"
    supplied = canonical.replace(repo, repo.lower()).replace("forge.example", "FORGE.EXAMPLE")
    if ":443" in supplied:
        supplied = supplied.replace(":443", "")
    elif root == ROOT:
        supplied = supplied.replace("FORGE.EXAMPLE", "FORGE.EXAMPLE:443")
    opener.expect("GET", f"{root}/api/v1/repos/{repo}/pulls/78", {**PR, "html_url": canonical})
    opener.expect("GET", f"{root}/api/v1/repos/{repo}/branches/main", MAIN)
    scripted_runner.expect(STATUS, EMPTY_STATUS)
    assert invoke(config_path, scripted_runner, opener, supplied, "--repo", repo.upper()) == 1
    assert "unticked human checks" in capsys.readouterr().err
    assert all(request.get_method() == "GET" for request, _ in opener.calls)
    assert [argv for argv, _ in scripted_runner.calls] == [STATUS]


@pytest.mark.scenario("gitea-landing/configured-landing-target/configured-url-selects-gitea")
def test_underscore_owner_run_discovery(config_path, scripted_runner, opener):
    repo = "My_Team/Project"
    canonical = f"{ROOT}/{repo}/pulls/78"
    config_path.write_text(forge_table() + enrollment(name=repo))
    ops = land.Operations(scripted_runner, lambda _: None, lambda: 0)
    identity, _ = land.resolve_target(canonical.lower(), None, config.load(config_path), ops)
    scripted_runner.expect(STATUS, {**EMPTY_STATUS, "runs": [{**RUN, "pr": canonical.lower()}]})
    found = land.discover_run(identity, ops)
    assert found is not None and found.pr == canonical
    assert identity.repo == repo and not opener.calls


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/repository-resolves")
@pytest.mark.scenario("reviewed-pr-landing/github-kind-enrollment-boundary/gitea-enrollment-grants-no-github-authority")
def test_gitea_owner_does_not_change_github_validation(config_path, scripted_runner, opener):
    config_path.write_text(forge_table() + enrollment(name="My_Team/Project")
                           + '[[repos]]\nname = "Owner/Project"\n')
    ops = land.Operations(scripted_runner, lambda _: None, lambda: 0)
    cfg = config.load(config_path)
    identity, definition = land.resolve_target("https://github.com/Owner/Project/pull/78", None, cfg, ops)
    assert identity == land.Identity("owner/project", 78) and definition.kind == "github"
    for pr, repo in [("https://github.com/My_Team/Project/pull/78", None), ("78", "My_Team/Project")]:
        with pytest.raises(land.Refusal, match="invalid repository identity"):
            land.resolve_target(pr, repo, cfg, ops)
    assert not opener.calls and not scripted_runner.calls


@pytest.mark.scenario("gitea-landing/configured-landing-target/invalid-or-ambiguous-target-refuses")
@pytest.mark.parametrize("repo", ["./Project", "../Project", "My%5fTeam/Project", "My@Team/Project",
                                  "My_Team/..", "My_Team/Pro%2fject", "My_Team/Project/extra"])
@pytest.mark.parametrize("source", ["url", "repo-option", "enrollment"])
def test_unsafe_gitea_repo_components_refuse(config_path, scripted_runner, opener, capsys, repo, source):
    args = ["--repo", repo] if source == "repo-option" else []
    url = f"{ROOT}/{repo}/pulls/78" if source == "url" else URL
    if source == "enrollment":
        config_path.write_text(forge_table() + enrollment(name=repo))
    assert invoke(config_path, scripted_runner, opener, url, *args) == 1
    assert capsys.readouterr().err.startswith("land: ")
    assert not opener.calls and not scripted_runner.calls


@pytest.mark.scenario("gitea-landing/configured-landing-target/invalid-or-ambiguous-target-refuses")
@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("url", [
    "https://unknown.example/team/Owner/Project/pulls/78",
    "http://forge.example/team/Owner/Project/pulls/78",
    "https://forge.example:444/team/Owner/Project/pulls/78",
    "https://forge.example/Owner/Project/pulls/78",
    "https://forge.example/team-other/Owner/Project/pulls/78",
    "https://forge.example/team/Other/Project/pulls/78",
    URL + "?token=secret", URL + "#fragment", URL + "?", URL + "#",
    URL.replace("forge.example", "operator@forge.example"),
    URL.replace("forge.example", "forge.example@evil.example"),
    URL.replace("forge.example", "forge.example:"),
    URL.replace("forge.example", "forge.example:0"),
    URL.replace("forge.example", "forge.example:bad"),
    URL.replace("forge.example", "[broken"),
    URL.replace("team/", "team/../team/"), URL.replace("team/", "team/./"),
    URL.replace("team/", "team/%2e%2e/"), URL.replace("Project", "Pro%6aect"),
    URL.replace("Project", "Pro%zzject"), URL.replace("Project", "Pro%252fject"),
    URL.replace("Project", "Pro\\ject"), URL.replace("Project", "Pro\nject"),
    URL.replace("Project", "Pro\x85ject"), " " + URL, URL + "/files",
    URL.replace("78", "0"), URL.replace("78", "-1"), URL.replace("78", "１２"),
    URL.replace("pulls", "pull"),
])
def test_unsafe_or_foreign_target_refuses(config_path, scripted_runner, opener, capsys, monkeypatch, url):
    def forbidden_credentials(*args):
        pytest.fail("invalid targets must refuse before credential loading")
    monkeypatch.setattr(config, "parse_env_file", forbidden_credentials)
    assert invoke(config_path, scripted_runner, opener, url, "--checks-done") == 1
    assert capsys.readouterr().err.startswith("land: ")
    assert not opener.calls and not scripted_runner.calls


@pytest.mark.scenario("gitea-landing/configured-landing-target/invalid-or-ambiguous-target-refuses")
@pytest.mark.parametrize("problem", ["ambiguous-host", "contradictory-repo", "wrong-forge",
                                     "unenrolled", "duplicate-enrollment", "bad-config", "missing-config"])
def test_configuration_and_enrollment_refusals(config_path, scripted_runner, opener, capsys, problem):
    text, args = forge_table(), []
    if problem == "ambiguous-host":
        text += forge_table("other", "https://FORGE.example:9443/elsewhere")
    if problem == "wrong-forge":
        text += forge_table("other", "https://other.example")
    if problem != "unenrolled":
        text += enrollment(forge="other" if problem == "wrong-forge" else "gitea")
    if problem == "duplicate-enrollment":
        text += enrollment(name=REPO.upper())
    if problem == "bad-config":
        text = "[broken"
    if problem == "contradictory-repo":
        args = ["--repo", "other/project"]
    config_path.write_text(text)
    if problem == "missing-config":
        config_path.unlink()
    assert invoke(config_path, scripted_runner, opener, URL, *args, "--checks-done") == 1
    assert capsys.readouterr().err.startswith("land: ")
    assert not opener.calls and not scripted_runner.calls


@pytest.mark.scenario("reviewed-pr-landing/github-kind-enrollment-boundary/gitea-enrollment-grants-no-github-authority")
@pytest.mark.scenario("gitea-landing/configured-landing-target/invalid-or-ambiguous-target-refuses")
@pytest.mark.parametrize("mode", ["url", "number", "inferred"])
def test_gitea_enrollment_grants_no_github_authority(config_path, scripted_runner, opener, capsys, mode):
    args = ["--repo", REPO] if mode == "number" else []
    if mode == "inferred":
        scripted_runner.expect(["gh", "repo", "view", "--json", "nameWithOwner"], {"nameWithOwner": REPO})
    pr = f"https://github.com/{REPO}/pull/78" if mode == "url" else "78"
    assert invoke(config_path, scripted_runner, opener, pr, *args, "--checks-done") == 1
    assert "not enrolled" in capsys.readouterr().err
    assert not opener.calls
    assert len(scripted_runner.calls) == int(mode == "inferred")


@pytest.mark.scenario("gitea-landing/configured-landing-target/invalid-or-ambiguous-target-refuses")
@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("patch", [
    {"number": 79}, {"number": True}, {"html_url": URL.replace("78", "79")},
    {"html_url": URL.replace("forge.example", "evil.example")},
    {"html_url": URL.replace("team/", "elsewhere/")},
    {"html_url": URL.replace("Owner", "Other")}, {"html_url": None},
    {"html_url": URL + "?secret=remote-secret"},
    {"state": "closed"}, {"state": "remote-secret"},
    {"base": {"ref": "develop"}}, {"base": {}}, {"base": "remote-secret"},
    {"head": {"sha": "abc"}}, {"head": None}, {"head": {"sha": "z" * 40}},
    {"title": None}, {"body": None},
])
def test_returned_pr_facts_must_match(config_path, scripted_runner, opener, capsys, patch):
    opener.expect("GET", PR_PATH, {**PR, **patch})
    assert invoke(config_path, scripted_runner, opener, URL, "--checks-done") == 1
    error = capsys.readouterr().err
    assert error.startswith("land: ") and "remote-secret" not in error
    assert len(opener.calls) == 1 and not scripted_runner.calls


@pytest.mark.scenario("gitea-landing/configured-landing-target/configured-url-selects-gitea")
@pytest.mark.parametrize("title, draft", [("Draft: change", True), ("draft: change", False),
                                          ("WIP: change", False), ("Review Draft: change", False)])
def test_wip_prefix_is_case_sensitive(config_path, scripted_runner, opener, title, draft):
    identity, ops = resolved(config_path, scripted_runner, opener)
    opener.expect("GET", PR_PATH, {**PR, "title": title})
    pr = land.read_pr(identity, ops)
    assert pr.identity.url == URL and pr.identity.repo == REPO and pr.draft is draft


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("main", [{}, [], {"name": "other", "commit": {"id": BASE}},
                                   {"name": "main", "commit": {}},
                                   {"name": "main", "commit": {"id": "short"}},
                                   {"name": "main", "commit": None}])
def test_existing_main_facts_required(config_path, scripted_runner, opener, capsys, main):
    opener.expect("GET", PR_PATH, PR).expect("GET", MAIN_PATH, main)
    assert invoke(config_path, scripted_runner, opener, URL, "--checks-done") == 1
    assert "land:" in capsys.readouterr().err
    assert not scripted_runner.calls


@pytest.mark.scenario("gitea-landing/human-credential-source/landing-file-overrides-process-token")
@pytest.mark.parametrize("file_present", [True, False])
def test_land_gitea_uses_land_token_only(config_path, isolated_operator, scripted_runner, opener,
                                        monkeypatch, capsys, file_present):
    directory = isolated_operator / ".config/nightshift"
    directory.mkdir(parents=True)
    (directory / "env").write_text(f"export {HUMAN}=wrong-bot-file\nexport {BOT}=bot-file-secret\n")
    file_token = "file-human-$LAND_BOT-$(do-not-execute)-`not-a-command`"
    if file_present:
        (directory / "land.env").write_text(
            f'export {HUMAN}="{file_token}"\nexport {BOT}=wrong-bot-secret\n'
            'export UNRELATED_LANDING_SECRET="unrelated-secret"\n')
    parsed_paths, parse = [], config.parse_env_file

    def recording_parse(path=None):
        parsed_paths.append(path)
        assert path == directory / "land.env"
        return parse(path)

    monkeypatch.setattr(config, "parse_env_file", recording_parse)
    before = dict(os.environ)
    opener.expect("GET", PR_PATH, PR).expect("GET", MAIN_PATH, MAIN)
    scripted_runner.expect(STATUS, EMPTY_STATUS)
    assert invoke(config_path, scripted_runner, opener) == 1
    assert parsed_paths == [directory / "land.env"]
    expected = file_token if file_present else "process-human-secret"
    assert all(req.get_header("Authorization") == "token " + expected for req, _ in opener.calls)
    assert dict(os.environ) == before
    assert [argv for argv, _ in scripted_runner.calls] == [STATUS]
    assert all("env" not in kwargs for _, kwargs in scripted_runner.calls)
    output = capsys.readouterr()
    for secret in [file_token, "process-human-secret", "process-bot-secret", "bot-file-secret",
                   "wrong-bot-secret", "unrelated-secret", "ambient-gh-secret"]:
        assert secret not in output.out + output.err + repr(scripted_runner.calls)


@pytest.mark.scenario("gitea-landing/configured-landing-target/configured-url-selects-gitea")
@pytest.mark.parametrize("matching", [False, True])
def test_discovery_retains_canonical_origin_and_prefix(config_path, scripted_runner, opener, matching):
    identity, ops = resolved(config_path, scripted_runner, opener)
    rows = [{**RUN, "pr": URL.replace("forge.example", "elsewhere.example")},
            {**RUN, "pr": URL.replace("78", "79")}]
    if matching:
        rows.append({**RUN, "pr": URL.replace(REPO, REPO.upper())})
    scripted_runner.expect(STATUS, {**EMPTY_STATUS, "runs": rows})
    found = land.discover_run(identity, ops)
    assert found == (land.LandingRun(RUN["change"], URL, HEAD, "paused", "human_checks")
                     if matching else None)
    assert not opener.calls


@pytest.mark.scenario("gitea-landing/configured-landing-target/invalid-or-ambiguous-target-refuses")
@pytest.mark.parametrize("url", [URL.replace("team/", "wrong/"), URL + "?query",
                                 URL.replace("pulls/78", "pulls/nope"),
                                 URL.replace("https:", "http:"),
                                 URL.replace("forge.example", "forge.example:444")])
def test_malformed_target_host_blocks_discovery(config_path, scripted_runner, opener, url):
    identity, ops = resolved(config_path, scripted_runner, opener)
    scripted_runner.expect(STATUS, {**EMPTY_STATUS, "runs": [{**RUN, "pr": url}]})
    with pytest.raises(land.Refusal, match="malformed PR URL"):
        land.discover_run(identity, ops)
    assert not opener.calls


@pytest.mark.scenario("gitea-landing/configured-landing-target/configured-url-selects-gitea")
@pytest.mark.parametrize("github_target", [False, True])
def test_configured_loopback_run_discovery(config_path, scripted_runner, opener, github_target):
    identity, ops = resolved(config_path, scripted_runner, opener)
    local = config.ForgeConfig(name="local", kind="gitea", url="http://localhost:3000/git")
    ops.gitea_forges += (local,)
    local_url = f"{local.url}/{REPO}/pulls/78"
    if github_target:
        identity = land.Identity("owner/project", 78)
    else:
        identity = land.Identity(REPO, 78, local.url, local.name)
    scripted_runner.expect(STATUS, {**EMPTY_STATUS, "runs": [{**RUN, "pr": local_url}]})
    found = land.discover_run(identity, ops)
    assert (found is None) is github_target
    if found is not None:
        assert found.pr == local_url


@pytest.mark.scenario("gitea-landing/human-credential-source/missing-human-token-refuses")
@pytest.mark.parametrize("failure", ["http", "redirect", "connection", "json", "shape", "main-missing"])
def test_transport_errors_never_leak_secrets(config_path, scripted_runner, opener, capsys, failure):
    secret = "process-human-secret"
    if failure == "connection":
        for _ in range(3):
            opener.expect("GET", PR_PATH, URLError(secret))
    elif failure == "http":
        opener.expect("GET", PR_PATH, HTTPError(URL + secret, 403, secret, Message(), BytesIO(secret.encode())))
    elif failure == "redirect":
        opener.expect("GET", PR_PATH, secret.encode(), status=302,
                      headers={"Location": "https://evil.example/" + secret})
    elif failure == "json":
        opener.expect("GET", PR_PATH, secret.encode())
    elif failure == "shape":
        opener.expect("GET", PR_PATH, [secret])
    else:
        opener.expect("GET", PR_PATH, PR).expect("GET", MAIN_PATH, secret.encode(), status=404)
    assert invoke(config_path, scripted_runner, opener, URL, "--checks-done") == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert not scripted_runner.calls
    assert all(req.get_method() == "GET" for req, _ in opener.calls)


@pytest.mark.scenario("gitea-landing/human-credential-source/landing-file-overrides-process-token")
def test_credential_is_invocation_local(config_path, isolated_operator, scripted_runner, opener, monkeypatch):
    path = isolated_operator / ".config/nightshift/land.env"
    path.parent.mkdir(parents=True)
    path.write_text(f"export {HUMAN}='first-secret'\n")
    identity, first = resolved(config_path, scripted_runner, opener)
    path.write_text(f"export {HUMAN}='second-secret'\n")
    _, second = resolved(config_path, scripted_runner, opener)
    monkeypatch.setenv(HUMAN, "changed-process-secret")
    opener.expect("GET", PR_PATH, PR).expect("GET", MAIN_PATH, MAIN)
    land.read_pr(identity, first)
    land.read_main(identity, second)
    assert [req.get_header("Authorization") for req, _ in opener.calls] == [
        "token first-secret", "token second-secret"]
    assert "secret" not in repr(first.forge) + repr(second.forge)


@pytest.mark.scenario("gitea-landing/configured-landing-target/configured-url-selects-gitea")
@pytest.mark.parametrize("expiry", ["before", "open", "body", "retry", "none"])
def test_http_reads_use_current_operations_budget(config_path, scripted_runner, opener, monkeypatch, expiry):
    identity, ops = resolved(config_path, scripted_runner, opener)
    clock = FakeClock()
    bounded = replace(ops, monotonic=clock.monotonic, deadline=clock.monotonic() + 2)
    if expiry == "before":
        clock.sleep(2)
    elif expiry == "open":
        def delayed_open():
            clock.sleep(2)
            return PR
        opener.expect("GET", PR_PATH, delayed_open, timeout=2)
    elif expiry == "body":
        response = addinfourl(BytesIO(json.dumps(PR).encode()), Message(), PR_PATH, 200)
        read = response.read
        def delayed_read():
            clock.sleep(2)
            return read()
        monkeypatch.setattr(response, "read", delayed_read)
        opener.expect("GET", PR_PATH, response, timeout=2)
    elif expiry == "retry":
        monkeypatch.setattr(http, "sleep", lambda _: clock.sleep(2))
        opener.expect("GET", PR_PATH, URLError("secret"), timeout=2)
    else:
        opener.expect("GET", PR_PATH, PR, timeout=2)
    if expiry == "none":
        assert land.read_pr(identity, bounded).head == HEAD
        # The same forge receives the next call's ordinary budget.
        opener.expect("GET", MAIN_PATH, MAIN)
        assert land.read_main(identity, ops) == BASE
    else:
        with pytest.raises(land.Refusal, match="shared CI deadline expired"):
            land.read_pr(identity, bounded)
        assert len(opener.calls) == (0 if expiry == "before" else 1)


@pytest.mark.scenario("gitea-landing/human-credential-source/missing-human-token-refuses")
@pytest.mark.parametrize("problem", ["missing", "empty-process", "empty-file", "bare-empty-file",
                                     "unreadable", "stat-denied", "directory", "invalid-encoding"])
def test_missing_empty_or_unreadable_human_credentials(config_path, isolated_operator, scripted_runner,
                                                      opener, monkeypatch, capsys, problem):
    path = isolated_operator / ".config/nightshift/land.env"
    path.parent.mkdir(parents=True)
    if problem == "missing":
        monkeypatch.delenv(HUMAN)
    elif problem == "empty-process":
        monkeypatch.setenv(HUMAN, "")
    elif problem in {"empty-file", "bare-empty-file"}:
        path.write_text(f"export {HUMAN}=" + ("''" if problem == "empty-file" else "") + "\n")
    elif problem == "directory":
        path.mkdir()
    elif problem == "invalid-encoding":
        path.write_bytes(b"\xff")
    else:
        path.write_text("secret")
        method = "stat" if problem == "stat-denied" else "read_text"
        original = getattr(Path, method)

        def unreadable(self, *args, **kwargs):
            if self == path:
                raise PermissionError("underlying-human-secret")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, method, unreadable)
    before = dict(os.environ)
    assert invoke(config_path, scripted_runner, opener, URL, "--checks-done") == 1
    error = capsys.readouterr().err
    assert "human credential loading" in error and HUMAN in error
    assert "secret" not in error
    assert not opener.calls and not scripted_runner.calls
    assert dict(os.environ) == before


@pytest.mark.scenario("gitea-landing/human-credential-source/landing-file-overrides-process-token")
@pytest.mark.parametrize("selected_in_file", [False, True])
def test_only_selected_human_variable_is_consulted(config_path, isolated_operator, monkeypatch, selected_in_file):
    path = isolated_operator / ".config/nightshift/land.env"
    path.parent.mkdir(parents=True)
    path.write_text(f"export {BOT}=bot-file-secret\nexport UNRELATED=unrelated-secret\n"
                    + (f"export {HUMAN}='human-file-secret'\n" if selected_in_file else ""))
    definition = config.load(config_path).forges[0]
    reads, original = [], os.environ.get
    def selected_get(key, default=None):
        reads.append(key)
        assert key == HUMAN
        return original(key, default)
    with monkeypatch.context() as scoped:
        scoped.setattr(os.environ, "get", selected_get)
        token = land.load_land_token(definition)
    assert token == ("human-file-secret" if selected_in_file else "process-human-secret")
    assert reads == ([] if selected_in_file else [HUMAN])


@pytest.mark.scenario("gitea-landing/human-credential-source/landing-file-overrides-process-token")
@pytest.mark.parametrize("method", ["PATCH", "POST"])
def test_human_transport_serializes_writes_without_export(config_path, isolated_operator, scripted_runner,
                                                         opener, method):
    path = isolated_operator / ".config/nightshift/land.env"
    path.parent.mkdir(parents=True)
    path.write_text(f"export {HUMAN}='human-file-secret'\n")
    identity, ops = resolved(config_path, scripted_runner, opener)
    before = dict(os.environ)
    body = {"body": "reviewed"}
    opener.expect(method, PR_PATH, {}, body=body)
    # The credential/transport boundary is shared by the later evidence and
    # merge tasks; those workflow operations still refuse in this task.
    ops.forge._transport(ops).request(method, f"/api/v1/repos/{identity.repo}/pulls/78", json_body=body)
    assert opener.calls[0][0].get_header("Authorization") == "token human-file-secret"
    assert dict(os.environ) == before and not scripted_runner.calls
