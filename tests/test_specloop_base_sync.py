"""Current-base transport against an isolated, local bare origin."""

import subprocess
from types import SimpleNamespace

import pytest

from specloop import loop, manifest, seal, state


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
