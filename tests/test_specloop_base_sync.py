"""Current-base transport against an isolated, local bare origin."""

import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift.cli import main

from specloop import cli, loop, manifest, seal, serve, state
from test_specloop_messages import parse_build_hint


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, path, text):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    sh(repo, "git", "add", path)
    sh(repo, "git", *seal.SAFE_FLAGS, "-c", "user.name=test", "-c", "user.email=test@example.test",
       "commit", "-qm", path)
    return sh(repo, "git", "rev-parse", "HEAD")


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    # Keep the fixtures independent of the developer's Git configuration.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    config = tmp_path / "gitconfig"
    config.write_text("[credential]\n\thelper = fixture-helper\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    origin, src, upstream = (tmp_path / name for name in ("origin with spaces.git", "source", "upstream"))
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "release/stable", str(origin))
    sh(tmp_path, "git", "clone", "-q", str(origin), str(src))
    initial = commit(src, "base.txt", "original\n")
    sh(src, "git", "push", "-q", "origin", "release/stable")
    sh(tmp_path, "git", "clone", "-q", str(origin), str(upstream))
    sh(src, "git", "checkout", "-qb", "spec/c")
    commit(src, "openspec/changes/c/tasks.md", "# Tasks\n\n## 1. Core\n\n- [x] 1.1 Done\n")
    run = state.Run.for_change("c")
    run.dir.mkdir(parents=True)
    sh(tmp_path, "git", "clone", "-q", "--bare", str(src), str(run.dir / "repo.git"))
    run.save()
    m = manifest.parse({
        "run": {"repo": str(src), "change": "c", "branch": "spec/c", "pr_base": "release/stable"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "m"}},
        "paths": {"allowed": ["base.txt", "out/**"]},
        "verify": [{"id": "ok", "run": ["/usr/bin/true"]}],
    })
    return SimpleNamespace(ctx=loop.Ctx(run, m, {}), src=src, origin=origin,
                           upstream=upstream, initial=initial, config=config)


def advance_base(env, text="upstream change\n"):
    head = commit(env.upstream, "base.txt", text)
    sh(env.upstream, "git", "push", "-q", "origin", "release/stable")
    return head


@pytest.fixture
def snapshot_env(base_env, monkeypatch):
    from test_snapshot_warming import SUCCESS
    env, ctx = base_env, base_env.ctx
    commit(env.upstream, "warm.sh", SUCCESS)
    commit(env.upstream, "lock", "original")
    sh(env.upstream, "git", "push", "-q", "origin", "release/stable")
    sh(env.src, "git", "fetch", "-q", "origin")
    sh(env.src, "git", *seal.SAFE_FLAGS, "-c", "user.name=test", "-c", "user.email=t@t",
       "merge", "--no-edit", "origin/release/stable")
    env.candidate = commit(env.src, "warm.sh", "exit 99")
    sh(ctx.repo, "git", "fetch", "-q", str(env.src), f"+spec/c:{ctx.ref}")
    env.base = commit(env.upstream, "lock", "base")
    sh(env.upstream, "git", "push", "-q", "origin", "release/stable")
    ctx.m = replace(ctx.m, snapshots=(manifest.Snapshot("deps", ("lock",),
        ("/bin/sh", "{src}/warm.sh", "{src}", "{sha}", "{snapshot:deps}")),),
        prepare=(manifest.Step("ordinary", ("/bin/sh", "-ec",
            'test -d "$1"; printf "%s" "$1" > "$1/prepared"', "sh", "{snapshot:deps}")),),
        verify=(manifest.Step("ok", ("/bin/sh", "-ec",
            'test "$(cat "$1/prepared")" = "$1"', "sh", "{snapshot:deps}")),))
    monkeypatch.setattr(loop.verify, "wrap", lambda argv, sb: argv)  # Portable, not sandbox evidence.
    return env


def snapshot(path):
    # Include the index, refs, config, reflogs and dirty/untracked working files.
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.mark.scenario("verify-preparation/private-snapshot-consumption/preparation-and-checks-share-the-private-path")
def test_driver_snapshot_event_forwarding_is_serialized(base_env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    import time
    ctx, active = base_env.ctx, Lock()
    event = ctx.run.event
    def forward(kind, **data):
        assert active.acquire(blocking=False)
        try:
            time.sleep(0.002)  # Yield while inside the callback to expose overlapping writers.
            event(kind, **data)
        finally:
            active.release()
    monkeypatch.setattr(ctx.run, "event", forward)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: ctx.snapshots.emit("snapshot_fallback", execution=n), range(16)))
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert sorted(e["execution"] for e in events if e["event"] == "snapshot_fallback") == list(range(16))


