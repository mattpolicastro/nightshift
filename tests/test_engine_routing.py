"""Per-repository intake engines, with real claim code and fake CLI boundaries."""

import json
import subprocess
from functools import partial
from pathlib import Path

import pytest

from manifest_fixtures import manifest_text

from nightshift import daemon, queue, specloop_client
from nightshift.config import Config, Repo, load
from test_queue import FakeGh


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
name = "owner/worker"
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
    assert cfg.repos[0].engine == "worker"
    assert cfg.repos[0].manifest is None
    assert cfg.repos[0].verify == "pytest"
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
    ('engine = "specloop"\nmanifest = ""', "non-empty path"),
    ('engine = "specloop"\nmanifest = 42', "non-empty path"),
])
def test_invalid_engine_configuration_rejected(tmp_path, settings, reason):
    config = tmp_path / "config.toml"
    config.write_text('[[repos]]\nname = "owner/repo"\nverify = "true"\n' + settings)
    with pytest.raises(ValueError, match=reason):
        load(config)


def test_unknown_engine_without_worker_settings_is_rejected(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[[repos]]\nname = "o/r"\nengine = "typo"')
    with pytest.raises(ValueError, match="engine"):
        load(config)


@pytest.mark.scenario("issue-intake/engine-per-repository/worker-repository-unchanged")
@pytest.mark.parametrize("engine", [None, "worker"])
def test_worker_repository_still_claims_ready_issue(isolated, monkeypatch, engine):
    config = isolated / "config.toml"
    config.write_text('[[repos]]\nname = "owner/worker"\nverify = "pytest"\n'
                      + (f'engine = "{engine}"\n' if engine else ""))
    cfg = load(config)
    gh = FakeGh({"agent:ready": [7]})
    monkeypatch.setattr(queue, "claim", partial(queue.claim, runner=gh))
    monkeypatch.setattr(daemon.specloop_intake, "propose_pending",
                        lambda *a, **k: pytest.fail("worker entered proposal routing"))
    daemon.poll_specloop(cfg, daemon.RepoHealth())
    claimed = daemon.claim_next(cfg, {"owner/worker": isolated})
    assert claimed is not None
    assert claimed.issue.number == 7
    assert claimed.claim.branch == "claude/7"
    assert queue.claim_path("owner/worker", 7).is_file()
    assert any("--add-label" in edit and "agent:working" in edit
               and "--remove-label" in edit and "agent:ready" in edit for edit in gh.edits())
    assert not any(call[0] == "api" for call in gh.calls)


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
@pytest.mark.real_trust
@pytest.mark.parametrize("labels", [("agent:ready",), ("spec:draft",), ("agent:ready", "spec:draft")])
def test_specloop_repository_drafts_without_worker_claims(isolated, monkeypatch, manifest, labels):
    cfg = Config(repos=[Repo("owner/sample", engine="specloop", manifest=manifest)])
    gh = DraftGh(labels)
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        expected = {"capture_output": True, "text": True, "check": False}
        if args[0] == "git":
            expected["timeout"] = 30
        assert kwargs == expected
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "version": 1, "outcome": "drafted", "branch": "spec/sample-issue-7-add-a-gate",
            "commit": "abc123", "questions": [], "contradictions": [], "risks": [], "problems": [],
        }), "")

    for name in ("claim", "reconcile"):
        monkeypatch.setattr(queue, name, lambda *a, **k: pytest.fail("worker queue used"))
    monkeypatch.setattr(daemon, "endpoints_ready", lambda *a, **k: pytest.fail("worker endpoint probed"))
    monkeypatch.setattr(daemon, "run_claimed", lambda *a, **k: pytest.fail("worker started"))
    monkeypatch.setattr(queue, "_gh", gh)
    monkeypatch.setattr(specloop_client.subprocess, "run", run)
    # Inject completed CLI results for this routing/delivery test; production
    # now starts propose in a separate process (covered by the background tests).
    monkeypatch.setattr(daemon, "poll_specloop", partial(daemon.poll_specloop, specloop_runner=run))

    # No worker checkout is required. A second poll does not duplicate delivery.
    daemon.loop(cfg, {}, once=True)
    daemon.loop(cfg, {}, once=True)
    assert daemon.claim_next(cfg, {"owner/sample": isolated}) is None
    assert len(commands) == 2
    assert commands[1] == ["git", "-C", str(isolated / "source"), "push", "origin",
                           "abc123:refs/heads/spec/sample-issue-7-add-a-gate"]
    command = commands[0]
    assert command[:3] == ["specloop", "propose", str(manifest)]
    assert command[-1] == "--json"
    assert command[command.index("--change") + 1] == "sample-issue-7-add-a-gate"
    assert command[command.index("--branch") + 1] == "spec/sample-issue-7-add-a-gate"
    assert "--idea" not in command
    payload = Path(command[command.index("--idea-file") + 1])
    assert payload.is_absolute()
    assert payload.read_text(encoding="utf-8") == "Add a gate\n\nPlease draft it.\n\nKeep it small."
    receipt = json.loads((daemon.STATE_DIR / "proposals/owner__sample/7.json").read_text())
    assert receipt["result"]["outcome"] == "drafted"
    assert receipt["result"]["commit"] == "abc123"
    assert not queue.CLAIM_DIR.exists()
    assert len([call for call in gh.calls if call[:2] == ["pr", "create"]]) == 1
    assert gh.labels == set()
    assert gh.pr_url in gh.comments[-1]["body"]


def test_pause_prevents_specloop_commands(isolated, monkeypatch, manifest):
    daemon.PAUSE_FILE.touch()
    monkeypatch.setattr(daemon.specloop_intake, "propose_pending",
                        lambda *a, **k: pytest.fail("drafted while paused"))
    daemon.loop(Config(repos=[Repo("o/r", engine="specloop", manifest=manifest)]), {}, once=True)


def test_specloop_poll_is_independent_of_worker_cap(isolated, monkeypatch, manifest):
    seen = []
    monkeypatch.setattr(queue, "_gh", DraftGh(()))
    monkeypatch.setattr(daemon.specloop_intake, "propose_pending", lambda *a, **k: seen.append(a[0].name))
    daemon.loop(Config(repos=[Repo("o/r", engine="specloop", manifest=manifest)], max_tasks_per_night=0),
                {}, once=True)
    assert seen == ["o/r"]


def test_mixed_repos_keep_worker_recovery_and_claims(isolated, monkeypatch, manifest):
    cfg = Config(repos=[Repo("o/spec", engine="specloop", manifest=manifest), Repo("o/worker", "true")])
    recovered, claimed = [], []
    monkeypatch.setattr(queue, "reconcile", lambda name, labels: recovered.append(name) or [])
    monkeypatch.setattr(queue, "claim", lambda name, *a: claimed.append(name))
    dirs = {repo.name: isolated for repo in cfg.repos}
    daemon.startup(cfg, dirs)
    daemon.claim_next(cfg, dirs)
    assert recovered == claimed == ["o/worker"]


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
