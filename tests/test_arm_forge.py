"""Arming selection and issue facts, using synthetic credentials and no sockets."""

import json
import os
from pathlib import Path
import subprocess
from urllib.error import URLError

import pytest

from nightshift import arm, config
from fake_gitea import FakeGitea, Reply
from test_arm import issue_body


@pytest.fixture
def arming_io(configured, monkeypatch, issue_body):
    path, _, root, runner, _ = configured
    fake = FakeGitea()
    draft, bug = {"id": 19, "name": "spec:draft"}, {"id": 2, "name": "bug"}
    rows = [dict(id=n, number=n, body=issue_body().replace("nightshift/arm.py", f"docs/{n}.md"),
                 state="open", labels=[bug]) for n in (76, 77)]
    fake.repository("Owner/Repo", labels=[bug, draft], issues=rows)
    constructor = arm.GiteaForge
    monkeypatch.setattr(arm, "GiteaForge", lambda *a, **kw: constructor(*a, **kw, opener=fake))
    monkeypatch.setattr(arm.subprocess, "run", runner)
    before = list(root.rglob("*"))
    yield fake, rows, ["--config", str(path)], draft
    assert list(root.rglob("*")) == before
    assert all(c.timeout == 30 for c in fake.calls)
    assert all(r.closed for r in fake.responses)


@pytest.mark.scenario("issue-arming/gitea-arming-context/issue-facts-preserve-uncertainty")
def test_arm_gitea_reads_issue_with_null_pull_request(arming_io, capsys):
    fake, rows, args, _ = arming_io
    assert rows[0]["pull_request"] is None
    assert arm.main([*args, "--check", "76"]) == 0
    output = capsys.readouterr().out
    assert "lookup failed" not in output
    assert "#76: " in output
    assert all(call.method == "GET" for call in fake.calls)
    assert rows[0]["labels"] == [{"id": 2, "name": "bug"}]


@pytest.mark.scenario("issue-arming/gitea-arming-context/issue-facts-preserve-uncertainty")
def test_arm_gitea_rejects_pr_backed_issue():
    fake = FakeGitea()
    fake.repository("Owner/Repo", labels=[], pulls=[
        dict(id=1, number=1, body="", state="open", labels=[])])
    facet = arm._GiteaArmingForge(arm.GiteaForge(
        fake.base_url, "fake-token", bot_login="Robot", trusted_logins=[], opener=fake))
    with pytest.raises(arm.IssueLookupError, match="Issue #1 lookup failed"):
        facet.read_issue("Owner/Repo", 1)
    assert len(fake.calls) == 1
    assert fake.calls[0].path.endswith("/issues/1")


@pytest.mark.scenario("issue-arming/verified-gitea-arming-writes/every-write-is-read-back")
@pytest.mark.scenario("issue-arming/verified-gitea-arming-writes/silent-or-unreadable-mutation-fails")
@pytest.mark.scenario("issue-arming/verified-gitea-arming-writes/lost-response-is-reconciled-once")
@pytest.mark.parametrize("fault", ["ok", "silent", "lost", "absent", "malformed", "unavailable", "verification"])
def test_arm_gitea_label_write_read_back(arming_io, capsys, fault):
    fake, rows, args, draft = arming_io
    path = "/gitea/api/v1/repos/Owner/Repo/issues/76/labels"
    if fault != "ok":
        def post(call):
            if fault not in ("silent", "absent"):
                rows[0]["labels"].append(draft)
            if fault in ("malformed", "unavailable", "verification"):
                bad = Reply(raw=b"invalid") if fault == "malformed" else Reply(status=403)
                fake.route("GET", path, bad, *([Reply(rows[0]["labels"][:1])]
                                               if fault == "verification" else []), page=1)
            return (URLError("lost response") if fault == "lost" else
                    Reply([]) if fault in ("silent", "verification") else Reply(status=503))
        fake.route("POST", path, post)
    success = fault in ("ok", "lost", "verification")
    assert arm.main([*args, "--force", "76", "77", "76"]) == (0 if success else 1)
    output = capsys.readouterr().out
    assert ("#76: forced arming" if fault == "ok" else
            "#76: armed (label confirmed after write failure)" if success else
            "#76: unconfirmed (label write failed)") in output
    assert "#77: forced arming" in output
    writes = [c for c in fake.calls if c.method != "GET"]
    assert [(c.method, c.path, c.body) for c in writes] == [
        ("POST", path.replace("76", str(n)), {"labels": [19]}) for n in (76, 77)]
    first = fake.calls.index(writes[0])
    # Fresh label resolution and complete reads precede POST; read-back precedes target 77.
    assert [(c.method, c.path.rsplit("/", 2)[-2:], c.query.get("page"))
            for c in fake.calls[first - 5:first]] == [
        ("GET", ["Repo", "labels"], [str(n)]) for n in (1, 2, 3)] + [
        ("GET", ["76", "labels"], [str(n)]) for n in (1, 2)]
    following = [c for c in fake.calls[first + 1:] if c.path == path]
    pages = ([1, 2, 1, 2] if fault == "silent" else [1] if fault in ("malformed", "unavailable")
             else [1, 2] if fault == "absent" else [1, 1, 2, 3] if fault == "verification"
             else [1, 2, 3])
    assert [c.query["page"] for c in following] == [[str(n)] for n in pages]
    assert all(c.method == "GET" and fake.calls.index(c) < fake.calls.index(writes[1]) for c in following)
    assert all(row["labels"][0]["name"] == "bug" for row in rows)
    assert draft in rows[1]["labels"]


