"""Selected construction, guarded Gitea dispatch and foreign journal holds."""

from itertools import product
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fake_gitea import FakeGitea, Reply
from gitea_dispatch_fixtures import gitea_pipeline
from manifest_fixtures import manifest_text
from nightshift import daemon, queue, specloop_intake as intake, specloop_job as jobs
from nightshift.config import Config, ForgeConfig, Repo
from nightshift.forge import http
from test_specloop_isolation import Pipeline


TEA = ForgeConfig("tea", "gitea", "https://forge.example/gitea", "TEA_BOT", "TEA_LAND",
                  "Robot", ["Alice"], wip_prefix="Draft: ")
HUB = ForgeConfig("hub", "github")
pytestmark = pytest.mark.real_trust


def forbidden(*args, **kwargs):
    pytest.fail("held operation reached")


@pytest.mark.scenario("forge-interface/selected-forge-construction/github-retains-runner-construction")
@pytest.mark.parametrize("selection", [None, "hub"])
def test_github_factory_retains_runner(monkeypatch, selection):
    repo = Repo("owner/hub", forge=selection)
    cfg = Config([repo], forges=[HUB])
    for _ in range(2):
        runner = Mock(side_effect=forbidden)
        monkeypatch.setattr(queue, "_gh", runner)
        assert daemon.forge_for(repo, cfg=cfg)._runner is runner
        explicit = Mock(side_effect=forbidden)
        assert daemon.forge_for(repo, cfg=cfg, runner=explicit)._runner is explicit
        runner.assert_not_called()
        explicit.assert_not_called()


@pytest.mark.scenario("forge-interface/selected-forge-construction/gitea-uses-its-configured-adapter")
@pytest.mark.parametrize("token", [None, "", "bot-secret"])
def test_gitea_factory_uses_configured_adapter(monkeypatch, token):
    fake, guard = FakeGitea(), Mock(side_effect=forbidden)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setattr(queue, "_gh", forbidden)
    monkeypatch.setenv("GH_TOKEN", "not-the-gitea-token")
    monkeypatch.delenv("TEA_BOT", raising=False)
    repo = Repo("owner/tea", forge="tea")
    cfg = Config([repo], forges=[TEA])
    if token is not None:
        monkeypatch.setenv("TEA_BOT", token)
    if not token:
        with pytest.raises(intake.ConfigurationError, match="tea.*TEA_BOT") as caught:
            daemon.forge_for(repo, cfg=cfg, guard=guard)
        assert not caught.value.transient and "not-the-gitea-token" not in str(caught.value)
    else:
        forge = daemon.forge_for(repo, cfg=cfg, guard=guard)
        assert isinstance(forge, daemon.GiteaForge)
        assert (forge._transport._base, forge._transport._token) == (TEA.url, token)
        assert (forge._bot_login, forge._trusted_logins, forge._wip_prefix) == (
            "robot", frozenset({"alice"}), "Draft: ")
        assert forge._after_request is guard
    assert not fake.calls
    guard.assert_not_called()


@pytest.mark.scenario("forge-interface/human-token-exclusion/landing-environment-refuses-startup")
@pytest.mark.parametrize("entry", ["service", "once", "startup", "poll"])
@pytest.mark.parametrize("value,paused", list(product(["", "human-secret"], [False, True])))
def test_daemon_refuses_land_token_in_env(monkeypatch, entry, value, paused):
    # TEA is deliberately unused: all configured landing keys are excluded.
    cfg = Config([Repo("owner/hub")], forges=[TEA])
    monkeypatch.setenv("TEA_LAND", value)
    if paused:
        daemon.PAUSE_FILE.touch()
    with monkeypatch.context() as patch:
        for owner, names in ((daemon, ["beat", "_note_unclean_exit", "_reconcile_engine_switch"]),
                             (daemon.notify, ["send"]), (jobs, ["reap"]),
                             (daemon.subprocess, ["run", "Popen"])):
            for name in names:
                patch.setattr(owner, name, forbidden)
        with pytest.raises(intake.ConfigurationError, match="tea.*TEA_LAND") as caught:
            if entry == "startup":
                daemon.startup(cfg, {})
            elif entry == "poll":
                daemon.poll_specloop(cfg, daemon.RepoHealth())
            else:
                daemon.loop(cfg, {}, once=entry == "once")
        assert "human-secret" not in str(caught.value)
    monkeypatch.delenv("TEA_LAND")
    daemon.PAUSE_FILE.unlink(missing_ok=True)
    recovered = Mock()
    monkeypatch.setattr(daemon, "_reconcile_engine_switch", recovered)
    daemon.startup(cfg, {})
    recovered.assert_called_once_with(cfg.repos[0], cfg.labels)


