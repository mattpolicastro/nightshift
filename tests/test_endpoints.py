"""Arbitrary endpoints: resolution, credential isolation, accounting, preflight.

SPEC-endpoints.md Phase A. The mechanism is small; what these pin is the three
guarantees that were load-bearing on there being exactly ONE endpoint and that
break quietly rather than loudly when a second appears — the subscription cap
counting tasks it never billed, invented money in the tally, and a
subscription credential travelling to a host that is not Anthropic.

The isolation tests are the ones that matter. Asserted isolation is not proven
isolation, which is the lesson `forbidden_probe` already encodes for GH_TOKEN.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nightshift import config as config_mod
from nightshift import daemon, preflight, task, trace, worker
from nightshift.config import Config, Endpoint, Repo

class FakePopen:
    """Stands in for `claude -p`, which is now streamed rather than captured.

    `stdout` must be a line ITERABLE: `_run` reads it line by line so a reader
    can watch the transcript arrive, which is the whole point of the change.
    """

    def __init__(self, lines=(), returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self):
        return self.returncode


SANDBOX = "matt/sandbox"

EVO = Endpoint(
    name="evo",
    protocol="anthropic",
    billing="none",
    base_url="http://evo-host:11434",
    auth_env="NIGHTSHIFT_EVO_TOKEN",
    models=("glm-4.7-flash", "qwen3-coder-next"),
    context_tokens={"glm-4.7-flash": 203_000, "qwen3-coder-next": 262_000},
    max_turns_multiplier=1.5,
)

OAI = Endpoint(
    name="some-oai-host",
    protocol="openai",
    billing="metered",
    base_url="https://api.example.com/v1",
    auth_env="NIGHTSHIFT_OAI_TOKEN",
)


def cfg(**kw) -> Config:
    kw.setdefault("repos", [Repo(name=SANDBOX, verify="pnpm test")])
    return Config(**kw)


# --- §4: resolution ---------------------------------------------------------


def test_a_bare_model_name_resolves_to_the_default_endpoint():
    a = cfg().assign("implement")
    assert a.endpoint.is_default
    assert a.model == "sonnet"


def test_endpoint_colon_model_selects_explicitly():
    a = cfg(endpoints=[EVO], implement_model="evo:glm-4.7-flash").assign("implement")
    assert a.endpoint.name == "evo"
    assert a.model == "glm-4.7-flash"


def test_a_colon_in_a_model_name_is_not_an_endpoint():
    """Ollama tags carry colons of their own: `qwen3.8:27b`, `glm-…:latest`.

    A blind split on the first colon would read `qwen3.8` as an endpoint and
    `27b` as a model. The prefix is an endpoint only if one is DECLARED under
    that name — which is also what keeps every pre-endpoints config meaning
    what it meant.
    """
    c = cfg(endpoints=[EVO], implement_model="qwen3.8:27b")
    assert c.assign("implement").endpoint.is_default
    assert c.assign("implement").model == "qwen3.8:27b"

    tagged = cfg(endpoints=[EVO], implement_model="evo:glm-4.7-flash:latest")
    assert tagged.assign("implement").endpoint.name == "evo"
    assert tagged.assign("implement").model == "glm-4.7-flash:latest"


def test_a_typo_d_endpoint_is_reported_rather_than_silently_defaulted():
    """`evoo:glm-…` would otherwise run on the SUBSCRIPTION, billed and slow.

    Silent fallback turns free work into billed work and hides that the local
    tier is down — the failure §6 refuses.
    """
    c = cfg(endpoints=[EVO], implement_model="evoo:glm-4.7-flash")
    assert c.undeclared_endpoint_refs() == ["evoo:glm-4.7-flash"]
    checks = preflight.run(c, {}, runner=lambda args: '{"name":"x"}')
    assert _find(checks, "endpoint refs").ok is False


def test_a_config_with_no_endpoints_block_behaves_exactly_as_today(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[[repos]]\nname = "o/r"\nverify = "pnpm test"\n\n'
        '[models]\nimplement = "sonnet"\nreview = "opus"\n'
    )
    c = config_mod.load(p)
    assert c.endpoints == []
    for phase, model in (("implement", "sonnet"), ("review", "opus")):
        a = c.assign(phase, c.repos[0])
        assert a.endpoint is config_mod.DEFAULT_ENDPOINT
        assert a.model == model
        assert a.context_tokens == 0
        assert a.max_turns(140) == 140
    assert c.undeclared_endpoint_refs() == []


def test_a_per_repo_models_table_overrides_the_global_one(tmp_path):
    """The setting that makes the feature worth having: a low-stakes repo runs
    local while anything that matters stays on the subscription."""
    p = tmp_path / "config.toml"
    p.write_text(
        "[[endpoints]]\n"
        'name = "evo"\nbase_url = "http://evo-host:11434"\n'
        'auth_env = "NIGHTSHIFT_EVO_TOKEN"\n\n'
        '[[repos]]\nname = "o/sandbox"\nverify = "pnpm test"\n'
        '[repos.models]\nimplement = "evo:glm-4.7-flash"\n\n'
        '[[repos]]\nname = "o/real"\nverify = "pnpm test"\n\n'
        '[models]\nimplement = "sonnet"\nreview = "opus"\n'
    )
    c = config_mod.load(p)
    sandbox, real = c.repos
    assert c.assign("implement", sandbox).endpoint.name == "evo"
    assert c.assign("implement", real).endpoint.is_default
    # The override is per phase, not per repo: review is untouched.
    assert c.assign("review", sandbox).endpoint.is_default


def test_the_turn_budget_is_scaled_by_the_endpoint():
    """A local model is slower per turn, and a truncation costs the whole run."""
    a = cfg(endpoints=[EVO], implement_model="evo:glm-4.7-flash").assign("implement")
    assert a.max_turns(140) == 210
    assert a.context_tokens == 203_000


def test_the_default_endpoint_declared_with_a_url_is_not_the_default_any_more():
    """A URL is what makes an endpoint a different place, not its name.

    §4 allows declaring `anthropic` in order to change it. One declared with a
    `base_url` is somewhere else, so it must not inherit the subscription
    credential just because of what it is called.
    """
    redirected = Endpoint(
        name="anthropic", base_url="https://gateway.example.com",
        auth_env="NIGHTSHIFT_GATEWAY_TOKEN",
    )
    assert redirected.is_default is False
    # Declared without one, it is still just the subscription endpoint.
    assert Endpoint(name="anthropic", billing="metered").is_default is True


# --- §3: one credential, one endpoint --------------------------------------


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """The real credentials file, as `worker._env` merges it."""
    path = tmp_path / "env"
    monkeypatch.setattr(worker, "ENV_FILE", path)
    monkeypatch.setattr(config_mod, "ENV_FILE", path)
    return path


def test_a_local_worker_carries_no_subscription_token(env_file, monkeypatch):
    """The §3 invariant, and the one that must fail loudly if `_env` is reordered."""
    env_file.write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat-real'\n"
        "export NIGHTSHIFT_EVO_TOKEN='ignored-by-ollama'\n"
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    env = worker._env(EVO)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://evo-host:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "ignored-by-ollama"
    # No context window claimed unless one is configured: the CLI's own
    # catalog is right for a model it knows, and wrong only for one it does not.
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in env


def test_the_configured_context_window_reaches_the_run(env_file):
    """Claude Code has no catalog entry for these models, so absent this it
    assumes 200k and auto-compacts to it — wasting a 203k model."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    env = worker._env(EVO, context_tokens=203_000)
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "203000"


