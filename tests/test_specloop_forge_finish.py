"""Gitea finish uses real archive commits/pushes and an offline HTTP opener."""

from contextlib import contextmanager

import pytest

from fake_gitea import FakeGitea, Reply
from nightshift.cli import main
from nightshift.forge import http
from specloop import cli, seal
from specloop.state import Run
from test_finish_expect_head import service
from test_specloop_act import act, admitted
from test_specloop_command_json import BRANCH, CHANGE, TASKS, git, template
from test_specloop_forge_selection import configured

REQUEST = cli.GiteaForge._request
PATH = "/git/api/v1/repos/example/repo/pulls/42"


@pytest.fixture
def finish_service(service, configured, monkeypatch):
    s = service
    s.run.manifest_path.write_text(s.run.manifest_path.read_text().replace(
        '"example/repo"', '"forge.example/example/repo"'))
    s.url = "https://forge.example:8443/git/example/repo/pulls/42"
    origin = "https://forge.example:8443/git/example/repo.git"
    git(s.source, "remote", "set-url", "origin", origin)
    s.run.state.pr = s.url
    s.run.save()
    s.fake = FakeGitea("https://forge.example:8443/git")
    monkeypatch.setattr(cli.GiteaForge, "_request", REQUEST)
    monkeypatch.setattr(http, "build_opener", lambda: s.fake)
    monkeypatch.setattr(http, "sleep", lambda _: None)
    real_push = cli._push

    def push(repo, url, ref, **kwargs):
        assert url == origin
        real_push(repo, str(s.remote), ref, **kwargs)

    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(cli, "_gh", lambda *a: pytest.fail("Gitea finish called gh"))
    return s


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/prefix-removal-is-verified")
@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/matching-and-omitted-head-retain-finish-gates")
@pytest.mark.parametrize("mode", ["matching", "omitted", "act"])
def test_finish_on_gitea_drops_wip_prefix(finish_service, capfd, mode):
    s = finish_service
    head = s.local_head()

    def read(call):
        s.run.reload()
        assert s.run.state.pause.reason == "human_checks"
        assert s.archives == [CHANGE] and s.pushes == [s.local_head()]
        assert s.local_head() == s.remote_head() != head
        return Reply({"title": "Draft: Keep Draft: inside" if len(s.fake.calls) == 1
                      else "Keep Draft: inside"})

    s.fake.route("GET", PATH, read, read)
    s.fake.route("PATCH", PATH, Reply({"title": "ignored response title"}))
    if mode == "act":
        act(capfd, s.run, "finish")
    else:
        assert main(["finish", CHANGE] + (["--expect-head", head.upper()] if mode == "matching" else [])) == 0
    s.run.reload()
    assert s.run.state.status == "done" and s.run.state.pause is None
    assert [(c.method, c.path, c.body) for c in s.fake.calls] == [
        ("GET", PATH, None), ("PATCH", PATH, {"title": "Keep Draft: inside"}), ("GET", PATH, None)]


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/prefix-removal-is-verified")
@pytest.mark.parametrize("title,intended", [
    ("[WIP.*]  Résumé [WIP.*]  ", " Résumé [WIP.*]  "),
    ("[wip.*] Ready", "[wip.*] Ready"), ("Ready [WIP.*] title", "Ready [WIP.*] title"),
    ("[WIP.*] ", None), ("[WIP.*] [WIP.*] title", None), ("", None), (None, None),
])
def test_finish_custom_prefix_is_literal(finish_service, configured, capfd, title, intended):
    s = finish_service
    path, _ = configured
    path.write_text(path.read_text().replace('wip_prefix = "Draft: "', 'wip_prefix = "[WIP.*] "'))
    s.fake.route("GET", PATH, Reply({"title": title}), Reply({"title": intended}))
    s.fake.route("PATCH", PATH, Reply({}))
    act(capfd, s.run, "finish", code=int(intended is None))
    assert s.run.state.status == ("paused" if intended is None else "done")
    patched = intended is not None and intended != title
    assert [c.method for c in s.fake.calls] == (["GET", "PATCH", "GET"] if patched else ["GET"])
    if patched:
        assert s.fake.calls[1].body == {"title": intended}


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/retry-observes-current-title")
@pytest.mark.parametrize("failure", ["lost", "lost_ignored", "ignored", "mismatch", "verify", "malformed"])
def test_finish_patch_readback_and_retry(finish_service, capfd, failure):
    s = finish_service
    before = (s.run.dir / "state.json").read_bytes()
    verified = {"ignored": "Draft: Title", "lost_ignored": "Draft: Title", "mismatch": "Other title"}.get(failure, "Title")
    verification = Reply({"title": verified})
    if failure == "verify":
        verification = Reply(status=403)
    elif failure == "malformed":
        verification = Reply([])
    s.fake.route("GET", PATH, Reply({"title": "Draft: Title"}), verification)
    s.fake.route("PATCH", PATH, OSError("response lost") if failure.startswith("lost") else Reply({}))
    act(capfd, s.run, "finish", code=int(failure != "lost"))
    assert [c.method for c in s.fake.calls] == ["GET", "PATCH", "GET"]
    assert s.fake.calls[1].body == {"title": "Title"}
    archived = s.local_head()
    assert archived == s.remote_head() and s.archives == [CHANGE]
    if failure != "lost":
        assert (s.run.dir / "state.json").read_bytes() == before
        assert s.run.state.pause.reason == "human_checks"
        # A fresh read, including a human's intervening edit, establishes readiness.
        s.fake.route("GET", PATH, Reply({"title": "Human's current title"}))
        act(capfd, s.run, "finish")
        assert [c.method for c in s.fake.calls] == ["GET", "PATCH", "GET", "GET"]
        assert s.archives == [CHANGE] and s.pushes == [archived, archived]
        assert s.local_head() == s.remote_head() == archived
    assert s.run.state.status == "done" and s.run.state.pause is None


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/existing-finish-gates-remain")
@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/matching-and-omitted-head-retain-finish-gates")
@pytest.mark.parametrize("gate", ["unfinished", "origin", "foreign_pr", "archive", "push"])
def test_gitea_finish_gates_and_partial_archive_retry(finish_service, monkeypatch, capfd, gate):
    s = finish_service
    if gate == "unfinished":
        head = seal.commit_files(s.bare, s.local_head(), {f"openspec/changes/{CHANGE}/tasks.md": TASKS}, "unfinished")
        git(s.bare, "update-ref", f"refs/heads/{BRANCH}", head)
    elif gate == "origin":
        git(s.source, "remote", "set-url", "origin", "http://forge.example:8443/git/example/repo.git")
    elif gate == "foreign_pr":
        s.run.state.pr = s.url.replace("example/repo", "other/repo")
        s.run.save()
    elif gate == "archive":
        def fail(*args, **kwargs):
            raise RuntimeError("archive failed")
        monkeypatch.setattr(cli.openspec, "archive", fail)
    else:
        s.failure = "push"
    before = (s.run.dir / "state.json").read_bytes()
    head = s.local_head()
    if gate == "unfinished":
        assert main(["finish", CHANGE, "--expect-head", head]) == 1
    else:
        with pytest.raises((ValueError, RuntimeError)):
            main(["finish", CHANGE, "--expect-head", head])
    assert (s.run.dir / "state.json").read_bytes() == before
    assert s.fake.calls == []
    if gate == "push":
        archived = s.local_head()
        assert archived != head and s.remote_head() == head
        s.failure = None
        s.fake.route("GET", PATH, Reply({"title": "Already ready"}))
        act(capfd, s.run, "finish")
        assert s.run.state.status == "done" and s.archives == [CHANGE]
        assert s.pushes == [archived, archived] and s.remote_head() == archived
        assert [c.method for c in s.fake.calls] == ["GET"]
    else:
        assert s.local_head() == head and s.archives == s.pushes == []


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/existing-finish-gates-remain")
@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/expected-head-checked-under-lock")
def test_gitea_revision_wins_finish_lock(finish_service, monkeypatch, capfd):
    s = finish_service
    expected, original_lock = s.local_head(), Run.lock
    revised_state = None

    @contextmanager
    def interleaved_lock(run):
        nonlocal revised_state
        s.republish()
        revised_state = (run.dir / "state.json").read_bytes()
        with original_lock(run) as mine:
            yield mine

    monkeypatch.setattr(Run, "lock", interleaved_lock)
    assert main(["finish", CHANGE, "--expect-head", expected]) == 1
    reason = capfd.readouterr().err
    assert expected in reason and s.local_head() in reason
    assert s.local_head() == s.remote_head() != expected
    assert (s.run.dir / "state.json").read_bytes() == revised_state
    assert s.archives == s.pushes == s.fake.calls == []


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/existing-finish-gates-remain")
@pytest.mark.parametrize("repository", ["example/repo", "github.com/example/repo"])
def test_github_finish_ready_readback_argv(service, monkeypatch, repository):
    s, calls = service, []
    s.run.manifest_path.write_text(s.run.manifest_path.read_text().replace('"example/repo"', f'"{repository}"'))

    def gh(*args):
        calls.append(args)
        if args[:2] == ("pr", "ready"):
            raise RuntimeError("response lost")
        return "false"

    monkeypatch.setattr(cli, "_gh", gh)
    assert main(["finish", CHANGE]) == 0
    s.run.reload()
    assert s.run.state.status == "done" and s.local_head() == s.remote_head()
    assert calls == [("pr", "ready", s.url, "-R", repository),
                     ("pr", "view", s.url, "-R", repository, "--json", "isDraft", "--jq", ".isDraft")]
