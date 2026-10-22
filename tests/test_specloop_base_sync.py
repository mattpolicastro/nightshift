"""Current-base transport against an isolated, local bare origin."""

import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from specloop import cli, loop, manifest, seal, state


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


def snapshot(path):
    # Include the index, refs, config, reflogs and dirty/untracked working files.
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


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

    def fail_fetch(args, **kwargs):
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
