"""Specloop intake routing, with observable claims and fake CLI boundaries."""

import json
import os
import subprocess
from functools import partial
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text

from nightshift import cli, daemon, queue
from nightshift.config import Config, Repo, load


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "repository.toml"
    path.write_text(manifest_text(tmp_path / "source"))
    return path


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "daemon")
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    return tmp_path


def test_engine_and_manifest_config(tmp_path, manifest):
    config = tmp_path / "config.toml"
    config.write_text('''
[[repos]]
name = "owner/default"
verify = "pytest"
[[repos]]
name = "owner/spec"
engine = "specloop"
manifest = "repository.toml"
[labels]
spec_draft = "draft-me"
spec_review = "review-me"
spec_approved = "approved"
''')
    cfg = load(config)
    assert cfg.repos[0].engine == "specloop"
    assert cfg.repos[0].manifest is None
    assert cfg.repos[0].verify == ""
    assert any(note.startswith("repos.verify ") for note in cfg.notes)
    assert cfg.repos[1].engine == "specloop"
    assert cfg.repos[1].manifest == manifest.resolve()
    assert cfg.repos[1].verify == ""
    assert (cfg.labels.spec_draft, cfg.labels.spec_review, cfg.labels.spec_approved) == (
        "draft-me", "review-me", "approved"
    )
    assert queue.Labels().admission == ("agent:ready", "agent:revise")
    assert (queue.Labels().spec_draft, queue.Labels().spec_review, queue.Labels().spec_approved) == (
        "spec:draft", "spec:review", "spec:approved"
    )


@pytest.mark.parametrize("settings,reason", [
    ('engine = "typo"', "engine"),
    ('manifest = ""', "non-empty path"),
    ('manifest = 42', "non-empty path"),
    ('engine = "specloop"\nmanifest = ""', "non-empty path"),
    ('engine = "specloop"\nmanifest = 42', "non-empty path"),
])
def test_invalid_engine_configuration_rejected(tmp_path, settings, reason):
    config = tmp_path / "config.toml"
    config.write_text('[[repos]]\nname = "owner/repo"\nverify = "true"\n' + settings)
    with pytest.raises(ValueError, match=reason):
        load(config)


@pytest.mark.parametrize("engine", [None, "specloop"])
def test_direct_repo_defaults_to_specloop(manifest, engine):
    kwargs = {} if engine is None else {"engine": engine}
    repo = Repo("owner/sample", manifest=manifest, **kwargs)
    assert repo.engine == "specloop"
    assert repo.verify == ""
    assert repo.manifest == manifest.resolve()
    assert Repo("owner/sample", **kwargs).manifest is None


@pytest.mark.parametrize("manifest", ["", "   ", 42, False])
def test_default_repo_still_rejects_invalid_manifest_paths(manifest):
    with pytest.raises(ValueError, match="owner/sample: manifest must be a non-empty path"):
        Repo("owner/sample", manifest=manifest)


RETIRED = ('owner/sample: worker engine is retired; use engine = "specloop" '
           '(or omit engine) and configure a trusted manifest')


@pytest.mark.scenario("issue-intake/specloop-repository-engine/explicit-worker-fails-clearly")
@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/worker-rejection-takes-precedence")
@pytest.mark.parametrize("engine,reason", [
    ("worker", RETIRED),
    ("typo", "owner/sample: unknown engine 'typo'; engine must be 'specloop'"),
])
@pytest.mark.parametrize("verify", [None, "pytest"])
@pytest.mark.parametrize("manifest", [None, "", 42])
def test_direct_repo_rejects_engine_before_manifest(engine, reason, verify, manifest):
    kwargs = {} if verify is None else {"verify": verify}
    with pytest.raises(ValueError) as error:
        Repo("owner/sample", engine=engine, manifest=manifest, **kwargs)
    assert str(error.value) == reason


