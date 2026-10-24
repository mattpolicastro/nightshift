"""Exact proposal sources remain separate from public delivery destinations."""

import json
import subprocess

import pytest

from nightshift import daemon, queue, specloop_client, specloop_intake, specloop_job
from nightshift.config import Config
from test_engine_routing import DraftGh
from test_specloop_intake import BRANCH, CHANGE, Commands, calls, poll, repo
from test_specloop_spec_revision import CHANGE as LEGACY_CHANGE
from test_specloop_spec_revision import setup as revision_setup


def background_result(path, receipt, result):
    receipt["job"] = "a" * 32
    directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
    specloop_job.save(directory / "request.json", {"args": ["propose"]})
    specloop_job.save(directory / "result.json", {"result": result})
    specloop_job.save(path, receipt)


@pytest.mark.scenario("issue-intake/exact-proposal-source-refs/both-supported-forms-are-accepted")
@pytest.mark.parametrize("change", [CHANGE, LEGACY_CHANGE])
@pytest.mark.parametrize("prefix", ["spec/", "refs/specloop/proposals/"])
@pytest.mark.parametrize("background", [False, True])
def test_exact_refs_survive_completion(tmp_path, change, prefix, background):
    path, receipt = tmp_path / "receipt.json", {"change": change}
    commands = Commands(branch=prefix + change)
    if background:
        background_result(path, receipt, commands.result)
        assert specloop_intake._collect(path, receipt, "propose")
    else:
        assert specloop_intake._start(path, receipt, ["propose"], runner=commands, launcher=None)
        specloop_job.save(path, receipt)
    assert json.loads(path.read_text())["result"] == commands.result