def test_the_default_endpoint_still_gets_the_oauth_token(env_file):
    env_file.write_text("export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat-real'\n")
    env = worker._env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-real"
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["CLAUDE_CONFIG_DIR"] == str(worker.CONFIG_DIR)


def test_an_endpoint_gets_its_own_config_dir(env_file):
    """CONFIG_DIR caches credentials, so nothing from the subscription session
    is reachable from a worker aimed somewhere else."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    assert worker._env(EVO)["CLAUDE_CONFIG_DIR"] == str(worker.CONFIG_DIR / "evo")


def test_a_base_url_in_the_env_file_is_not_an_override(env_file):
    """The strip is FINAL: it runs after the env file is merged, not before.

    Before this, a `ANTHROPIC_BASE_URL` line in that file survived the strip
    AND travelled alongside the OAuth token — a subscription credential pointed
    at a third-party host.
    """
    env_file.write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat-real'\n"
        "export ANTHROPIC_BASE_URL='http://evo-host:11434'\n"
        "export ANTHROPIC_AUTH_TOKEN='whatever'\n"
    )
    env = worker._env()
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-real"


def test_a_base_url_in_the_env_file_fails_preflight(env_file):
    checks = preflight.run(
        cfg(),
        {"GH_TOKEN": "github_pat_x", "ANTHROPIC_BASE_URL": "http://evo-host:11434"},
        runner=lambda args: '{"name":"x"}',
    )
    assert _find(checks, "ANTHROPIC_BASE_URL").ok is False


def test_a_worker_holds_no_other_endpoint_s_credential(env_file):
    env_file.write_text(
        "export NIGHTSHIFT_EVO_TOKEN='evo'\nexport NIGHTSHIFT_OAI_TOKEN='oai'\n"
    )
    c = cfg(endpoints=[EVO, OAI])
    env = worker._env(EVO, foreign_auth_envs=c.foreign_auth_envs(EVO))
    assert env["ANTHROPIC_AUTH_TOKEN"] == "evo"
    assert "NIGHTSHIFT_OAI_TOKEN" not in env


def test_an_endpoint_whose_credential_is_unset_refuses_to_run(env_file):
    env_file.write_text("")
    with pytest.raises(ValueError, match="NIGHTSHIFT_EVO_TOKEN"):
        worker._env(EVO)


def test_an_openai_endpoint_is_driven_through_its_proxy(env_file):
    """`claude -p` speaks Anthropic only, so the worker talks to the proxy."""
    env_file.write_text("export NIGHTSHIFT_OAI_TOKEN='oai'\n")
    proxied = Endpoint(**{**OAI.__dict__, "proxy_url": "http://127.0.0.1:4000"})
    assert worker._env(proxied)["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4000"


def test_an_openai_endpoint_with_no_proxy_cannot_be_driven_at_all(env_file):
    env_file.write_text("export NIGHTSHIFT_OAI_TOKEN='oai'\n")
    with pytest.raises(ValueError, match="proxy_url"):
        worker._env(OAI)


def test_the_invocation_carries_the_endpoint_all_the_way_to_the_subprocess(
    env_file, monkeypatch
):
    """The seam between `Assignment` and `claude -p`. Unit-testing `_env` and
    the call site separately left the join itself unchecked."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='ollama-ignores-this'\n")
    seen = {}

    def fake_popen(args, **kw):
        seen["args"], seen["env"] = args, kw["env"]
        return FakePopen()

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)

    c = Config(repos=[Repo(name=SANDBOX, verify="pnpm test",
                           models={"implement": "evo:glm-4.7-flash"})],
               endpoints=[EVO])
    a = c.assign("implement", c.repos[0])
    worker.implement(
        Path("/tmp"), "prompt", model=a.model,
        max_turns=a.max_turns(c.implement_max_turns),
        endpoint=a.endpoint, context_tokens=a.context_tokens,
        foreign_auth_envs=c.foreign_auth_envs(a.endpoint),
    )

    args, env = seen["args"], seen["env"]
    assert args[args.index("--model") + 1] == "glm-4.7-flash"
    assert args[args.index("--max-turns") + 1] == "210"  # 140 x 1.5
    assert env["ANTHROPIC_BASE_URL"] == "http://evo-host:11434"
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "203000"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_the_tool_loop_probe_does_not_deny_the_tool_it_probes_for(
    env_file, monkeypatch
):
    """It did, and it failed a healthy endpoint for it.

    `worker.probe` was passing the REVIEWER's deny list, which contains
    `Write`, and deny beats allow — so the probe could never pass whatever the
    model did. Found on 2026-09-04 by running it against the EVO-X2, where
    `glm-4.7-flash` came back "no usable tool loop" after 142 seconds, having
    already been measured holding one.
    """
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    seen = {}

    monkeypatch.setattr(
        worker.subprocess, "Popen",
        lambda args, **kw: (seen.update(args=args), FakePopen())[1],
    )
    worker.probe(EVO, "glm-4.7-flash")

    args = seen["args"]
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    denied = args[args.index("--disallowedTools") + 1:]
    assert "Write" in allowed
    assert "Write" not in denied


