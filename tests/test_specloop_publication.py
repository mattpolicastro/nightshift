"""Publication and the daemon kill switch through injected CLI/GitHub runners."""

import json
import subprocess
from dataclasses import asdict, replace
from functools import partial
from types import SimpleNamespace

import pytest

from manifest_fixtures import manifest_text
from merge_readiness_fixtures import PublicationOnlyGh

from nightshift import daemon, queue, specloop_intake
from nightshift.config import Config, Repo
from specloop import cli, state
from test_engine_routing import DraftGh
from test_specloop_approval import ApprovalGh, Commands, CHANGE, HEAD


CHECKS = "- [ ] `gate/manual/audition`: Audition the gate.\n- [ ] `gate/manual/reopen`: Save and reopen."
DETAIL = f"all tasks landed at {HEAD[:12]}; human checks after publishing:\n{CHECKS}\nNext: `specloop publish {CHANGE}`"


class PublishCommands(Commands):
    def __init__(self):
        super().__init__()
        self.publish_result = None
        self.lose_publish_response = False

    def __call__(self, args, **kwargs):
        if args[:2] != ["specloop", "publish"]:
            return super().__call__(args, **kwargs)
        self.calls.append(args)
        assert args == ["specloop", "publish", CHANGE, "--json"]
        run = self.runs[0]
        result = self.publish_result or {"version": 1, "outcome": "published", "pr": run["pr"]}
        if result["outcome"] == "published":
            run["pause"] = {"reason": "human_checks", "task": "", "detail": "Review the PR"}
        if self.lose_publish_response:
            self.lose_publish_response = False
            raise OSError("publish response lost")
        return subprocess.CompletedProcess(args, int(result["outcome"] == "refused"), json.dumps(result), "")

    @property
    def publications(self):
        return [args for args in self.calls if args[:2] == ["specloop", "publish"]]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    repo = Repo("owner/sample", engine="specloop", manifest=manifest)
    gh, commands = PublicationOnlyGh(), PublishCommands()
    # Use actual admission to establish daemon ownership, not hand-written state.
    specloop_intake.admit_pending(repo, queue.Labels(), tmp_path,
                                 runner=gh, specloop_runner=commands, git_runner=commands)
    commands.runs[0].update(status="paused", head=HEAD, tasks={"done": 3, "total": 3},
                            pause={"reason": "ready_to_publish", "task": "", "detail": DETAIL})
    gh.calls.clear()
    return repo, gh, commands


def poll(setup, health=None, runner=None):
    repo, gh, commands = setup
    health = health or daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=runner or gh, specloop_runner=commands)
    return health


def comments(gh):
    return [c["body"] for c in gh.comments if f"<!-- nightshift:publish:{CHANGE}:" in c["body"]]


@pytest.mark.scenario("issue-intake/publication/ready-run-published")
def test_ready_run_published_to_recorded_pr_with_checks(setup, tmp_path):
    _, gh, commands = setup
    assert not poll(setup).failures
    assert not poll(setup).failures
    assert commands.publications == [["specloop", "publish", CHANGE, "--json"]]
    assert commands.runs[0]["pause"]["reason"] == "human_checks"
    assert len(comments(gh)) == 1
    assert CHECKS in comments(gh)[0]
    assert gh.pr_url in comments(gh)[0]
    assert "hosted CI" in comments(gh)[0]
    assert f"specloop finish {CHANGE}" in comments(gh)[0]
    assert f"specloop publish {CHANGE}" not in comments(gh)[0]
    assert not any(call[:2] == ["pr", "create"] for call in gh.calls)
    receipt = json.loads((tmp_path / "publications/owner__sample/8.json").read_text())
    assert receipt["delivered"] and receipt["head"] == HEAD and receipt["pr"] == gh.pr_url


@pytest.mark.scenario("issue-intake/publication/ready-run-published")
@pytest.mark.parametrize("failure", ["publish_response", "comment_before", "comment_after", "comment_read"])
def test_restart_finishes_publication_without_duplicates(setup, failure):
    repo, gh, commands = setup
    commands.lose_publish_response = failure == "publish_response"

    def interrupted(args):
        if args[:2] == ["pr", "comment"] and failure.startswith("comment_"):
            if failure == "comment_after":
                gh(args)
            raise queue.GhError("response lost", transient=True)
        if failure == "comment_read" and args[0] == "api" and args[1].endswith("/comments"):
            raise queue.GhError("HTTP 503", transient=True)
        return gh(args)

    health = poll(setup, runner=interrupted)
    assert health.failures == {repo.name: 1}
    assert commands.runs[0]["pause"]["reason"] == "human_checks"
    assert not poll(setup, health).failures
    poll(setup)
    assert len(commands.publications) == 1
    assert len(comments(gh)) == 1
    assert CHECKS in comments(gh)[0]


