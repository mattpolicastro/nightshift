"""Current-base transport against an isolated, local bare origin."""

import subprocess
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
