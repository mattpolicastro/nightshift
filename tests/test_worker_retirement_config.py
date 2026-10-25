"""Legacy worker keys are reported by presence, never converted into policy."""

import os
import re
import socket
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text
from nightshift import config
from nightshift import specloop_intake as intake
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


@pytest.fixture
def example_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NIGHTSHIFT_SLACK_WEBHOOK", raising=False)
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    selected = tmp_path / "config.toml"
    selected.write_text(example.read_text())
    return selected


@pytest.mark.scenario("specloop-documentation/worker-retirement-guidance/new-enrollment-follows-one-engine")
@pytest.mark.parametrize("engine", [None, "specloop", "worker"])
def test_example_enrollment_structure_and_loader(example_config, engine):
    text = example_config.read_text()
    raw = tomllib.loads(text)
    assert set(raw) == {"daemon", "repos", "notify", "labels"}
    assert set(raw["daemon"]) == {"poll_seconds", "forbidden_probe"}
    assert len(raw["repos"]) == 1
    enrollment = raw["repos"][0]
    assert set(enrollment) == {"name", "base", "manifest"}
    assert isinstance(enrollment["manifest"], str) and enrollment["manifest"].strip()
    if engine is not None:
        example_config.write_text(text.replace("[[repos]]", f'[[repos]]\nengine = "{engine}"'))
    if engine == "worker":
        with pytest.raises(ValueError, match="worker engine is retired"):
            config.load(example_config)
        return
    cfg = config.load(example_config)
    repo, = cfg.repos
    assert repo.engine == "specloop" and cfg.notes == []
    assert (repo.name, repo.base) == (enrollment["name"], enrollment["base"])
    assert repo.manifest == Path(enrollment["manifest"]).expanduser().resolve()
    assert (repo.verify, repo.concurrency, cfg.mirror_to_paseo) == ("", 1, False)
    assert cfg.labels == config.Labels(**raw["labels"]) == config.Labels()
    assert cfg.slack_webhook == raw["notify"]["slack_webhook"] == ""
    assert cfg.poll_seconds == raw["daemon"]["poll_seconds"] == 120
    assert cfg.forbidden_probe == raw["daemon"]["forbidden_probe"]


@pytest.mark.scenario("specloop-documentation/worker-retirement-guidance/new-enrollment-follows-one-engine")
@pytest.mark.parametrize("state", ["missing", "directory", "malformed"])
def test_example_manifest_is_checked_by_readiness_not_loading(example_config, tmp_path, state):
    repo, = config.load(example_config).repos
    trusted = repo.manifest
    assert trusted is not None and not trusted.exists()
    trusted.parent.mkdir(parents=True)
    if state == "directory":
        trusted.mkdir()
    elif state == "malformed":
        trusted.write_text("[broken")
    assert config.load(example_config).repos == [repo]
    assert isinstance(intake.manifest_readiness(repo), intake.ConfigurationError)
    if state == "directory":
        trusted.rmdir()
    template = manifest_text(tmp_path / "source")
    trusted.write_text(template)
    assert isinstance(intake.manifest_readiness(repo), manifest.Manifest)
    assert trusted.read_text() == template


@pytest.mark.scenario("specloop-documentation/worker-retirement-guidance/new-enrollment-follows-one-engine")
def test_example_preparation_is_separate_manifest_configuration(example_config, tmp_path):
    blocks = re.findall(r"(?:^#   .*\n)+", example_config.read_text(), re.MULTILINE)
    snippet, = [block for block in blocks if block.startswith("#   [vars]\n")]
    raw = tomllib.loads("\n".join(line.removeprefix("#   ") for line in snippet.splitlines()))
    template = tomllib.loads(manifest_text(tmp_path / "source"))
    template["run"].update(change="example", branch="spec/example")
    configured = manifest.parse(template | raw)
    assert configured.vars["node_bin"] == str(Path.home() / "n/bin")
    assert configured.prepare_read == (str(Path.home() / "n"),)
    prepare, = configured.prepare
    verify, = configured.verify
    assert prepare.id == "dependencies" and prepare.run and prepare.timeout == 600
    assert verify.id == "tests" and verify.run and verify.expect == 0


@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/old-knobs-cannot-control-builds")
def test_example_with_legacy_keys_keeps_manifest_policy(example_config, tmp_path, caplog):
    baseline = config.load(example_config)
    repo, = baseline.repos
    trusted = repo.manifest
    assert trusted is not None
    trusted.parent.mkdir(parents=True)
    trusted.write_text(manifest_text(tmp_path / "source"))
    policy = intake.manifest_readiness(repo)
    assert isinstance(policy, manifest.Manifest)
    text = example_config.read_text()
    text = 'models = {implement = "retired:secret"}\nendpoints = [{base_url = "https://unused.invalid"}]\n' + text
    text = text.replace("[daemon]", "[daemon]\nmax_tasks_per_night = 0\nconcurrency = 0")
    text = text.replace("[[repos]]", '[[repos]]\nverify = "private-command"\nconcurrency = 0')
    example_config.write_text(text)
    cfg = config.load(example_config)
    assert replace(cfg, notes=[]) == baseline
    keys = ["daemon.concurrency", "daemon.max_tasks_per_night", "endpoints",
            "models", "repos.concurrency", "repos.verify"]
    assert cfg.notes == caplog.messages == [
        f"{key} is ignored; configure a trusted specloop manifest" for key in keys
    ]
    assert intake.manifest_readiness(cfg.repos[0]) == policy
