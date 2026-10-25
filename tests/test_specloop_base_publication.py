"""Publication polls and supervisor restarts over real local Git and CLI state."""

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift.cli import main

from nightshift import queue, specloop_intake
from nightshift.config import Repo
from specloop import cli, loop, serve, state
from test_specloop_approval import ApprovalGh
from test_specloop_base_sync import (
    advance_base, base_env, controlled_verify, forbid_publication,
    publish_without_preparing, sh,
)


class Interrupted(BaseException):
    """Process death must bypass the driver's ordinary infrastructure handler."""


class PublicationGh(ApprovalGh):
    def __init__(self):
        super().__init__()
        self.body = "## Checks a human must make\n(none)"
        self.checks = [{"name": "CI", "bucket": "pass", "state": "COMPLETED"}]

    @property
    def publication_comments(self):
        return [c for c in self.comments if "<!-- nightshift:publish:" in c["body"]]

    @property
    def readiness_comments(self):
        return [c for c in self.comments if "<!-- nightshift:approval:c:merge-readiness -->" in c["body"]]

    def __call__(self, args):
        if args[:2] == ["pr", "checks"]:
            self.calls.append(args)
            assert args == ["pr", "checks", "8", "--repo", "owner/sample", "--json", "name,bucket,state"]
            return json.dumps(self.checks)
        result = super().__call__(args)
        if args[:2] == ["pr", "view"]:
            return json.dumps({**json.loads(result), "body": self.body})
        return result


