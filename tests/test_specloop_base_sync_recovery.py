"""Additive integration state and legacy run compatibility."""

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from specloop import cli, loop, revise, seal, state
from test_specloop_base_sync import advance_base, base_env, commit, forbid_publication, sh


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/legacy-state")
@pytest.mark.parametrize("moved", [False, True])
@pytest.mark.parametrize("unknown_fields", [False, True])
def test_legacy_state_uses_fresh_base_and_preserves_run(base_env, moved, unknown_fields):
    ctx = base_env.ctx
    raw = {
        "change": "c", "status": "paused",
        "pause": {"reason": "requested", "detail": "keep pause", "resume_at": 123},
        "tasks": {"1.1": {"status": "passed", "attempts": 2}},
        "gates_passed": [1], "pr": "https://example.test/pull/17",
    }
    if unknown_fields:
        raw.update(future_state={"value": True})
        raw["pause"]["future_pause"] = True
        raw["tasks"]["1.1"]["future_task"] = True
    (ctx.run.dir / "state.json").write_text(json.dumps(raw))
    ctx.run = state.Run(ctx.run.dir, require_existing=True)
    assert ctx.run.state.base_sync == {}
    previous = ctx.run.state
    head = ctx.head()
    expected_base = advance_base(base_env) if moved else base_env.initial
    fetched = loop._fetch_publish_base(ctx, timeout=300)
    assert fetched == expected_base
    assert loop._is_ancestor(ctx.repo, fetched, head) is not moved
    merged = loop._merge_publish_base(ctx, fetched, head)
    assert merged and (merged != head) == moved
    ctx.run.save()
    ctx.run.reload()
    assert ctx.run.state == previous
    assert ctx.run.state.pause == state.Pause("requested", detail="keep pause", resume_at=123)
    assert ctx.run.state.tasks["1.1"] == state.TaskState(status="passed", attempts=2)
    assert ctx.run.state.pr == raw["pr"] and ctx.run.state.gates_passed == [1]


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/legacy-state")
def test_base_sync_intent_round_trips_with_unknown_keys(base_env):
    run = base_env.ctx.run
    intent = {"base": "B", "previous_head": "H", "merged_head": "M", "verified_head": "",
              "receipts": ["final/base-sync/attempt-1"], "retry_verify": True, "future_key": {"v": 1}}
    run.state.base_sync = intent
    run.save()
    loaded = state.Run(run.dir, require_existing=True)
    assert loaded.state.base_sync == intent
    loaded.save()
    run.reload()
    assert run.state.base_sync == intent
    first, second = state.RunState("one"), state.RunState("two")
    first.base_sync["retry_verify"] = True
    assert second.base_sync == {}


class Interrupted(BaseException):
    """Simulate process death, bypassing the driver's infrastructure handler."""