@pytest.mark.scenario("issue-intake/publication/ready-run-published")
@pytest.mark.parametrize("failure", ["create_before", "create_after", "patch_after", "lookup"])
def test_publication_comment_reconciles_through_real_gh_retries(setup, monkeypatch, failure):
    repo, gh, commands = setup
    creates = []
    lookups = []

    def run(args, **kwargs):
        assert args[0] == "gh"
        command = args[1:]
        if command[:2] == ["pr", "comment"]:
            creates.append(command)
            if len(creates) == 1:
                if failure != "create_before":
                    gh(command)
                return subprocess.CompletedProcess(args, 1, "", "HTTP 502: response lost")
        if creates and command[0] == "api" and command[1].endswith("/comments"):
            lookups.append(command)
            if failure == "lookup":
                return subprocess.CompletedProcess(args, 1, "", "HTTP 503: lookup unavailable")
        result = gh(command)
        if failure == "patch_after" and "PATCH" in command:
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: patch response lost")
        return subprocess.CompletedProcess(args, 0, result, "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    health = poll(setup, runner=partial(queue._gh, sleep=lambda _: None))
    assert bool(health.failures) == (failure in ("patch_after", "lookup"))
    assert len(creates) == (2 if failure == "create_before" else 1)
    assert lookups  # A lost response must be reconciled before another create.
    assert len(comments(gh)) == 1
    assert CHECKS in comments(gh)[0]
    assert gh.pr_url in comments(gh)[0]
    # The next poll recovers even after the real retry budget is exhausted.
    poll(setup, health)
    assert not health.failures
    assert len(commands.publications) == len(comments(gh)) == 1


@pytest.mark.scenario("issue-intake/kill-switch/paused-daemon-does-nothing")
@pytest.mark.parametrize("stage", [
    "label_retry", "comment_retry", "lookup_retry", "patch_retry",
    "after_lookup", "after_absent_lookup",
])
def test_pause_stops_real_gh_retries_and_comment_reconciliation(setup, monkeypatch, tmp_path, stage):
    _, gh, commands = setup
    if stage == "label_retry":
        commands.publish_result = {"version": 1, "outcome": "refused", "reasons": ["Not ready."]}
    failed = []

    def run(args, **kwargs):
        assert args[0] == "gh"
        assert not daemon.PAUSE_FILE.exists(), "GitHub subprocess attempted while paused"
        command = args[1:]
        operation = None
        if stage == "label_retry" and command[:2] == ["pr", "edit"]:
            operation = "label_retry"
        elif stage != "label_retry" and command[:2] == ["pr", "comment"]:
            if stage != "after_absent_lookup":
                gh(command)  # GitHub saved it but the response was lost.
            operation = "comment_retry"
        elif failed and command[0] == "api" and command[1].endswith("/comments"):
            if stage in ("after_lookup", "after_absent_lookup"):
                daemon.PAUSE_FILE.touch()
            elif stage == "lookup_retry":
                operation = "lookup_retry"
        elif "PATCH" in command and stage == "patch_retry":
            operation = "patch_retry"
        if operation:
            failed.append(operation)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: response lost")
        return subprocess.CompletedProcess(args, 0, gh(command), "")

    def backoff(_):
        if failed[-1] == stage:
            daemon.PAUSE_FILE.touch()

    monkeypatch.setattr(queue.subprocess, "run", run)
    health = poll(setup, runner=partial(queue._gh, sleep=backoff))
    assert daemon.PAUSE_FILE.exists()
    assert not health.failures  # Holding work is not a repository outage.
    assert gh.labels == set()
    assert len(commands.publications) == 1
    receipt = json.loads((tmp_path / "publications/owner__sample/8.json").read_text())
    assert not receipt.get("delivered")
    # The scoped guard must not leak into unrelated worker GitHub operations.
    monkeypatch.setattr(queue.subprocess, "run", lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, gh(args[1:]), ""))
    assert json.loads(queue._gh(["pr", "view", "8", "--repo", "owner/sample"]))["state"] == "OPEN"
    daemon.PAUSE_FILE.unlink()
    poll(setup, health)
    assert not health.failures
    assert len(commands.publications) == len(comments(gh)) == 1
    assert gh.labels == ({"needs-human"} if stage == "label_retry" else set())


