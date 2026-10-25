"""Legacy worker keys are reported by presence, never converted into policy."""

import os
import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text
from nightshift import config
from specloop import manifest


KEYS = (
    "models", "endpoints", "repos.models", "repos.verify", "repos.concurrency",
    "daemon.max_tasks_per_night", "daemon.concurrency", "daemon.review_attempts",
    "daemon.implement_max_turns", "daemon.review_max_turns", "daemon.mirror_to_paseo",
)


def legacy_toml(keys, literal, engine=""):
    sections = {"": [], "daemon": [], "repos": []}
    for qualified in keys:
        scope, _, key = qualified.rpartition(".")
        sections[scope].append(f"{key} = {literal}\n")
    return ("".join(sections[""]) + "[daemon]\n" + "".join(sections["daemon"])
            + '[[repos]]\nname = "owner/repo"\n' + engine + "".join(sections["repos"]))


@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/old-knobs-cannot-control-builds")
@pytest.mark.parametrize("engine", ["", 'engine = "specloop"\n'])
@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("literal", [
    "0", "-1", "false", "true", '""', '"secret-value"', "[]", "1979-05-27",
    '{nested = ["secret-value"], context_tokens = {hidden = "not-an-integer"}}',
])
def test_obsolete_values_are_uninterpreted(tmp_path, caplog, engine, key, literal):
    path = tmp_path / "config.toml"
    path.write_text(legacy_toml([], "0", engine))
    baseline = config.load(path)
    assert baseline.notes == []
    path.write_text(legacy_toml([key], literal, engine))
    cfg = config.load(path)
    assert replace(cfg, notes=[]) == baseline
    assert cfg.notes == [f"{key} is ignored; configure a trusted specloop manifest"]
    assert caplog.messages == cfg.notes
    assert cfg.repos[0].verify == "" and cfg.repos[0].concurrency == 1
    assert cfg.mirror_to_paseo is False


@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/old-knobs-cannot-control-builds")
@pytest.mark.parametrize("reverse", [False, True])
def test_notes_are_sorted_deduplicated_and_redacted(tmp_path, caplog, reverse):
    keys = list(reversed(KEYS)) if reverse else KEYS
    path = tmp_path / "config.toml"
    secret = '{secret_key = "https://user:token@unused.invalid/private", auth_env = "SECRET_TOKEN"}'
    path.write_text(legacy_toml(keys, secret) + '''[[repos]]
name = "private/repo-name"
verify = "private verification command"
models = {private_model = "private assignment"}
concurrency = 0
''')
    cfg = config.load(path)
    expected = [f"{key} is ignored; configure a trusted specloop manifest" for key in sorted(KEYS)]
    assert cfg.notes == caplog.messages == expected
    caplog.clear()
    assert config.load(path).notes == caplog.messages == expected


@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/worker-rejection-takes-precedence")
@pytest.mark.parametrize("settings", ["", 'manifest = "missing.toml"\n', 'manifest = 42\n'])
@pytest.mark.parametrize("obsolete", [False, True])
def test_worker_rejection_precedes_notes_and_path_validation(tmp_path, caplog, settings, obsolete):
    path = tmp_path / "config.toml"
    path.write_text(legacy_toml(KEYS if obsolete else [], "false")
                    + 'manifest = 42\n[[repos]]\nname = "owner/retired"\nengine = "worker"\n'
                    + settings)
    with pytest.raises(ValueError) as error:
        config.load(path)
    assert str(error.value) == (
        'owner/retired: worker engine is retired; use engine = "specloop" '
        '(or omit engine) and configure a trusted manifest'
    )
    assert caplog.messages == []


@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/old-knobs-cannot-control-builds")
@pytest.mark.parametrize("enrolled", [False, True])
def test_loading_does_not_translate_manifests_providers_or_credentials(tmp_path, monkeypatch, enrolled):
    trusted = tmp_path / "trusted.toml"
    trusted.write_text(manifest_text(tmp_path / "source"))
    identity = {"change": "owner__repo-issue-7-test", "branch": "spec/test"}
    original = manifest.template(trusted, **identity)
    path = tmp_path / "config.toml"
    path.write_text('''models = {implement = "retired:private-model"}
endpoints = [{name = "retired", base_url = "http://unused.invalid", auth_env = "RETIRED_TOKEN"}]
[daemon]
max_tasks_per_night = 0
concurrency = 0
review_attempts = 0
implement_max_turns = 0
review_max_turns = 0
mirror_to_paseo = true
[[repos]]
name = "owner/repo"
verify = "private verification command"
concurrency = 0
models = {review = "retired:private-reviewer"}
''' + ('manifest = "trusted.toml"\n' if enrolled else ""))
    monkeypatch.setenv("RETIRED_TOKEN", "private-token")
    environment = dict(os.environ)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("config loading must not write, launch a process or contact endpoints")

    with monkeypatch.context() as patch:
        for owner, name in ((subprocess, "run"), (subprocess, "Popen"),
                            (socket, "create_connection"), (Path, "write_text"),
                            (Path, "write_bytes"), (Path, "mkdir")):
            patch.setattr(owner, name, forbidden)
        cfg = config.load(path)
    assert cfg.repos[0].manifest == (trusted if enrolled else None)
    assert manifest.template(trusted, **identity) == original
    assert dict(os.environ) == environment
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert len(cfg.notes) == len(KEYS)


def test_shared_repo_constructor_compatibility():
    repo = config.Repo("owner/repo", "historical verify", "develop", "/clone", 0)
    assert (repo.verify, repo.concurrency) == ("historical verify", 0)
    assert repo.base_ref == "origin/develop" and repo.clone_dir() == Path("/clone")
    assert repo.engine == "specloop"