@pytest.mark.scenario("verify-preparation/trusted-base-warming/sync-failure-and-publish-preserve-behavior")
@pytest.mark.parametrize("requested_pause", [False, True])
def test_snapshot_initial_fetch_failure_and_pause(snapshot_env, monkeypatch, requested_pause):
    env, ctx = snapshot_env, snapshot_env.ctx
    stale = loop._fetch_publish_base(ctx, timeout=300)
    base = commit(env.src, ctx.tasks_path, "## 1. Core\n\n- [ ] 1.1 Pending\n")
    sh(ctx.repo, "git", "fetch", "-q", str(env.src), f"+spec/c:{ctx.ref}")
    fetches, prepared = [], []
    def failed_fetch(current, *, timeout):
        fetches.append(timeout)
        raise RuntimeError("offline fixture")
    def step(current, task, head):
        assert head == base and ctx.run.state.base_sync == {}
        ok, _, receipts = loop._seen_scenarios(ctx, head, ctx.run.dir / "task-preparation")
        assert ok and [r.id for r in receipts] == ["ordinary", "ok"]
        prepared.append(task.key)
        ctx.pause("requested")
    monkeypatch.setattr(loop, "_fetch_publish_base", failed_fetch)
    monkeypatch.setattr(loop, "step", step)
    monkeypatch.setattr(loop.verify, "warm_snapshots", lambda *a: pytest.fail("a stale fetch must never warm"))
    if requested_pause:
        ctx.run.request_pause()
    with ctx.run.lock() as mine:
        assert mine and loop.run(ctx) == "paused"
    assert fetches == ([] if requested_pause else [300])
    assert prepared == ([] if requested_pause else [loop.tasks.parse(ctx.tasks_text(base))[0].key])
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events if e["event"].startswith("snapshot_")] == (
        [] if requested_pause else ["snapshot_sync_failed", "snapshot_fallback"])
    assert ctx.run.state.pause.reason == "requested" and not ctx.run.pause_requested()
    assert not (ctx.run.dir / "snapshots").exists()
    with ctx.run.lock() as mine:
        assert mine and not loop.prepare_publish(ctx)
    assert ctx.run.state.pause.reason == "base_fetch" and "offline fixture" in ctx.run.state.pause.detail
    assert sh(ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == stale


@pytest.mark.scenario("publish-base-sync/fetch-current-base/readiness-uses-origin")
@pytest.mark.parametrize("origin_kind", ["absolute", "relative", "file-url"])
@pytest.mark.parametrize("supplied_remote", [False, True])
def test_fetch_uses_current_source_origin_without_mutating_source(base_env, origin_kind, supplied_remote):
    env, ctx = base_env, base_env.ctx
    latest = advance_base(env)
    sh(env.upstream, "git", "tag", "upstream-only")
    sh(env.upstream, "git", "push", "-q", "origin", "refs/tags/upstream-only", "HEAD:refs/heads/unrelated")
    remote = {"absolute": str(env.origin), "relative": "../origin with spaces.git",
              "file-url": env.origin.as_uri()}[origin_kind]
    sh(env.src, "git", "remote", "set-url", "origin", remote)
    (env.src / "base.txt").write_text("staged source edit\n")
    sh(env.src, "git", "add", "base.txt")
    (env.src / "base.txt").write_text("unstaged source edit\n")
    (env.src / "untracked.txt").write_text("keep me\n")
    before, head = snapshot(env.src), ctx.head()
    run_state = (ctx.run.dir / "state.json").read_bytes()
    assert sh(ctx.repo, "git", "remote", "get-url", "origin") == str(env.src)
    assert sh(env.src, "git", "rev-parse", "release/stable") == env.initial
    assert sh(env.src, "git", "rev-parse", "origin/release/stable") == env.initial

    fetched = loop._fetch_publish_base(ctx, remote if supplied_remote else None, timeout=300)

    assert fetched == latest != env.initial
    assert sh(ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == latest
    assert sh(ctx.repo, "git", "tag", "--list") == ""
    assert not sh(ctx.repo, "git", "for-each-ref", "refs/heads/unrelated")
    assert ctx.head() == head
    assert snapshot(env.src) == before
    assert (ctx.run.dir / "state.json").read_bytes() == run_state


@pytest.mark.scenario("publish-base-sync/fetch-current-base/readiness-uses-origin")
def test_fetch_refreshes_destination_even_after_origin_rewinds(base_env):
    env, ctx = base_env, base_env.ctx
    latest = advance_base(env)
    head = ctx.head()
    assert loop._fetch_publish_base(ctx, timeout=300) == latest
    sh(env.origin, "git", "update-ref", "refs/heads/release/stable", env.initial)
    assert loop._fetch_publish_base(ctx, timeout=300) == env.initial
    assert ctx.head() == head


@pytest.mark.scenario("publish-base-sync/fetch-current-base/fetch-cannot-establish-base")
@pytest.mark.parametrize("failure", ["missing-base", "unavailable-origin", "missing-origin", "timeout", "os-error"])
@pytest.mark.parametrize("timeout", [20, 300])
def test_fetch_failure_never_returns_stale_base(base_env, monkeypatch, failure, timeout):
    env, ctx = base_env, base_env.ctx
    stale = loop._fetch_publish_base(ctx, timeout=timeout)
    advance_base(env)
    if failure == "missing-base":
        sh(env.origin, "git", "update-ref", "-d", "refs/heads/release/stable")
        sh(env.origin, "git", "update-ref", "refs/tags/release/stable", env.initial)
    elif failure == "unavailable-origin":
        sh(env.src, "git", "remote", "set-url", "origin", "../missing.git")
    elif failure == "missing-origin":
        sh(env.src, "git", "remote", "remove", "origin")
    before, head = snapshot(env.src), ctx.head()
    saved_state = (ctx.run.dir / "state.json").read_bytes()
    real_run, fetches = subprocess.run, []

    def runner(args, **kwargs):
        if "fetch" in args:
            fetches.append(args)
            assert kwargs["timeout"] == timeout
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
            assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == str(env.config)
            assert args[-1] == "+refs/heads/release/stable:refs/specloop/publish-base"
            assert "--no-tags" in args
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, timeout, stderr=b"transport stalled")
            if failure == "os-error":
                raise OSError("transport could not start")
        return real_run(args, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    with pytest.raises(RuntimeError, match="refs/heads/release/stable") as caught:
        loop._fetch_publish_base(ctx, timeout=timeout)
    detail = str(caught.value)
    if failure == "timeout":
        assert f"timed out after {timeout}s" in detail and "transport stalled" in detail
    else:
        assert "failed" in detail
        diagnostic = {"missing-base": "couldn't find remote ref", "unavailable-origin": "missing.git",
                      "missing-origin": "No such remote", "os-error": "transport could not start"}
        assert diagnostic[failure] in detail
    assert len(fetches) == (0 if failure == "missing-origin" else 1)
    assert ctx.head() == head
    assert snapshot(env.src) == before
    assert (ctx.run.dir / "state.json").read_bytes() == saved_state
    # A retained old destination is not a successful result of this invocation.
    assert sh(ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == stale


@pytest.mark.scenario("publish-base-sync/fetch-current-base/fetch-cannot-establish-base")
@pytest.mark.parametrize("failure", ["timeout", "error"])
@pytest.mark.parametrize("stream", ["stderr", "stdout"])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_fetch_failure_diagnostic_is_stripped_and_capped_at_400(
        base_env, monkeypatch, failure, stream, as_bytes):
    diagnostic = " \n" + "d" * 400 + "MUST BE TRUNCATED\n "
    output = diagnostic.encode() if as_bytes else diagnostic
    error_type = subprocess.TimeoutExpired if failure == "timeout" else subprocess.CalledProcessError
    # Both exception classes expose stdout via their output constructor argument.
    output_arg = "stderr" if stream == "stderr" else "output"
    if failure == "timeout":
        error = error_type(["git", "fetch"], 20, **{output_arg: output})
    else:
        error = error_type(1, ["git", "fetch"], **{output_arg: output})

    real_run = subprocess.run

    def fail_fetch(args, **kwargs):
        if args[3:] == ["config", "--local", "--null", "--get-all", "credential.helper"]:
            return real_run(args, **kwargs)
        assert "fetch" in args
        raise error

    monkeypatch.setattr(loop.subprocess, "run", fail_fetch)
    with pytest.raises(RuntimeError) as caught:
        loop._fetch_publish_base(base_env.ctx, str(base_env.origin), timeout=20)

    reason = "timed out after 20s" if failure == "timeout" else "failed"
    assert str(caught.value) == f"fetch refs/heads/release/stable from source origin {reason}: " + "d" * 400
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("remote", ["https://example.test/owner/repo.git", "git@example.test:owner/repo.git",
                                     "example.test:owner/repo.git", "ssh://git@example.test/owner/repo.git"])
def test_hosted_origin_is_passed_as_an_unchanged_argument(base_env, monkeypatch, remote):
    real_run, calls = subprocess.run, []

    def runner(args, **kwargs):
        if "fetch" in args:
            calls.append(args)
            assert args[-2] == remote
            raise subprocess.CalledProcessError(1, args, stderr="transport unavailable")
        return real_run(args, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    with pytest.raises(RuntimeError, match="transport unavailable"):
        loop._fetch_publish_base(base_env.ctx, remote, timeout=20)
    assert len(calls) == 1


@pytest.mark.parametrize("fast_forward", [
    pytest.param(False, marks=pytest.mark.scenario("publish-base-sync/preserve-run-history/clean-moved-base")),
    pytest.param(True, marks=pytest.mark.scenario("publish-base-sync/preserve-run-history/fast-forward-shaped-base")),
])
def test_merge_constructs_two_harness_authored_parents(base_env, monkeypatch, fast_forward):
    env, ctx = base_env, base_env.ctx
    head = ctx.head()
    if fast_forward:
        sh(env.upstream, "git", "fetch", "-q", str(ctx.repo), head)
        sh(env.upstream, "git", "merge", "--ff-only", head)
    base = advance_base(env)
    # Upstream content need not pass the agent seal's path allowlist.
    base = commit(env.upstream, "upstream-only.txt", "trusted base content\n")
    sh(env.upstream, "git", "push", "-q", "origin", "release/stable")
    assert loop._fetch_publish_base(ctx, timeout=300) == base
    before = snapshot(env.src)
    refs = sh(ctx.repo, "git", "show-ref")
    for key in seal.IDENTITY:
        monkeypatch.setenv(key, "outsider@example.test" if "EMAIL" in key else "outsider")
    env.config.write_text("[commit]\n\tgpgsign = true\n[user]\n\tname = outsider\n\temail = outsider@example.test\n")

    merged = loop._merge_publish_base(ctx, base, head)

    assert merged and merged not in (head, base)
    assert sh(ctx.repo, "git", "show", "-s", "--format=%P", merged).split() == [head, base]
    assert sh(ctx.repo, "git", "show", "-s", "--format=%an%n%ae%n%cn%n%ce", merged).splitlines() == [
        "specloop", "specloop@localhost", "specloop", "specloop@localhost"]
    assert sh(ctx.repo, "git", "show", f"{merged}:base.txt") == "upstream change"
    assert sh(ctx.repo, "git", "show", f"{merged}:upstream-only.txt") == "trusted base content"
    assert sh(ctx.repo, "git", "show", f"{merged}:{ctx.tasks_path}") == ctx.tasks_text(head).strip()
    assert loop._is_ancestor(ctx.repo, head, base) == fast_forward
    # Construction is reusable for evidence; the driver later saves intent and installs it.
    assert ctx.head() == head and sh(ctx.repo, "git", "show-ref") == refs
    assert snapshot(env.src) == before
    assert not list(ctx.run.dir.glob("base-merge-*"))


@pytest.mark.scenario("publish-base-sync/preserve-run-history/conflicting-files")
def test_base_conflict_pauses_without_publishing(base_env, monkeypatch):
    env, ctx = base_env, base_env.ctx
    paths = ["base.txt", "space name.txt", "tab\tname.txt", "line\nname.txt"]
    head = seal.commit_files(ctx.repo, ctx.head(), dict.fromkeys(paths, "run edits\n"), "run edits")
    seal.git(ctx.repo, "update-ref", ctx.ref, head, ctx.head())
    for path in paths:
        commit(env.upstream, path, "base edits\n")
    sh(env.upstream, "git", "push", "-q", "origin", "release/stable")
    base = loop._fetch_publish_base(ctx, timeout=300)
    ctx.run.state.base_sync = {"base": env.initial, "verified_head": head, "receipts": ["old receipt"]}
    before, refs = snapshot(env.src), sh(ctx.repo, "git", "show-ref")
    tree = sh(ctx.repo, "git", "rev-parse", f"{head}^{{tree}}")

    def forbidden(*args, **kwargs):
        pytest.fail("conflict must stop verification and publication")

    monkeypatch.setattr(loop.verify, "run_steps", forbidden)
    monkeypatch.setattr(cli, "_push", forbidden)
    monkeypatch.setattr(cli, "_gh", forbidden)
    assert loop._merge_publish_base(ctx, base, head) is None
    ctx.run.reload()
    pause = ctx.run.state.pause
    assert pause.reason == "base_conflict" and pause.resume_at == 0
    assert "\n".join(sorted(paths)) in pause.detail
    assert base in pause.detail and head in pause.detail and "specloop resume c" in pause.detail
    assert ctx.run.state.base_sync == {"base": env.initial, "verified_head": head,
                                       "receipts": ["old receipt"], "retry_verify": True}
    assert ctx.head() == head and sh(ctx.repo, "git", "show-ref") == refs
    assert sh(ctx.repo, "git", "rev-parse", f"{ctx.ref}^{{tree}}") == tree
    assert not (ctx.repo / "index").exists() and not (ctx.repo / "MERGE_HEAD").exists()
    assert not list(ctx.run.dir.glob("base-merge-*"))
    assert snapshot(env.src) == before
    assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1


def test_operational_merge_error_is_not_a_file_conflict(base_env, monkeypatch):
    ctx = base_env.ctx
    base = advance_base(base_env)
    loop._fetch_publish_base(ctx, timeout=300)
    head, real_git = ctx.head(), loop.git

    def fail_merge(repo, *args, **kwargs):
        if args[0] == "merge":
            raise RuntimeError("merge process failed")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(loop, "git", fail_merge)
    assert loop._merge_publish_base(ctx, base, head) is None
    ctx.run.reload()
    assert ctx.run.state.pause.reason == "infra"
    assert ctx.run.state.pause.resume_at == 0
    assert "merge process failed" in ctx.run.state.pause.detail
    assert ctx.head() == head and ctx.run.state.base_sync == {}
    assert not list(ctx.run.dir.glob("base-merge-*"))


def forbid_publication(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("driver preparation must not publish or append acceptance-fix tasks")
    for owner, name in ((cli, "_push"), (cli, "_gh"), (cli, "_existing_pr"), (loop, "_final_fix")):
        monkeypatch.setattr(owner, name, forbidden)


def controlled_verify(monkeypatch, ctx, exit_code=0, error=False):
    """Portable receipts, with the same retained and transient files as verify."""
    calls = []

    def check(repo, head, steps, m, out):
        assert repo == ctx.repo and head == ctx.head()
        assert steps == m.verify and m is ctx.m
        saved = state.Run(ctx.run.dir, require_existing=True).state.base_sync
        assert saved["base"] and saved["merged_head"] == head
        assert saved["verified_head"] == "" and saved["retry_verify"]
        assert str(out) in saved["receipts"]
        calls.append((head, out))
        for name in ("src", "build"):
            (out / name).mkdir()
        (out / "ok.log").write_text("upstream broke the check\n")
        if error:
            raise OSError("verifier unavailable")
        receipt = loop.verify.Receipt("ok", ["check"], 0, exit_code, exit_code == 0, 0.1, "check output")
        (out / "receipts.json").write_text(json.dumps([asdict(receipt)]))
        return [receipt]

    monkeypatch.setattr(loop.verify, "run_steps", check)
    return calls


@pytest.mark.scenario("publish-base-sync/verify-integrated-head/merged-head-passes")
def test_prepare_publish_saves_intent_before_advancing_and_verifies(base_env, monkeypatch):
    ctx = base_env.ctx
    old = ctx.head()
    base = advance_base(base_env)
    forbid_publication(monkeypatch)
    calls = controlled_verify(monkeypatch, ctx)
    real_git = loop.git
    updates = []

    def checked_git(repo, *args, **kwargs):
        if args[0] == "update-ref":
            saved = state.Run(ctx.run.dir, require_existing=True).state.base_sync
            assert args == ("update-ref", ctx.ref, saved["merged_head"], old)
            assert saved["base"] == base and saved["previous_head"] == old
            assert saved["verified_head"] == "" and saved["retry_verify"]
            assert ctx.head() == old
            updates.append(args)
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(loop, "git", checked_git)
    assert loop.prepare_publish(ctx)
    ctx.run.reload()
    sync = ctx.run.state.base_sync
    assert len(updates) == len(calls) == 1
    assert sync["merged_head"] == sync["verified_head"] == ctx.head() != old
    assert sync["base"] == base and not sync["retry_verify"]
    out = Path(sync["receipts"][0])
    assert out.parent == ctx.run.dir / "final" / "base-sync"
    assert (out / "receipts.json").is_file() and (out / "ok.log").is_file()
    assert not (out / "src").exists() and not (out / "build").exists()
    assert loop.prepare_publish(ctx)
    assert len(updates) == len(calls) == 1


@pytest.mark.scenario("publish-base-sync/verify-integrated-head/drift-breaks-verify")
@pytest.mark.parametrize("exit_code", [1, None])
def test_drift_that_breaks_verify_pauses_with_receipts(base_env, monkeypatch, exit_code):
    ctx = base_env.ctx
    old, tasks = ctx.head(), ctx.tasks_text(ctx.head())
    base = advance_base(base_env)
    forbid_publication(monkeypatch)
    calls = controlled_verify(monkeypatch, ctx, exit_code)
    ctx.run.state.pr = "https://example.test/pull/17"
    for attempt in range(2):
        ctx.run.state.status, ctx.run.state.pause = "running", None
        assert not loop.prepare_publish(ctx)
        ctx.run.reload()
        pause, sync = ctx.run.state.pause, ctx.run.state.base_sync
        assert pause.reason == "base_drift" and pause.resume_at == 0
        assert base in pause.detail and ctx.head() in pause.detail
        assert "ok: expected exit 0" in pause.detail
        assert ("timeout" if exit_code is None else "got 1") in pause.detail
        assert "specloop resume c" in pause.detail
        out = calls[-1][1]
        for name in ("receipts.json", "ok.log"):
            assert (out / name).is_file() and str(out / name) in pause.detail
        assert sync["receipts"] == [str(out) for _, out in calls]
        assert sync["verified_head"] == "" and sync["retry_verify"]
        assert ctx.head() != old and ctx.tasks_text(ctx.head()) == tasks
        assert ctx.run.state.acceptance_rounds == 0 and ctx.run.state.tasks == {}
        assert ctx.run.state.pr == "https://example.test/pull/17"
        assert cli._publish(SimpleNamespace(change="c"), ctx.run) == 1
    assert calls[0][0] == calls[1][0] and calls[0][1] != calls[1][1]
    assert (calls[0][1] / "receipts.json").read_bytes() == (calls[1][1] / "receipts.json").read_bytes()


@pytest.mark.scenario("specloop-documentation/publication-guidance/base-moves-before-publication")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-preparation-pauses")
@pytest.mark.parametrize("prepare_fails", [
    pytest.param(False, marks=pytest.mark.scenario("publish-base-sync/verify-integrated-head/merged-head-passes")),
    pytest.param(True, marks=pytest.mark.scenario("publish-base-sync/verify-integrated-head/preparation-fails")),
])
def test_prepare_publish_uses_trusted_prepare_then_verify(base_env, monkeypatch, prepare_fails):
    ctx = base_env.ctx
    base = advance_base(base_env)
    forbid_publication(monkeypatch)
    # Run the real verifier and its normal sandbox policy, including expected
    # nonzero exits. Verification depends on preparation and on the merged tree.
    prepare = manifest.Step("dependencies", ("/bin/sh", "-c", "echo prepared; touch dependency; exit 3"),
                            expect=0 if prepare_fails else 3)
    check = manifest.Step("merged-tree", ("/bin/sh", "-c",
                          'test -f dependency && test "$(cat {src}/base.txt)" = "upstream change"'))
    ctx.m = replace(ctx.m, prepare=(prepare,), verify=(check,))
    assert loop.prepare_publish(ctx) is not prepare_fails
    ctx.run.reload()
    sync = ctx.run.state.base_sync
    out = Path(sync["receipts"][-1])
    receipts = json.loads((out / "receipts.json").read_text())
    assert receipts[0]["id"] == "dependencies" and receipts[0]["exit"] == 3
    assert (out / "dependencies.log").read_text().strip() == "prepared"
    assert not (out / "src").exists() and not (out / "build").exists()
    assert base == sync["base"] and loop._is_ancestor(ctx.repo, base, ctx.head())
    if prepare_fails:
        assert len(receipts) == 1 and not receipts[0]["passed"]
        assert not (out / "merged-tree.log").exists()
        assert ctx.run.state.pause.reason == "base_drift"
        assert "dependencies" in ctx.run.state.pause.detail and str(out) in ctx.run.state.pause.detail
        assert sync["verified_head"] == "" and sync["retry_verify"]
    else:
        assert [r["id"] for r in receipts] == ["dependencies", "merged-tree"]
        assert all(r["passed"] for r in receipts)
        assert sync["verified_head"] == ctx.head() and not sync["retry_verify"]


def test_prepare_publish_infrastructure_error_retains_obligation_and_logs(base_env, monkeypatch):
    ctx = base_env.ctx
    base = advance_base(base_env)
    calls = controlled_verify(monkeypatch, ctx, error=True)
    forbid_publication(monkeypatch)
    assert not loop.prepare_publish(ctx)
    ctx.run.reload()
    out = calls[0][1]
    assert ctx.run.state.pause.reason == "infra" and ctx.run.state.pause.resume_at == 0
    assert "verifier unavailable" in ctx.run.state.pause.detail and str(out) in ctx.run.state.pause.detail
    assert "specloop resume c" in ctx.run.state.pause.detail
    assert ctx.run.state.base_sync["retry_verify"] and not ctx.run.state.base_sync["verified_head"]
    assert loop._is_ancestor(ctx.repo, base, ctx.head())
    assert (out / "ok.log").is_file() and (out / "diagnostic.txt").is_file()
    assert not (out / "src").exists() and not (out / "build").exists()


def test_prepare_publish_cas_mismatch_preserves_intent_without_verifying(base_env, monkeypatch):
    ctx = base_env.ctx
    old = ctx.head()
    base = advance_base(base_env)
    other = seal.commit_files(ctx.repo, old, {"out/other": "concurrent edit\n"}, "other writer")
    real_git = loop.git
    calls = controlled_verify(monkeypatch, ctx)

    def concurrent_update(repo, *args, **kwargs):
        if args[0] == "update-ref":
            real_git(repo, "update-ref", ctx.ref, other, old)
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(loop, "git", concurrent_update)
    assert not loop.prepare_publish(ctx)
    ctx.run.reload()
    sync = ctx.run.state.base_sync
    assert ctx.head() == other and calls == []
    assert sync["base"] == base and sync["previous_head"] == old
    assert sync["merged_head"] not in (old, other, base)
    assert sync["retry_verify"] and sync["verified_head"] == ""
    assert ctx.run.state.pause.reason == "infra" and ctx.run.state.pause.resume_at == 0
    assert "specloop resume c" in ctx.run.state.pause.detail
    assert (Path(sync["receipts"][-1]) / "diagnostic.txt").is_file()


def track_base_fetches(monkeypatch):
    real_run, fetches = subprocess.run, []

    def runner(args, **kwargs):
        if "fetch" in args and args[-1].endswith(":refs/specloop/publish-base"):
            assert kwargs["timeout"] == 300
            fetches.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    return fetches


@pytest.mark.scenario("publish-base-sync/fetch-current-base/readiness-uses-origin")
@pytest.mark.scenario("publish-base-sync/verify-integrated-head/merged-head-passes")
def test_driver_checks_merged_head_before_readiness(base_env, monkeypatch):
    env, ctx = base_env, base_env.ctx
    old, base = ctx.head(), advance_base(env)
    before = snapshot(env.src)
    assert sh(env.src, "git", "rev-parse", "release/stable") == env.initial != base
    checks = {"cap/req/manual": "inspect the result", "cap/req/other": "check the UI"}
    ctx.m = replace(ctx.m, prepare=(manifest.Step("prepare", ("prepare",)),),
                    coverage_step="ok", coverage_exempt=checks,
                    acceptance=(manifest.Step("accept", ("accept",)),))
    fetches = track_base_fetches(monkeypatch)
    forbid_publication(monkeypatch)
    stages = []

    def verify(repo, head, steps, m, out):
        assert len(fetches) == 1
        assert head == ctx.head() != old and repo == ctx.repo
        assert sh(repo, "git", "show", "-s", "--format=%P", head).split() == [old, base]
        assert sh(repo, "git", "show", f"{head}:base.txt") == "upstream change"
        saved = state.Run(ctx.run.dir, require_existing=True).state
        assert saved.status == "running" and saved.pause is None
        stage = "integration" if out.parent.name == "base-sync" else out.name
        if stage == "integration":
            assert not saved.base_sync["verified_head"] and saved.base_sync["retry_verify"]
        else:
            assert saved.base_sync["verified_head"] == head and not saved.base_sync["retry_verify"]
        stages.append(stage)
        out.mkdir(parents=True, exist_ok=True)
        (out / "ok.log").write_text("cap/req/automated\n")
        return [loop.verify.Receipt(s.id, list(s.run), s.expect, s.expect, True, 0.1, "")
                for s in m.prepare + steps]

    def scenarios(current, head):
        assert current is ctx and head == ctx.head() != old
        return {"cap/req/automated": "automated", **checks}

    def notify(message):
        assert stages == ["integration", "coverage-0", "acceptance-0"]
        assert ctx.run.state.pause.reason == "ready_to_publish"
        stages.append("ready")

    monkeypatch.setattr(loop.verify, "run_steps", verify)
    monkeypatch.setattr(loop, "_scenarios_at", scenarios)
    ctx.notify = notify
    assert loop.run(ctx) == "paused"
    ctx.run.reload()
    assert stages == ["integration", "coverage-0", "acceptance-0", "ready"]
    assert len(fetches) == 1 and snapshot(env.src) == before
    assert ctx.run.state.base_sync["verified_head"] == ctx.head()
    pause = ctx.run.state.pause
    assert pause.reason == "ready_to_publish" and ctx.head()[:12] in pause.detail
    assert old[:12] not in pause.detail
    for sid, why in checks.items():
        assert f"- [ ] `{sid}`: {why}" in pause.detail
    assert pause.detail.endswith("Next: `specloop publish c`")


@pytest.mark.scenario("publish-base-sync/verify-integrated-head/unmoved-base")
@pytest.mark.scenario("verify-preparation/snapshot-fallback-events/legacy-manifests-keep-their-behavior")
@pytest.mark.parametrize("previously_integrated", [False, True])
def test_driver_unmoved_base_fetches_once_without_merge_or_extra_verify(base_env, monkeypatch,
                                                                     previously_integrated):
    ctx = base_env.ctx
    if previously_integrated:
        advance_base(base_env)
        controlled_verify(monkeypatch, ctx)
        assert loop.prepare_publish(ctx)
        ctx.run.reload()
    head = ctx.head()
    sync = json.dumps(ctx.run.state.base_sync, sort_keys=True)
    fetches = track_base_fetches(monkeypatch)

    def unexpected(*args, **kwargs):
        pytest.fail("an incorporated, verified base needs neither merge nor extra verification")

    monkeypatch.setattr(loop, "_merge_publish_base", unexpected)
    monkeypatch.setattr(loop.verify, "run_steps", unexpected)
    assert loop.run(ctx) == "paused"
    ctx.run.reload()
    assert len(fetches) == 1 and ctx.head() == head
    assert json.dumps(ctx.run.state.base_sync, sort_keys=True) == sync
    assert ctx.run.state.pause.reason == "ready_to_publish"
    assert head[:12] in ctx.run.state.pause.detail
    assert not (ctx.run.dir / "snapshots").exists()
    assert not list(ctx.run.dir.rglob("snapshot-events.jsonl"))
    assert not any(json.loads(line)["event"].startswith("snapshot_")
                   for line in (ctx.run.dir / "events.jsonl").read_text().splitlines())


@pytest.mark.scenario("publish-base-sync/fetch-current-base/fetch-cannot-establish-base")
@pytest.mark.parametrize("failure", ["missing-base", "transport", "timeout"])
def test_driver_fetch_failure_blocks_final_checks_and_readiness(base_env, monkeypatch, failure):
    env, ctx = base_env, base_env.ctx
    stale = loop._fetch_publish_base(ctx, timeout=300)
    head = ctx.head()
    ctx.m = replace(ctx.m, coverage_step="ok", acceptance=(manifest.Step("accept", ("accept",)),))
    ctx.run.state.base_sync = {"base": stale, "retry_verify": True, "receipts": ["previous attempt"]}
    ctx.run.state.pr = "https://example.test/pull/17"
    ctx.run.save()
    if failure == "missing-base":
        sh(env.origin, "git", "update-ref", "-d", "refs/heads/release/stable")
    before = snapshot(env.src)
    real_run, fetches, notes = subprocess.run, [], []

    def runner(args, **kwargs):
        if "fetch" in args:
            fetches.append(args)
            assert kwargs["timeout"] == 300
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 300, stderr=b"transport stalled")
            if failure == "transport":
                raise subprocess.CalledProcessError(1, args, stderr="transport unavailable")
        return real_run(args, **kwargs)

    def unexpected(*args, **kwargs):
        pytest.fail("fetch failure must stop before merging, final checks or readiness")

    monkeypatch.setattr(loop.subprocess, "run", runner)
    for owner, name in ((loop, "_merge_publish_base"), (loop, "_seen_scenarios"),
                        (loop.verify, "run_steps")):
        monkeypatch.setattr(owner, name, unexpected)
    forbid_publication(monkeypatch)
    ctx.notify = notes.append
    assert loop.run(ctx) == "paused"
    ctx.run.reload()
    pause = ctx.run.state.pause
    assert pause.reason == "base_fetch" and pause.resume_at == 0
    assert "refs/heads/release/stable" in pause.detail and "specloop resume c" in pause.detail
    diagnostic = {"missing-base": "couldn't find remote ref", "transport": "transport unavailable",
                  "timeout": "timed out after 300s"}[failure]
    assert diagnostic in pause.detail
    assert len(fetches) == len(notes) == 1
    assert "Publish:" not in notes[0].text()
    assert ctx.head() == head and snapshot(env.src) == before
    assert sh(ctx.repo, "git", "rev-parse", "refs/specloop/publish-base") == stale
    assert ctx.run.state.base_sync == {"base": stale, "retry_verify": True, "receipts": ["previous attempt"]}
    assert ctx.run.state.pr == "https://example.test/pull/17"
    assert ctx.run.state.coverage_rounds == ctx.run.state.acceptance_rounds == 0
    assert not (ctx.run.dir / "final").exists()


@pytest.mark.scenario("publish-base-sync/fetch-current-base/fetch-cannot-establish-base")
def test_finals_returns_false_when_base_preparation_pauses(base_env, monkeypatch):
    ctx = base_env.ctx
    head = ctx.head()
    tasks_before = ctx.tasks_text(head)
    ctx.m = replace(ctx.m, pr_base="missing-base", coverage_step="ok",
                    acceptance=(manifest.Step("accept", ("accept",)),))

    def unexpected(*args, **kwargs):
        pytest.fail("a preparation pause must stop final checks and task appends")

    for owner, name in ((loop, "_seen_scenarios"), (loop.verify, "run_steps"), (loop, "_append")):
        monkeypatch.setattr(owner, name, unexpected)

    # False tells the caller to stop; True is reserved for appended tasks.
    assert loop.finals(ctx, head) is False
    ctx.run.reload()
    assert ctx.run.state.status == "paused"
    assert ctx.run.state.pause.reason == "base_fetch"
    assert "refs/heads/missing-base" in ctx.run.state.pause.detail
    assert ctx.head() == head and ctx.tasks_text(head) == tasks_before
    assert ctx.run.state.coverage_rounds == ctx.run.state.acceptance_rounds == 0


@pytest.fixture
def publish_env(base_env, monkeypatch):
    env, ctx = base_env, base_env.ctx
    ctx.m = replace(ctx.m, github="example/repo", coverage_exempt={
        "cap/req/manual": "inspect the result", "cap/req/other": "check the UI"})

    def load(run):
        ctx.run = run
        return ctx

    monkeypatch.setattr(cli, "_ctx", load)
    env.operations = []
    real_run = subprocess.run

    def runner(args, **kwargs):
        if "fetch" in args and args[-1].endswith(":refs/specloop/publish-base"):
            env.operations.append(("fetch", kwargs["timeout"]))
        return real_run(args, **kwargs)

    monkeypatch.setattr(loop.subprocess, "run", runner)
    env.url = "https://github.com/example/repo/pull/17"
    env.existing_pr = ""

    def push(repo, remote, ref):
        assert env.operations[-1] == ("fetch", 20)
        assert (repo, remote, ref) == (ctx.repo, str(env.origin), ctx.ref)
        env.operations.append(("push", ctx.head()))

    def gh(*args):
        env.operations.append(("gh", args[:2]))
        if args[0] == "api":
            return json.dumps([{"html_url": env.existing_pr, "head": {
                "ref": ctx.m.branch, "repo": {"full_name": ctx.m.github}},
                "base": {"ref": ctx.m.pr_base}}] if env.existing_pr else [])
        assert args[:3] == ("pr", "create", "--draft")
        return env.url

    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(cli, "_gh", gh)
    return env


def publish_without_preparing(monkeypatch, command, code):
    def forbidden(*args, **kwargs):
        pytest.fail("publish must leave all preparation and recovery to the driver")

    with monkeypatch.context() as guard:
        for name in ("prepare_publish", "_merge_publish_base", "_merge_commits", "run", "finals"):
            guard.setattr(loop, name, forbidden)
        guard.setattr(loop.verify, "run_steps", forbidden)
        guard.setattr(loop.verify, "warm_snapshots", forbidden)
        assert main(command) == code


@pytest.mark.scenario("verify-preparation/trusted-base-warming/sync-failure-and-publish-preserve-behavior")
def test_snapshot_publish_only_schedules_preparation(publish_env, monkeypatch, capfd):
    env, ctx = publish_env, publish_env.ctx
    ctx.m = replace(ctx.m, snapshots=(manifest.Snapshot("deps", ("base.txt",), ("false",)),))
    ctx.pause("ready_to_publish")
    advance_base(env)
    head = ctx.head()
    publish_without_preparing(monkeypatch, ["publish", "c", "--json"], 1)
    assert "scheduled" in json.loads(capfd.readouterr().out)["error"]
    ctx.run.reload()
    assert ctx.run.state.status == "running" and ctx.run.state.base_sync == {}
    assert ctx.head() == head and env.operations == [("fetch", 20)]
    assert not (ctx.run.dir / "snapshots").exists()


@pytest.mark.scenario("publish-base-sync/fetch-current-base/publish-fetches-again")
@pytest.mark.scenario("publish-base-sync/verify-integrated-head/merged-head-passes")
@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/moved-base-schedules-preparation")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-moves-before-publication")
@pytest.mark.parametrize("pr_kind", ["adopted", "reconciled", "new"])
@pytest.mark.parametrize("failed_push", [False, True])
def test_moved_base_is_merged_and_verified_before_publish(publish_env, monkeypatch, capfd,
                                                        pr_kind, failed_push):
    env, ctx = publish_env, publish_env.ctx
    old = ctx.head()
    ctx.run.state.pr = env.url if pr_kind == "adopted" else ""
    env.existing_pr = env.url if pr_kind == "reconciled" else ""
    assert loop.run(ctx) == "paused"
    assert ctx.run.state.pause.reason == "ready_to_publish"
    if failed_push:
        with monkeypatch.context() as failure:
            def fail(*args):
                raise RuntimeError("push unavailable")
            failure.setattr(cli, "_push", fail)
            publish_without_preparing(failure, ["publish", "c", "--json"], 1)
        assert json.loads(capfd.readouterr().out) == {"version": 1, "error": "push unavailable"}
        ctx.run.reload()
        assert ctx.run.state.pause.reason == "ready_to_publish"
    env.operations.clear()
    base = advance_base(env)
    before, source = asdict(ctx.run.state), snapshot(env.src)
    publish_without_preparing(monkeypatch, ["publish", "c", "--json"], 1)
    document = json.loads(capfd.readouterr().out)
    assert set(document) == {"version", "error"} and document["version"] == 1
    assert "scheduled" in document["error"] and "nothing was published" in document["error"]
    assert "Retry" in document["error"] and "readiness" in document["error"]
    ctx.run.reload()
    assert asdict(ctx.run.state) == {**before, "status": "running", "pause": None}
    assert ctx.head() == old and env.operations == [("fetch", 20)]
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert not any(e["event"] == "published" for e in events)
    assert events[-1]["event"] == "publish_preparation_scheduled"
    assert (events[-1]["base"], events[-1]["head"]) == (base, old)
    selected = []
    assert serve.pass_once(lambda _: None, spawn=selected.append) == selected == ["c"]
    verified = controlled_verify(monkeypatch, ctx)
    with ctx.run.lock() as mine:
        assert mine
        assert loop.run(ctx) == "paused"
    ctx.run.reload()
    merged = ctx.head()
    assert merged != old and sh(ctx.repo, "git", "show", "-s", "--format=%P", merged).split() == [old, base]
    assert len(verified) == 1 and verified[0][0] == ctx.run.state.base_sync["verified_head"] == merged
    assert ctx.run.state.pause.reason == "ready_to_publish"
    assert merged[:12] in ctx.run.state.pause.detail
    for sid, why in ctx.m.coverage_exempt.items():
        assert f"- [ ] `{sid}`: {why}" in ctx.run.state.pause.detail
    assert ctx.run.state.pause.detail.endswith("Next: `specloop publish c`")
    assert env.operations == [("fetch", 20), ("fetch", 300)]
    publish_without_preparing(monkeypatch, ["publish", "c", "--json"], 0)
    assert json.loads(capfd.readouterr().out) == {"version": 1, "outcome": "published", "pr": env.url}
    assert env.operations[:4] == [("fetch", 20), ("fetch", 300), ("fetch", 20), ("push", merged)]
    assert [op for op in env.operations if op[0] == "gh"] == (
        [] if pr_kind == "adopted" else [("gh", ("api", "repos/example/repo/pulls"))]
        + ([("gh", ("pr", "create"))] if pr_kind == "new" else []))
    ctx.run.reload()
    assert ctx.run.state.pause.reason == "human_checks" and ctx.run.state.pr == env.url
    assert ctx.head() == merged and snapshot(env.src) == source


@pytest.mark.scenario("publish-base-sync/verify-integrated-head/unmoved-base")
@pytest.mark.parametrize("previously_integrated", [False, True])
def test_unmoved_base_publishes_without_merge(publish_env, monkeypatch, capfd, previously_integrated):
    env, ctx = publish_env, publish_env.ctx
    if previously_integrated:
        advance_base(env)
        controlled_verify(monkeypatch, ctx)
    assert loop.run(ctx) == "paused"
    head, sync = ctx.head(), dict(ctx.run.state.base_sync)
    env.operations.clear()
    publish_without_preparing(monkeypatch, ["publish", "c", "--json"], 0)
    assert json.loads(capfd.readouterr().out)["outcome"] == "published"
    assert env.operations[:2] == [("fetch", 20), ("push", head)]
    assert sum(op[0] == "fetch" for op in env.operations) == 1
    ctx.run.reload()
    assert ctx.head() == head and ctx.run.state.base_sync == sync
    assert ctx.run.state.pause.reason == "human_checks"


@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/pending-verification-schedules-preparation")
@pytest.mark.scenario("specloop-documentation/publication-guidance/base-moves-before-publication")
@pytest.mark.parametrize("obligation", ["intent", "retry", "changed-head"])
def test_pending_verification_schedules_preparation(publish_env, monkeypatch, capfd, obligation):
    env, ctx = publish_env, publish_env.ctx
    head = ctx.head()
    merged = seal.commit_files(ctx.repo, head, {"out/repair": "repair"}, "repair")
    if obligation == "changed-head":
        seal.git(ctx.repo, "update-ref", ctx.ref, merged, head)
    ctx.run.state.base_sync = {
        "base": env.initial, "previous_head": head,
        "merged_head": merged if obligation == "intent" else head,
        "verified_head": "" if obligation == "intent" else head,
        "retry_verify": obligation == "retry", "receipts": ["retained attempt"],
    }
    ctx.run.state.pr = env.url
    ctx.run.state.task("1.1").status = "passed"
    ctx.run.state.gates_passed = [1]
    ctx.run.pause("ready_to_publish", detail="all human checks")
    before = asdict(ctx.run.state)
    entry_head = ctx.head()
    publish_without_preparing(monkeypatch, ["publish", "c", "--json"], 1)
    assert set(json.loads(capfd.readouterr().out)) == {"version", "error"}
    ctx.run.reload()
    assert asdict(ctx.run.state) == {**before, "status": "running", "pause": None}
    assert ctx.head() == entry_head and env.operations == [("fetch", 20)]
    verified = []

    def verify(repo, current, steps, m, out):
        saved = state.Run(ctx.run.dir, require_existing=True).state
        assert saved.status == "running" and saved.pause is None
        assert saved.base_sync["retry_verify"] and not saved.base_sync["verified_head"]
        assert current == ctx.head() and loop._is_ancestor(repo, env.initial, current)
        verified.append(current)
        return [loop.verify.Receipt("ok", ["check"], 0, 0, True, 0.1, "")]

    monkeypatch.setattr(loop.verify, "run_steps", verify)
    assert loop.run(ctx) == "paused"
    assert len(verified) == 1
    assert ctx.head() == (head if obligation == "retry" else merged)
    assert ctx.run.state.base_sync["verified_head"] == ctx.head()
    assert not ctx.run.state.base_sync["retry_verify"]
    assert ctx.run.state.pause.reason == "ready_to_publish"

    # Follow the guide's retry through delivery to the already recorded PR.
    checked = ctx.head()
    assert env.operations == [("fetch", 20), ("fetch", 300)]
    publish_without_preparing(monkeypatch, ["publish", "c", "--json"], 0)
    assert json.loads(capfd.readouterr().out) == {"version": 1, "outcome": "published", "pr": env.url}
    assert env.operations == [("fetch", 20), ("fetch", 300), ("fetch", 20), ("push", checked)]
    ctx.run.reload()
    assert ctx.run.state.pr == env.url and ctx.run.state.pause.reason == "human_checks"
    assert ctx.head() == checked and verified == [checked]


@pytest.mark.scenario("publish-base-sync/driver-preparation-handoff/moved-base-schedules-preparation")
@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
@pytest.mark.parametrize("mode", ["plain", "act"])
def test_scheduling_output_and_independent_stop(publish_env, monkeypatch, capfd, mode):
    env, ctx = publish_env, publish_env.ctx
    advance_base(env)
    ctx.run.pause("ready_to_publish")
    ctx.run.request_pause()
    command = ["publish", "c"] if mode == "plain" else ["act", "c", "publish", "--json"]
    publish_without_preparing(monkeypatch, command, 1)
    output = capfd.readouterr()
    if mode == "act":
        document = json.loads(output.out)
        assert set(document) == {"version", "outcome", "verb", "detail"}
        assert (document["version"], document["outcome"], document["verb"]) == (1, "refused", "publish")
        explanation = document["detail"]
    else:
        assert output.out == ""
        explanation = output.err
    assert "scheduled" in explanation and "nothing was published" in explanation
    assert "readiness" in explanation and "nightshift run c" in explanation
    hints = [parse_build_hint(command) for command in explanation.split("`")[1::2]]
    assert [hint.cmd for hint in hints] == ["serve", "run", "publish"]
    assert all(hint.change == "c" for hint in hints[1:])
    ctx.run.reload()
    assert ctx.run.state.status == "running" and ctx.run.state.pause is None
    assert ctx.run.pause_requested() and env.operations == [("fetch", 20)]
