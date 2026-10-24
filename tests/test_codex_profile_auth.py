"""A codex profile can use its own login (an API key), and the seal still scans it."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from specloop import manifest, seal
from specloop.agents import codex


def data(**api):
    return {"run": {"repo": "/tmp/repo", "change": "c", "branch": "spec/c"},
            "roles": {"implement": ["sub", "api"], "review": "sub"},
            "profiles": {"sub": {"adapter": "codex", "model": "m"},
                         "api": {"adapter": "codex", "model": "m", **api}},
            "paths": {"allowed": ["src/**"]},
            "verify": [{"id": "t", "run": ["true"]}]}


@pytest.mark.scenario("provider-fallback/per-profile-codex-login/a-profile-names-its-own-login")
def test_profile_auth_parses_into_the_chain():
    m = manifest.parse(data(auth="~/.codex-api/auth.json"))
    assert [p.name for p in m.chains["implement"]] == ["sub", "api"]
    assert m.chains["implement"][1].auth == "~/.codex-api/auth.json"
    assert m.chains["implement"][0].auth == ""


@pytest.mark.scenario("provider-fallback/per-profile-codex-login/a-profile-names-its-own-login")
@pytest.mark.parametrize("profile", [{"adapter": "claude", "model": "m", "auth": "~/x.json"},
                                     {"adapter": "codex", "model": "m", "base_url": "http://h", "auth": "~/x.json"}])
def test_auth_is_refused_outside_a_subscription_codex_profile(profile):
    d = data(); d["profiles"]["api"] = profile
    with pytest.raises(ValueError, match="auth applies only"):
        manifest.parse(d)


@pytest.mark.scenario("provider-fallback/per-profile-codex-login/a-profile-names-its-own-login")
def test_codex_home_links_the_profile_login(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".specloop" / "homes" / "api"
    default = SimpleNamespace(home=home, profile=manifest.Profile("api", "codex", "m"))
    codex._home(default)
    assert Path(os.readlink(home / "auth.json")) == tmp_path / ".codex" / "auth.json"
    api = SimpleNamespace(home=home, profile=manifest.Profile("api", "codex", "m", auth="~/.codex-api/auth.json"))
    codex._home(api)  # a changed login replaces the stale link
    assert Path(os.readlink(home / "auth.json")) == tmp_path / ".codex-api" / "auth.json"
    codex._home(api)  # idempotent
    assert Path(os.readlink(home / "auth.json")) == tmp_path / ".codex-api" / "auth.json"


@pytest.mark.scenario("provider-fallback/per-profile-codex-login/every-codex-login-is-a-sealed-secret")
def test_seal_scans_every_codex_login(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for directory, payload in ((".codex", {"tokens": {"access_token": "a" * 30}}),
                               (".codex-api", {"OPENAI_API_KEY": "sk-" + "b" * 40})):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "auth.json").write_text(json.dumps(payload))
    values = seal.credential_values({})
    assert "a" * 30 in values and "sk-" + "b" * 40 in values