@pytest.mark.parametrize("reason", ["stuck", "blocked", "coverage", "human_checks"])
def test_other_pauses_do_not_publish(setup, reason):
    _, gh, commands = setup
    commands.runs[0]["pause"]["reason"] = reason
    poll(setup)
    assert not commands.publications and not comments(gh)


@pytest.mark.parametrize("condition", ["unmanaged", "other_repo", "other_pr", "closed", "fork", "branch",
                                      "revise", "needs_human", "running", "worker", "missing_pr"])
def test_only_managed_eligible_runs_are_published(setup, tmp_path, condition):
    repo, gh, commands = setup
    if condition == "unmanaged":
        (tmp_path / "admissions/owner__sample/8.json").unlink()
    elif condition == "other_repo":
        repo = replace(repo, name="someone/sample")
    elif condition == "other_pr":
        commands.runs[0]["pr"] = "https://github.com/other/repo/pull/8"
    elif condition == "missing_pr":
        commands.runs[0]["pr"] = ""
    elif condition == "closed":
        gh.state = "CLOSED"
    elif condition == "fork":
        gh.fork = True
    elif condition == "branch":
        gh.branch = "spec/another-change"
    elif condition in ("revise", "needs_human"):
        gh.labels.add("agent:revise" if condition == "revise" else "needs-human")
    elif condition == "running":
        commands.runs[0]["status"] = "running"
    elif condition == "worker":
        with pytest.raises(ValueError, match="owner/sample: worker engine is retired"):
            replace(repo, engine="worker")
        assert not commands.publications and not comments(gh)
        return
    poll((repo, gh, commands))
    assert not commands.publications and not comments(gh)


@pytest.mark.parametrize("failure", ["status", "github"])
def test_unavailable_publication_is_retried(setup, failure):
    repo, gh, commands = setup
    commands.status_error = failure == "status"

    def runner(args):
        if failure == "github" and args[:2] == ["pr", "view"]:
            raise queue.GhError("HTTP 503", transient=True)
        return gh(args)

    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=runner, specloop_runner=commands)
    assert health.failures == {repo.name: 1}
    assert not commands.publications and not comments(gh)
    assert commands.runs[0]["pause"]["reason"] == "ready_to_publish"
    commands.status_error = False
    poll(setup, health)
    assert not health.failures
    assert len(commands.publications) == len(comments(gh)) == 1


