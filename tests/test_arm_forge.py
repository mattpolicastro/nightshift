"""Arming selection and issue facts, using synthetic credentials and no sockets."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from nightshift import arm, config
from fake_gitea import FakeGitea, Reply


@pytest.fixture
def configured(tmp_path, monkeypatch):
    root = Path.home() / "worktree"
    root.mkdir()
    path, env = Path.home() / "arming.toml", tmp_path / "env"
    path.write_text('''[[forges]]
name = "internal"
kind = "gitea"
url = "https://forge.example/gitea"
token_env = "TEST_ARM_BOT"
land_token_env = "TEST_ARM_LAND"
bot_login = "Robot"
trusted_logins = ["Reviewer"]
wip_prefix = "Draft: "
[[repos]]
name = "Owner/Repo"
forge = "internal"
dir = ''' + json.dumps(str(root)) + "\n")
    env.write_text("export TEST_ARM_BOT='file-bot'\nexport TEST_ARM_LAND='file-land'\n")
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setenv("TEST_ARM_BOT", "process-bot")
    monkeypatch.setenv("GH_TOKEN", "ambient-gh")
    monkeypatch.setenv("TEST_ARM_LAND", "process-land")
    monkeypatch.setattr(arm.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected subprocess"))
    monkeypatch.chdir(tmp_path)
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        assert argv == ["git", "rev-parse", "--show-toplevel"]
        assert kw == dict(capture_output=True, text=True, check=True, timeout=30)
        return subprocess.CompletedProcess(argv, 0, str(root))

    return path, env, root, runner, calls


@pytest.mark.scenario("issue-arming/configured-arming-selection/configured-gitea-checkout")
@pytest.mark.scenario("issue-arming/configured-arming-selection/bot-credential-precedence")
@pytest.mark.parametrize("style", ["absolute", "relative", "tilde", "default", "canonical"])
@pytest.mark.parametrize("file_token", [True, False])
def test_configured_selection(configured, monkeypatch, style, file_token):
    path, env, root, runner, calls = configured
    selected = str(path)
    directory = str(root)
    if style == "relative":
        selected, directory = os.path.relpath(path), os.path.relpath(root)
    elif style == "tilde":
        selected, directory = "~/arming.toml", "~/worktree"
    elif style == "default":
        root.rename(root.with_name("Repo"))
        root = root.with_name("Repo")
        monkeypatch.setattr(config, "DEFAULT_CLONE_ROOT", root.parent)
        directory = ""
        runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, str(root))
    elif style == "canonical":
        alias = root.with_name("alias")
        alias.symlink_to(root, target_is_directory=True)
        directory = str(alias)
    path.write_text(path.read_text().replace(json.dumps(str(configured[2])), json.dumps(directory)))
    if not file_token:
        env.unlink()
    before = dict(os.environ)
    selected_root, repo, facet = arm._select_forge(selected, runner=runner)
    assert (selected_root, repo) == (root, "Owner/Repo")
    forge = facet.forge
    assert forge._transport._base == "https://forge.example/gitea"
    assert forge._transport._token == ("file-bot" if file_token else "process-bot")
    assert (forge._bot_login, forge._trusted_logins, forge._wip_prefix) == (
        "robot", {"reviewer"}, "Draft: ")
    assert dict(os.environ) == before
    assert all(call[0] == "git" for call in calls)
    server = FakeGitea()
    server.repository(repo, labels=[], issues=[dict(number=1, body=None, state="open", labels=[])])
    forge._transport._opener = server
    assert arm.read_issue(repo, 1, forge=facet) == arm.Issue(1, "", "OPEN", None, frozenset())
    assert [call.path for call in server.calls] == ["/gitea/api/v1/repos/Owner/Repo/issues/1"]
    assert len(server.calls) == 1
    assert facet.armed_issues(repo, arm.IssueCache(forge=facet))[1]  # Missing intake label.
    assert facet.open_pr_context(repo) == arm.ConcurrentContext([], [])
    reads = len(server.calls)
    for operation, args in ((facet.add_intake_label, (repo, 1)),
                            (facet.confirm_intake_label, (repo, 1))):
        with pytest.raises(NotImplementedError):
            operation(*args)
    assert len(server.calls) == reads


@pytest.mark.scenario("issue-arming/configured-arming-selection/explicit-selection-refuses-safely")
@pytest.mark.scenario("issue-arming/configured-arming-selection/bot-credential-precedence")
@pytest.mark.parametrize("failure", ["missing", "invalid", "unreadable", "unmatched", "ambiguous",
                                     "credentials", "credential-stat", "empty", "no_token", "malformed"])
def test_selection_refuses_before_api(configured, monkeypatch, failure):
    path, env, root, runner, calls = configured
    monkeypatch.setattr(arm.GiteaForge, "_request", lambda *a, **kw: pytest.fail("API access"))
    if failure == "missing":
        path.unlink()
    elif failure == "invalid":
        path.write_text("SECRET invalid TOML")
    elif failure in ("unreadable", "credentials"):
        target = path if failure == "unreadable" else env
        target.unlink()
        target.mkdir()
    elif failure == "unmatched":
        path.write_text(path.read_text().replace(str(root), str(root / "other")))
    elif failure == "ambiguous":
        path.write_text(path.read_text() + '\n[[repos]]\nname="Other/Repo"\ndir=' + json.dumps(str(root)))
    elif failure == "malformed":
        path.write_text('repos = ["SECRET"]')
    elif failure == "credential-stat":
        original = Path.stat

        def stat(self, *args, **kwargs):
            if self == env:
                raise PermissionError("SECRET")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", stat)
    else:
        env.write_text("export TEST_ARM_BOT=''\n" if failure == "empty" else "")
        if failure == "no_token":
            monkeypatch.delenv("TEST_ARM_BOT")
    with pytest.raises(arm.IssueLookupError) as error:
        arm._select_forge(path, runner=runner)
    assert str(error.value) == "Could not select the configured arming repository and bot credentials."
    assert len(calls) == 1


@pytest.mark.scenario("issue-arming/configured-arming-selection/explicit-selection-refuses-safely")
@pytest.mark.parametrize("matches", [True, False])
def test_named_github_identity(configured, monkeypatch, matches):
    path, _, root, git, calls = configured
    path.write_text('[[forges]]\nname="github"\nkind="github"\n[[repos]]\n'
                    'name="Owner/Repo"\nforge="github"\ndir=' + json.dumps(str(root)))
    monkeypatch.setattr(config, "parse_env_file", lambda: pytest.fail("credential read"))

    def runner(argv, **kw):
        if argv[0] == "git":
            return git(argv, **kw)
        calls.append(argv)
        assert argv == ["gh", "repo", "view", "--json", "nameWithOwner,url"]
        assert kw["cwd"] == root
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "nameWithOwner": "owner/repo" if matches else "Other/Repo",
            "url": "https://github.example/Owner/Repo"}))

    if matches:
        assert arm._select_forge(path, runner=runner)[1] == "github.example/owner/repo"
    else:
        with pytest.raises(arm.IssueLookupError):
            arm._select_forge(path, runner=runner)
    assert len(calls) == 2


@pytest.mark.scenario("issue-arming/configured-arming-selection/explicit-selection-refuses-safely")
@pytest.mark.parametrize("args", [["--config", "SECRET", "--force", "1"], ["--force", "0"]])
def test_cli_cannot_select_partial_gitea_backend(monkeypatch, args):
    monkeypatch.setattr(arm.subprocess, "run", lambda *a, **kw: pytest.fail("discovery or write"))
    with pytest.raises(SystemExit) as error:
        arm.main(args)
    assert error.value.code == 2


@pytest.mark.scenario("issue-arming/gitea-arming-context/issue-facts-preserve-uncertainty")
@pytest.mark.parametrize("updates,code", [({}, "incomplete-context"),
    ({"state_reason": "COMPLETED"}, None), ({"state_reason": "NOT_PLANNED"}, "dependency-completion"),
    ({"state_reason": "future"}, "incomplete-context"),
    *[(change, "failure") for change in ({"number": 2}, {"number": True}, {"pull_request": {}},
        {"state": None}, {"state": "invalid"}, {"labels": [None]}, {"labels": None},
        {"labels": [{"name": ""}]}, {"labels": [{"name": "  "}]},
        {"body": 1}, {"state_reason": 1})]])
def test_gitea_issue_facts(updates, code):
    server = FakeGitea()
    row = dict(number=1, body=None, state="closed", labels=[])
    row.update(updates)
    route = "/gitea/api/v1/repos/Owner/Repo/issues/1"
    server.route("GET", route, Reply(row), Reply(row))
    facet = arm._GiteaArmingForge(arm.GiteaForge(server.base_url, "fake-token", bot_login="Robot",
                                              trusted_logins=[], opener=server))
    for invocation in range(2):
        cache = arm.IssueCache(forge=facet)
        for _ in range(2):
            findings = arm.lint_dependencies(9, "blocked-by: #1", "Owner/Repo", cache)
            assert [f.code for f in findings] == (["incomplete-context"] if code == "failure"
                                                 else [code] if code else [])
            if code == "failure":
                with pytest.raises(arm.IssueLookupError):
                    cache.get("Owner/Repo", 1)
            else:
                issue = cache.get("Owner/Repo", 1)
                reason = updates.get("state_reason")
                assert issue.body == ""
                assert issue.state_reason == (reason.upper() if reason is not None else None)
        assert len(server.calls) == invocation + 1
    assert all(call.path == route and call.method == "GET" for call in server.calls)


@pytest.mark.scenario("issue-arming/gitea-arming-context/issue-facts-preserve-uncertainty")
@pytest.mark.parametrize("reply", [Reply(None), Reply([]), Reply(raw=b"SECRET invalid JSON"),
                                  Reply({"message": "SECRET"}, status=403)])
def test_failed_gitea_reads_are_sanitized_and_cached(reply):
    server = FakeGitea()
    server.route("GET", "/gitea/api/v1/repos/Owner/Repo/issues/1", reply, reply)
    facet = arm._GiteaArmingForge(arm.GiteaForge(server.base_url, "fake-token", bot_login="Robot",
                                              trusted_logins=[], opener=server))
    for invocation in range(2):
        cache = arm.IssueCache(forge=facet)
        for _ in range(2):
            with pytest.raises(arm.IssueLookupError, match="Issue #1 lookup failed"):
                cache.get("Owner/Repo", 1)
        assert len(server.calls) == invocation + 1


@pytest.mark.scenario("issue-arming/github-arming-compatibility/runners-and-snapshots-remain-local")
@pytest.mark.parametrize("explicit", [True, False])
def test_arm_github_argv_unchanged(monkeypatch, explicit):
    calls = []
    repo = "github.example/Owner/Repo"

    def runner(argv, **kw):
        calls.append(argv)
        assert kw == dict(capture_output=True, text=True, check=True, timeout=30)
        if argv[-1] == "labels":
            data = {"labels": [{"name": "spec:draft"}]}
        elif argv[:3] == ["gh", "issue", "view"]:
            data = dict(number=1, body="", state="OPEN", stateReason=None, labels=[])
        else:
            raise subprocess.CalledProcessError(1, argv, stderr="SECRET")
        return subprocess.CompletedProcess(argv, 0, json.dumps(data))

    monkeypatch.setattr(arm.subprocess, "run", (lambda *a, **kw: pytest.fail("ambient runner"))
                        if explicit else runner)
    for _ in range(2):
        cache = arm.IssueCache(**({"runner": runner} if explicit else {}))
        assert cache.get(repo, 1) is cache.get(repo, 1)
        assert cache.reaches(repo, 1, 2) == (False, ())
        assert cache.armed(repo) is cache.armed(repo) and cache.armed(repo)[1]
        assert cache.open_prs(repo) is cache.open_prs(repo) and cache.open_prs(repo).notes
        assert arm.confirm_intake_label(repo, 1, forge=cache.forge)
        assert not cache.get(repo, 1).labels
    assert calls[:len(calls) // 2] == calls[len(calls) // 2:]
    assert calls[0] == ["gh", "issue", "view", "1", "--repo", repo,
                        "--json", "number,body,state,stateReason,labels"]
    assert calls[1] == ["gh", "api", "--hostname", "github.example",
                        "repos/Owner/Repo/issues?state=open&labels=spec%3Adraft&per_page=100&page=1"]
    assert calls[-1] == ["gh", "issue", "view", "1", "--repo", repo, "--json", "labels"]


@pytest.mark.scenario("issue-arming/github-arming-compatibility/runners-and-snapshots-remain-local")
@pytest.mark.parametrize("injected", [False, True])
def test_cache_runner_identity_and_reassignment(monkeypatch, injected):
    from unittest.mock import Mock
    initial = Mock(return_value=subprocess.CompletedProcess([], 0, "[]"))
    replacement = Mock(return_value=subprocess.CompletedProcess([], 0, "[]"))
    helper = Mock(return_value=({}, []))
    monkeypatch.setattr(arm.critic, "_github_open_pr_files", helper)
    monkeypatch.setattr(arm.subprocess, "run", initial)
    facet = arm._GitHubArmingForge(initial) if injected else None
    cache = arm.IssueCache(forge=facet)
    cache.open_prs("Owner/First")
    helper.assert_called_with("Owner/First", "", runner=initial)
    cache.runner = replacement
    cache.open_prs("Owner/Second")
    helper.assert_called_with("Owner/Second", "", runner=initial if injected else replacement)
    assert cache.runner is replacement
    assert initial.call_count == (2 if injected else 1)
    assert replacement.call_count == (0 if injected else 1)
    # A subsequent invocation binds the current subprocess runner independently.
    monkeypatch.setattr(arm.subprocess, "run", replacement)
    arm.IssueCache().open_prs("Owner/First")
    helper.assert_called_with("Owner/First", "", runner=replacement)


@pytest.mark.scenario("issue-arming/github-arming-compatibility/runners-and-snapshots-remain-local")
@pytest.mark.parametrize("failed", [False, True])
def test_first_read_wins_even_after_listing(failed):
    row = dict(number=1, body="first", state="OPEN", labels=[])

    def read(argv, **kw):
        if failed:
            raise OSError("SECRET")
        return subprocess.CompletedProcess(argv, 0, json.dumps(row))

    cache = arm.IssueCache(runner=read)
    if failed:
        with pytest.raises(arm.IssueLookupError):
            cache.get("Owner/Repo", 1)
    else:
        first = cache.get("Owner/Repo", 1)
    row.update(body="later", labels=[{"name": "spec:draft"}])
    cache.runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps([row]))
    issues, notes = cache.armed("Owner/Repo")
    assert (issues, bool(notes)) == (([], True) if failed else ([first], False))
    assert cache.armed("Owner/Repo") == (issues, notes)


@pytest.mark.scenario("issue-arming/github-arming-compatibility/runners-and-snapshots-remain-local")
def test_injected_forge_has_no_backend_construction(monkeypatch):
    from unittest.mock import Mock
    facet = Mock(spec=arm.ArmingForge)
    facet.read_issue.side_effect = arm.IssueLookupError("unavailable")
    for name in ("_GitHubArmingForge", "GiteaForge"):
        monkeypatch.setattr(arm, name, lambda *a, **kw: pytest.fail("unrelated backend"))
    for _ in range(2):
        cache = arm.IssueCache(forge=facet)
        for _ in range(2):
            with pytest.raises(arm.IssueLookupError):
                cache.get("Owner/Repo", 1)
            assert cache.reaches("Owner/Repo", 1, 9) == (False, ((1, "unavailable"),))
        cache.armed("Owner/Repo")
        cache.armed("Owner/Repo")
        cache.open_prs("Owner/Repo")
        cache.open_prs("Owner/Repo")
    assert facet.read_issue.call_count == facet.armed_issues.call_count == facet.open_pr_context.call_count == 2
    with pytest.raises(arm.IssueLookupError):
        arm.read_issue("Owner/Repo", 1, forge=facet)
    assert arm.read_pr_context("Owner/Repo", forge=facet) is facet.open_pr_context.return_value
    assert arm.confirm_intake_label("Owner/Repo", 1, forge=facet) is facet.confirm_intake_label.return_value


@pytest.mark.scenario("issue-arming/issue-body-lint/missing-sections-accumulate")
@pytest.mark.scenario("issue-arming/issue-body-lint/named-tests-are-required")
@pytest.mark.scenario("issue-arming/issue-body-lint/discouraged-phrases-have-replacements")
@pytest.mark.scenario("issue-arming/issue-body-lint/human-checks-stay-outside-done")
@pytest.mark.parametrize("done", ["uv run pytest", "", '"unrelated sentence"',
                                 "`test_named`", '"test_named"', 'test "descriptive title"'])
def test_lint_remains_independent_of_forge(done):
    body = (f"**Definition of done:** {done} manually by hand visually\n"
            "**What CI cannot prove:** manually by hand visually `test_outside`\n"
            "No existing TESTS are changed; Don't edit existing tests\n"
            "```\n**Scope:** example\n**What to do:** example\n```\n")
    findings = arm.lint_body(1, body)
    missing = [f for f in findings if f.code == "missing-section"]
    assert len(missing) == 2
    assert any("Scope" in f.message for f in missing)
    assert any("What to do" in f.message for f in missing)
    assert len([f for f in findings if f.code == "named-test"]) == (done in (
        "uv run pytest", "", '"unrelated sentence"'))
    assert [f.line for f in findings if f.code == "manual-check"] == [1, 1, 1]
    assert all("What CI cannot prove" in f.suggestion for f in findings if f.code == "manual-check")
    discouraged = [f for f in findings if f.code == "discouraged-phrase"]
    assert [f.line for f in discouraged] == [3, 3]
    assert all(f.suggestion == "existing assertions are preserved" for f in discouraged)
    assert not any(f.code == "discouraged-phrase" for f in arm.lint_body(1, "existing assertions are preserved"))