@pytest.fixture
def publication(base_env, tmp_path, monkeypatch, capfd):
    env, ctx = base_env, base_env.ctx
    gh = PublicationGh()
    gh.branch, gh.head = "spec/c", ctx.head()
    ctx.run.manifest_path.write_text(f'''
[run]
repo = {json.dumps(str(env.src))}
change = "c"
branch = "spec/c"
pr_base = "release/stable"
github = "owner/sample"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "m"
[paths]
allowed = ["out/**"]
[[verify]]
id = "ok"
run = ["/usr/bin/true"]
[coverage.exempt]
"gate/manual/audition" = "Audition the gate."
"gate/manual/reopen" = "Save and reopen."
''')
    ctx.m = cli._manifest(ctx.run)
    ctx.run.state.pr = gh.pr_url
    ctx.run.state.tasks["1.1"] = state.TaskState(status="passed", attempts=2)
    ctx.run.state.gates_passed = [1]
    ctx.run.save()
    p = SimpleNamespace(env=env, ctx=ctx, gh=gh, calls=[], documents=[], verified=[],
                        merges=[], fetches=[], pushes=[], lose_response=False,
                        lose_published_response=False,
                        operations=[], push_attempts=[], fail_push=False)
    real_merge, real_run, real_push = loop._merge_commits, subprocess.run, cli._push

    def merge(*args):
        result = real_merge(*args)
        p.merges.append(result)
        p.operations.append(("merge", result))
        return result

    def transport(args, **kwargs):
        if "fetch" in args and args[-1].endswith(":refs/specloop/publish-base"):
            p.fetches.append(kwargs["timeout"])
            p.operations.append(("fetch", kwargs["timeout"]))
        return real_run(args, **kwargs)

    def push(*args):
        assert p.fetches[-1] == 20
        p.push_attempts.append(ctx.head())
        p.operations.append(("push", ctx.head()))
        if p.fail_push:
            p.fail_push = False
            raise OSError("injected push transport failure")
        real_push(*args)  # Push to the local bare origin, never a hosted service.
        gh.head = ctx.head()
        p.pushes.append(ctx.head())

    def verify(repo, head, steps, m, out):
        saved = state.Run.for_change("c", require_existing=True).state
        assert saved.status == "running" and saved.pause is None
        assert saved.base_sync["retry_verify"] and not saved.base_sync["verified_head"]
        assert str(out) in saved.base_sync["receipts"]
        assert head == ctx.head() and loop._is_ancestor(repo, saved.base_sync["base"], head)
        receipt = loop.verify.Receipt("ok", ["check"], 0, 0, True, 0.1, "passed")
        (out / "receipts.json").write_text(json.dumps([asdict(receipt)]))
        p.verified.append(head)
        p.operations.append(("verify", head))
        return [receipt]

    def forbidden(*args, **kwargs):
        pytest.fail("publish must not merge, verify, recover intent, or drive the loop")

    def runner(args, **kwargs):
        assert args[0] == "specloop" and args[1] in ("status", "publish")
        p.calls.append(args[1])
        expected_error = ("injected push transport failure" if p.fail_push
                          else getattr(p, "expected_error", "scheduled"))
        capfd.readouterr()
        if args[1] == "publish":
            assert kwargs["timeout"] == 30
            before_head = ctx.head()
            before_sync = state.Run.for_change("c").state.base_sync
            try:
                with monkeypatch.context() as guard:
                    for name in ("_merge_commits", "_merge_publish_base", "prepare_publish", "run", "finals"):
                        guard.setattr(loop, name, forbidden)
                    guard.setattr(loop.verify, "run_steps", forbidden)
                    guard.setattr(cli, "_existing_pr", forbidden)
                    code = cli.main(args[1:])
            except Interrupted as exc:
                raise OSError("publication process interrupted") from exc
            finally:
                assert ctx.head() == before_head
                assert state.Run.for_change("c").state.base_sync == before_sync
        else:
            code = cli.main(args[1:])
        output = capfd.readouterr()
        document = json.loads(output.out)
        p.documents.append(document)
        if args[1] == "publish" and code:
            assert code == 1 and document["version"] == 1
            assert set(document) == {"version", "error"} and expected_error in document["error"]
        if args[1] == "publish" and p.lose_response:
            p.lose_response = False
            assert code == 1 and set(document) == {"version", "error"}
            raise OSError("handoff response lost")
        if args[1] == "publish" and p.lose_published_response:
            p.lose_published_response = False
            assert code == 0 and document == {"version": 1, "outcome": "published", "pr": gh.pr_url}
            assert state.Run.for_change("c").state.pause.reason == "human_checks"
            raise OSError("stable publish response lost")
        return subprocess.CompletedProcess(args, code, output.out, output.err)

    monkeypatch.setattr(loop, "_merge_commits", merge)
    monkeypatch.setattr(loop.subprocess, "run", transport)
    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(cli, "_gh", forbidden)  # The adopted PR must be reused.
    monkeypatch.setattr(loop.verify, "run_steps", verify)
    repo = Repo("owner/sample", engine="specloop", manifest=ctx.run.manifest_path)
    # Establish daemon ownership through unchanged admission, using real status.
    specloop_intake.admit_pending(repo, queue.Labels(), tmp_path, runner=gh, specloop_runner=runner)
    gh.calls.clear()
    gh.comments.clear()
    receipt_path = tmp_path / "publications/owner__sample/8.json"
    p.receipt = lambda: json.loads(receipt_path.read_text())

    def poll_once(gh_runner=None):
        errors = []
        specloop_intake.publish_pending(repo, queue.Labels(), tmp_path, runner=gh_runner or gh,
                                       specloop_runner=runner, on_error=lambda n, e: errors.append(e))
        return errors

    def poll(gh_runner=None):
        errors = poll_once(gh_runner)
        assessed = (tmp_path / "merge-readiness/owner__sample/8.json").exists()
        assert gh.labels == ({"merge:blocked"} if assessed else set())
        assert [call for call in gh.calls if "--add-label" in call] == (
            [["pr", "edit", "8", "--repo", repo.name, "--add-label", "merge:blocked"]] if assessed else [])
        assert len(gh.readiness_comments) == int(assessed)
        assert gh.comments == gh.publication_comments + gh.readiness_comments
        assert not any("Publication refused" in c["body"] for c in gh.comments)
        return errors

    p.poll = poll
    # New integration cases assert their own exact readiness effects; legacy
    # publication cases retain every assertion in the wrapper above.
    p.poll_once = poll_once
    p.status = lambda: json.loads(runner(["specloop", "status", "--json"]).stdout)["runs"][0]
    p.publish = lambda: runner(["specloop", "publish", "c", "--json"], timeout=30)
    assert main(["run", "c"]) == 0
    assert p.status()["pause"]["reason"] == "ready_to_publish"
    p.fetches.clear()
    p.operations.clear()
    return p


def scheduled(p):
    selected = []
    assert serve.pass_once(lambda _: None, spawn=selected.append) == selected == ["c"]
    status = p.status()
    assert status["status"] == "running" and status["pause"] is None
    before = (len(p.calls), list(p.gh.calls), p.receipt())
    assert p.poll() == []
    assert p.calls[before[0]:] == ["status"]  # Intake skips a running run even at M.
    assert p.gh.calls == before[1] and p.receipt() == before[2]
    assert not p.receipt().get("delivered") and "result" not in p.receipt()
    assert not p.pushes and not p.gh.comments