@pytest.mark.scenario("issue-intake/exact-proposal-source-refs/unrelated-or-incomplete-results-are-rejected")
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("field,value", [
    ("branch", None), ("branch", 42), ("branch", []), ("branch", {}),
    ("branch", "HEAD"), ("branch", "spec/another-change"),
    ("branch", "refs/specloop/proposals/another-change"),
    ("branch", f"refs/heads/{BRANCH}"), ("branch", f"refs/tags/{CHANGE}"),
    ("branch", f"refs/specloop/proposals/{CHANGE}/extra"),
    ("branch", f"refs/specloop/proposals/{CHANGE}^"),
    ("branch", f"refs/specloop/proposals/{CHANGE}:refs/heads/other"),
    ("commit", None), ("commit", ""),
])
def test_invalid_results_never_deliver(repo, tmp_path, background, field, value):
    gh, commands = DraftGh(), Commands(branch=f"refs/specloop/proposals/{CHANGE}")
    commands.result[field] = value
    if value is None:
        del commands.result[field]
    path = tmp_path / "proposals/owner__sample/7.json"
    if background:
        background_result(path, {"change": CHANGE}, commands.result)
    with pytest.raises(specloop_client.SpecloopError, match="no commit on the requested branch"):
        poll(repo, tmp_path, gh, commands)
    assert not any(c[0] == "git" for c in commands.calls)
    assert not calls(gh, "pr", "create") and not calls(gh, "issue", "comment")
    assert gh.labels == {"agent:ready"}
    if path.exists():
        saved = json.loads(path.read_text())
        assert "result" not in saved and "job" not in saved and not saved.get("delivered")


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def proposal_repository(source, origin, change):
    source.mkdir()
    origin.mkdir()
    git(origin, "init", "--bare")
    git(source, "init", "-b", f"spec/{change}")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "Test")
    git(source, "config", "commit.gpgsign", "false")
    (source / "review.txt").write_text("original\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "reviewed spec")
    old = git(source, "rev-parse", "HEAD")
    commit = git(source, "commit-tree", "HEAD^{tree}", "-p", old, "-m", "proposal")
    ref = f"refs/specloop/proposals/{change}"
    git(source, "update-ref", ref, commit)
    git(source, "remote", "add", "origin", str(origin))
    (source / "review.txt").write_text("staged review\n")
    git(source, "add", "review.txt")
    (source / "review.txt").write_text("unstaged review\n")
    (source / "notes.txt").write_text("untracked review\n")
    return old, commit, ref


def checkout_snapshot(source):
    return (git(source, "symbolic-ref", "HEAD"), git(source, "rev-parse", "HEAD"),
            git(source, "diff"), git(source, "diff", "--cached"),
            (source / ".git/index").read_bytes(), (source / "notes.txt").read_bytes())


@pytest.mark.scenario("issue-intake/proposal-source-and-destination-separation/fallback-proposal-opens-public-spec-pr")
@pytest.mark.scenario("issue-intake/proposal-source-and-destination-separation/saved-fallback-result-retries-delivery")
@pytest.mark.parametrize("failure", [None, "push", "create", "comment"])
@pytest.mark.parametrize("background", [False, True])
def test_daemon_delivers_a_fallback_ref_proposal(repo, tmp_path, monkeypatch, failure, background):
    source, origin = tmp_path / "manifest-source", tmp_path / "origin.git"
    old, commit, ref = proposal_repository(source, origin, CHANGE)
    snapshot = checkout_snapshot(source)
    # Fetch the reported source verbatim from the manifest repository.
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    git(consumer, "init")
    git(consumer, "fetch", str(source), ref)
    assert git(consumer, "rev-parse", "FETCH_HEAD") == commit
    gh = DraftGh(("agent:ready", "spec:draft"))
    commands = Commands(branch=ref, commit=commit)
    path = tmp_path / "proposals/owner__sample/7.json"
    if background:
        idea = "Add a gate\n\nPlease draft it.\n\nKeep it small."
        background_result(path, {
            "repo": repo.name, "issue": 7, "change": CHANGE, "title": "Add a gate",
            "idea": idea, "input": idea, "round_id": "b" * 32,
            "drafting_events": {event["label"]["name"]: event["id"] for event in gh.events},
        }, commands.result)

    def interrupted(args):
        result = gh(args)
        if args[:2] == (["pr", "create"] if failure == "create" else ["issue", "comment"]):
            raise queue.GhError("response lost", transient=True)
        return result

    if failure:
        if failure == "push":
            git(source, "remote", "set-url", "origin", str(tmp_path / "unavailable.git"))
        with pytest.raises((specloop_intake.DeliveryError, queue.GhError)):
            specloop_intake.propose_pending(
                repo, queue.Labels(), tmp_path, runner=interrupted, specloop_runner=commands)
        saved = json.loads(path.read_text())
        assert saved["result"] == commands.result and not saved.get("delivered")
        assert gh.labels == {"agent:ready", "spec:draft"}
        git(source, "remote", "set-url", "origin", str(origin))
        # A later proposal ref move must not change the saved commit on retry.
        git(source, "update-ref", ref, old)
    previous_calls = len(commands.calls)

    def cli(args, **kwargs):
        if args[1] == "status":
            return subprocess.CompletedProcess(args, 0, json.dumps({"version": 1, "runs": []}), "")
        return commands(args, **kwargs)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    health = daemon.RepoHealth()  # Restart with only disk receipts and remote state.
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh, specloop_runner=cli)
    assert not health.failures
    assert len(commands.calls) == (0 if background else 1)
    if failure:
        assert len(commands.calls) == previous_calls
    assert git(origin, "rev-parse", f"refs/heads/{BRANCH}") == commit
    assert git(origin, "for-each-ref", "--format=%(refname)") == f"refs/heads/{BRANCH}"
    assert checkout_snapshot(source) == snapshot
    create, = calls(gh, "pr", "create")
    assert create == ["pr", "create", "--repo", repo.name, "--head", BRANCH, "--base", "develop",
                      "--draft", "--title", "Spec: Add a gate", "--body",
                      "Closes #7\n\nFeasibility risks:\n- Timing needs a human check."]
    assert calls(gh, "pr", "edit")[-1][-2:] == ["--add-label", "spec:review"]
    assert len(calls(gh, "issue", "comment")) == 1 and gh.pr_url in gh.comments[-1]["body"]
    assert not gh.labels
    if failure in (None, "push"):
        assert json.loads(path.read_text())["delivered"]