def test_the_probe_fails_a_run_that_errored_or_truncated(env_file, monkeypatch, tmp_path):
    """A correct file left behind by a failed run is not a working endpoint —
    the daemon's own rule is that a run which hit `--max-turns` is a failed
    task, not a partial success."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")

    def fake_run(worktree, prompt, model, max_turns, allowed, denied,
                 endpoint=None, context_tokens=0, foreign_auth_envs=()):
        (worktree / "out.txt").write_text("NIGHTSHIFT PROBE\n")
        return worker.Run(returncode=1, events=json.dumps({
            "type": "result", "subtype": "error_max_turns", "is_error": True,
            "num_turns": 9, "result": "", "modelUsage": {},
        }))

    monkeypatch.setattr(worker, "_run", fake_run)
    ok, detail = worker.probe(EVO, "glm-4.7-flash", directory=tmp_path)
    assert ok is False
    assert "9 turns" in detail


def test_the_probe_accepts_what_a_model_that_held_the_loop_actually_writes():
    """`Read` returns `cat -n`-style output, and glm-4.7-flash uppercased the
    line numbers along with the text: `1\tNIGHTSHIFT PROBE\n2`. Measured
    2026-09-04 — an exact-match check failed a model that held the loop."""
    assert worker.probe_content_ok("1\tNIGHTSHIFT PROBE\n2") is True
    assert worker.probe_content_ok("NIGHTSHIFT PROBE") is True
    # Strict about the transformation: the input file is lowercase, so a model
    # that merely copied it proves nothing about having read and transformed.
    assert worker.probe_content_ok("nightshift probe") is False
    assert worker.probe_content_ok("") is False


# --- §7: preflight ----------------------------------------------------------


def _find(checks, fragment: str) -> preflight.Check:
    for c in checks:
        if fragment in c.name:
            return c
    raise AssertionError(f"no check matching {fragment!r} in {[c.name for c in checks]}")


def _has(checks, fragment: str) -> bool:
    return any(fragment in c.name for c in checks)


def _preflight(c: Config, env: dict, available=("glm-4.7-flash", "qwen3-coder-next")):
    return preflight.run(
        c,
        {"GH_TOKEN": "github_pat_x", **env},
        runner=lambda args: '{"name":"x"}',
        prober=lambda ep, token: list(available),
    )


def test_an_openai_endpoint_with_no_proxy_fails_preflight(env_file):
    checks = _preflight(cfg(endpoints=[OAI]), {"NIGHTSHIFT_OAI_TOKEN": "x"})
    assert _find(checks, "endpoint some-oai-host").ok is False
    assert "proxy_url" in _find(checks, "endpoint some-oai-host").detail


def test_preflight_fails_when_a_model_is_absent_from_its_endpoint(env_file):
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    checks = _preflight(
        cfg(endpoints=[EVO]),
        {"NIGHTSHIFT_EVO_TOKEN": "x"},
        available=("glm-4.7-flash",),
    )
    absent = _find(checks, "endpoint evo models")
    assert absent.ok is False
    assert "qwen3-coder-next" in absent.detail


def test_preflight_fails_when_a_model_declares_no_context_window(env_file):
    """Absent it the run inherits an assumed 200k window — silently wasting a
    262k model, and silently overrunning a smaller one."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    thin = Endpoint(**{**EVO.__dict__, "context_tokens": {"glm-4.7-flash": 203_000}})
    checks = _preflight(cfg(endpoints=[thin]), {"NIGHTSHIFT_EVO_TOKEN": "x"})
    assert _find(checks, "endpoint evo context windows").ok is False