def finish(p, old_receipt):
    assert main(["run", "c"]) == 0
    status = p.status()
    merged = p.ctx.head()
    saved = state.Run.for_change("c").state
    assert status["head"] == merged != old_receipt["head"]
    assert status["pause"]["reason"] == "ready_to_publish"
    assert saved.base_sync["verified_head"] == merged and not saved.base_sync["retry_verify"]
    assert p.verified[-1] == merged
    checks = [f"- [ ] `{sid}`: {why}" for sid, why in sorted(p.ctx.m.coverage_exempt.items())]
    assert all(check in status["pause"]["detail"] for check in checks)
    assert status["pause"]["detail"].endswith("Next: `specloop publish c`")
    assert p.poll() == []
    receipt = p.receipt()
    assert receipt["head"] == merged and receipt["pr"] == p.gh.pr_url and receipt["delivered"]
    assert receipt["round_id"] != old_receipt["round_id"]
    assert receipt["checks"] == status["pause"]["detail"]
    assert p.pushes == [merged]
    assert sh(p.env.origin, "git", "rev-parse", "spec/c") == merged
    assert len(p.gh.publication_comments) == 1
    body = p.gh.publication_comments[0]["body"]
    assert all(check in body for check in checks) and "hosted CI" in body
    assert receipt["round_id"] in body and old_receipt["round_id"] not in body
    assert "specloop publish c" not in body and "specloop finish c" in body
    publishes = p.calls.count("publish")
    assert p.poll() == [] and p.calls.count("publish") == publishes
    assert len(p.gh.publication_comments) == 1
    events = [json.loads(line) for line in (p.ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert [e["head"] for e in events if e["event"] == "published"] == [merged]


def recover_merged_publication(p, monkeypatch, failure):
    old, base = p.ctx.head(), advance_base(p.env)
    assert len(p.poll()) == 1
    old_receipt = p.receipt()
    assert old_receipt["head"] == old
    p.ctx.run.reload()
    scheduled(p)
    assert not p.merges and not p.verified

    assert main(["run", "c"]) == 0
    p.ctx.run.reload()
    ready = p.status()
    merged = p.ctx.head()
    assert ready["head"] == merged != old and ready["pause"]["reason"] == "ready_to_publish"
    assert sh(p.ctx.repo, "git", "show", "-s", "--format=%P", merged).split() == [old, base]
    assert p.ctx.run.state.base_sync["verified_head"] == merged
    assert not p.ctx.run.state.base_sync["retry_verify"]
    assert p.merges == p.verified == [merged]
    assert p.receipt() == old_receipt and not p.pushes and not p.gh.comments
    checks = [f"- [ ] `{sid}`: {why}" for sid, why in sorted(p.ctx.m.coverage_exempt.items())]
    assert all(check in ready["pause"]["detail"] for check in checks)

    # Inspect the durable M receipt before any external publication effect.
    real_push, captured = cli._push, []

    def push(*args):
        receipt = p.receipt()
        assert (receipt["change"], receipt["head"], receipt["pr"]) == ("c", merged, p.gh.pr_url)
        assert receipt["checks"] == ready["pause"]["detail"]
        assert receipt["round_id"] != old_receipt["round_id"]
        assert not receipt.get("delivered") and "result" not in receipt
        captured.append(receipt)
        return real_push(*args)

    monkeypatch.setattr(cli, "_push", push)
    comment_attempts = []

    def interrupted_comment(args):
        if args[:2] == ["pr", "comment"] and "<!-- nightshift:publish:" in args[-1]:
            comment_attempts.append(args)
            if failure == "comment-after":
                p.gh(args)  # GitHub accepted the comment before the transport failed.
            raise queue.GhError("comment delivery interrupted", transient=True)
        return p.gh(args)

    p.lose_published_response = failure == "publish-response"
    errors = p.poll(gh_runner=interrupted_comment)
    assert len(errors) == 1
    assert ("stable publish response lost" if failure == "publish-response"
            else "comment delivery interrupted") in str(errors[0])
    p.ctx.run.reload()
    status = p.status()
    assert status["head"] == merged and status["pause"]["reason"] == "human_checks"
    assert p.ctx.run.state.pause.reason == "human_checks"
    assert p.push_attempts == p.pushes == [merged]
    assert sh(p.env.origin, "git", "rev-parse", "spec/c") == merged
    receipt = p.receipt()
    assert len(captured) == 1
    assert all(receipt[key] == value for key, value in captured[0].items())
    assert not receipt.get("delivered")
    if failure == "publish-response":
        assert "result" not in receipt and not comment_attempts
    else:
        assert receipt["result"] == {"version": 1, "outcome": "published", "pr": p.gh.pr_url}
        assert len(comment_attempts) == 1
    assert len(p.gh.comments) == (1 if failure == "comment-after" else 0)
    accepted = [dict(comment) for comment in p.gh.comments]
    operations = list(p.operations)
    assert operations == [("fetch", 20), ("fetch", 300), ("merge", merged),
                          ("verify", merged), ("fetch", 20), ("push", merged)]
    assert p.calls.count("publish") == 2  # One handoff, then one stable publication.

    # New polls reload CLI state and the on-disk receipt, just as after restart.
    for _ in range(2):
        p.ctx.run.reload()
        calls, gh_calls = len(p.calls), len(p.gh.calls)
        assert p.poll() == []
        assert p.calls[calls:] == ["status"]  # Delivery must never invoke publish again.
        delivered = p.receipt()
        assert all(delivered[key] == value for key, value in captured[0].items())
        assert delivered["delivered"]
        assert delivered["result"]["outcome"] == "published"
        assert delivered["result"]["pr"] == p.gh.pr_url
        assert p.operations == operations and p.merges == p.verified == p.pushes == [merged]
        assert p.fetches == [20, 300, 20]
        assert len(p.gh.publication_comments) == 1
        assert len(p.gh.readiness_comments) == int(_ == 1)
        if accepted:
            assert p.gh.publication_comments == accepted  # Reconcile the accepted comment's ID and body.
        body = p.gh.publication_comments[0]["body"]
        assert all(check in body for check in checks)
        assert "hosted CI" in body and p.gh.pr_url in body and merged[:12] in body
        assert f"<!-- nightshift:publish:c:{receipt['round_id']} -->" in body
        assert old_receipt["round_id"] not in body and old[:12] not in body
        assert "specloop publish c" not in body and "specloop finish c" in body
        if _ == 0 and failure == "comment-after":
            patches = [call for call in p.gh.calls[gh_calls:] if "PATCH" in call]
            assert len(patches) == 1
            assert patches[0][1] == f"repos/owner/sample/issues/comments/{accepted[0]['id']}"
        if _ == 1:
            assert [call[:2] for call in p.gh.calls[gh_calls:]] == [
                ["pr", "view"], ["pr", "checks"], ["pr", "edit"], ["api", "repos/owner/sample/issues/8/comments"],
                ["pr", "comment"]]
    gh_calls = len(p.gh.calls)
    assert p.poll() == []
    assert p.gh.calls[gh_calls:] == [["pr", "view", "8", "--repo", "owner/sample", "--json",
                                    "state,labels,headRefName,headRefOid,isCrossRepository,url,body"]]
    creates = [call for call in p.gh.calls if call[:2] == ["pr", "comment"]
               and "<!-- nightshift:publish:" in call[-1]]
    assert len(creates) == 1
    assert len([call for call in p.gh.calls if call[:2] == ["pr", "comment"]
                and "<!-- nightshift:approval:c:merge-readiness -->" in call[-1]]) == 1
    events = [json.loads(line) for line in (p.ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert [event["head"] for event in events if event["event"] == "published"] == [merged]


@pytest.mark.scenario("publish-base-sync/recover-publication-comment-after-integration/published-response-is-lost")
def test_lost_stable_publish_response_recovers_merged_receipt(publication, monkeypatch):
    recover_merged_publication(publication, monkeypatch, "publish-response")


@pytest.mark.scenario("publish-base-sync/recover-publication-comment-after-integration/comment-delivery-is-interrupted")
@pytest.mark.parametrize("failure", ["comment-before", "comment-after"])
def test_interrupted_comment_recovers_merged_receipt(publication, monkeypatch, failure):
    recover_merged_publication(publication, monkeypatch, failure)


@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/handoff-survives-interruption")
@pytest.mark.parametrize("boundary", ["before-save", "after-save", "lost-response"])
def test_handoff_restart_keeps_receipt_and_schedules_driver(publication, monkeypatch, boundary):
    p = publication
    old = p.ctx.head()
    base = advance_base(p.env)
    before = asdict(state.Run.for_change("c").state)
    real_save = state.Run.save

    def interrupted_save(run):
        assert run.state.status == "running" and run.state.pause is None
        assert p.receipt()["head"] == old  # Receipt was durable before publication began.
        if boundary == "after-save":
            real_save(run)
        raise Interrupted()

    with monkeypatch.context() as crash:
        if boundary == "lost-response":
            p.lose_response = True
        else:
            crash.setattr(state.Run, "save", interrupted_save)
        assert len(p.poll()) == 1
    old_receipt = p.receipt()
    if boundary == "before-save":
        assert asdict(state.Run.for_change("c").state) == before
        assert serve.pass_once(lambda _: None, spawn=lambda _: pytest.fail("ready run scheduled")) == []
        assert len(p.poll()) == 1
        assert p.receipt() == old_receipt
    assert asdict(state.Run.for_change("c").state) == {**before, "status": "running", "pause": None}
    assert p.ctx.head() == old and not p.merges and not p.verified
    scheduled(p)
    finish(p, old_receipt)
    assert p.fetches == [20] * (2 if boundary == "before-save" else 1) + [300, 20]
    assert len(p.merges) == len(p.verified) == 1
    assert sh(p.ctx.repo, "git", "show", "-s", "--format=%P", p.ctx.head()).split() == [old, base]


@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/handoff-survives-interruption")
@pytest.mark.scenario("publish-base-sync/recover-pending-verification/interrupted-integration")
@pytest.mark.parametrize("boundary", ["intent", "advance", "success"])
@pytest.mark.parametrize("eligible_publish", [False, True])
def test_driver_restart_retains_obligation_even_when_base_is_incorporated(
        publication, monkeypatch, boundary, eligible_publish):
    p = publication
    old, base = p.ctx.head(), advance_base(p.env)
    assert len(p.poll()) == 1
    old_receipt = p.receipt()
    scheduled(p)
    real_git, real_save = loop.git, state.Run.save

    def interrupted_git(repo, *args, **kwargs):
        if args[0] == "update-ref" and boundary == "intent":
            raise Interrupted()
        result = real_git(repo, *args, **kwargs)
        if args[0] == "update-ref" and boundary == "advance":
            raise Interrupted()
        return result

    def interrupted_save(run):
        if boundary == "success" and run.state.base_sync.get("verified_head"):
            raise Interrupted()
        real_save(run)

    with monkeypatch.context() as crash:
        crash.setattr(loop, "git", interrupted_git)
        crash.setattr(state.Run, "save", interrupted_save)
        with pytest.raises(Interrupted):
            main(["run", "c"])
    run = state.Run.for_change("c", require_existing=True)
    intent = run.state.base_sync
    merged = intent["merged_head"]
    assert intent["previous_head"] == old and intent["base"] == base
    assert intent["retry_verify"] and intent["verified_head"] == ""
    assert p.ctx.head() == (old if boundary == "intent" else merged)
    retained = {path: path.read_bytes() for path in Path(intent["receipts"][0]).iterdir()}
    scheduled(p)
    assert p.status()["head"] == p.ctx.head()
    # Also exercise an eligible publish carrying the outstanding intent: ancestry
    # at M must not authorize it, nor may the CLI install the saved M from H.
    if eligible_publish:
        run.pause("ready_to_publish", detail=old_receipt["checks"])
        assert p.publish().returncode == 1
        assert p.receipt() == old_receipt
        assert state.Run.for_change("c").state.base_sync == intent
        assert p.ctx.head() == (old if boundary == "intent" else merged)
        scheduled(p)
    finish(p, old_receipt)
    assert p.fetches == [20, 300] + ([20] if eligible_publish else []) + [300, 20]
    assert p.merges == [merged] and p.ctx.head() == merged
    assert p.verified == [merged] * (2 if boundary == "success" else 1)
    assert all(path.read_bytes() == contents for path, contents in retained.items())
    assert len(state.Run.for_change("c").state.base_sync["receipts"]) == 2


@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/base-moves-on-successive-attempts")
@pytest.mark.scenario("publish-base-sync/fetch-current-base/publish-fetches-again")
@pytest.mark.parametrize("push_failure", [False, True], ids=["repeated-advance", "push-failure-then-advance"])
def test_successive_base_advances_publish_only_latest_verified_receipt(publication, push_failure):
    p = publication
    initial_head = p.ctx.head()
    heads, observed_bases, stale_receipts, verification_receipts = [], [], [], {}
    expected_fetches, expected_operations = [], []

    for attempt in range(3):
        ready = p.status()
        old = p.ctx.head()
        before = asdict(state.Run.for_change("c").state)
        assert ready["head"] == old and ready["pause"]["reason"] == "ready_to_publish"
        observed_base = advance_base(p.env, f"publish observes base {attempt}\n")
        observed_bases.append(observed_base)

        # Every new ready head gets its own receipt before the CLI hands off.
        assert len(p.poll()) == 1
        expected_fetches.append(20)
        expected_operations.append(("fetch", 20))
        assert p.fetches == expected_fetches and p.operations == expected_operations
        assert sh(p.ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == observed_base
        assert p.ctx.head() == old
        assert asdict(state.Run.for_change("c").state) == {**before, "status": "running", "pause": None}
        receipt = p.receipt()
        assert (receipt["change"], receipt["head"], receipt["pr"]) == ("c", old, p.gh.pr_url)
        assert receipt["checks"] == ready["pause"]["detail"]
        assert all(receipt["round_id"] != prior["round_id"] for prior in stale_receipts)
        stale_receipts.append(receipt)
        scheduled(p)
        assert p.operations == expected_operations  # Running polls do no preparation or publication.

        # Move again while scheduled: preparation must refetch, not integrate
        # the CLI's snapshot, and it must verify that latest merge in the driver.
        latest_base = advance_base(p.env, f"driver observes newer base {attempt}\n")
        if attempt == 2:
            finish(p, receipt)
        else:
            assert main(["run", "c"]) == 0
        merged = p.ctx.head()
        heads.append(merged)
        expected_fetches.append(300)
        expected_operations.extend([("fetch", 300), ("merge", merged), ("verify", merged)])
        if attempt == 2:
            expected_fetches.append(20)
            expected_operations.extend([("fetch", 20), ("push", merged)])
        assert p.fetches == expected_fetches and p.operations == expected_operations
        assert sh(p.ctx.repo, "git", "show", "-s", "--format=%P", merged).split() == [old, latest_base]
        assert sh(p.ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == latest_base
        assert p.merges == p.verified == heads
        saved = state.Run.for_change("c").state
        assert saved.base_sync["base"] == latest_base
        assert saved.base_sync["verified_head"] == merged and not saved.base_sync["retry_verify"]
        assert len(saved.base_sync["receipts"]) == attempt + 1
        for directory in saved.base_sync["receipts"]:
            path = Path(directory) / "receipts.json"
            contents = path.read_bytes()
            assert contents == verification_receipts.setdefault(path, contents)

        if attempt < 2:
            status = p.status()
            assert status["head"] == merged and status["pause"]["reason"] == "ready_to_publish"
            assert p.receipt() == receipt and not p.pushes and not p.gh.comments
            assert all(f"- [ ] `{sid}`: {why}" in status["pause"]["detail"]
                       for sid, why in p.ctx.m.coverage_exempt.items())

        if attempt == 0 and push_failure:
            before_push = asdict(saved)
            p.fail_push = True
            assert len(p.poll()) == 1
            expected_fetches.append(20)
            expected_operations.extend([("fetch", 20), ("push", merged)])
            assert p.fetches == expected_fetches and p.operations == expected_operations
            assert asdict(state.Run.for_change("c").state) == before_push
            assert p.ctx.head() == merged and p.merges == p.verified == heads
            failed_receipt = p.receipt()
            assert failed_receipt["head"] == merged
            assert failed_receipt["checks"] == status["pause"]["detail"]
            assert failed_receipt["round_id"] != receipt["round_id"]
            assert not failed_receipt.get("delivered") and "result" not in failed_receipt
            assert not p.pushes and not p.gh.comments
            assert sh(p.env.origin, "git", "for-each-ref", "refs/heads/spec/c") == ""
        elif attempt == 1 and push_failure:
            # The handoff reused the failed attempt's still-matching receipt;
            # only readiness at another head authorizes its replacement.
            assert receipt == failed_receipt

    assert p.push_attempts == ([heads[0]] if push_failure else []) + [heads[-1]]
    assert sh(p.ctx.repo, "git", "rev-list", "--merges", f"{initial_head}..{heads[-1]}").splitlines() == heads[::-1]
    assert sh(p.env.src, "git", "rev-parse", "release/stable") == p.env.initial
    assert sh(p.env.src, "git", "rev-parse", "origin/release/stable") == p.env.initial
    delivered = p.receipt()
    assert delivered["result"]["outcome"] == "published"
    assert delivered["head"] == heads[-1]
    assert delivered["result"]["pr"] == delivered["pr"] == p.gh.pr_url
    body = p.gh.publication_comments[0]["body"]
    assert heads[-1][:12] in body
    assert all(prior["round_id"] not in body and prior["head"][:12] not in body for prior in stale_receipts)
    events = [json.loads(line) for line in (p.ctx.run.dir / "events.jsonl").read_text().splitlines()]
    handoffs = [event for event in events if event["event"] == "publish_preparation_scheduled"]
    assert [event["head"] for event in handoffs] == [initial_head, *heads[:-1]]
    assert [event["base"] for event in handoffs] == observed_bases
    assert len(handoffs) == len(heads) == 3


def fail_publication_fetch(p, monkeypatch, failure):
    """Fail the real transport boundary immediately, retaining the stale ref."""
    if failure == "missing-base":
        sh(p.env.origin, "git", "update-ref", "-d", "refs/heads/release/stable")
    real_run, attempts = subprocess.run, []

    def transport(args, **kwargs):
        if "fetch" in args and args[-1].endswith(":refs/specloop/publish-base"):
            assert kwargs["timeout"] == 20
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
            assert args[-1] == "+refs/heads/release/stable:refs/specloop/publish-base"
            attempts.append(args)
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 20, stderr=b"transport stalled")
            if failure == "transport":
                raise subprocess.CalledProcessError(128, args, stderr="transport unavailable")
            if failure == "os-error":
                raise OSError("transport could not start")
        return real_run(args, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", transport)
    return attempts


def publication_command(mode):
    return (["act", "c", "publish", "--json"] if mode == "act"
            else ["publish", "c", *(["--json"] if mode == "json" else [])])


def retry_explanation(output, mode):
    assert "Traceback" not in output.err
    if mode == "plain":
        assert output.out == ""
        return output.err
    document = json.loads(output.out)  # Rejects extra documents or stray stdout.
    if mode == "json":
        assert set(document) == {"version", "error"} and document["version"] == 1
        return document["error"]
    assert set(document) == {"version", "outcome", "verb", "detail"}
    assert (document["version"], document["outcome"], document["verb"]) == (1, "refused", "publish")
    return document["detail"]


@pytest.mark.scenario("publish-base-sync/fetch-current-base/publish-fetch-fails-retryably")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-preparation-pauses")
@pytest.mark.parametrize("failure", ["timeout", "transport", "missing-base", "os-error"])
@pytest.mark.parametrize("mode", ["json", "plain", "act"])
@pytest.mark.parametrize("pending", [False, True])
def test_publish_fetch_failure_retains_readiness(publication, monkeypatch, capfd, failure, mode, pending):
    p = publication
    run = state.Run.for_change("c")
    head = p.ctx.head()
    if pending:
        run.state.base_sync = {"base": p.env.initial, "previous_head": head, "merged_head": head,
                               "verified_head": "", "retry_verify": True, "receipts": ["saved attempt"]}
        run.save()
    before = (run.dir / "state.json").read_bytes()
    events = (run.dir / "events.jsonl").read_bytes()
    attempts = fail_publication_fetch(p, monkeypatch, failure)
    forbid_publication(monkeypatch)

    def stale_fallback(*args):
        pytest.fail("a failed fetch must not evaluate stale ancestry or schedule preparation")

    monkeypatch.setattr(loop, "_publish_needs_prepare", stale_fallback)
    capfd.readouterr()
    publish_without_preparing(monkeypatch, publication_command(mode), 1)
    explanation = retry_explanation(capfd.readouterr(), mode)
    diagnostic = {"timeout": "timed out after 20s", "transport": "transport unavailable",
                  "missing-base": "couldn't find remote ref", "os-error": "transport could not start"}
    assert diagnostic[failure] in explanation and "refs/heads/release/stable" in explanation
    assert "Retry `specloop publish c`" in explanation and "nothing was published" in explanation
    assert len(attempts) == 1
    assert (run.dir / "state.json").read_bytes() == before
    assert (run.dir / "events.jsonl").read_bytes() == events
    assert p.ctx.head() == head and not p.pushes and not p.merges and not p.verified
    assert sh(p.ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == p.env.initial
    status = p.status()
    assert status["pr"] == p.gh.pr_url and status["pause"]["reason"] == "ready_to_publish"
    assert status["pause"]["detail"] == run.state.pause.detail


@pytest.mark.scenario("publish-base-sync/fetch-current-base/publish-fetch-fails-retryably")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-preparation-pauses")
@pytest.mark.parametrize("failure", ["timeout", "transport", "missing-base"])
def test_daemon_retries_fetch_without_refusal_or_delivery(publication, monkeypatch, failure):
    p = publication
    ready = p.status()
    before = (p.ctx.run.dir / "state.json").read_bytes()
    p.expected_error = "refs/heads/release/stable"
    with monkeypatch.context() as failing:
        attempts = fail_publication_fetch(p, failing, failure)
        forbid_publication(failing)
        for count in (1, 2):
            assert len(p.poll()) == 1
            assert len(attempts) == count  # Each poll retries once; no inline retry.
            assert (p.ctx.run.dir / "state.json").read_bytes() == before
            receipt = p.receipt()
            assert receipt["head"] == ready["head"] and receipt["checks"] == ready["pause"]["detail"]
            assert receipt["pr"] == p.gh.pr_url
            assert not receipt.get("delivered") and "result" not in receipt
            assert not p.gh.comments and not p.gh.labels and not p.pushes
    if failure == "missing-base":
        sh(p.env.origin, "git", "update-ref", "refs/heads/release/stable", p.env.initial)
    assert p.poll() == []
    delivered = p.receipt()
    assert delivered["delivered"] and delivered["result"]["outcome"] == "published"
    assert delivered["head"] == ready["head"] and delivered["checks"] == ready["pause"]["detail"]
    assert p.pushes == [ready["head"]] and len(p.gh.comments) == 1
    assert not p.merges and not p.verified


@pytest.mark.scenario("publish-base-sync/preserve-publication-contracts/wrong-publication-target")
@pytest.mark.parametrize("target", ["absent", "mismatch"])
@pytest.mark.parametrize("mode", ["json", "act"])
def test_target_refusal_precedes_fetch(publication, monkeypatch, capfd, target, mode):
    p = publication
    if target == "absent":
        path = p.ctx.run.manifest_path
        path.write_text(path.read_text().replace('github = "owner/sample"', 'github = ""'))
        expected = "manifest names no GitHub repository"
    else:
        sh(p.env.src, "git", "remote", "set-url", "origin", "https://github.com/other/repository.git")
        expected = "manifest publishes to owner/sample"
    before = (p.ctx.run.dir / "state.json").read_bytes()
    head = p.ctx.head()
    forbid_publication(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("target refusal must precede fetching")

    monkeypatch.setattr(loop, "_fetch_publish_base", forbidden)
    capfd.readouterr()
    publish_without_preparing(monkeypatch, publication_command(mode), 1)
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1 and document["outcome"] == "refused"
    if mode == "json":
        assert set(document) == {"version", "outcome", "reasons"}
        assert len(document["reasons"]) == 1 and expected in document["reasons"][0]
    else:
        assert set(document) == {"version", "outcome", "verb", "detail"}
        assert document["verb"] == "publish" and expected in document["detail"]
    assert (p.ctx.run.dir / "state.json").read_bytes() == before
    assert p.ctx.head() == head and not p.fetches


@pytest.mark.scenario("publish-base-sync/preserve-publication-contracts/driver-pause-blocks-publication")
@pytest.mark.parametrize("reason", ["base_conflict", "base_drift"])
def test_driver_pause_diagnostics_survive_publication_refusal(publication, monkeypatch, capfd, reason):
    p, ctx = publication, publication.ctx
    ctx.run.reload()
    if reason == "base_conflict":
        old = ctx.head()
        repaired = loop.seal.commit_files(ctx.repo, old, {"base.txt": "run change\n"}, "run change")
        loop.git(ctx.repo, "update-ref", ctx.ref, repaired, old)
    head = ctx.head()
    base = advance_base(p.env)
    ctx.run.state.status, ctx.run.state.pause = "running", None
    ctx.run.save()
    checked = controlled_verify(monkeypatch, ctx, exit_code=1)
    forbid_publication(monkeypatch)
    with ctx.run.lock() as mine:
        assert mine and loop.run(ctx) == "paused"
    status = p.status()
    assert status["pause"]["reason"] == reason and status["pr"] == p.gh.pr_url
    detail = status["pause"]["detail"]
    if reason == "base_conflict":
        assert "base.txt" in detail and ctx.head() == head and not checked
    else:
        assert base in detail and ctx.head() in detail and ctx.head() != head
        assert "ok: expected exit 0, got 1" in detail
        for name in ("receipts.json", "ok.log"):
            path = checked[0][1] / name
            assert str(path) in detail and path.is_file()
    before = (ctx.run.dir / "state.json").read_bytes()
    events = (ctx.run.dir / "events.jsonl").read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("non-ready publication must not fetch or validate a target")

    monkeypatch.setattr(loop, "_fetch_publish_base", forbidden)
    monkeypatch.setattr(cli, "_remote", forbidden)
    for mode in ("json", "act"):
        capfd.readouterr()
        publish_without_preparing(monkeypatch, publication_command(mode), 1)
        document = json.loads(capfd.readouterr().out)
        assert document == ({"version": 1, "outcome": "refused", "reasons": ["c is not ready to publish"]}
                            if mode == "json" else {"version": 1, "outcome": "refused", "verb": "publish",
                                                    "detail": "publish is not allowed for c's current pause"})
        assert (ctx.run.dir / "state.json").read_bytes() == before
        assert (ctx.run.dir / "events.jsonl").read_bytes() == events
        assert p.status()["pause"]["detail"] == detail


@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/moved-base-schedules-preparation")
@pytest.mark.parametrize("mode", ["json", "plain", "act"])
def test_handoff_cli_formats_preserve_checks_until_driver_readiness(publication, monkeypatch, capfd, mode):
    p = publication
    head = p.ctx.head()
    before = asdict(state.Run.for_change("c").state)
    advance_base(p.env)
    capfd.readouterr()
    with monkeypatch.context() as guard:
        forbid_publication(guard)
        publish_without_preparing(guard, publication_command(mode), 1)
    explanation = retry_explanation(capfd.readouterr(), mode)
    assert all(text in explanation for text in ("scheduled", "nothing was published", "Retry", "readiness"))
    assert p.ctx.head() == head and p.operations == [("fetch", 20)]
    assert asdict(state.Run.for_change("c").state) == {**before, "status": "running", "pause": None}
    selected = []
    assert serve.pass_once(lambda _: None, spawn=selected.append) == selected == ["c"]
    finish(p, {"head": head, "round_id": "no prior publication"})
    assert p.fetches == [20, 300, 20] and p.merges == p.verified == p.pushes
