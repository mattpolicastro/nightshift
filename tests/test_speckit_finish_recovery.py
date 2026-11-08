"""Native completion survives transport failures without new recovery state."""

import json
import os
import subprocess
import traceback
from types import SimpleNamespace

import pytest

from fake_gitea import Reply
from nightshift.cli import main
from specloop import agents, cli, seal, speckit, spec_format
from specloop.state import Run
from test_speckit_build import (
    CHANGE, native_admission, native_commit, native_service, three_phase_feature,
)
from test_specloop_credential_transport import HELPERS, SENTINEL
from test_specloop_fetch_credentials import assert_safe, set_helpers
from test_specloop_forge_selection import configured


PUSH = cli._push
ENDPOINT = "/git/api/v1/repos/example/repo/pulls/42"


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


@pytest.fixture
def transport(native_service, monkeypatch):
    """Run production push, substituting only its network endpoint with local Git."""
    s = native_service
    # human_checks starts from a published branch with all task ticks committed.
    seal.git(s.ctx.repo, "push", "-q", str(s.remote), f"{s.ctx.ref}:{s.ctx.ref}")
    s.pushes, s.commands, s.transport_error = [], [], None
    real = subprocess.run
    monkeypatch.setattr(cli, "_push", PUSH)

    def process(argv, **kwargs):
        assert not {"fetch", "merge"}.intersection(argv)
        s.commands.append((list(argv), dict(kwargs)))
        if "push" in argv:
            assert s.origin in argv
            assert Run(s.run.dir).state.pause.reason == "human_checks"
            s.pushes.append((s.ctx.head(), list(argv), dict(kwargs)))
            if s.transport_error is not None:
                raise s.transport_error
            argv = [str(s.remote) if arg == s.origin else arg for arg in argv]
        return real(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", process)
    return s


def snapshot(root):
    return {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def assert_completion(s, original, completed):
    assert completed != original
    assert seal.git(s.ctx.repo, "rev-list", f"{original}..{completed}").decode().splitlines() == [completed]
    assert seal.git(s.ctx.repo, "diff", "--name-only", original, completed).decode().splitlines() == [
        f"specs/{CHANGE}/spec.md"]
    assert spec_format.SPECKIT.tasks_paths_at(s.ctx.repo, completed, CHANGE) == [s.ctx.tasks_path]
    assert all(t.done for t in speckit.parse_tasks(s.ctx.tasks_text(completed)))
    spec = f"specs/{CHANGE}/spec.md"
    original_spec = seal.git(s.ctx.repo, "show", f"{original}:{spec}").decode()
    assert seal.git(s.ctx.repo, "show", f"{completed}:{spec}").decode() == speckit.mark_complete(original_spec)
    assert b"openspec/" not in seal.git(s.ctx.repo, "ls-tree", "-r", "--name-only", completed)


def assert_partial(s, saved, original, *, pushed):
    completed = s.ctx.head()
    assert_completion(s, original, completed)
    assert (s.run.dir / "state.json").read_bytes() == saved
    fresh = Run(s.run.dir)
    assert fresh.state.status == "paused" and fresh.state.pause.reason == "human_checks"
    assert not any(e["event"] == "finished" for e in agents.read_jsonl(s.run.dir / "events.jsonl"))
    remote = seal.git(s.remote, "rev-parse", s.ctx.ref).decode().strip()
    assert remote == (completed if pushed else original)
    return completed


def assert_read_only_status(s, completed, monkeypatch, capfd):
    before = snapshot(s.run.dir), snapshot(s.ctx.m.repo), snapshot(s.remote)
    effects = len(s.pushes), len(s.gh), len(s.fake.calls)
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail("status attempted execution or state mutation")
        for obj, name in ((cli, "_ctx"), (cli, "_forge_target"), (Run, "save"),
                          (Run, "event"), (seal, "commit_files")):
            patch.setattr(obj, name, forbidden)
        assert main(["status", CHANGE, "--json"]) == 0
    entry, = json.loads(capfd.readouterr().out)["runs"]
    assert entry["head"] == completed and entry["tasks"] == {"done": 7, "total": 7}
    assert entry["status"] == "paused" and entry["pause"]["reason"] == "human_checks"
    assert entry["pr"] == s.url and "finish" in entry["action"]["actions"]
    assert before == (snapshot(s.run.dir), snapshot(s.ctx.m.repo), snapshot(s.remote))
    assert effects == (len(s.pushes), len(s.gh), len(s.fake.calls))


def assert_done_once(s, original, completed, capfd):
    fresh = Run(s.run.dir)
    assert fresh.state.status == "done" and fresh.state.pause is None
    assert s.ctx.head() == seal.git(s.remote, "rev-parse", s.ctx.ref).decode().strip() == completed
    assert_completion(s, original, completed)
    finished = [e for e in agents.read_jsonl(s.run.dir / "events.jsonl") if e["event"] == "finished"]
    assert len(finished) == 1 and finished[0]["head"] == completed
    before = snapshot(s.run.dir)
    effects = len(s.pushes), len(s.gh), len(s.fake.calls)
    assert main(["finish", CHANGE]) == 1
    assert main(["act", CHANGE, "finish", "--json"]) == 1
    capfd.readouterr()
    # Commands take the existing lock, whose mtime can change on refusal.
    assert {p: data for p, data in snapshot(s.run.dir).items() if str(p) != "lock"} == {
        p: data for p, data in before.items() if str(p) != "lock"}
    assert effects == (len(s.pushes), len(s.gh), len(s.fake.calls))


@pytest.mark.scenario("specloop-interface/native-publication-lifecycle/status-after-native-partial-finish")
@pytest.mark.scenario("specloop-forge/native-finish-recovery-on-both-forges/native-push-and-ready-retries")
@pytest.mark.scenario("publish-base-sync/native-completion-credential-boundary/helpers-stay-out-of-completion-objects")
@pytest.mark.scenario("publish-base-sync/finish-push-helper-forwarding/finish-and-retry-use-source-helpers")
@pytest.mark.scenario("publish-base-sync/credential-failure-boundary/failed-transport-diagnostics-are-safe")
@pytest.mark.parametrize("mode", ["direct", "act"])
@pytest.mark.parametrize("failure", ["exit", "timeout"])
@pytest.mark.parametrize("with_stderr", [False, True])
def test_native_push_failure_and_corrected_helper_retry(
        transport, monkeypatch, capfd, caplog, mode, failure, with_stderr):
    s = transport
    source = SimpleNamespace(src=s.ctx.m.repo)
    set_helpers(source, HELPERS)
    configs = [source.src / ".git/config", s.ctx.repo / "config"]
    before = [p.read_bytes() for p in configs]
    parent = dict(os.environ)
    saved, original = (s.run.dir / "state.json").read_bytes(), s.ctx.head()
    stderr = b"discard-prefix " + b"z" * 450 + b" / ".join(HELPERS) + b" safe-tail" if with_stderr else b""
    stdout = SENTINEL + b"stdout-only-secret"
    # An unsafe command representation must also disappear from displayed tracebacks.
    unsafe_argv = ["git", "push", os.fsdecode(SENTINEL)]
    s.transport_error = (subprocess.CalledProcessError(23, unsafe_argv, stdout, stderr)
                         if failure == "exit" else subprocess.TimeoutExpired(unsafe_argv, 300, stdout, stderr))
    argv = ["finish", CHANGE] if mode == "direct" else ["act", CHANGE, "finish", "--json"]
    rendered = ""
    if mode == "direct":
        with pytest.raises(RuntimeError) as caught:
            main(argv)
        detail = str(caught.value)
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
        rendered = "".join(traceback.format_exception(caught.value))
    else:
        assert main(argv) == 1
        output = capfd.readouterr()
        assert_safe(output.out + output.err)
        document = json.loads(output.out)
        assert document["outcome"] == "refused"
        detail = document["detail"]
    prefix = f"push {s.ctx.ref} to source origin " + (
        "failed (git exited 23)" if failure == "exit" else "timed out after 300s")
    assert detail.startswith(prefix)
    if with_stderr:
        tail = detail.removeprefix(prefix + ": ")
        assert len(tail) == 400 and "[REDACTED]" in tail and tail.endswith("safe-tail")
        assert "discard-prefix" not in tail
    else:
        assert detail == prefix  # Never fall back to stdout.
    assert s.gh == s.fake.calls == []
    completed = assert_partial(s, saved, original, pushed=False)
    output = capfd.readouterr()
    assert_safe(output.out + output.err + detail + rendered + caplog.text)
    assert_read_only_status(s, completed, monkeypatch, capfd)
    # A pre-completion confirmation is stale even though completion is retryable.
    with monkeypatch.context() as patch:
        patch.setattr(speckit, "finish", lambda *a, **kw: pytest.fail("stale head reached completion"))
        assert main(["finish", CHANGE, "--expect-head", original]) == 1
    output = capfd.readouterr()
    assert original in output.err and completed in output.err
    assert len(s.pushes) == 1 and s.gh == s.fake.calls == []
    assert [p.read_bytes() for p in configs] == before
    corrected = (b"corrected-native-helper",)
    set_helpers(source, corrected)
    before = [p.read_bytes() for p in configs]
    s.transport_error = None
    if s.gitea:
        s.fake.route("GET", ENDPOINT, Reply({"title": "Already ready"}))
    # main constructs a fresh Run and context; no in-memory recovery state is reused.
    retry = ["finish", CHANGE, "--expect-head", completed] if mode == "direct" else argv
    assert main(retry) == 0
    output = capfd.readouterr()
    assert [head for head, _, _ in s.pushes] == [completed, completed]
    for (_, command, options), helpers in zip(s.pushes, (HELPERS, corrected), strict=True):
        assert command == ["git", *seal.SAFE_FLAGS, "-C", str(s.ctx.repo), "push", "-q",
                           s.origin, f"{s.ctx.ref}:{s.ctx.ref}"]
        child = options["env"]
        assert options == dict(check=True, timeout=300, capture_output=True, env=child)
        assert child["GIT_TERMINAL_PROMPT"] == "0"
        assert child["GIT_CONFIG_COUNT"] == str(len(helpers))
        assert [child[f"GIT_CONFIG_VALUE_{i}"] for i in range(len(helpers))] == list(map(os.fsdecode, helpers))
        assert all(child[f"GIT_CONFIG_KEY_{i}"] == "credential.helper" for i in range(len(helpers)))
    internal = {"hash-object", "read-tree", "write-tree", "update-index", "commit-tree", "update-ref"}
    assert internal <= {arg for command, _ in s.commands for arg in command}
    for command, options in s.commands:
        if "push" not in command:
            child = options.get("env", {})
            assert "GIT_CONFIG_COUNT" not in child
            assert not any(os.fsdecode(h) in child.values() for h in (*HELPERS, *corrected) if h)
        if "config" not in command:  # Only the test's explicit local configuration writes contain helpers.
            assert not any(os.fsdecode(h) in command for h in (*HELPERS, *corrected) if h)
    assert sum("commit-tree" in command for command, _ in s.commands) == 1
    assert [p.read_bytes() for p in configs] == before and dict(os.environ) == parent
    artifacts = "".join(p.read_text() for p in s.run.dir.rglob("*")
                        if p.is_file() and p.suffix in {".json", ".jsonl", ".log", ".toml"})
    diagnostics = output.out + output.err + detail + rendered + artifacts + caplog.text
    assert_safe(diagnostics)
    assert "corrected-native-helper" not in diagnostics
    assert b"credential" not in configs[1].read_bytes()
    assert_done_once(s, original, completed, capfd)


@pytest.mark.scenario("specloop-interface/native-publication-lifecycle/status-after-native-partial-finish")
@pytest.mark.scenario("specloop-forge/native-finish-recovery-on-both-forges/native-push-and-ready-retries")
@pytest.mark.scenario("specloop-forge/verified-gitea-finish/retry-observes-current-title")
@pytest.mark.parametrize("failure", ["lost", "lost_ignored", "ignored", "mismatch", "verify", "malformed"])
def test_native_readiness_readback_and_retry(transport, monkeypatch, capfd, failure):
    s = transport
    saved, original = (s.run.dir / "state.json").read_bytes(), s.ctx.head()
    if s.gitea:
        verified = {"ignored": "Draft: Title", "lost_ignored": "Draft: Title", "mismatch": "Other"}.get(failure, "Title")
        reply = (Reply(status=403) if failure == "verify" else Reply([]) if failure == "malformed"
                 else Reply({"title": verified}))
        s.fake.route("GET", ENDPOINT, Reply({"title": "Draft: Title"}), reply)
        s.fake.route("PATCH", ENDPOINT, OSError("response lost") if failure.startswith("lost") else Reply({}))
    else:
        def gh(*args):
            s.gh.append(args)
            assert Run(s.run.dir).state.pause.reason == "human_checks"
            assert seal.git(s.remote, "rev-parse", s.ctx.ref).decode().strip() == s.ctx.head()
            if args[:2] == ("pr", "ready") or failure == "verify":
                raise subprocess.TimeoutExpired(["gh", *args], 30)
            assert args[:2] == ("pr", "view")
            return "false" if failure == "lost" else "true" if failure != "malformed" else "invalid"
        monkeypatch.setattr(cli, "_gh", gh)
    argv = ["act", CHANGE, "finish", "--json"]
    assert main(argv) == int(failure != "lost")
    result = json.loads(capfd.readouterr().out)
    assert result["outcome"] == ("done" if failure == "lost" else "refused")
    if s.gitea:
        assert [(c.method, c.body) for c in s.fake.calls] == [
            ("GET", None), ("PATCH", {"title": "Title"}), ("GET", None)]
    else:
        assert s.gh == [("pr", "ready", s.url, "-R", s.ctx.m.github),
                        ("pr", "view", s.url, "-R", s.ctx.m.github, "--json", "isDraft", "--jq", ".isDraft")]
    completed = s.ctx.head()
    if failure != "lost":
        assert_partial(s, saved, original, pushed=True)
        assert_read_only_status(s, completed, monkeypatch, capfd)
        retry_patch = failure in {"ignored", "lost_ignored"}
        if s.gitea:
            title = "Human's current title"
            s.fake.route("GET", ENDPOINT, Reply({"title": "Draft: " + title if retry_patch else title}),
                         Reply({"title": title}))
            if retry_patch:
                s.fake.route("PATCH", ENDPOINT, Reply({}))
        else:
            # Already ready remotely: ready fails again, but fresh read-back reconciles it.
            failure = "lost"
        assert main(argv) == 0
        capfd.readouterr()
        assert [head for head, _, _ in s.pushes] == [completed, completed]
        if s.gitea:
            assert [c.method for c in s.fake.calls] == ["GET", "PATCH", "GET", "GET"] + (
                ["PATCH", "GET"] if retry_patch else [])
            if retry_patch:
                assert s.fake.calls[4].body == {"title": "Human's current title"}
        else:
            assert s.gh[2:] == s.gh[:2]
    assert sum("commit-tree" in command for command, _ in s.commands) == 1
    assert_done_once(s, original, completed, capfd)


@pytest.mark.scenario("speckit-format/native-completion-commit/complete-status-is-idempotent")
@pytest.mark.parametrize("tasks", ["valid", "unfinished", "empty", "ambiguous"])
def test_completed_adapter_rechecks_live_tasks(transport, tasks):
    s = transport
    head = spec_format.SPECKIT.finish(s.ctx.repo, CHANGE, True, head=s.ctx.head())["commit"]
    seal.git(s.ctx.repo, "update-ref", s.ctx.ref, head)
    text = s.ctx.tasks_text(head)
    if tasks != "valid":
        text = (text.replace("[X]", "[ ]", 1) if tasks == "unfinished" else "" if tasks == "empty"
                else text + "\n## Phase 4: Duplicate\n- [X] T001 Duplicate\n")
        head = native_commit(s.ctx, {s.ctx.tasks_path: text})
    before = snapshot(s.ctx.repo)
    if tasks == "valid":
        assert spec_format.SPECKIT.finish(s.ctx.repo, CHANGE, True, head=head) == {"commit": head}
    else:
        with pytest.raises(speckit.SpecError):
            spec_format.SPECKIT.finish(s.ctx.repo, CHANGE, True, head=head)
    assert snapshot(s.ctx.repo) == before
    assert spec_format.SPECKIT.tasks_paths_at(s.ctx.repo, head, CHANGE) == [s.ctx.tasks_path]
    assert s.pushes == s.gh == s.fake.calls == []