@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/clean-issue-is-armed")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/findings-prevent-default-arming")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/check-mode-never-labels")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/force-labels-and-reports")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/existing-labels-are-idempotent")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/target-and-label-failures-remain-failures")
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/batch-results-retain-failures")
@pytest.mark.parametrize("mode", [[], ["--check"], ["--force"], ["--check", "--force"]])
@pytest.mark.parametrize("state", ["clean", "findings", "armed", "armed-findings", "closed", "unreadable"])
def test_gitea_modes_and_batches(arming_io, capsys, mode, state):
    fake, rows, args, draft = arming_io
    if "armed" in state:
        rows[0]["labels"].append(draft)
    if "findings" in state:
        rows[0]["body"] = ""
    if state == "closed":
        rows[0]["state"] = "closed"
    if state == "unreadable":
        fake.route("GET", "/gitea/api/v1/repos/Owner/Repo/issues/76", Reply(status=403))
    check, force = "--check" in mode, "--force" in mode
    success = state not in ("closed", "unreadable") and ("findings" not in state or force and not check)
    assert arm.main([*args, *mode, "76", "77", "76"]) == (0 if success else 1)
    output = capsys.readouterr().out
    findings = [line for line in output.splitlines() if "[missing-section]" in line]
    assert len(findings) == (4 if "findings" in state else 0)
    assert findings == sorted(findings)
    assert output.count("#76: ") == output.count("#77: ") == 1
    assert output.index("#76: ") < output.index("#77: ")
    assert len([c for c in fake.calls if c.path.endswith("/issues/76")]) == 1
    writes = [c.path for c in fake.calls if c.method != "GET"]
    expected = ([] if check else ([76] if success and "armed" not in state else []) + [77])
    assert writes == [f"/gitea/api/v1/repos/Owner/Repo/issues/{n}/labels" for n in expected]
    assert (draft in rows[1]["labels"]) == (not check)


@pytest.mark.scenario("issue-arming/verified-gitea-arming-writes/lost-response-is-reconciled-once")
@pytest.mark.parametrize("fault", ["missing", "duplicate", "guard-forge", "guard-os"])
def test_label_definition_and_guard_fail_closed(arming_io, monkeypatch, fault):
    fake, _, args, draft = arming_io
    select = arm._select_forge
    error = arm.ForgeError("guard", transient=False) if fault == "guard-forge" else OSError("guard")
    cutoff = []

    def selected(path):
        root, repo, facet = select(path)
        original = facet.add_intake_label
        def add(*a):
            if fault.startswith("guard"):
                def guard():
                    cutoff.append(len(fake.calls))
                    raise error
                facet.forge._transport._guard = guard
            else:
                fake.repositories[repo]["labels"] = [] if fault == "missing" else [draft, {**draft, "id": 20}]
            return original(*a)
        facet.add_intake_label = add
        return root, repo, facet

    monkeypatch.setattr(arm, "_select_forge", selected)
    if fault.startswith("guard"):
        with pytest.raises(type(error)) as caught:
            arm.main([*args, "--force", "76"])
        assert caught.value is error and cutoff == [len(fake.calls)]
    else:
        assert arm.main([*args, "--force", "76"]) == 1
    assert all(c.method == "GET" for c in fake.calls)