def test_preflight_fails_when_an_endpoint_cannot_be_reached(env_file):
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")

    def dead(ep, token):
        raise OSError("Connection refused")

    checks = preflight.run(
        cfg(endpoints=[EVO]),
        {"GH_TOKEN": "github_pat_x", "NIGHTSHIFT_EVO_TOKEN": "x"},
        runner=lambda args: '{"name":"x"}',
        prober=dead,
    )
    assert _find(checks, "endpoint evo reachable").ok is False


def test_preflight_proves_the_isolation_rather_than_asserting_it(env_file):
    env_file.write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat-real'\n"
        "export NIGHTSHIFT_EVO_TOKEN='x'\n"
    )
    checks = _preflight(cfg(endpoints=[EVO]), {"NIGHTSHIFT_EVO_TOKEN": "x"})
    assert _find(checks, "endpoint evo isolation").ok is True


def test_review_on_a_non_default_endpoint_says_so_out_loud(env_file):
    """Not a failure — a decision the operator has to see on the night they
    made it, rather than find in a config file three weeks later."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    checks = _preflight(
        cfg(endpoints=[EVO], review_model="evo:glm-4.7-flash"),
        {"NIGHTSHIFT_EVO_TOKEN": "x"},
    )
    loud = _find(checks, "review endpoint")
    assert loud.ok is True
    assert "NOT the default endpoint" in loud.detail
    assert "manufactures confidence" in loud.detail


def test_a_per_repo_override_is_reported_too(env_file):
    """Found by running preflight against the real EVO-X2: `[repos.models]` is
    the RECOMMENDED way to route — one repo local, everything that matters on
    the subscription — so leaving it out made the most likely routing the one
    nothing announced."""
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    local = Repo(name="matt/low-stakes", verify="pnpm test",
                 models={"implement": "evo:glm-4.7-flash"})
    c = Config(repos=[Repo(name=SANDBOX, verify="pnpm test"), local],
               endpoints=[EVO])
    checks = _preflight(c, {"NIGHTSHIFT_EVO_TOKEN": "x"})
    loud = _find(checks, "implement endpoint matt/low-stakes")
    assert loud.ok is True
    assert "evo:glm-4.7-flash" in loud.detail
    # The repo that was NOT overridden says nothing.
    assert not _has(checks, f"implement endpoint {SANDBOX}")


def test_a_global_route_is_not_repeated_for_every_repo(env_file):
    env_file.write_text("export NIGHTSHIFT_EVO_TOKEN='x'\n")
    c = Config(repos=[Repo(name=SANDBOX, verify="pnpm test")],
               endpoints=[EVO], implement_model="evo:glm-4.7-flash")
    checks = _preflight(c, {"NIGHTSHIFT_EVO_TOKEN": "x"})
    assert len([c_ for c_ in checks if c_.name.startswith("implement endpoint")]) == 1


def test_nothing_endpoint_shaped_is_reported_when_nothing_is_configured():
    checks = preflight.run(cfg(), {"GH_TOKEN": "github_pat_x"},
                           runner=lambda args: '{"name":"x"}')
    assert not _has(checks, "endpoint evo")
    assert not _has(checks, "review endpoint")


# --- §2.1: the cap counts subscription-billed work --------------------------


def _run_result(*, local: bool) -> trace.Result:
    usage = (
        {"glm-4.7-flash": {"costUSD": 0.32, "costBasis": "unknown"}}
        if local
        else {"claude-sonnet-5": {"costUSD": 2.0}}
    )
    return trace.parse(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 3,
                "total_cost_usd": 0.32 if local else 2.0,
                "modelUsage": usage,
                "result": "OK",
            }
        )
    )


def test_a_run_priced_on_an_unknown_basis_is_not_subscription_billed():
    assert _run_result(local=True).subscription_billed is False
    assert _run_result(local=False).subscription_billed is True


def test_a_run_that_reported_a_window_is_billed_whatever_it_priced():
    """`rate_limit_info` arrives on SUCCESSFUL runs, not just on exhaustion —
    a run that reported one consumed the window it reported on."""
    r = _run_result(local=True)
    r.rate_limit = trace.RateLimit("allowed", "five_hour", 1, False)
    assert r.subscription_billed is True


def test_a_task_run_wholly_off_subscription_does_not_decrement_the_cap():
    report = task.Report(
        issue=None, step=task.Step.SHIP, reason="",
        attempts=[task.Attempt(implement=_run_result(local=True),
                               review=_run_result(local=True))],
    )
    assert report.subscription_billed is False


def test_one_billed_phase_makes_the_whole_task_billed():
    """A local implement reviewed on the subscription still spent the window."""
    report = task.Report(
        issue=None, step=task.Step.SHIP, reason="",
        attempts=[task.Attempt(implement=_run_result(local=True),
                               review=_run_result(local=False))],
    )
    assert report.subscription_billed is True


def test_a_task_with_no_runs_at_all_counts_against_the_cap():
    """A crash before anything was invoked tells us nothing, and over-counting
    is the safe direction to be wrong in."""
    report = task.Report(issue=None, step=task.Step.ESCALATE, reason="crashed")
    assert report.subscription_billed is True


def test_run_claimed_marks_an_off_subscription_task_unbilled(monkeypatch):
    """The join between a task's telemetry and the loop's cap arithmetic.

    `Report.subscription_billed` and `Tally.billed` were each covered; that the
    daemon actually carries one into the other was not.
    """
    report = task.Report(
        issue=None, step=task.Step.SHIP, reason="", pr_url="http://pr/1",
        attempts=[task.Attempt(implement=_run_result(local=True),
                               review=_run_result(local=True))],
    )
    monkeypatch.setattr(daemon.task, "run", lambda *a, **k: report)
    monkeypatch.setattr(daemon.queue, "complete", lambda *a, **k: None)
    monkeypatch.setattr(daemon.notify, "send", lambda *a, **k: None)

    class Issue:
        number, title, repo = 1, "t", SANDBOX

    class Claim:
        worktree, branch = "/tmp/wt", "claude/1"

    tally = daemon.Tally()
    daemon.run_claimed(
        cfg(),
        daemon.Claimed(repo=cfg().repos[0], repo_dir=Path("/tmp/x"),
                       issue=Issue(), claim=Claim()),
        tally,
    )
    assert (tally.shipped, tally.unbilled, tally.billed) == (1, 1, 0)
    # And the invented money stayed out of the tally.
    assert tally.cost == 0.0


def test_the_tally_caps_on_billed_work_not_on_tasks_done():
    t = daemon.Tally(shipped=6, escalated=0, unbilled=4)
    assert t.handled == 6  # what the digest reports
    assert t.billed == 2  # what the cap counts


def test_a_night_of_local_work_does_not_hit_the_cap(monkeypatch):
    """The §2.1 guarantee end to end: eight free tasks under a cap of two."""
    from tests.test_daemon_loop import Stop, harness

    harness(monkeypatch, work=8, stop_after_sleeps=2)

    def run_claimed(cfg_, claimed, tally):
        tally.shipped += 1
        tally.unbilled += 1

    monkeypatch.setattr(daemon, "run_claimed", run_claimed)

    with pytest.raises(Stop):
        daemon.loop(cfg(max_tasks_per_night=2), {SANDBOX: Path("/tmp/x")})


# --- §6: an endpoint is a machine that may be off ---------------------------


def test_an_unreachable_endpoint_leaves_the_task_armed(monkeypatch):
    """Skip, do not claim: an `agent:working` label with nothing working on it
    is recoverable only by `reconcile`. And no fallback — a silent one converts
    a free run into a billed one and hides that the local tier is down."""
    claimed = []
    monkeypatch.setattr(
        daemon.queue, "claim", lambda *a, **k: claimed.append(a) or None
    )

    def dead(ep, token):
        raise OSError("Connection refused")

    c = cfg(endpoints=[EVO], implement_model="evo:glm-4.7-flash")
    assert daemon.claim_next(c, {SANDBOX: Path("/tmp/x")}, prober=dead) is None
    assert claimed == []


def test_an_unproxied_openai_endpoint_is_refused_in_its_own_words():
    """It cannot be driven at all, so say that rather than leaking a urllib
    artifact — `unknown url type: '/v1/models'` — into the daemon log."""
    c = Config(repos=[Repo(name=SANDBOX, verify="pnpm test",
                           models={"review": "some-oai-host:gpt-whatever"})],
               endpoints=[OAI])
    ok, why = daemon.endpoints_ready(c, c.repos[0], prober=None)
    assert ok is False
    assert "proxy_url" in why and "Anthropic only" in why


def test_a_repo_on_the_default_endpoint_probes_nothing(monkeypatch):
    def explode(ep, token):
        raise AssertionError("today's configuration must not probe anything")

    ok, why = daemon.endpoints_ready(cfg(), cfg().repos[0], prober=explode)
    assert (ok, why) == (True, "")