def verification(monkeypatch, ctx, *, passing=True):
    calls = []

    def check(repo, head, steps, m, out):
        saved = state.Run(ctx.run.dir, require_existing=True).state.base_sync
        assert head == ctx.head() and saved["verified_head"] == ""
        assert saved["retry_verify"] and str(out) in saved["receipts"]
        assert loop._is_ancestor(repo, saved["base"], head)
        receipt = loop.verify.Receipt("ok", ["check"], 0, 0 if passing else 1, passing, 0.1, "check")
        (out / "receipts.json").write_text(json.dumps([asdict(receipt)]))
        (out / "ok.log").write_text(f"checked {head}\n")
        calls.append((head, out))
        return [receipt]

    monkeypatch.setattr(loop.verify, "run_steps", check)
    forbid_publication(monkeypatch)
    return calls


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/interrupted-integration")
@pytest.mark.parametrize("boundary", ["intent", "advance", "success"])
@pytest.mark.parametrize("base_moves_again", [False, True])
def test_restart_reconciles_intent_before_verifying_latest_base(
        base_env, monkeypatch, boundary, base_moves_again):
    ctx = base_env.ctx
    old, base = ctx.head(), advance_base(base_env)
    calls = verification(monkeypatch, ctx)
    real_git, real_save, real_merge, real_fetch = loop.git, ctx.run.save, loop._merge_commits, loop._fetch_publish_base
    merges, fetches = [], []

    def merge(repo, head, fetched):
        merged = real_merge(repo, head, fetched)
        merges.append(merged)
        return merged

    def fetch(*args, **kwargs):
        fetched = real_fetch(*args, **kwargs)
        fetches.append(fetched)
        return fetched

    def crash_update(repo, *args, **kwargs):
        if args[0] == "update-ref" and boundary == "intent":
            raise Interrupted()
        result = real_git(repo, *args, **kwargs)
        if args[0] == "update-ref" and boundary == "advance":
            raise Interrupted()
        return result

    def crash_save():
        if boundary == "success" and ctx.run.state.base_sync.get("verified_head"):
            raise Interrupted()
        real_save()

    monkeypatch.setattr(loop, "_merge_commits", merge)
    monkeypatch.setattr(loop, "_fetch_publish_base", fetch)
    with monkeypatch.context() as crash:
        crash.setattr(loop, "git", crash_update)
        crash.setattr(ctx.run, "save", crash_save)
        with pytest.raises(Interrupted):
            loop.prepare_publish(ctx)
    ctx.run = state.Run(ctx.run.dir, require_existing=True)
    sync = ctx.run.state.base_sync
    intended = sync["merged_head"]
    assert sync["previous_head"] == old and sync["base"] == base
    assert sync["retry_verify"] and sync["verified_head"] == ""
    assert ctx.head() == (old if boundary == "intent" else intended)
    assert len(calls) == (1 if boundary == "success" else 0)
    retained = {p: p.read_bytes() for p in Path(sync["receipts"][0]).iterdir()}
    latest = advance_base(base_env, "newer upstream\n") if base_moves_again else base
    assert loop.prepare_publish(ctx)
    ctx.run.reload()
    sync = ctx.run.state.base_sync
    assert fetches == [base, latest]
    assert len(merges) == (2 if base_moves_again else 1)
    assert ctx.head() == merges[-1] and sync["verified_head"] == ctx.head()
    assert sync["base"] == latest and not sync["retry_verify"]
    assert loop._is_ancestor(ctx.repo, intended, ctx.head())
    assert calls[-1][0] == ctx.head()
    assert len(sync["receipts"]) == 2 and len(set(sync["receipts"])) == 2
    assert all(p.read_bytes() == contents for p, contents in retained.items())
    checked = len(calls)
    assert loop.prepare_publish(ctx) and len(calls) == checked
    assert fetches == [base, latest, latest]


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/interrupted-integration")
def test_recovery_rejects_unrelated_head_without_replacing_intent(base_env, monkeypatch):
    ctx = base_env.ctx
    old, base = ctx.head(), advance_base(base_env)
    loop._fetch_publish_base(ctx, timeout=300)
    intended = loop._merge_commits(ctx.repo, old, base)
    ctx.run.state.base_sync = dict(base=base, previous_head=old, merged_head=intended,
                                   verified_head="", retry_verify=True)
    other = seal.commit_files(ctx.repo, old, {"out/other": "unrelated\n"}, "other writer")
    seal.git(ctx.repo, "update-ref", ctx.ref, other, old)
    ctx.run.save()
    ctx.run.reload()
    intent = dict(ctx.run.state.base_sync)
    calls = verification(monkeypatch, ctx)
    assert not loop.prepare_publish(ctx)
    ctx.run.reload()
    assert ctx.head() == other and calls == [] and ctx.run.state.base_sync == intent
    assert ctx.run.state.pause.reason == "infra" and ctx.run.state.pause.resume_at == 0
    assert intended in ctx.run.state.pause.detail and other in ctx.run.state.pause.detail


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/repair-against-unchanged-base")
@pytest.mark.parametrize("when", ["during-verification", "after-success"])
def test_receipts_only_authorize_the_exact_verified_head(base_env, monkeypatch, when):
    ctx = base_env.ctx
    advance_base(base_env)
    calls = verification(monkeypatch, ctx)
    real_verify = loop.verify.run_steps

    def advance():
        head = ctx.head()
        descendant = seal.commit_files(ctx.repo, head, {"out/new": "changed\n"}, "changed head")
        seal.git(ctx.repo, "update-ref", ctx.ref, descendant, head)

    def change_during_verify(*args):
        receipts = real_verify(*args)
        advance()
        return receipts

    if when == "during-verification":
        monkeypatch.setattr(loop.verify, "run_steps", change_during_verify)
        assert not loop.prepare_publish(ctx)
        assert ctx.run.state.pause.reason == "infra"
        assert ctx.run.state.base_sync["verified_head"] == ""
    else:
        assert loop.prepare_publish(ctx)
        advance()
    ctx.run.reload()
    assert ctx.run.state.base_sync["verified_head"] != ctx.head()
    monkeypatch.setattr(loop.verify, "run_steps", real_verify)
    assert loop.prepare_publish(ctx)
    ctx.run.reload()
    assert len(calls) == 2 and calls[0][0] != calls[1][0] == ctx.head()
    assert calls[0][1] != calls[1][1]
    assert ctx.run.state.base_sync["verified_head"] == ctx.head()


