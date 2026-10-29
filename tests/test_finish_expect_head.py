"""Exercise the locked finish boundary with real isolated Git repositories."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict
from io import StringIO
import json
import subprocess
import sys

import pytest

from nightshift import factory_land, land
from nightshift.cli import main
from specloop import cli, seal
from specloop.state import Run
from test_specloop_act import admitted
from test_specloop_command_json import BRANCH, CHANGE, TASKS, git, template


class LandingService:
    """Fake GitHub transport; status, finish, archive commit and pushes are real."""

    def __init__(self, run, source, remote, config, pause_file):
        self.run, self.source, self.remote = run, source, remote
        self.bare = run.dir / "repo.git"
        self.config, self.pause_file = config, pause_file
        self.url = "https://github.com/example/repo/pull/42"
        self.calls, self.archives, self.pushes, self.ready = [], [], [], []
        self.comments, self.pending = [], []
        self.body = "- [x] Inspect behavior\n"
        self.draft, self.merged = True, False
        self.failure = None
        self.before_finish = lambda: None
        self.post_mode = "ack"
        self.fail_after_post = False
        self.after_post = False
        self.comment_reads = 0

    def local_head(self):
        return git(self.bare, "rev-parse", BRANCH)

    def remote_head(self, _=None):
        return git(self.remote, "rev-parse", BRANCH)

    def status(self, _=None):
        out = StringIO()
        with redirect_stdout(out):
            assert main(["status", CHANGE, "--json"]) == 0
        return json.loads(out.getvalue())

    def offer(self):
        return factory_land.offer_from_status(self.status(), CHANGE)

    def archive(self, work, change, all_tasks_done):
        assert all_tasks_done
        self.archives.append(change)
        directory = work / "openspec" / "changes" / change
        target = directory.parent / "archive" / f"2026-10-20-{change}"
        target.parent.mkdir()
        directory.rename(target)

    def gh(self, *args):
        if args[:2] == ("pr", "view"):
            return "true" if self.draft else "false"
        assert args == ("pr", "ready", self.url, "-R", "example/repo")
        self.ready.append(self.local_head())
        if self.failure == "ready":
            raise RuntimeError("ready unavailable")
        self.draft = False
        return ""

    def republish(self):
        revised = seal.commit_files(self.bare, self.local_head(), {"revision.txt": "review again"},
                                    "revision won the handoff")
        git(self.bare, "update-ref", f"refs/heads/{BRANCH}", revised)
        git(self.bare, "push", "-q", str(self.remote), f"{BRANCH}:{BRANCH}")
        self.run.reload()
        self.run.pause("human_checks", detail="Republished")
        return revised

    def __call__(self, argv, **options):
        self.calls.append((list(argv), dict(options)))
        value = {}
        if argv[:4] == [sys.executable, "-m", "nightshift.cli", "status"]:
            if self.after_post and self.fail_after_post:
                return subprocess.CompletedProcess(argv, 1, "", "late status read failed")
            value = self.status()
        elif argv[:4] == [sys.executable, "-m", "nightshift.cli", "finish"]:
            assert options["timeout"] == 300
            self.before_finish()
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = main(argv[3:])
                except (RuntimeError, OSError) as exc:
                    code = 1
                    print(exc, file=err)
            return subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())
        elif argv[:3] == ["gh", "pr", "view"]:
            value = {"number": 42, "url": self.url, "state": "MERGED" if self.merged else "OPEN",
                     "isDraft": self.draft, "headRefOid": self.remote_head(),
                     "baseRefName": "main", "body": self.body}
        elif argv[:3] == ["gh", "pr", "checks"]:
            value = [{"name": "tests", "bucket": "pass", "state": "SUCCESS", "link": ""}]
        elif argv[:3] == ["gh", "pr", "merge"]:
            assert argv[-2:] == ["--match-head-commit", self.remote_head()]
            self.merged = True
        elif argv == ["gh", "api", "user"]:
            value = {"login": "operator"}
        elif argv[:3] == ["gh", "api", "--method"]:
            method, endpoint, body = argv[3], argv[4], argv[6].removeprefix("body=")
            assert argv[5] == "-f"
            if endpoint.endswith("pulls/42"):
                assert method == "PATCH"
                self.body = body
            elif method == "POST":
                assert endpoint == "repos/example/repo/issues/42/comments"
                self.after_post = True
                value = {"id": len(self.comments) + len(self.pending) + 1, "body": body,
                         "author_association": "MEMBER"}
                if self.post_mode == "unsent":
                    raise OSError("failed before sending POST")
                if self.post_mode in ("delayed", "lost"):
                    (self.pending if self.post_mode == "delayed" else self.comments).append(value)
                    raise subprocess.TimeoutExpired(argv, 30)
                self.comments.append(value)
            else:
                assert method == "PATCH"
                comment_id = int(endpoint.rsplit("/", 1)[1])
                comment = next(c for c in self.comments if c["id"] == comment_id)
                comment["body"] = body
        elif argv[:2] == ["gh", "api"]:
            endpoint = argv[2]
            if endpoint.endswith("/comments"):
                assert argv[-2:] == ["--paginate", "--slurp"]
                self.comment_reads += 1
                # Put keyed comments on a later page, also during reconciliation.
                value = [[], list(self.comments)]
            elif endpoint.endswith("/git/ref/heads/main"):
                value = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": "b" * 40}}
            elif "/compare/" in endpoint:
                value = {"status": "ahead", "merge_base_commit": {"sha": "b" * 40}}
            else:
                pytest.fail(f"unexpected read: {argv}")
        else:
            pytest.fail(f"unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    def land(self, *, factory=False, offer=None):
        if factory:
            return factory_land.invoke_confirmed(
                offer or self.offer(), runner=self, read_status=self.status,
                remote_head=self.remote_head, pause_file=self.pause_file, config_path=self.config)
        return land.main([self.url, "--checks-done", "--config", str(self.config)], runner=self)

    def commands(self, name):
        return [argv for argv, _ in self.calls if name in argv]


@pytest.fixture
def service(admitted, tmp_path, monkeypatch):
    run, source = admitted
    bare, remote = run.dir / "repo.git", tmp_path / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", str(remote))
    head = seal.commit_files(bare, git(bare, "rev-parse", BRANCH), {
        f"openspec/changes/{CHANGE}/tasks.md": TASKS.replace("[ ]", "[x]"),
    }, "complete task")
    git(bare, "update-ref", f"refs/heads/{BRANCH}", head)
    git(bare, "push", "-q", str(remote), f"{BRANCH}:{BRANCH}")
    config = tmp_path / "land.toml"
    config.write_text('[[repos]]\nname = "example/repo"\nverify = "true"\n')
    fake = LandingService(run, source, remote, config, tmp_path / "pause")
    run.state.pr = fake.url
    run.pause("human_checks", detail="Inspect behavior")
    real_push = cli._push

    def push(repo, url, ref):
        fake.pushes.append(git(repo, "rev-parse", ref))
        if fake.failure == "push":
            raise RuntimeError("push unavailable")
        real_push(repo, url, ref)

    monkeypatch.setattr(cli.openspec, "archive", fake.archive)
    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(cli, "_gh", fake.gh)
    return fake


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/malformed-expected-head-rejected")
@pytest.mark.parametrize("entry", [main, cli.main])
@pytest.mark.parametrize("sha", ["", "HEAD", "abc123", "a" * 39, "a" * 41, "g" * 40, "a" * 40 + "\n",
                                 "a" * 40 + " ", "refs/heads/main"])
def test_malformed_head_is_parser_error(entry, sha, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "dispatch", lambda _: pytest.fail("malformed input dispatched"))
    with pytest.raises(SystemExit) as error:
        entry(["finish", "no-such-change", "--expect-head", sha])
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/expected-head-checked-under-lock")
@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/revision-wins-the-finish-handoff")
@pytest.mark.scenario("factory-landing-jobs/confirmed-landing-handoff/head-changes-at-handoff")
@pytest.mark.parametrize("factory", [False, True])
def test_revision_after_final_identity_check_wins_locked_finish(service, monkeypatch, capfd, factory):
    old = service.offer()
    original_lock = Run.lock
    original_reload = Run.reload
    locked, reloaded = False, False
    revised_state = None

    @contextmanager
    def interleaved_lock(run):
        nonlocal locked, revised_state
        service.republish()  # After adapter's final validation, before acquiring finish's lock.
        revised_state = (run.dir / "state.json").read_bytes()
        with original_lock(run) as mine:
            locked = True
            yield mine
            locked = False

    def reload(run):
        nonlocal reloaded
        original_reload(run)
        if locked:
            reloaded = True

    original_head = cli.loop.Ctx.head

    def guarded_head(ctx):
        if locked:
            assert reloaded, "head must be read after locked reload"
        return original_head(ctx)

    def finish_boundary():
        monkeypatch.setattr(Run, "lock", interleaved_lock)
        monkeypatch.setattr(Run, "reload", reload)
        monkeypatch.setattr(cli.loop.Ctx, "head", guarded_head)
        monkeypatch.setattr(cli, "_tasks_path", lambda *a: pytest.fail("mismatch inspected archive/tasks"))

    service.before_finish = finish_boundary
    assert service.land(factory=factory, offer=old) == 1
    reason = capfd.readouterr().err
    assert old.head in reason and service.local_head() in reason
    assert reloaded and service.local_head() != old.head
    assert service.local_head() == service.remote_head()
    assert (service.run.dir / "state.json").read_bytes() == revised_state
    assert service.run.state.pause.reason + ":" + service.run.state.pause.task == old.pause_id
    assert service.commands("finish") == [[sys.executable, "-m", "nightshift.cli", "finish",
                                           CHANGE, "--expect-head", old.head]]
    assert service.archives == service.pushes == service.ready == []
    assert service.commands("checks") == service.commands("merge") == []


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/expected-head-checked-under-lock")
@pytest.mark.parametrize("failure", ["missing-ref", "invalid-output", "read-error"])
def test_unreadable_head_refuses_before_inspection(service, monkeypatch, capfd, failure):
    expected = service.local_head()
    state_before = (service.run.dir / "state.json").read_bytes()
    if failure == "missing-ref":
        git(service.bare, "update-ref", "-d", f"refs/heads/{BRANCH}")
    elif failure == "invalid-output":
        monkeypatch.setattr(cli.loop.Ctx, "head", lambda _: "HEAD")
    else:
        def unreadable(_):
            raise OSError("head unreadable")
        monkeypatch.setattr(cli.loop.Ctx, "head", unreadable)
    monkeypatch.setattr(cli, "_tasks_path", lambda *a: pytest.fail("unreadable head inspected tasks"))
    assert main(["finish", CHANGE, "--expect-head", expected]) == 1
    assert "could not read run branch head" in capfd.readouterr().err
    assert (service.run.dir / "state.json").read_bytes() == state_before
    assert service.archives == service.pushes == service.ready == []


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/matching-and-omitted-head-retain-finish-gates")
@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/malformed-expected-head-rejected")
@pytest.mark.parametrize("mode", ["matching-uppercase", "omitted", "act"])
def test_matching_and_omitted_finish_succeed(service, mode):
    argv = ["act", CHANGE, "finish", "--json"] if mode == "act" else ["finish", CHANGE]
    if mode == "matching-uppercase":
        argv += ["--expect-head", service.local_head().upper()]
    assert main(argv) == 0
    service.run.reload()
    assert service.run.state.status == "done" and service.run.state.pause is None
    assert service.local_head() == service.remote_head()
    assert service.archives == [CHANGE] and service.ready == service.pushes == [service.local_head()]


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/matching-and-omitted-head-retain-finish-gates")
@pytest.mark.parametrize("guarded", [False, True])
@pytest.mark.parametrize("gate", ["unfinished", "repository", "state"])
def test_head_does_not_bypass_existing_gates(service, guarded, gate, capfd):
    if gate == "unfinished":
        head = seal.commit_files(service.bare, service.local_head(), {
            f"openspec/changes/{CHANGE}/tasks.md": TASKS,
        }, "unfinished task")
        git(service.bare, "update-ref", f"refs/heads/{BRANCH}", head)
    elif gate == "repository":
        git(service.source, "remote", "set-url", "origin", "https://github.com/upstream/project")
    else:
        service.run.pause("ready_to_publish")
    argv = ["finish", CHANGE] + (["--expect-head", service.local_head()] if guarded else [])
    if gate == "repository":
        with pytest.raises(ValueError, match="upstream/project"):
            main(argv)
    else:
        assert main(argv) == 1
        assert ("unfinished" if gate == "unfinished" else "has not been published") in capfd.readouterr().err
        assert service.archives == []
    assert service.pushes == service.ready == []


@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/fork-still-pointing-upstream")
def test_publish_still_refuses_upstream_origin(service, capfd):
    path = service.run.dir / "manifest.toml"
    path.write_text(path.read_text().replace('example/repo', 'owner/fork'))
    git(service.source, "remote", "set-url", "origin", "https://github.com/upstream/project")
    service.run.pause("ready_to_publish")
    assert main(["publish", CHANGE, "--json"]) == 1
    output = capfd.readouterr().out
    assert "owner/fork" in output and "upstream/project" in output
    assert service.pushes == service.ready == service.archives == []


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/archived-commit-was-not-pushed")
@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.scenario("run-actions/publish-only-to-the-named-repository/matching-and-omitted-head-retain-finish-gates")
@pytest.mark.parametrize("recovery", ["finish", "act"])
def test_unpushed_archive_requires_inspected_unguarded_recovery(service, capfd, recovery):
    head = service.remote_head()
    service.failure = "push"
    assert service.land() == 1
    assert "push unavailable" in capfd.readouterr().err
    archived = service.local_head()
    assert archived != head and git(service.bare, "rev-parse", f"{archived}^") == head
    assert service.remote_head() == head
    service.run.reload()
    before = asdict(service.run.state)
    assert before["status"] == "paused" and before["pause"]["reason"] == "human_checks"
    assert service.archives == [CHANGE] and service.pushes == [archived] and service.ready == []

    assert service.land() == 1
    error = capfd.readouterr().err
    assert head in error and archived in error
    assert [argv[-2:] for argv in service.commands("finish")] == [["--expect-head", head]] * 2
    assert service.local_head() == archived and service.remote_head() == head
    service.run.reload()
    assert asdict(service.run.state) == before
    assert service.archives == [CHANGE] and service.pushes == [archived] and service.ready == []
    assert service.commands("checks") == service.commands("merge") == []
    with pytest.raises(factory_land.Refusal, match="remote PR head differs"):
        factory_land.validate_offer(service.offer(), read_status=service.status,
                                    remote_head=service.remote_head, pause_file=service.pause_file)
    assert service.land(factory=True) == 1
    assert len(service.commands("finish")) == 2

    # Model operator inspection of local archive/tasks, refs and prior push effects.
    assert "[x]" in git(service.bare, "show",
                        f"{archived}:openspec/changes/archive/2026-10-20-{CHANGE}/tasks.md")
    assert service.remote_head() == head and service.pushes == [archived]
    service.failure = None
    argv = ["finish", CHANGE] if recovery == "finish" else ["act", CHANGE, "finish", "--json"]
    assert main(argv) == 0
    service.run.reload()
    assert service.run.state.status == "done" and service.run.state.pause is None
    assert service.archives == [CHANGE] and service.pushes == [archived, archived]
    assert service.local_head() == service.remote_head() == archived
    service.calls.clear()
    assert service.land() == 0
    assert not service.commands("finish")
    assert service.commands("checks") and service.commands("merge")[-1][-1] == archived
    assert any("/compare/" in " ".join(argv) for argv, _ in service.calls)
    assert archived in service.commands("POST")[0][-1]  # Fresh attestation for A.


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/archived-commit-was-already-pushed")
@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-at-human-checks")
@pytest.mark.scenario("reviewed-pr-landing/finish-through-the-interpreter/eligible-finish-uses-the-current-interpreter")
def test_pushed_archive_failed_ready_allows_guarded_retry(service, capfd):
    head = service.local_head()
    service.failure = "ready"
    assert service.land() == 1
    assert "ready unavailable" in capfd.readouterr().err
    archived = service.local_head()
    assert archived != head and service.remote_head() == archived
    service.run.reload()
    assert service.run.state.pause.reason == "human_checks"
    service.failure = None
    assert service.land(factory=True) == 0
    assert [argv[-1] for argv in service.commands("finish")] == [head, archived]
    assert service.archives == [CHANGE]
    assert service.pushes == service.ready == [archived, archived]
    assert service.commands("merge")[-1][-1] == archived