@pytest.mark.scenario("issue-arming/github-arming-compatibility/github-transcripts-stay-identical")
@pytest.mark.parametrize("host", ["github.com", "github.example"])
@pytest.mark.parametrize("mode", ["success", "check", "force", "armed", "error-confirmed", "error-absent"])
def test_arm_github_argv_unchanged(configured, monkeypatch, issue_body, host, mode):
    path, _, root, _, _ = configured
    path.write_text('[[forges]]\nname="github"\nkind="github"\n[[repos]]\n'
                    'name="Owner/Repo"\nforge="github"\ndir=' + json.dumps(str(root)))
    monkeypatch.setattr(config, "parse_env_file", lambda: pytest.fail("credential read"))
    monkeypatch.setattr(arm.time, "monotonic", lambda: 0)
    repo = "Owner/Repo" if host == "github.com" else f"{host}/Owner/Repo"
    host_args = [] if host == "github.com" else ["--hostname", host]
    issue = dict(number=76, body="" if mode == "force" else issue_body(), state="OPEN",
                 stateReason=None, labels=[{"name": "spec:draft"}] if mode == "armed" else [])
    pr = dict(number=901, html_url=f"https://{host}/Owner/Repo/pull/901",
              head={"ref": "ordinary", "repo": {"full_name": "Owner/Repo"}})
    transcript = [
        (["git", "rev-parse", "--show-toplevel"], str(root)),
        (["gh", "repo", "view", "--json", "nameWithOwner,url"],
         json.dumps(dict(nameWithOwner="Owner/Repo", url=f"https://{host}/Owner/Repo"))),
        (["gh", "issue", "view", "76", "--repo", repo, "--json", "number,body,state,stateReason,labels"], json.dumps(issue)),
        (["gh", "api", "--hostname", host, "repos/Owner/Repo/issues?state=open&labels=spec%3Adraft&per_page=100&page=1"], "[]"),
        (["gh", "api", "repos/Owner/Repo/pulls?state=open&per_page=100&page=1", *host_args, "--method", "GET"], json.dumps([pr])),
        (["gh", "api", "repos/Owner/Repo/pulls/901/files?per_page=100&page=1", *host_args, "--method", "GET"], '[{"filename":"docs/other.md"}]'),
        (["gh", "api", "--hostname", host, "repos/Owner/Repo/pulls?state=open&per_page=100&page=1"], json.dumps([pr])),
    ]
    if mode not in ("check", "armed"):
        transcript.append((["gh", "issue", "edit", "76", "--repo", repo, "--add-label", "spec:draft"], ""))
    if mode.startswith("error"):
        transcript.append((["gh", "issue", "view", "76", "--repo", repo, "--json", "labels"],
                           json.dumps({"labels": [{"name": "spec:draft"}] if mode == "error-confirmed" else []})))
    for named in (False, True):
        calls = []
        def runner(argv, **kw):
            expected, output = transcript[len(calls)]
            calls.append((argv, kw))
            assert argv == expected
            assert kw == dict(capture_output=True, text=True, check=True, timeout=30,
                              **({"cwd": root} if argv[1:3] == ["repo", "view"] else {}))
            if mode.startswith("error") and argv[1:3] == ["issue", "edit"]:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0, output)
        with monkeypatch.context() as patch:
            patch.setattr(arm.subprocess, "run", runner)
            if not named:
                patch.setattr(config, "load", lambda *a: pytest.fail("legacy config read"))
            args = ["--config", str(path)] if named else []
            assert arm.main([*args, *([f"--{mode}"] if mode in ("check", "force") else []), "76"]) == (1 if mode == "error-absent" else 0)
        assert len(calls) == len(transcript)


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
    with pytest.raises(arm._LabelWriteError):  # Never create the missing repository label.
        facet.add_intake_label(repo, 1)
    assert not facet.confirm_intake_label(repo, 1)
    assert len(server.calls) == reads + 2
    assert all(call.method == "GET" for call in server.calls)


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
    monkeypatch.setattr(arm.subprocess, "run", runner)
    assert arm.main(["--config", str(path), "--force", "76"]) == 1
    assert len(calls) == 2


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
@pytest.mark.scenario("issue-arming/arming-modes-and-outcomes/invalid-arguments-fail-before-reads")
@pytest.mark.parametrize("args", [[], ["--config", "SECRET", "--force", "0"], ["--force", "0"], ["bad"]])
def test_invalid_cli_cannot_dispatch(monkeypatch, args):
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
def test_arm_github_snapshots_unchanged(monkeypatch, explicit):
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