def resume(base_env, monkeypatch):
    ctx = base_env.ctx
    monkeypatch.setattr(cli, "_ctx", lambda run: loop.Ctx(run, ctx.m, {}))
    assert cli.resume(SimpleNamespace(change="c", note="", no_run=True)) == 0
    ctx.run = state.Run(ctx.run.dir, require_existing=True)
    assert ctx.run.state.status == "running" and ctx.run.state.pause is None


def checkout_run(base_env):
    ctx = base_env.ctx
    sh(base_env.src, "git", "fetch", str(ctx.repo), ctx.ref)
    sh(base_env.src, "git", "merge", "--ff-only", "FETCH_HEAD")


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/repair-against-unchanged-base")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-preparation-pauses")
@pytest.mark.parametrize("repair", [False, True])
def test_resume_drift_rechecks_unchanged_or_repaired_head(base_env, monkeypatch, repair):
    ctx = base_env.ctx
    head = ctx.head()
    base = advance_base(base_env)
    verification(monkeypatch, ctx, passing=False)
    assert not loop.prepare_publish(ctx)
    merged = ctx.head()
    assert sh(ctx.repo, "git", "show", "-s", "--format=%P", merged).split() == [head, base]
    pause = ctx.run.state.pause
    assert pause.reason == "base_drift" and base in pause.detail and merged in pause.detail
    ctx.run.state.pr = "https://example.test/pull/17"
    ctx.run.save()
    old_receipt = Path(ctx.run.state.base_sync["receipts"][0]) / "receipts.json"
    assert str(old_receipt) in pause.detail
    assert str(old_receipt.parent / "ok.log") in pause.detail
    assert (old_receipt.parent / "ok.log").is_file()
    assert "specloop resume c" in pause.detail
    failed = old_receipt.read_bytes()
    if repair:
        checkout_run(base_env)
        commit(base_env.src, "out/repair", "fixed\n")
    resume(base_env, monkeypatch)
    repaired = ctx.head()
    assert (repaired != merged) == repair
    calls = verification(monkeypatch, ctx, passing=repair)
    assert loop.prepare_publish(ctx) is repair
    ctx.run.reload()
    sync = ctx.run.state.base_sync
    assert calls[0][0] == repaired and ctx.head() == repaired
    assert sync["base"] == base and sync["merged_head"] == merged
    assert sync["verified_head"] == (repaired if repair else "")
    assert sync["retry_verify"] is not repair
    assert len(sync["receipts"]) == 2 and old_receipt.read_bytes() == failed
    assert ctx.run.state.pr == "https://example.test/pull/17"
    # Resume and a successful integration check alone do not authorize a push.
    assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1
    if not repair:
        assert ctx.run.state.pause.reason == "base_drift"
        assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/repair-against-unchanged-base")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-preparation-pauses")
def test_resume_conflict_after_manual_base_integration_requires_verification(base_env, monkeypatch):
    ctx = base_env.ctx
    commit(base_env.src, "base.txt", "run edits\n")
    cli._sync(ctx)
    head = ctx.head()
    base = advance_base(base_env)
    calls = verification(monkeypatch, ctx)
    assert not loop.prepare_publish(ctx) and calls == []
    assert ctx.run.state.pause.reason == "base_conflict"
    assert ctx.head() == head
    detail = ctx.run.state.pause.detail
    assert all(value in detail for value in ("base.txt", base, head, "specloop resume c"))
    diagnostic = Path(ctx.run.state.base_sync["receipts"][0]) / "diagnostic.txt"
    assert diagnostic.parent.parent == ctx.run.dir / "final" / "base-sync"
    assert diagnostic.read_text() == detail
    assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1
    assert ctx.run.state.base_sync["retry_verify"]
    # The human resolves the conflicting file in their source checkout.
    sh(base_env.src, "git", "fetch", "origin", "release/stable")
    sh(base_env.src, "git", *seal.SAFE_FLAGS, "-c", "user.name=test", "-c", "user.email=test@example.test",
       "merge", "--no-ff", "-X", "ours", "-m", "resolve base conflict", "FETCH_HEAD")
    repaired = sh(base_env.src, "git", "rev-parse", "HEAD")
    resume(base_env, monkeypatch)
    assert ctx.head() == repaired and loop._is_ancestor(ctx.repo, base, repaired)
    assert loop.prepare_publish(ctx)
    ctx.run.reload()
    assert [h for h, _ in calls] == [repaired]
    assert ctx.run.state.base_sync["base"] == base
    assert ctx.run.state.base_sync["verified_head"] == repaired
    assert diagnostic.read_text() == detail