@pytest.mark.scenario("issue-intake/publication/ready-run-published")
@pytest.mark.parametrize("failure", ["push", "timeout", "executable"])
def test_publication_recovers_through_cli_json(setup, tmp_path, monkeypatch, capfd, failure):
    repo, gh, commands = setup
    monkeypatch.setattr(state, "ROOT", tmp_path / "specloop")
    run = state.Run.for_change(CHANGE)
    run.state.pr = gh.pr_url
    run.pause("ready_to_publish", detail=DETAIL)
    monkeypatch.setattr(cli, "_ctx", lambda run: SimpleNamespace(
        repo=tmp_path, ref=f"refs/heads/spec/{CHANGE}", head=lambda: HEAD, pause=run.pause, run=run,
        m=SimpleNamespace(github="owner/sample"),
    ))
    monkeypatch.setattr(cli, "_remote", lambda ctx, target=None: "test-origin")
    # The state-only transport fixture has an unchanged base; exercise push recovery.
    monkeypatch.setattr(cli.loop, "_fetch_publish_base", lambda ctx, remote, **kw: HEAD)
    monkeypatch.setattr(cli, "_gh", lambda *args: pytest.fail("must reuse the adopted PR"))
    pushes = []
    unavailable = True

    def push(args, **kwargs):
        if "diff" in args:
            assert args[args.index("diff"):] == ["diff", "--no-renames", "--name-only", "-z", f"{HEAD}..{HEAD}", "--"]
            return subprocess.CompletedProcess(args, 0, b"", b"")
        assert args[:4] == ["git", "-C", str(tmp_path), "push"]
        assert kwargs == {"check": True, "timeout": 300}
        pushes.append(args)
        if unavailable:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 300)
            if failure == "executable":
                raise FileNotFoundError("git unavailable")
            raise subprocess.CalledProcessError(128, args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cli.subprocess, "run", push)
    responses = []

    def cli_runner(args, **kwargs):
        if args[:2] != ["specloop", "publish"]:
            return commands(args, **kwargs)
        commands.calls.append(args)
        code = cli.main(args[1:])
        output = capfd.readouterr()
        responses.append(json.loads(output.out))
        saved = state.Run.for_change(CHANGE).state
        commands.runs[0].update(status=saved.status, pause=asdict(saved.pause))
        return subprocess.CompletedProcess(args, code, output.out, output.err)

    def poll_cli(health):
        daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=cli_runner)

    health = daemon.RepoHealth()
    receipt_path = tmp_path / "publications/owner__sample/8.json"
    # Repeated outages leave the durable receipt pending, including after restart.
    for _ in range(2):
        poll_cli(health)
        assert health.failures[repo.name]
        assert responses[-1]["version"] == 1 and responses[-1]["error"]
        assert "outcome" not in responses[-1]
        receipt = json.loads(receipt_path.read_text())
        assert "result" not in receipt and not receipt.get("delivered")
        assert commands.runs[0]["pause"]["reason"] == "ready_to_publish"
        assert not comments(gh) and not gh.labels
    assert len(pushes) == 2
    unavailable = False
    gh.labels.add("needs-human")
    poll_cli(health)
    assert len(pushes) == 2  # Restored connectivity does not override a human hold.
    gh.labels.remove("needs-human")
    poll_cli(health)
    poll_cli(health)
    assert not health.failures
    assert len(pushes) == len(commands.publications) == 3
    assert responses[-1] == {"version": 1, "outcome": "published", "pr": gh.pr_url}
    assert commands.runs[0]["pause"]["reason"] == "human_checks"
    assert len(comments(gh)) == 1 and CHECKS in comments(gh)[0]
    receipt = json.loads(receipt_path.read_text())
    assert receipt["delivered"] and receipt["result"] == responses[-1]


def test_refusal_is_explained_and_not_repeated(setup):
    _, gh, commands = setup
    commands.publish_result = {"version": 1, "outcome": "refused", "reasons": ["Run is no longer ready."]}
    poll(setup)
    poll(setup)
    assert "needs-human" in gh.labels
    gh.labels.remove("needs-human")
    poll(setup)
    assert len(commands.publications) == len(comments(gh)) == 1
    assert "Run is no longer ready." in comments(gh)[0]
    assert "Published" not in comments(gh)[0]


@pytest.mark.scenario("issue-intake/publication/ready-run-published")
def test_new_revision_preserves_checks_and_retries_its_own_comment(setup, tmp_path):
    repo, gh, commands = setup
    assert not poll(setup).failures
    path = tmp_path / "publications/owner__sample/8.json"
    first = json.loads(path.read_text())
    original = gh.comments[-1].copy()
    commands.runs[0].update(head="b" * 40, pause={"reason": "ready_to_publish", "detail": "Check the revised gate."})

    def interrupted(args):
        result = gh(args)
        if args[:2] == ["pr", "comment"] or "PATCH" in args:
            raise queue.GhError("comment response lost", transient=True)
        return result

    health = poll(setup, runner=interrupted)
    assert health.failures == {repo.name: 1}
    second = json.loads(path.read_text())
    assert second["round_id"] != first["round_id"]
    assert not second.get("delivered")
    assert len(comments(gh)) == 2
    revised = gh.comments[-1].copy()
    assert f":{second['round_id']} -->" in revised["body"]

    # A lost PATCH response also preserves the round across daemon polls.
    poll(setup, health, runner=interrupted)
    assert health.failures
    assert not poll(setup, health).failures
    assert not poll(setup).failures
    assert len(commands.publications) == 2
    assert len(comments(gh)) == 2
    assert gh.comments[-2] == original
    assert gh.comments[-1] == revised
    assert CHECKS in original["body"]
    assert "Check the revised gate." in revised["body"]
    assert CHECKS not in revised["body"]
    patches = [args for args in gh.calls if "PATCH" in args]
    assert len(patches) == 2
    assert all(args[1] == f"repos/{repo.name}/issues/comments/{revised['id']}" for args in patches)
    delivered = json.loads(path.read_text())
    assert delivered["delivered"] and delivered["round_id"] == second["round_id"]