@pytest.mark.scenario("issue-intake/specloop-repository-engine/explicit-worker-fails-clearly")
@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/worker-rejection-takes-precedence")
@pytest.mark.parametrize("engine,reason", [
    ("worker", RETIRED),
    ("typo", "owner/sample: unknown engine 'typo'; engine must be 'specloop'"),
])
@pytest.mark.parametrize("verify", ["", 'verify = "pytest"\n'])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("command", [["run", "--once"], ["daemon", "status"], ["preflight"]])
def test_invalid_engine_rejected_before_dispatch(
    tmp_path, monkeypatch, capsys, engine, reason, verify, legacy, command,
):
    config = tmp_path / "config.toml"
    # Invalid legacy settings and an earlier bad manifest must never mask the
    # engine diagnostic. Neither missing verify nor a missing manifest does so.
    prefix = '''endpoints = [{context_tokens = {unused = "not-an-integer"}}]
models = false
[daemon]
concurrency = "obsolete"
[[repos]]
name = "owner/earlier"
manifest = 42
''' if legacy else ""
    config.write_text(prefix + f'[[repos]]\nname = "owner/sample"\nengine = "{engine}"\n'
                      + verify + ('models = false\nmanifest = ""\n' if legacy else ""))
    with pytest.raises(ValueError) as error:
        load(config)
    assert str(error.value) == reason

    def forbidden(*args, **kwargs):
        pytest.fail("configuration failure must precede dispatch")

    monkeypatch.setattr(cli.config, "ENV_FILE", tmp_path / "absent-env")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(daemon, "loop", forbidden)
    assert cli.main([*command, "--config", str(config)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"configuration error: {reason}\n"
    assert "Traceback" not in output.err


@pytest.mark.parametrize("settings", ['manifest = ""', 'manifest = 42', 'engine = ['])
def test_cli_reports_other_config_value_errors(isolated, monkeypatch, capsys, settings):
    config = isolated / "config.toml"
    config.write_text('[[repos]]\nname = "owner/sample"\n' + settings)
    with pytest.raises(ValueError) as error:
        load(config)
    monkeypatch.setattr(cli.config, "ENV_FILE", isolated / "absent-env")
    monkeypatch.setattr(daemon, "loop", lambda *a, **kw: pytest.fail("dispatched invalid config"))
    assert cli.main(["run", "--once", "--config", str(config)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"configuration error: {error.value}\n"


@pytest.mark.scenario("nightshift-cli/explicit-daemon-controls/daemon-controls-retain-boundaries")
@pytest.mark.parametrize("command", ["pause", "resume", "jobs"])
def test_config_rejection_does_not_block_local_controls(isolated, monkeypatch, capsys, command):
    config = isolated / "config.toml"
    config.write_text('[[repos]]\nname = "owner/sample"\nengine = "worker"\n')
    monkeypatch.setattr(cli.config, "ENV_FILE", isolated / "absent-env")

    def forbidden(*args, **kwargs):
        pytest.fail("local controls must not load enrollment or dispatch external commands")

    monkeypatch.setattr(cli.config, "load", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    jobs = []
    monkeypatch.setattr(cli, "_jobs", lambda *a, **kw: jobs.append((a, kw)) or 0)
    if command == "resume":
        daemon.PAUSE_FILE.touch()
    if command == "jobs":
        monkeypatch.setattr(cli.config, "load_env_file", forbidden)
    assert cli.main([*([] if command == "jobs" else ["daemon"]), command, "--config", str(config)]) == 0
    assert daemon.PAUSE_FILE.exists() == (command == "pause")
    assert jobs == ([((None, None), {"show_all": False})] if command == "jobs" else [])
    assert capsys.readouterr().err == ""


class DraftGh:
    def __init__(self, labels=("agent:ready",)):
        self.labels = set()
        self.events = []
        for label in labels:
            self.label_event(label, "labeled")
        self.calls = []
        self.prs = []
        self.comments = [{"id": 1, "body": "Keep it small.", "authorAssociation": "COLLABORATOR"}]
        self.pr_url = "https://github.com/owner/sample/pull/8"

    def label_event(self, label, event, actor="maintainer"):
        if event == "labeled":
            if label in self.labels:
                return
            self.labels.add(label)
        else:
            if label not in self.labels:
                return
            self.labels.remove(label)
        self.events.append({"id": len(self.events) + 1, "event": event, "label": {"name": label},
                            "actor": {"login": actor}})

    def __call__(self, args):
        self.calls.append(args)
        if args == ["api", "repos/owner/sample"]:
            return json.dumps({"owner": {"login": "owner", "type": "User"}})
        if args[:2] == ["api", "repos/owner/sample/issues/7"]:
            return json.dumps({"author_association": "OWNER"})
        if args[0] == "api" and args[1].startswith("repos/owner/sample/collaborators/"):
            assert args[2:] == ["--include"]
            status = 204 if args[1].rsplit("/", 1)[1] == "maintainer" else 404
            return f"HTTP/2.0 {status} Status\r\n\r\n"
        if args[:2] == ["issue", "list"]:
            armed = args[args.index("--label") + 1] in self.labels
            return json.dumps([{"number": 7, "title": "Add a gate", "body": "Please draft it."}] if armed else [])
        if args[:2] == ["issue", "view"]:
            return json.dumps({"state": "OPEN", "title": "Add a gate", "body": "Please draft it.",
                               "labels": [{"name": label} for label in self.labels],
                               "comments": self.comments})
        if args[:2] == ["pr", "list"]:
            if "--label" in args:
                return "[]"  # This drafting fixture has no approved PRs.
            return json.dumps(self.prs)
        if args[:2] == ["pr", "create"]:
            number = max((pr["number"] for pr in self.prs), default=7) + 1
            self.prs.append({"number": number, "state": "OPEN", "headRefOid": "abc123"})
            self.pr_url = f"https://github.com/owner/sample/pull/{number}"
            return self.pr_url
        if args[:2] == ["pr", "view"]:
            number = int(args[2].split("/")[-1])
            pr = next(pr for pr in self.prs if pr["number"] == number)
            return json.dumps({"state": "OPEN", "headRefOid": "abc123", **pr,
                               "url": f"https://github.com/owner/sample/pull/{number}"})
        if args[:2] == ["pr", "edit"]:
            return ""
        if args[0] == "api":
            if args[1].endswith("/events"):
                assert args[-2:] == ["--paginate", "--slurp"]
                return json.dumps([self.events[:1], self.events[1:]])
            if "PATCH" in args:
                number = int(args[1].split("/")[-1])
                next(c for c in self.comments if c["id"] == number)["body"] = args[-1].removeprefix("body=")
                return "{}"
            return json.dumps([self.comments])
        if args[:2] == ["issue", "comment"]:
            self.comments.append({"id": len(self.comments) + 1, "body": args[-1]})
            return ""
        if args[:2] == ["issue", "edit"]:
            for i, arg in enumerate(args):
                if arg == "--remove-label":
                    self.label_event(args[i + 1], "unlabeled")
                elif arg == "--add-label":
                    self.label_event(args[i + 1], "labeled")
            return ""
        pytest.fail(f"unexpected GitHub call: {args}")


@pytest.mark.scenario("issue-intake/engine-per-repository/specloop-repository-skips-worker-claims")
@pytest.mark.scenario("issue-intake/specloop-repository-engine/omitted-engine-uses-specloop")
@pytest.mark.scenario("issue-intake/specloop-repository-engine/specloop-repository-skips-worker-claims")
@pytest.mark.scenario("issue-intake/specloop-repository-engine/omitted-manifest-holds-only-its-repository")
@pytest.mark.scenario("worker-retirement/sole-execution-engine/worker-execution-is-absent")
@pytest.mark.scenario("worker-retirement/retired-configuration-is-inert/old-knobs-cannot-control-builds")
@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/codex-enrollment-needs-no-worker-login")
@pytest.mark.real_trust
@pytest.mark.parametrize("entry", ["daemon", "cli", "legacy-config", "missing-manifest"])
@pytest.mark.parametrize("engine", [None, "specloop"])
@pytest.mark.parametrize("labels", [("agent:ready",), ("spec:draft",), ("agent:ready", "spec:draft")])
def test_specloop_repository_drafts_without_worker_claims(
    isolated, monkeypatch, manifest, labels, entry, engine,
):
    config = isolated / "config.toml"
    config.write_text('''[[repos]]
name = "owner/sample"
manifest = "repository.toml"
''' + (f'engine = "{engine}"\n' if engine else "") + ('''verify = "worker-only-verify"
[repos.models]
implement = "retired:unused"
review = "retired:unused"
[daemon]
max_tasks_per_night = 0
[[endpoints]]
name = "retired"
base_url = "http://unused.invalid"
auth_env = "RETIRED_ENDPOINT_TOKEN"
[models]
implement = "retired:unused"
''' if entry == "legacy-config" else "") + ('''
[[repos]]
name = "owner/held"
''' if entry == "missing-manifest" else ""))
    cfg = load(config)
    assert all(repo.engine == "specloop" for repo in cfg.repos)
    if entry == "legacy-config":
        assert any(note.startswith("daemon.max_tasks_per_night ") for note in cfg.notes)
    if entry == "missing-manifest":
        # Hold the first repository and still deliver the healthy one's draft.
        cfg.repos.reverse()
    original_manifest = manifest.read_bytes()
    # Exercise Python's env-file loading with only front-door credentials.
    env_file = isolated / "env"
    env_file.write_text("export GH_TOKEN='github_pat_test'\n")
    monkeypatch.setattr(cli.config, "ENV_FILE", env_file)
    monkeypatch.setattr(os, "environ", {"PATH": "", "HOME": str(isolated)})
    gh = DraftGh(labels)
    held = DraftGh(labels)
    commands = []
    health = daemon.RepoHealth()

    def github(args):
        if any("owner/held" in arg for arg in args):
            # Reuse the same API fixture with independent labels/events for
            # the held repository, including its production trust lookups.
            return held([arg.replace("owner/held", "owner/sample") for arg in args])
        return gh(args)

    def run(args, **kwargs):
        commands.append(args)
        expected = {"capture_output": True, "text": True, "check": False}
        if args[0] == "git":
            expected["timeout"] = 30
        assert kwargs == expected
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "version": 1, "outcome": "drafted", "branch": "spec/owner__sample-issue-7-add-a-gate",
            "commit": "abc123", "questions": [], "contradictions": [], "risks": [], "problems": [],
        }), "")

    monkeypatch.setattr(queue, "_gh", github)
    monkeypatch.setattr(daemon.specloop_intake, "run_git", run)
    # Inject completed CLI results for this routing/delivery test; production
    # now starts propose in a separate process (covered by the background tests).
    monkeypatch.setattr(daemon, "poll_specloop", partial(daemon.poll_specloop, specloop_runner=run))

    # No worker checkout is required. A second poll does not duplicate delivery.
    for _ in range(2):
        if entry == "missing-manifest":
            daemon.poll_specloop(cfg, health)
            assert health.failures == {"owner/held": _ + 1}
            assert not health.item_failures
        elif entry == "daemon":
            daemon.loop(cfg, {}, once=True)
        else:
            assert cli.main(["run", "--once", "--config", str(config)]) == 0
            assert os.environ["GH_TOKEN"] == "github_pat_test"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
    assert "RETIRED_ENDPOINT_TOKEN" not in os.environ
    assert manifest.read_bytes() == original_manifest
    assert len(commands) == 2
    assert commands[1] == ["git", "-C", str(isolated / "source"), "push", "origin",
                           "abc123:refs/heads/spec/owner__sample-issue-7-add-a-gate"]
    command = commands[0]
    assert command[:3] == ["specloop", "propose", str(manifest)]
    assert command[-1] == "--json"
    assert command[command.index("--change") + 1] == "owner__sample-issue-7-add-a-gate"
    assert command[command.index("--branch") + 1] == "spec/owner__sample-issue-7-add-a-gate"
    assert "--idea" not in command
    payload = Path(command[command.index("--idea-file") + 1])
    assert payload.is_absolute()
    assert payload.read_text(encoding="utf-8") == "Add a gate\n\nPlease draft it.\n\nKeep it small."
    receipt = json.loads((daemon.STATE_DIR / "proposals/owner__sample/7.json").read_text())
    assert receipt["result"]["outcome"] == "drafted"
    assert receipt["result"]["commit"] == "abc123"
    assert not queue.CLAIM_DIR.exists()
    assert not any("--add-label" in call and "agent:working" in call for call in gh.calls)
    assert len([call for call in gh.calls if call[:2] == ["pr", "create"]]) == 1
    assert gh.labels == set()
    assert gh.pr_url in gh.comments[-1]["body"]
    if entry == "missing-manifest":
        assert cfg.repos[0].manifest is None
        assert held.labels == set(labels)
        assert held.prs == []
        assert len(held.comments) == 1
        assert not any(call[:2] in (["issue", "edit"], ["issue", "comment"],
                                   ["pr", "create"], ["pr", "edit"]) for call in held.calls)
        assert not (daemon.STATE_DIR / "proposals/owner__held").exists()


def test_pause_prevents_specloop_commands(isolated, monkeypatch, manifest):
    daemon.PAUSE_FILE.touch()
    monkeypatch.setattr(daemon.specloop_intake, "propose_pending",
                        lambda *a, **k: pytest.fail("drafted while paused"))
    daemon.loop(Config(repos=[Repo("o/r", engine="specloop", manifest=manifest)]), {}, once=True)


@pytest.mark.scenario("issue-intake/specloop-repository-engine/omitted-engine-uses-specloop")
@pytest.mark.scenario("issue-intake/latest-label-actor-authorization/untrusted-actor-ignored")
@pytest.mark.real_trust
@pytest.mark.parametrize("engine", [None, "specloop"])
def test_default_engine_preserves_latest_label_actor_gate(isolated, monkeypatch, manifest, engine):
    config = isolated / "config.toml"
    config.write_text('[[repos]]\nname = "owner/sample"\nmanifest = "repository.toml"\n'
                      + (f'engine = "{engine}"\n' if engine else ""))
    cfg = load(config)
    gh = DraftGh()
    gh.label_event("agent:ready", "unlabeled")
    gh.label_event("agent:ready", "labeled", actor="outsider")
    original_manifest = manifest.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("untrusted trigger must not launch specloop or git")

    monkeypatch.setattr(daemon.specloop_intake, "run_git", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    health = daemon.RepoHealth()
    for _ in range(2):
        daemon.poll_specloop(cfg, health, runner=gh, specloop_runner=forbidden)
    assert cfg.repos[0].engine == "specloop"
    assert not health.failures and not health.item_failures
    assert gh.labels == {"agent:ready"}
    assert not gh.prs and len(gh.comments) == 2
    assert "<!-- nightshift:draft:label-authorization-7:3 -->" in gh.comments[-1]["body"]
    assert len([call for call in gh.calls if call[:2] == ["issue", "comment"]]) == 1
    assert not queue.CLAIM_DIR.exists()
    assert not (daemon.STATE_DIR / "proposals/owner__sample").exists()
    assert manifest.read_bytes() == original_manifest
    assert any(call[:2] == ["api", "repos/owner/sample/collaborators/outsider"]
               for call in gh.calls)
    assert not any(call[:2] in (["issue", "edit"],
                               ["pr", "create"], ["pr", "edit"]) for call in gh.calls)


def test_unavailable_specloop_repo_does_not_stop_other_repos(isolated, monkeypatch, manifest):
    cfg = Config(repos=[Repo(name, engine="specloop", manifest=manifest) for name in ("o/bad", "o/good")])
    seen = []

    def poll(repo, *args, **kwargs):
        seen.append(repo.name)
        if repo.name == "o/bad":
            raise queue.GhError("offline", transient=True)

    monkeypatch.setattr(daemon.specloop_intake, "propose_pending", poll)
    health = daemon.RepoHealth()
    daemon.poll_specloop(cfg, health, runner=DraftGh(()))
    assert seen == ["o/bad", "o/good"]
    assert health.failures == {"o/bad": 1}