@pytest.mark.scenario("pr-revision/fallback-spec-revision-delivery/checked-out-spec-revision-updates-same-pr")
@pytest.mark.scenario("pr-revision/fallback-spec-revision-delivery/revision-failure-preserves-feedback-and-result")
@pytest.mark.parametrize("failure", [None, "push", "moved"])
@pytest.mark.parametrize("background", [False, True])
def test_fallback_revision_preserves_head_and_feedback(revision_setup, tmp_path, monkeypatch, failure, background):
    repo, gh, commands = revision_setup
    source, origin = tmp_path / "source", tmp_path / "origin.git"
    old, commit, ref = proposal_repository(source, origin, LEGACY_CHANGE)
    git(source, "push", "origin", f"{old}:refs/heads/{gh.branch}")
    gh.head = old
    snapshot = checkout_snapshot(source)
    commands.result.update(branch=ref, commit=commit)
    path = tmp_path / "revisions/owner__sample/8.json"
    consumed = json.loads(path.read_text())["consumed_feedback_ids"]
    pushes = []

    def cli(args, **kwargs):
        result = commands(args, **kwargs)
        if args[1] == "propose":
            gh.feedback.append({"id": 104, "author": {"login": "reviewer"},
                                "body": "Later feedback", "createdAt": "2026-10-20T14:00:00Z"})
        return result

    def run_git(args, **kwargs):
        if args[3] == "push":
            pushes.append(args)
            assert args[4:] == ["origin", f"{commit}:refs/heads/{gh.branch}"]
            assert json.loads(path.read_text())["consumed_feedback_ids"] == consumed
            if failure:
                return subprocess.CompletedProcess(args, 1, "", "push unavailable")
        result = subprocess.run(args, **kwargs)
        if args[3] == "push" and result.returncode == 0:
            gh.head = git(origin, "rev-parse", f"refs/heads/{gh.branch}")
        return result

    def poll_revision():
        return specloop_intake.revise_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                             specloop_runner=cli, git_runner=run_git)

    if background:
        def start(path, receipt, args, **kwargs):
            completed = cli(["specloop", *args, "--json"], capture_output=True, text=True, check=False)
            background_result(path, receipt, json.loads(completed.stdout))

        monkeypatch.setattr(specloop_job, "start", start)
        assert specloop_intake.revise_pending(
            repo, queue.Labels(), tmp_path, runner=gh, status_runner=commands, git_runner=run_git,
        ) == []
        assert "result" not in json.loads(path.read_text())
        assert not pushes and not gh.comments

    if failure:
        with pytest.raises(specloop_intake.DeliveryError, match="could not push"):
            poll_revision()
        saved = json.loads(path.read_text())
        assert saved["result"] == commands.result and saved["head"] == old
        assert saved["feedback_ids"] == ["conversation:102"]
        assert saved["consumed_feedback_ids"] == consumed and not saved.get("delivered")
        assert not gh.comments and "agent:revise" in gh.labels
        if failure == "moved":
            moved = git(source, "commit-tree", "HEAD^{tree}", "-p", old, "-m", "human change")
            git(source, "push", "origin", f"{moved}:refs/heads/{gh.branch}")
            gh.head = moved
            with pytest.raises(specloop_intake.DeliveryError, match="moved"):
                poll_revision()
            assert json.loads(path.read_text()) == saved
            assert len(pushes) == len(commands.proposals) == 1
            assert not gh.comments and "agent:revise" in gh.labels
            assert checkout_snapshot(source) == snapshot
            return
        failure = None
        git(source, "update-ref", ref, old)
    poll_revision()
    saved = json.loads(path.read_text())
    assert saved["delivered"] and saved["result"]["commit"] == commit
    assert saved["consumed_feedback_ids"] == sorted([*consumed, "conversation:102"])
    assert len(commands.proposals) == 1 and gh.head == commit
    assert git(origin, "rev-parse", f"refs/heads/{gh.branch}") == commit
    assert checkout_snapshot(source) == snapshot
    assert not calls(gh, "pr", "create") and gh.labels == {"spec:review"}
    comment, = gh.comments
    assert commit in comment["body"] and "Handle empty input" in comment["body"]
    assert "Later feedback" not in comment["body"]