@pytest.fixture(params=list(product([False, True], ["refused", "admitted"],
                                   [([], "owner"), (["Alice"], "owner"), (["Alice"], "Alice")])))
def held(request, tmp_path, monkeypatch):
    broken, outcome, (logins, actor) = request.param
    template = tmp_path / "template.toml"
    template.write_text(manifest_text(tmp_path / "source"))
    hub = Pipeline(Repo("owner/hub", manifest=template, forge="hub" if broken else None))
    hub.approved(8)
    tea_template = tmp_path / "tea.toml"
    tea_template.write_text("[broken" if broken else template.read_text())
    tea = Repo("owner/tea", forge="tea", manifest=tea_template)
    cfg = Config([tea, hub.repo], forges=[HUB, ForgeConfig(**{**vars(TEA), "trusted_logins": logins})])
    fake = FakeGitea()
    labels = [{"id": 1, "name": "spec:approved"}, {"id": 2, "name": "agent:revise"}]
    fake.repository(tea.name, labels=labels, pulls=[dict(number=8, labels=list(labels),
                    user={"login": actor}, timeline=[{"actor": {"login": actor}}])])
    def lost_delete(call):
        fake._state_reply(call, None)
        return TimeoutError("accepted DELETE; response lost")
    fake.route("DELETE", "/gitea/api/v1/repos/owner/tea/issues/8/labels/1", lost_delete)
    monkeypatch.setattr(http, "build_opener", lambda: fake)
    monkeypatch.setenv("TEA_BOT", "configured-bot")
    for folder, success in (("approvals", "approved"), ("admissions", "admitted"),
                            ("revisions", "revised"), ("publications", "published")):
        jobs.save(daemon.STATE_DIR / folder / "owner__tea/8.json", {
            "change": "tea-issue-8-gate", "head": "a" * 40, "approval_event": 1,
            "result": {"outcome": "refused" if outcome == "refused" else success},
            "delivered": False, "consumed_feedback_ids": ["old"]})
    jobs.save(daemon.STATE_DIR / "admissions/owner__tea/8.jobs" / ("a" * 32) / "result.json",
              {"result": {"outcome": outcome}})
    run = daemon.STATE_DIR / "runs/tea-issue-8-gate"
    jobs.save(run / "state.json", dict(change=run.name, status="paused",
              pr=TEA.url + "/owner/tea/pulls/8", pause={"reason": "ready_to_publish", "detail": "Check it"}))
    (run / "manifest.toml").write_text(template.read_text().replace("[run]\n",
        '[run]\nchange = "tea-issue-8-gate"\nbranch = "spec/tea-issue-8-gate"\n'))
    (tmp_path / "work").mkdir()
    (tmp_path / "work/diff.txt").write_text("surviving work")
    queue.Claim(tea.name, 8, "claude/8", str(tmp_path / "work"), "yesterday").write()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before.update({p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*") if p.is_file()})
    originals = {}
    for owner, name in [(daemon, "_reconcile_engine_switch"), (intake, "manifest_readiness"),
                        *[(intake, n) for n in ("propose_pending", "admit_pending", "revise_pending",
                                               "publish_pending", "report_failure")]]:
        original = getattr(owner, name)
        originals[owner, name] = original
        def github_only(repo, *args, _original=original, **kwargs):
            if repo.name != hub.repo.name:
                forbidden()
            assert "forge" not in kwargs
            return _original(repo, *args, **kwargs)
        monkeypatch.setattr(owner, name, github_only)
    for owner, name in ((socket, "socket"), (daemon.subprocess, "run"),
                        (daemon.subprocess, "Popen"), (daemon, "forge_for")):
        originals[owner, name] = getattr(owner, name)
        monkeypatch.setattr(owner, name, forbidden)
    def github(args):
        if tea.name in " ".join(args):
            forbidden()
        return hub.gh(args)
    monkeypatch.setattr(queue, "_gh", github)
    monkeypatch.setattr(intake, "run_git", hub.command)
    messages = []
    monkeypatch.setattr(daemon.notify, "send", lambda hook, message: messages.append(message))
    return SimpleNamespace(cfg=cfg, hub=hub, fake=fake, before=before, messages=messages,
                           remote=json.dumps(fake.repositories, sort_keys=True), originals=originals)


def check_hold(held):
    daemon.PAUSE_FILE.touch()
    daemon.startup(held.cfg, {})
    daemon.poll_specloop(held.cfg, daemon.RepoHealth(), runner=forbidden, specloop_runner=forbidden)
    assert not held.messages
    daemon.PAUSE_FILE.unlink()
    for _ in range(2):  # Restart loses in-memory health, never durable evidence.
        health = daemon.RepoHealth()
        daemon.startup(held.cfg, {}, health)
        for _ in range(2):
            daemon.poll_specloop(held.cfg, health, runner=queue._gh, specloop_runner=held.hub.command)
        assert health.failures == {"owner/tea": 3} and not health.item_failures
    assert not held.fake.calls and json.dumps(held.fake.repositories, sort_keys=True) == held.remote
    assert {p: p.read_bytes() for p in held.before} == held.before
    assert not held.hub.prs[8]["labels"] and len(held.hub.runs) == 1
    assert len(held.messages) == 2


@pytest.mark.scenario("forge-interface/eligible-gitea-dispatch/healthy-gitea-front-door-runs")
@pytest.mark.scenario("forge-interface/selected-forge-construction/gitea-uses-its-configured-adapter")
@pytest.mark.scenario("config-resilience/foreign-recovery-journal-hold/journal-location-is-uncertain-or-clear")
@pytest.mark.parametrize("location", ["absent", "empty", "nested"])
def test_gitea_dispatch_runs_after_hold_lifted(gitea_pipeline, monkeypatch, location):
    e = gitea_pipeline
    root = daemon.STATE_DIR / "engine-switch/owner__tea"
    if location != "absent":
        root.mkdir(parents=True)
    if location == "nested":
        jobs.save(root / "completed/8.json", {"done": True})
    stages = []
    for name in ("propose_pending", "admit_pending", "revise_pending", "publish_pending"):
        original = getattr(intake, name)
        def stage(*args, _name=name, _original=original, **kwargs):
            stages.append((_name, kwargs["forge"]))
            return _original(*args, **kwargs)
        monkeypatch.setattr(intake, name, stage)
    daemon.startup(e.cfg, {}, e.health)
    assert not e.fake.calls
    e.poll()
    assert len(stages) == 4 and len({id(forge) for _, forge in stages}) == 1
    assert isinstance(stages[0][1], daemon.GiteaForge)
    assert not e.health.failures and not e.messages
    issue, = e.remote["issues"]
    pr, = e.remote["pulls"]
    assert pr["title"].startswith("Draft: ")
    assert [label["name"] for label in pr["labels"]] == ["spec:review"]
    assert not issue["labels"] and pr["html_url"] in issue["comments"][0]["body"]
    assert json.loads(e.receipt_path.read_text())["delivered"]
    assert sum(c[:2] == ["specloop", "propose"] for c in e.pipeline.calls) == 1


@pytest.mark.scenario("forge-interface/eligible-gitea-dispatch/pending-delivery-retains-existing-recovery")
@pytest.mark.parametrize("boundary", ["pulls", "comments"])
def test_gitea_visible_accepted_create_recovers_after_restart(gitea_pipeline, boundary):
    e = gitea_pipeline
    path = e.root + ("/pulls" if boundary == "pulls" else "/issues/1/comments")
    def accepted(call):
        e.fake._state_reply(call, None)
        return TimeoutError("accepted response lost")
    e.fake.route("POST", path, accepted)
    if boundary == "comments":
        # Fail the immediate reconciliation; the next complete listing sees it.
        e.fake.route("GET", path, *[Reply([])] * 3, *[Reply(status=503)] * 3)
    e.poll()
    pending = json.loads(e.receipt_path.read_text())
    assert pending["result"]["outcome"] == "drafted" and not pending.get("delivered")
    assert len(e.remote["pulls"]) == 1
    assert len(e.remote["issues"][0].get("comments", [])) == int(boundary == "comments")
    e.fake.routes.clear()
    e.health = daemon.RepoHealth()
    daemon.startup(e.cfg, {}, e.health)
    for _ in range(3):
        e.poll()
    # Synchronous legacy receipts lack a job identity; existing PR recovery
    # finishes remote delivery without rewriting that historical receipt.
    assert json.loads(e.receipt_path.read_text())["result"] == pending["result"]
    assert not e.remote["issues"][0]["labels"]
    assert len(e.remote["pulls"]) == len(e.remote["issues"][0]["comments"]) == 1
    assert sum(c.method == "POST" and c.path == path for c in e.fake.calls) == 1
    assert sum(c[:2] == ["specloop", "propose"] for c in e.pipeline.calls) == 1
    assert not e.health.failures


@pytest.mark.scenario("forge-interface/guarded-daemon-calls/pause-during-dispatch-and-retry")
@pytest.mark.parametrize("boundary", ["final-write", "retry", "reconcile"])
def test_gitea_pause_stops_delivery_and_transport_followups(gitea_pipeline, monkeypatch, boundary):
    e = gitea_pipeline
    if boundary == "final-write":
        trigger = next(d["id"] for d in e.remote["labels"] if d["name"] == "spec:draft")
        path = e.root + f"/issues/1/labels/{trigger}"
        def accepted(call):
            reply = e.fake._state_reply(call, None)
            daemon.PAUSE_FILE.touch()
            return reply
        e.fake.route("DELETE", path, accepted)
    elif boundary == "retry":
        path = e.root + "/issues/1/timeline"
        e.fake.route("GET", path, Reply(status=503), page=1)
        monkeypatch.setattr(http, "sleep", lambda _: daemon.PAUSE_FILE.touch())
    else:
        path = e.root + "/issues/1/comments"
        def accepted(call):
            e.fake._state_reply(call, None)
            daemon.PAUSE_FILE.touch()
            return TimeoutError("accepted response lost")
        e.fake.route("POST", path, accepted)
    e.poll()
    assert daemon.PAUSE_FILE.exists() and e.fake.calls[-1].path == path
    assert not e.health.failures and not e.health.item_failures
    saved = e.receipt_path.read_bytes() if e.receipt_path.exists() else None
    assert saved is None or not json.loads(saved).get("delivered")
    calls = list(e.fake.calls)
    e.poll()
    assert e.fake.calls == calls
    if boundary != "final-write":
        e.fake.routes.clear()
        daemon.PAUSE_FILE.unlink()
        e.poll()
        assert not e.remote["issues"][0]["labels"]
        assert len(e.remote["issues"][0]["comments"]) == len(e.remote["pulls"]) == 1


@pytest.mark.scenario("forge-interface/eligible-gitea-dispatch/transient-outages-never-become-item-holds")
def test_gitea_transient_timeline_never_escalates_item(gitea_pipeline):
    e = gitea_pipeline
    jobs.save(e.receipt_path, {"retained": "unfinished work"})
    before = e.receipt_path.read_bytes()
    path = e.root + "/issues/1/timeline"
    attempts = e.health.threshold + 2
    e.fake.route("GET", path, *[Reply(status=503)] * (3 * attempts), page=1)
    for _ in range(attempts):
        e.poll()
        assert e.receipt_path.read_bytes() == before
        assert [d["name"] for d in e.remote["issues"][0]["labels"]] == ["spec:draft"]
        assert not e.remote["issues"][0]["comments"] and not e.remote["pulls"]
    assert e.health.failures == {e.repo.name: attempts} and len(e.messages) == 1
    assert e.health.item_failures[e.repo.name, "draft", 1] == attempts
    assert all(c.method == "GET" for c in e.fake.calls)
    e.fake.routes.clear()  # Comment and label endpoints were writable throughout.
    e.poll()
    assert json.loads(e.receipt_path.read_text())["delivered"]
    assert not e.health.failures and not e.health.item_failures


@pytest.mark.scenario("gitea-forge/verified-label-mutations/needs-human-remains-a-safe-hold")
@pytest.mark.scenario("forge-interface/eligible-gitea-dispatch/pending-delivery-retains-existing-recovery")
def test_gitea_failure_reporting_retains_trigger_and_human_hold(gitea_pipeline, monkeypatch):
    e = gitea_pipeline
    e.pipeline.fail = "push"
    reports = []
    adapters = []
    factory = daemon.forge_for
    def selected(*args, **kwargs):
        adapter = factory(*args, **kwargs)
        adapters.append(adapter)
        return adapter
    monkeypatch.setattr(daemon, "forge_for", selected)
    report = intake.report_failure
    def reporting(*args, **kwargs):
        reports.append(kwargs["forge"])
        return report(*args, **kwargs)
    monkeypatch.setattr(intake, "report_failure", reporting)
    def accepted(call):
        e.fake._state_reply(call, None)
        return TimeoutError("accepted needs-human response lost")
    e.fake.route("POST", e.root + "/issues/1/labels", accepted)
    e.poll()
    assert reports == adapters and len(reports) == 1
    assert isinstance(reports[0], daemon.GiteaForge)
    issue = e.remote["issues"][0]
    assert {d["name"] for d in issue["labels"]} == {"spec:draft", "needs-human"}
    saved = e.receipt_path.read_bytes()
    remote = json.dumps(e.remote, sort_keys=True)
    e.fake.routes.clear()
    e.pipeline.fail = None
    for _ in range(2):
        e.health = daemon.RepoHealth()
        daemon.startup(e.cfg, {}, e.health)
        start = len(e.fake.calls)
        e.poll()
        assert e.receipt_path.read_bytes() == saved and json.dumps(e.remote, sort_keys=True) == remote
        assert all(c.method == "GET" for c in e.fake.calls[start:])
    issue["labels"][:] = [d for d in issue["labels"] if d["name"] != "needs-human"]
    e.poll()
    assert json.loads(e.receipt_path.read_text())["delivered"]
    assert sum(c[:2] == ["specloop", "propose"] for c in e.pipeline.calls) == 1


@pytest.mark.scenario("forge-interface/eligible-gitea-dispatch/repository-failures-stay-local")
@pytest.mark.scenario("forge-interface/selected-forge-construction/github-retains-runner-construction")
@pytest.mark.parametrize("failure", [None, "credential", "manifest", "stage"])
def test_gitea_mixed_repositories_remain_independent(gitea_pipeline, monkeypatch, failure):
    e = gitea_pipeline
    hub = Pipeline(Repo("owner/hub", manifest=e.repo.manifest))
    hub.approved(8)
    second = Repo("owner/second", forge="tea", manifest=e.repo.manifest)
    e.fake.repository(second.name, labels=e.remote["labels"], issues=deepcopy(e.remote["issues"]))
    e.cfg.repos.extend([hub.repo, second])
    original_factory = daemon.forge_for
    selected = []
    def factory(repo, **kwargs):
        if failure == "credential" and repo == e.repo:
            with monkeypatch.context() as patch:
                patch.delenv("TEA_BOT")
                return original_factory(repo, **kwargs)
        forge = original_factory(repo, **kwargs)
        selected.append(repo.name)
        return forge
    monkeypatch.setattr(daemon, "forge_for", factory)
    if failure == "manifest":
        broken = e.repo.manifest.with_name("broken.toml")
        broken.write_text("[broken")
        e.cfg.repos[0] = Repo(e.repo.name, forge="tea", manifest=broken)
    if failure == "stage":
        e.fake.route("GET", e.root + "/issues", *[Reply(status=503)] * 3, page=1)
    recovered = []
    monkeypatch.setattr(daemon, "_reconcile_engine_switch",
                        lambda repo, *a, **kw: recovered.append(repo.name))
    original = intake.admit_pending
    def admit(repo, *args, **kwargs):
        assert ("forge" in kwargs) == (repo.forge == "tea")
        return original(repo, *args, **kwargs)
    monkeypatch.setattr(intake, "admit_pending", admit)
    daemon.poll_specloop(e.cfg, e.health, runner=hub.gh, specloop_runner=hub.command)
    assert recovered == [hub.repo.name]
    assert selected == ([second.name] if failure == "credential" else [e.repo.name, second.name])
    assert not hub.prs[8]["labels"] and len(hub.runs) == 1
    peer = e.fake.repositories[second.name]
    assert not peer["issues"][0]["labels"] and len(peer["pulls"]) == 1
    assert e.health.failures == ({e.repo.name: 1} if failure else {})
    if failure == "credential":
        assert "TEA_BOT" in e.messages[0] and "test-bot-token" not in e.messages[0]


@pytest.mark.scenario("forge-interface/eligible-gitea-dispatch/healthy-gitea-front-door-runs")
@pytest.mark.parametrize("actor", ["owner", "outsider", "Robot", " Alice"])
def test_gitea_dispatch_still_requires_allowlisted_actor(gitea_pipeline, actor):
    e = gitea_pipeline
    issue = e.remote["issues"][0]
    issue["timeline"][0]["user"] = {"login": actor}
    for _ in range(2):
        e.health = daemon.RepoHealth()
        daemon.startup(e.cfg, {}, e.health)
        e.poll()
    assert not e.remote["pulls"] and not e.receipt_path.exists()
    assert len(issue["comments"]) == 1 and "allowlist" in issue["comments"][0]["body"]
    assert [d["name"] for d in issue["labels"]] == ["spec:draft"]
    assert not any(c[:2] == ["specloop", "propose"] for c in e.pipeline.calls)


@pytest.mark.scenario("forge-interface/human-token-exclusion/landing-environment-refuses-startup")
@pytest.mark.parametrize("value", ["", "test-human-token"])
def test_gitea_landing_environment_precedes_eligible_dispatch(gitea_pipeline, monkeypatch, value):
    e = gitea_pipeline
    monkeypatch.setenv("TEA_LAND", value)
    for entry in (lambda: daemon.startup(e.cfg, {}, e.health), e.poll):
        with pytest.raises(intake.ConfigurationError, match="TEA_LAND") as caught:
            entry()
        assert "test-human-token" not in str(caught.value)
    assert not e.fake.calls and not e.pipeline.calls and not e.messages
    monkeypatch.delenv("TEA_LAND")
    daemon.startup(e.cfg, {}, e.health)
    e.poll()
    assert json.loads(e.receipt_path.read_text())["delivered"] and not e.health.failures


@pytest.mark.scenario("config-resilience/foreign-recovery-journal-hold/gitea-journal-holds-the-repository")
@pytest.mark.scenario("config-resilience/foreign-recovery-journal-hold/journal-location-is-uncertain-or-clear")
@pytest.mark.scenario("config-resilience/health-gates-new-work-only/foreign-journal-overrides-admitted-delivery")
@pytest.mark.parametrize("journal", ['{"done": false}', '{"done": true}', 'broken', 'unreadable'])
def test_gitea_repo_with_engine_switch_journal_is_held(held, monkeypatch, journal):
    root = daemon.STATE_DIR / "engine-switch/owner__tea"
    root.mkdir(parents=True)
    path = root / "8.json"
    jobs.save(path, {})
    path.write_text(journal)
    held.before[path] = path.read_bytes()
    with monkeypatch.context() as patch:
        if journal == "unreadable":
            original = Path.iterdir
            def unreadable(self):
                if self == root:
                    raise PermissionError("unreadable")
                return original(self)
            patch.setattr(Path, "iterdir", unreadable)
        check_hold(held)
    assert all(str(root) in m for m in held.messages)
    path.unlink()  # Only the operator clears foreign evidence.
    del held.before[path]
    template = held.cfg.repos[0].manifest
    held.before[template] = held.hub.repo.manifest.read_bytes()
    template.write_bytes(held.before[template])
    held.messages.clear()
    monkeypatch.setattr(daemon, "forge_for", held.originals[daemon, "forge_for"])
    monkeypatch.setattr(intake, "manifest_readiness", held.originals[intake, "manifest_readiness"])
    reached = []
    for name in ("propose_pending", "admit_pending", "revise_pending", "publish_pending"):
        def stage(repo, *args, _name=name, **kwargs):
            if repo.name == "owner/tea":
                assert isinstance(kwargs["forge"], daemon.GiteaForge)
                reached.append(_name)
            else:
                assert "forge" not in kwargs
        monkeypatch.setattr(intake, name, stage)
    health = daemon.RepoHealth()
    daemon.startup(held.cfg, {}, health)
    daemon.poll_specloop(held.cfg, health, runner=queue._gh, specloop_runner=held.hub.command)
    assert len(reached) == 4 and not health.failures and not held.messages
    assert {p: p.read_bytes() for p in held.before} == held.before
