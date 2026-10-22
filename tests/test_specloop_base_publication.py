"""Publication polls and supervisor restarts over real local Git and CLI state."""

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import queue, specloop_intake
from nightshift.config import Repo
from specloop import cli, loop, serve, state
from test_specloop_approval import ApprovalGh
from test_specloop_base_sync import advance_base, base_env, sh


class Interrupted(BaseException):
    """Process death must bypass the driver's ordinary infrastructure handler."""


@pytest.fixture
def publication(base_env, tmp_path, monkeypatch, capfd):
    env, ctx = base_env, base_env.ctx
    gh = ApprovalGh()
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
        expected_error = "injected push transport failure" if p.fail_push else "scheduled"
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

    def poll():
        errors = []
        specloop_intake.publish_pending(repo, queue.Labels(), tmp_path, runner=gh,
                                       specloop_runner=runner, on_error=lambda n, e: errors.append(e))
        assert not gh.labels
        assert not any("--add-label" in call for call in gh.calls)
        assert not any("Publication refused" in c["body"] for c in gh.comments)
        return errors

    p.poll = poll
    p.status = lambda: json.loads(runner(["specloop", "status", "--json"]).stdout)["runs"][0]
    p.publish = lambda: runner(["specloop", "publish", "c", "--json"], timeout=30)
    assert cli.main(["run", "c"]) == 0
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
    assert cli.main(["run", "c"]) == 0
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
    assert len(p.gh.comments) == 1
    body = p.gh.comments[0]["body"]
    assert all(check in body for check in checks) and "hosted CI" in body
    assert receipt["round_id"] in body and old_receipt["round_id"] not in body
    assert "specloop publish c" not in body and "specloop finish c" in body
    publishes = p.calls.count("publish")
    assert p.poll() == [] and p.calls.count("publish") == publishes
    assert len(p.gh.comments) == 1
    events = [json.loads(line) for line in (p.ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert [e["head"] for e in events if e["event"] == "published"] == [merged]


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
            cli.main(["run", "c"])
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
            assert cli.main(["run", "c"]) == 0
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
    body = p.gh.comments[0]["body"]
    assert heads[-1][:12] in body
    assert all(prior["round_id"] not in body and prior["head"][:12] not in body for prior in stale_receipts)
    events = [json.loads(line) for line in (p.ctx.run.dir / "events.jsonl").read_text().splitlines()]
    handoffs = [event for event in events if event["event"] == "publish_preparation_scheduled"]
    assert [event["head"] for event in handoffs] == [initial_head, *heads[:-1]]
    assert [event["base"] for event in handoffs] == observed_bases
    assert len(handoffs) == len(heads) == 3
