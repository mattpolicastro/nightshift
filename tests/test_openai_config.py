"""Native endpoint refusal: no credentials, model calls, or fallback needed.

Negative parameter cases were proposed by Ollama GLM-4.7 Flash and reviewed
against the implementation. Integration assertions are independently authored.
"""
from dataclasses import replace

import pytest

from nightshift import config, daemon, preflight, worker


def native(**kw):
    base = config.Endpoint(name="openai", driver="codex-app-server",
        protocol="responses", base_url="https://api.openai.com/v1",
        auth="api_key", billing="metered", auth_env="NIGHTSHIFT_OPENAI_API_KEY",
        models=("test-model",))
    return replace(base, **kw)


INVALID_CASES = [({'protocol': 'http'}, 'requires protocol'),
 ({'base_url': 'https://api.openai.com/v1/'}, 'exact official API'),
 ({'base_url': 'https://api.openai.com/v1?query=1'}, 'exact official API'),
 ({'base_url': 'https://user:pass@api.openai.com/v1'}, 'exact official API'),
 ({'proxy_url': 'http://proxy.example.com'}, 'does not accept proxy_url'),
 ({'auth': 'bearer_token'}, 'explicit api_key'),
 ({'billing': 'unmetered'}, 'explicit api_key'),
 ({'auth_env': 'GH_TOKEN'}, 'another service'),
 ({'auth_env': 'CLAUDE_CODE_OAUTH_TOKEN'}, 'another service'),
 ({'models': []}, 'explicit model'),
 ({'models': 'OPERATOR_SELECTED_MODEL_ID'}, 'explicit model'),
 ({'max_runtime_s': 0}, 'positive integer'),
 ({'max_tool_calls': True}, 'positive integer'),
 ({'max_output_tokens_total': -1}, 'positive integer'),
 ({'reasoning_effort': ''}, 'reasoning_effort')]


@pytest.mark.parametrize("overrides, expected", INVALID_CASES)
def test_native_config_rejects_invalid_values(overrides, expected):
    assert expected in "; ".join(native(**overrides).configuration_errors())


def test_valid_native_route_is_configured_but_not_qualified():
    ep = native()
    assert ep.configuration_errors() == []
    assert "disabled" in ep.execution_blocker()
    assert not native(name="anthropic", base_url="").is_default


def test_preflight_never_sends_native_key_to_legacy_prober():
    ep = native()
    cfg = config.Config(repos=[], endpoints=[ep], implement_model="openai:test-model")
    def forbidden(*args):
        pytest.fail("unqualified native endpoint must not make a model request")
    checks = preflight._one_endpoint(cfg, ep, {ep.auth_env:"synthetic-only"}, prober=forbidden, deep=True)
    assert len(checks) == 1 and not checks[0].ok
    assert "disabled" in checks[0].detail


def test_native_env_refused_before_reading_credential_file(monkeypatch):
    def forbidden(*args):
        pytest.fail("native credentials must not enter Claude's environment builder")
    monkeypatch.setattr(config, "parse_env_file", forbidden)
    with pytest.raises(ValueError, match="disabled"):
        worker._env(native())


def test_native_queue_not_claimed_or_probed(monkeypatch, tmp_path):
    repo = config.Repo(name="owner/sandbox", verify="pnpm test")
    cfg = config.Config(repos=[repo], endpoints=[native()], implement_model="openai:test-model")
    def forbidden(*args):
        pytest.fail("unqualified native route must not claim or probe")
    monkeypatch.setattr(daemon.queue, "claim", forbidden)
    assert daemon.claim_next(cfg, {repo.name: tmp_path}, prober=forbidden) is None


def test_missing_endpoint_cannot_fall_back_to_claude_on_run(monkeypatch, tmp_path):
    repo = config.Repo(name="owner/sandbox", verify="pnpm test")
    cfg = config.Config(repos=[repo], implement_model="openai:test-model")
    monkeypatch.setattr(daemon.queue, "claim", lambda *args: pytest.fail("must not claim"))
    assert daemon.claim_next(cfg, {repo.name:tmp_path}) is None


def test_loading_preserves_native_fields_and_legacy_proxy_meaning(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('''[[endpoints]]
name="openai"
driver="codex-app-server"
protocol="responses"
auth="api_key"
billing="metered"
auth_env="NIGHTSHIFT_OPENAI_API_KEY"
base_url="https://api.openai.com/v1"
models=["test-model"]
max_tool_calls=7
[models]
implement="openai:test-model"
''')
    cfg = config.load(p)
    assert cfg.assign("implement").endpoint.driver == "codex-app-server"
    assert cfg.assign("implement").endpoint.max_tool_calls == 7
    assert cfg.assign("review").endpoint.is_default
    legacy = config.Endpoint(name="proxy", protocol="openai", proxy_url="https://proxy.example/v1")
    assert legacy.driver == "claude-code" and legacy.url == legacy.proxy_url


@pytest.mark.parametrize("body", [
    '[models]\nimplement="missing:test-model"',
    '[[endpoints]]\nname="x"\n[[endpoints]]\nname="x"',
    '[[endpoints]]\nname="x"\nmodels="test-model"',
    '[[endpoints]]\nname="x"\ndriver="unknown"',
])
def test_ambiguous_configuration_fails_loading(tmp_path, body):
    p=tmp_path/"config.toml";p.write_text(body)
    with pytest.raises(ValueError):config.load(p)


def test_invalid_verify_syntax_reported_before_worker():
    repo=config.Repo(name="owner/sandbox",verify="pnpm test | tail -20")
    checks=preflight.run(config.Config(repos=[repo]),{},runner=lambda args:"{}")
    assert any(c.name.startswith("verify syntax") and not c.ok for c in checks)


def test_explicit_auth_cannot_silently_use_default_subscription():
    ep = config.Endpoint(name="anthropic",auth="api_key",auth_env="NIGHTSHIFT_OPENAI_API_KEY")
    assert not ep.is_default
    with pytest.raises(ValueError,match="explicit auth"):
        worker._env(ep)


def test_duplicate_constructor_endpoints_cannot_shadow_native():
    repo=config.Repo(name="owner/sandbox",verify="pnpm test")
    cfg=config.Config(repos=[repo],endpoints=[config.Endpoint(name="openai"),native()],
                      implement_model="openai:test-model")
    ok, why=daemon.endpoints_ready(cfg,repo)
    assert not ok and "unique" in why


@pytest.mark.parametrize("overrides", [{"auth_env":123},{"driver":[]},{"protocol":[]}])
def test_bad_types_report_configuration_errors(overrides):
    assert native(**overrides).configuration_errors()


def test_nonzero_worker_exit_cannot_be_success():
    import json
    events=json.dumps({"type":"result","subtype":"success","is_error":False,
                       "result":"VERDICT: PASS","num_turns":1})
    result=worker.parse_result(worker.Run(returncode=1,events=events))
    assert result is not None and not result.ok
    assert result.subtype == "error_process_exit"
    assert worker.parse_result(worker.Run(returncode=0,events=events)).ok