@pytest.mark.scenario("specloop-documentation/publication-guidance/base-preparation-pauses")
def test_resume_after_restoring_base_fetch_runs_integration_checks(base_env, monkeypatch):
    """Follow the guide's base_fetch repair without accepting or pushing a stale head."""
    ctx = base_env.ctx
    head, base = ctx.head(), advance_base(base_env)
    calls = verification(monkeypatch, ctx)
    sh(base_env.origin, "git", "update-ref", "-d", "refs/heads/release/stable")
    assert loop.run(ctx) == "paused"
    ctx.run.reload()
    assert ctx.run.state.pause.reason == "base_fetch"
    detail = ctx.run.state.pause.detail
    assert "refs/heads/release/stable" in detail and "couldn't find remote ref" in detail
    assert "specloop resume c" in detail
    assert ctx.head() == head and calls == []
    assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1

    sh(base_env.origin, "git", "update-ref", "refs/heads/release/stable", base)
    resume(base_env, monkeypatch)
    assert ctx.head() == head and calls == []
    assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1
    assert loop.run(ctx) == "paused"
    ctx.run.reload()
    merged = ctx.head()
    assert sh(ctx.repo, "git", "show", "-s", "--format=%P", merged).split() == [head, base]
    assert [checked for checked, _ in calls] == [merged]
    assert ctx.run.state.base_sync["verified_head"] == merged
    assert not ctx.run.state.base_sync["retry_verify"]
    assert ctx.run.state.pause.reason == "ready_to_publish"


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/repair-against-unchanged-base")
def test_revision_recovery_requires_explicit_resume_and_checks_repaired_descendant(base_env, monkeypatch):
    ctx = base_env.ctx
    advance_base(base_env)
    verification(monkeypatch, ctx, passing=False)
    assert not loop.prepare_publish(ctx)
    merged = ctx.head()
    with pytest.raises(ValueError, match="base_drift"):
        revise.revise(ctx.run, ctx.m, "Repair the failing integration")
    resume(base_env, monkeypatch)
    revision = revise.revise(ctx.run, ctx.m, "Repair the failing integration")
    assert loop._is_ancestor(ctx.repo, merged, revision)
    assert ctx.run.state.base_sync["retry_verify"]
    # Supply the completed revision as a descendant, as task execution would.
    repaired = seal.commit_files(ctx.repo, revision, {
        "out/repair": "fixed\n", ctx.tasks_path: ctx.tasks_text(revision).replace("[ ]", "[x]"),
    }, "complete revision repair")
    seal.git(ctx.repo, "update-ref", ctx.ref, repaired, revision)
    ctx.run.reload()
    calls = verification(monkeypatch, ctx)
    assert loop.prepare_publish(ctx)
    ctx.run.reload()
    assert [h for h, _ in calls] == [repaired]
    assert ctx.run.state.base_sync["verified_head"] == repaired
    assert ctx.run.state.base_sync["merged_head"] == merged


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/legacy-state")
@pytest.mark.parametrize("moved", [False, True])
def test_legacy_driver_gate_uses_ancestry_without_inventing_obligation(base_env, monkeypatch, moved):
    ctx = base_env.ctx
    raw = json.loads((ctx.run.dir / "state.json").read_text())
    raw.pop("base_sync")
    raw["future_field"] = True
    (ctx.run.dir / "state.json").write_text(json.dumps(raw))
    ctx.run.reload()
    old = ctx.head()
    base = advance_base(base_env) if moved else base_env.initial
    calls = verification(monkeypatch, ctx)
    assert loop.prepare_publish(ctx)
    ctx.run.reload()
    assert loop._is_ancestor(ctx.repo, base, ctx.head())
    assert (ctx.head() != old) == moved and len(calls) == int(moved)
    if not moved:
        assert ctx.run.state.base_sync == {}