def test_human_hold_after_failed_publish_prevents_retry(setup):
    repo, gh, commands = setup

    def interrupted(args, **kwargs):
        if args[:2] == ["specloop", "publish"]:
            raise OSError("publish could not start")
        return commands(args, **kwargs)

    daemon.poll_specloop(Config(repos=[repo]), daemon.RepoHealth(),
                         runner=gh, specloop_runner=interrupted)
    gh.labels.add("needs-human")
    poll(setup)
    assert not commands.publications and not comments(gh)
    gh.labels.clear()
    poll(setup)
    assert len(commands.publications) == len(comments(gh)) == 1


@pytest.mark.scenario("issue-intake/kill-switch/paused-daemon-does-nothing")
@pytest.mark.parametrize("entry", ["loop", "poll"])
def test_paused_daemon_does_nothing(setup, monkeypatch, entry):
    repo, gh, commands = setup
    # A draft issue, an approval, a revision and a ready run are all waiting.
    draft = DraftGh(("spec:draft",))
    gh.labels.update(("spec:approved", "agent:revise"))
    daemon.PAUSE_FILE.touch()
    before = list(commands.calls)

    def github(args):
        return draft(args) if args[0] == "issue" else gh(args)

    monkeypatch.setattr(queue, "_gh", github)
    monkeypatch.setattr(daemon.subprocess, "run", commands)
    monkeypatch.setattr(specloop_intake, "run_git", commands)
    if entry == "loop":
        daemon.loop(Config(repos=[repo]), {}, once=True)
    else:
        assert not poll(setup, runner=github).failures
    assert commands.calls == before
    assert not draft.calls and not gh.calls
    assert draft.labels == {"spec:draft"}
    assert gh.labels == {"spec:approved", "agent:revise"}
    assert commands.runs[0]["pause"]["reason"] == "ready_to_publish"


@pytest.mark.scenario("issue-intake/kill-switch/paused-daemon-does-nothing")
@pytest.mark.parametrize("stage", ["draft", "admit", "publish", "comment"])
def test_pause_created_during_poll_stops_further_actions(setup, stage):
    repo, gh, commands = setup
    if stage == "draft":
        gh = DraftGh(("spec:draft",))
    elif stage == "admit":
        gh.labels.add("spec:approved")
    calls = []

    def github(args):
        calls.append(args)
        result = gh(args)
        if ((stage == "draft" and args[:2] == ["issue", "view"])
                or (stage in ("admit", "publish") and args[:2] == ["pr", "view"])
                or (stage == "comment" and args[0] == "api" and args[1].endswith("/comments"))):
            daemon.PAUSE_FILE.touch()
        return result

    before = list(commands.calls)
    health = poll((repo, gh, commands), runner=github)
    assert daemon.PAUSE_FILE.exists()
    assert not health.failures  # A deliberate pause is not an outage.
    assert not comments(gh)
    assert not any(call[:2] in (["pr", "edit"], ["issue", "edit"]) for call in calls)
    new_commands = commands.calls[len(before):]
    assert all(c[1] == "status" or (stage == "comment" and c[1] == "publish") for c in new_commands)
    assert len(commands.publications) == (1 if stage == "comment" else 0)


@pytest.mark.scenario("issue-intake/kill-switch/paused-daemon-does-nothing")
@pytest.mark.scenario("issue-intake/git-process-group-timeout/production-runner-preserves-daemon-progress")
def test_pause_during_propose_stops_push_and_delivery(setup, monkeypatch):
    repo, _, _ = setup
    gh = DraftGh(("spec:draft",))
    calls = []

    def cli(args, **kwargs):
        calls.append(args)
        assert args[:2] == ["specloop", "propose"]
        daemon.PAUSE_FILE.touch()
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "version": 1, "outcome": "drafted", "branch": f"spec/{CHANGE}", "commit": HEAD,
        }), "")

    monkeypatch.setattr(specloop_intake, "run_git", lambda *a, **k: pytest.fail("pushed while paused"))
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=cli)
    assert not health.failures
    assert len(calls) == 1
    assert not gh.prs
    assert gh.labels == {"spec:draft"}
    assert len(gh.comments) == 1  # Only the original human comment.
